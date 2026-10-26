"""
extraction.py – Document extraction for ClaimLens AI.

Public API
----------
    result  = extract_document(file_path)
    results = extract_documents_parallel(file_paths, max_workers=3)

Returns (per document)
-------
    {
        "document_type":     str,          # one of the DOCTYPE_* constants
        "fields":            list[dict],   # see FieldResult TypedDict
        "pages":             list[dict],   # {"page": int, "text": str}
        "extraction_method": str,          # "gemma-4" | "regex-fallback"
        "model_name":        str,          # model identifier used, or ""
    }

Extraction modes
----------------
    EXTRACTION_MODE=regex  (default)   – regex / next-line-label path
    EXTRACTION_MODE=gemma              – Gemma 4 via google-genai SDK

    GEMMA_MODEL (default "gemma-4-26b-a4b-it")
    GEMINI_API_KEY – loaded from backend/.env via main.py's load_dotenv()

API resilience (gemma mode)
---------------------------
    - 30 s timeout per API call
    - Up to 2 retries with 2 s wait on 500 / 503 / 429 / timeout errors
    - Final failure falls back to regex; extraction_method = "regex-fallback"

Parallel processing
-------------------
    extract_documents_parallel() runs up to 3 concurrent API calls via
    ThreadPoolExecutor so multi-document claims complete faster.

Disk cache
----------
    Results are persisted in backend/cache/extraction_cache.json keyed by
    SHA-256(file) + ":" + model_name so they survive server restarts.
    The cache/ directory is already in .gitignore.

Cross-method disagreement
-------------------------
    When EXTRACTION_MODE=gemma, the regex path also runs.  If both succeed
    on a key field but disagree, that field's confidence is lowered to 0.5
    and a note is appended to the value so reviewers are alerted.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json as _json
import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

import pymupdf  # PyMuPDF 2.x
from pydantic import BaseModel, ValidationError

# ── logging ───────────────────────────────────────────────────────────────────

log = logging.getLogger(__name__)

# ── document type constants ───────────────────────────────────────────────────

DOCTYPE_HOSPITAL_BILL     = "hospital_bill"
DOCTYPE_CLAIM_FORM        = "claim_form"
DOCTYPE_DISCHARGE_SUMMARY = "discharge_summary"
DOCTYPE_PRESCRIPTION      = "prescription"
DOCTYPE_UNKNOWN           = "unknown"

# ── confidence levels ─────────────────────────────────────────────────────────

CONF_MATCH    = 0.9
CONF_MISSING  = 0.3
CONF_DISAGREE = 0.5   # gemma and regex disagree

# ── file-extension sets ───────────────────────────────────────────────────────

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}
PDF_EXT    = ".pdf"

_MIME_MAP: dict[str, str] = {
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".bmp":  "image/bmp",
    ".tiff": "image/tiff",
    ".tif":  "image/tiff",
    ".webp": "image/webp",
}

# ── API call parameters ───────────────────────────────────────────────────────

_API_TIMEOUT_SECS = 30
_MAX_RETRIES      = 2
_RETRY_WAIT_SECS  = 2

# ── fields compared for disagreement detection ────────────────────────────────

_COMPARE_FIELDS = {
    "patient_name", "policy_number", "claimed_amount", "total_amount",
    "admission_date", "discharge_date", "bill_date", "policy_start_date",
}


# ── typed return shapes ───────────────────────────────────────────────────────

class FieldResult(TypedDict):
    name:        str
    value:       str
    confidence:  float
    page_number: int


class PageText(TypedDict):
    page: int
    text: str


class ExtractionResult(TypedDict):
    document_type:     str
    fields:            list[FieldResult]
    pages:             list[PageText]
    extraction_method: str   # "gemma-4" | "regex-fallback"
    model_name:        str


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic model for Gemma JSON output validation
# ─────────────────────────────────────────────────────────────────────────────

class _LineItem(BaseModel):
    description: str | None = None
    amount: float | str | None = None


class _GemmaResponse(BaseModel):
    document_type:     str | None = None
    patient_name:      Any = None
    policy_number:     Any = None
    policy_start:      Any = None
    hospital_name:     Any = None
    bill_number:       Any = None
    bill_date:         Any = None
    admission_date:    Any = None
    discharge_date:    Any = None
    diagnosis:         Any = None
    claimed_amount:    Any = None
    total_amount:      Any = None
    line_items:        list[_LineItem] | None = None
    signature_present: Any = None
    confidence:        dict[str, float] | float | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Disk-backed extraction cache
# ─────────────────────────────────────────────────────────────────────────────

_CACHE_DIR  = Path(__file__).resolve().parent.parent / "cache"
_CACHE_FILE = _CACHE_DIR / "extraction_cache.json"
_cache_lock = threading.Lock()

# In-memory mirror of the disk cache.  Loaded once at first access.
_mem_cache: dict[str, ExtractionResult] | None = None


def _load_cache() -> dict[str, ExtractionResult]:
    global _mem_cache
    if _mem_cache is not None:
        return _mem_cache
    with _cache_lock:
        if _mem_cache is not None:   # double-checked locking
            return _mem_cache
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if _CACHE_FILE.exists():
            try:
                _mem_cache = _json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
                log.info("Loaded %d entries from disk cache", len(_mem_cache))
            except Exception as exc:
                log.warning("Could not read disk cache (%s); starting fresh", type(exc).__name__)
                _mem_cache = {}
        else:
            _mem_cache = {}
    return _mem_cache


def _save_cache(cache: dict) -> None:
    """Flush the in-memory cache to disk (holding the lock)."""
    try:
        _CACHE_FILE.write_text(
            _json.dumps(cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        log.warning("Could not write disk cache (%s)", type(exc).__name__)


def _cache_key(file_hash: str, model_name: str) -> str:
    return f"{file_hash}:{model_name}"


def _cache_get(file_hash: str, model_name: str) -> ExtractionResult | None:
    cache = _load_cache()
    return cache.get(_cache_key(file_hash, model_name))


def _cache_set(file_hash: str, model_name: str, result: ExtractionResult) -> None:
    with _cache_lock:
        cache = _load_cache()
        cache[_cache_key(file_hash, model_name)] = result
        _save_cache(cache)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Gemma instruction prompt
# ─────────────────────────────────────────────────────────────────────────────

_GEMMA_PROMPT = (
    "Classify the document as claim_form, hospital_bill, discharge_summary or prescription. "
    "Extract the following fields: "
    "patient_name (called 'Insured Name' on claim forms), "
    "policy_number, policy_start, hospital_name, bill_number, bill_date, "
    "admission_date, discharge_date, diagnosis, claimed_amount, total_amount, "
    "line_items (array of {description, amount}), "
    "signature_present (set to false if the signature line says Missing or not signed). "
    "Use null for anything not visible instead of guessing. "
    "Give a confidence from 0 to 1 for each field in a 'confidence' object. "
    "Return JSON only."
)


# ─────────────────────────────────────────────────────────────────────────────
# Regex / label-based extraction helpers
# ─────────────────────────────────────────────────────────────────────────────

_DOCTYPE_RULES: list[tuple[str, list[str], list[str]]] = [
    (
        DOCTYPE_HOSPITAL_BILL,
        ["bill", "invoice"],
        ["room charges", "total amount", "hospital bill", "tax invoice",
         "operation theatre", "surgeon", "line item"],
    ),
    (
        DOCTYPE_CLAIM_FORM,
        ["claim"],
        ["claim form", "policy number", "claimed amount", "claimant",
         "insured", "reimbursement", "cashless"],
    ),
    (
        DOCTYPE_DISCHARGE_SUMMARY,
        ["discharge"],
        ["discharge summary", "admission", "diagnosis", "attending",
         "consultant", "procedure", "treatment"],
    ),
    (
        DOCTYPE_PRESCRIPTION,
        ["prescription", "rx"],
        ["dosage", "tablets", "capsules", "syrup", "mg", "once daily",
         "twice daily", "dr.", "physician"],
    ),
]


def _classify_document_type(text: str) -> str:
    lower = text.lower()
    best_type  = DOCTYPE_UNKNOWN
    best_score = 0
    for dtype, required, bonus in _DOCTYPE_RULES:
        if not all(kw in lower for kw in required):
            continue
        score = len(required) + sum(1 for kw in bonus if kw in lower)
        if score > best_score:
            best_score = score
            best_type  = dtype
    return best_type


_KNOWN_LABELS: frozenset[str] = frozenset({
    "patient name", "insured name", "name of patient", "claimant name",
    "patient", "insured",
    "policy number", "policy no", "policy start date",
    "hospital name", "admission date", "discharge date", "bill date",
    "claimed amount", "amount claimed", "bill number", "original bill ref.",
    "bill no.", "invoice no.", "bill ref.",
    "diagnosis", "date of birth", "insured details", "hospitalisation details",
    "claim details", "declaration", "treating doctor", "clinical summary",
    "bill particulars", "sr.", "description", "amount", "total amount", "grand total",
})


def _normalise_line(line: str) -> str:
    return re.sub(r"\s+", " ", line.replace("\xa0", " ")).strip()


def _value_after_label(lines: list[str], label_variants: list[str]) -> str | None:
    norm_variants = {v.lower().rstrip(":").strip() for v in label_variants}
    for i, raw_line in enumerate(lines):
        norm = _normalise_line(raw_line).lower().rstrip(":").strip()
        if norm not in norm_variants:
            continue
        colon_m = re.match(r"^[^:]+:\s*(.+)$", _normalise_line(raw_line), re.IGNORECASE)
        if colon_m:
            candidate = colon_m.group(1).strip()
            if candidate:
                return candidate
        for j in range(i + 1, len(lines)):
            candidate = _normalise_line(lines[j])
            if not candidate:
                continue
            if candidate.lower().rstrip(":").strip() in _KNOWN_LABELS:
                return None
            return candidate
    return None


def _parse_amount(raw: str) -> float | None:
    m = re.search(r"([\d,]+(?:\.\d{1,2})?)", raw)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_date_str(raw: str) -> datetime | None:
    raw = re.sub(r"[./]", "-", raw.strip())
    for fmt in ("%d-%b-%Y", "%d/%b/%Y", "%d-%m-%Y", "%d/%m/%Y",
                "%d-%b-%y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _extract_line_items(text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen:  set[str] = set()

    SKIP_WORDS = {
        "total", "bill no", "bill number", "patient", "policy",
        "admission", "discharge", "diagnosis", "hospital", "invoice",
        "description", "amount", "sr.", "date of birth",
    }

    def _keep(desc: str) -> bool:
        low = desc.lower()
        return len(desc) >= 6 and not any(w in low for w in SKIP_WORDS)

    def _add(desc: str, raw_amt: str) -> None:
        key = desc.lower()[:50]
        if key in seen:
            return
        seen.add(key)
        try:
            items.append({"description": desc, "amount": float(raw_amt.replace(",", ""))})
        except ValueError:
            pass

    block_m = re.search(
        r"Bill\s+Particulars.*?\n(.*?)(?:TOTAL\s+AMOUNT|GRAND\s+TOTAL|Total\b)",
        text, re.IGNORECASE | re.DOTALL,
    )
    if block_m:
        lines = [ln.strip() for ln in block_m.group(1).splitlines() if ln.strip()]
        start = next(
            (i + 1 for i, ln in enumerate(lines)
             if re.fullmatch(r"Sr\.?", ln, re.IGNORECASE) or ln.lower() in ("description", "amount")),
            0,
        )
        lines = lines[start:]
        i = 0
        while i < len(lines):
            if re.fullmatch(r"\d{1,3}", lines[i]):
                if i + 2 < len(lines):
                    desc  = lines[i + 1]
                    amt_m = re.search(r"([\d,]+(?:\.\d{1,2})?)", lines[i + 2])
                    if amt_m and _keep(desc):
                        _add(desc, amt_m.group(1))
                i += 3
                continue
            i += 1

    for m in re.finditer(
        r"^\s*(?:\d{1,3}\s+)?([A-Za-z][^\n]{5,100}?)\s+"
        r"(?:Rs\.?\s*|INR\s*|₹\s*|I(?=[\d,]))([\d,]+(?:\.\d{1,2})?)\s*(?:/-|/–)?\s*$",
        text, re.MULTILINE | re.IGNORECASE,
    ):
        desc = m.group(1).strip()
        if _keep(desc):
            _add(desc, m.group(2))

    return items


def _detect_signature(text: str) -> str:
    sig_line_m = re.search(
        r"claimant\s+signature\s*:?\s*\[([^\]]*)\]", text, re.IGNORECASE
    )
    if sig_line_m:
        inside = sig_line_m.group(1).strip()
        if not inside:
            return "Missing"
        if re.fullmatch(r"signed", inside, re.IGNORECASE):
            return "Present"
        if re.search(r"\bmissing\b|\bnot\s+signed\b", inside, re.IGNORECASE):
            return "Missing"
        return "Present"

    lower = text.lower()
    if re.search(r"\bsignature\b.*\bmissing\b|\bnot\s+signed\b|\bunsigned\b|\bno\s+signature\b", lower):
        return "Missing"
    if re.search(r"\bsignature\b.*\bpresent\b|\bsigned\b", lower):
        return "Present"
    if re.search(r"\bsignature\b", lower):
        return "Present"
    return "Unknown"


def _extract_fields_from_text(full_text: str, page_texts: list[str]) -> list[FieldResult]:
    all_lines: list[str] = []
    for pt in page_texts:
        all_lines.extend(pt.splitlines())

    def _page_of(value: str) -> int:
        for i, pt in enumerate(page_texts, start=1):
            if value.lower() in pt.lower():
                return i
        return 1

    def _field(name: str, value: str | None, page: int | None = None) -> FieldResult:
        v  = (value or "").strip()
        pg = page if page is not None else (_page_of(v) if v else 1)
        return FieldResult(name=name, value=v,
                           confidence=CONF_MATCH if v else CONF_MISSING,
                           page_number=pg)

    results: list[FieldResult] = []

    patient = _value_after_label(all_lines, [
        "Patient Name", "Insured Name", "Name of Patient", "Claimant Name",
        "Patient", "Insured",
    ])
    results.append(_field("patient_name", patient))

    hosp = _value_after_label(all_lines, ["Hospital Name"])
    if not hosp:
        hosp = next((ln.strip() for ln in all_lines if ln.strip()), None)
    results.append(_field("hospital_name", hosp, page=1))

    results.append(_field("policy_number",
                           _value_after_label(all_lines, ["Policy Number", "Policy No"])))

    raw_start = _value_after_label(all_lines, ["Policy Start Date"])
    dt = _parse_date_str(raw_start) if raw_start else None
    results.append(_field("policy_start_date", dt.strftime("%d-%b-%Y") if dt else raw_start))

    raw_bd = _value_after_label(all_lines, ["Bill Date"])
    dt = _parse_date_str(raw_bd) if raw_bd else None
    results.append(_field("bill_date", dt.strftime("%d-%b-%Y") if dt else raw_bd))

    raw_ad = _value_after_label(all_lines, ["Admission Date"])
    dt = _parse_date_str(raw_ad) if raw_ad else None
    results.append(_field("admission_date", dt.strftime("%d-%b-%Y") if dt else raw_ad))

    raw_dd = _value_after_label(all_lines, ["Discharge Date"])
    dt = _parse_date_str(raw_dd) if raw_dd else None
    results.append(_field("discharge_date", dt.strftime("%d-%b-%Y") if dt else raw_dd))

    results.append(_field("bill_number",
                           _value_after_label(all_lines, [
                               "Bill Number", "Original Bill Ref.",
                               "Bill No.", "Invoice No.", "Bill Ref.",
                           ])))
    results.append(_field("diagnosis", _value_after_label(all_lines, ["Diagnosis"])))

    raw_ca = _value_after_label(all_lines, ["Claimed Amount", "Amount Claimed"])
    if raw_ca:
        amt = _parse_amount(raw_ca)
        ca_val = f"Rs. {amt:,.2f}" if amt is not None else raw_ca
    else:
        ca_val = None
    results.append(_field("claimed_amount", ca_val))

    total_m = re.search(
        r"(?:TOTAL[\s\xa0]+AMOUNT|GRAND[\s\xa0]+TOTAL|Total\b)"
        r"[^\d\n]*\n?[\s\xa0]*"
        r"(?:Rs\.?[\s\xa0]*|INR[\s\xa0]*|₹[\s\xa0]*|I(?=[\d,]))?"
        r"([\d,]+(?:\.\d{1,2})?)[\s\xa0]*(?:/-|/–)?",
        full_text, re.IGNORECASE,
    )
    if total_m:
        t_amt = _parse_amount(total_m.group(1))
        total_val: str | None = f"Rs. {t_amt:,.2f}" if t_amt is not None else total_m.group(1)
    else:
        total_val = None
    results.append(_field("total_amount", total_val))

    results.append(_field("signature_present", _detect_signature(full_text)))

    items = _extract_line_items(full_text)
    results.append(FieldResult(
        name="line_items",
        value=_json.dumps(items),
        confidence=CONF_MATCH if items else CONF_MISSING,
        page_number=1,
    ))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Regex-based extraction
# ─────────────────────────────────────────────────────────────────────────────

def _regex_extract(path: Path) -> ExtractionResult:
    suffix = path.suffix.lower()

    if suffix in IMAGE_EXTS:
        return ExtractionResult(
            document_type=DOCTYPE_UNKNOWN,
            fields=[FieldResult(
                name="extraction_note",
                value=f"Image extraction pending model integration. File: {path.name}",
                confidence=0.0,
                page_number=0,
            )],
            pages=[PageText(page=1, text="[IMAGE FILE – no raw text available]")],
            extraction_method="regex-fallback",
            model_name="",
        )

    doc        = pymupdf.open(str(path))
    page_texts = [page.get_text() for page in doc]
    doc.close()

    full_text     = "\n".join(page_texts)
    document_type = _classify_document_type(full_text)
    fields        = _extract_fields_from_text(full_text, page_texts)
    pages         = [PageText(page=i + 1, text=t) for i, t in enumerate(page_texts)]

    return ExtractionResult(
        document_type=document_type,
        fields=fields,
        pages=pages,
        extraction_method="regex-fallback",
        model_name="",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Gemma 4 extraction helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_gemini_client():
    """Return a google.genai.Client. Key is read from env, never logged."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY not found. Set it in backend/.env or as an environment variable."
        )
    from google import genai
    return genai.Client(api_key=api_key)


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else text


def _coerce_amount(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, (int, float)):
        return f"Rs. {float(raw):,.2f}"
    parsed = _parse_amount(str(raw))
    return f"Rs. {parsed:,.2f}" if parsed is not None else str(raw)


def _coerce_date(raw: Any) -> str:
    if raw is None:
        return ""
    dt = _parse_date_str(str(raw))
    return dt.strftime("%d-%b-%Y") if dt else str(raw)


def _coerce_bool_to_str(raw: Any) -> str:
    if raw is None:
        return "Unknown"
    if isinstance(raw, bool):
        return "Present" if raw else "Missing"
    s = str(raw).strip().lower()
    if s in ("true", "yes", "present", "signed"):
        return "Present"
    if s in ("false", "no", "missing", "not signed", "unsigned"):
        return "Missing"
    return str(raw)


def _gemma_response_to_fields(gr: _GemmaResponse) -> list[FieldResult]:
    def _conf(field_name: str) -> float:
        if isinstance(gr.confidence, dict):
            return float(gr.confidence.get(field_name, CONF_MATCH))
        if isinstance(gr.confidence, float):
            return gr.confidence
        return CONF_MATCH

    def _f(name: str, value: str) -> FieldResult:
        v = (value or "").strip()
        return FieldResult(name=name, value=v,
                           confidence=_conf(name) if v else CONF_MISSING,
                           page_number=1)

    results: list[FieldResult] = [
        _f("patient_name",     str(gr.patient_name)  if gr.patient_name  is not None else ""),
        _f("hospital_name",    str(gr.hospital_name) if gr.hospital_name is not None else ""),
        _f("policy_number",    str(gr.policy_number) if gr.policy_number is not None else ""),
        _f("policy_start_date", _coerce_date(gr.policy_start)),
        _f("bill_date",        _coerce_date(gr.bill_date)),
        _f("admission_date",   _coerce_date(gr.admission_date)),
        _f("discharge_date",   _coerce_date(gr.discharge_date)),
        _f("bill_number",      str(gr.bill_number)   if gr.bill_number   is not None else ""),
        _f("diagnosis",        str(gr.diagnosis)     if gr.diagnosis     is not None else ""),
        _f("claimed_amount",   _coerce_amount(gr.claimed_amount)),
        _f("total_amount",     _coerce_amount(gr.total_amount)),
        _f("signature_present", _coerce_bool_to_str(gr.signature_present)),
    ]

    items: list[dict[str, Any]] = []
    if gr.line_items:
        for li in gr.line_items:
            desc = (li.description or "").strip()
            if not desc:
                continue
            amt_raw = li.amount
            if isinstance(amt_raw, str):
                parsed = _parse_amount(amt_raw)
                amt = parsed if parsed is not None else 0.0
            elif isinstance(amt_raw, (int, float)):
                amt = float(amt_raw)
            else:
                amt = 0.0
            items.append({"description": desc, "amount": amt})

    results.append(FieldResult(
        name="line_items",
        value=_json.dumps(items),
        confidence=CONF_MATCH if items else CONF_MISSING,
        page_number=1,
    ))
    return results


def _call_gemma_with_retry(
    client: Any,
    model_name: str,
    image_parts: list[Any],
    doc_name: str,
) -> str:
    """
    Call generate_content with a 30 s timeout.  Retries up to _MAX_RETRIES
    times on 500 / 503 / 429 / timeout.  Logs seconds per call and every
    retry or fallback with the document name — never the API key.
    """
    from google.genai import types
    import concurrent.futures as _cf

    config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_level="minimal"),
    )

    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 2):   # attempts = retries + 1 initial
        t0 = time.monotonic()
        try:
            # Run the blocking call in a thread so we can apply a wall-clock timeout
            with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
                future = _ex.submit(
                    client.models.generate_content,
                    model=model_name,
                    contents=image_parts + [_GEMMA_PROMPT],
                    config=config,
                )
                response = future.result(timeout=_API_TIMEOUT_SECS)

            elapsed = time.monotonic() - t0
            log.info(
                "Gemma call succeeded for %s in %.1f s (attempt %d)",
                doc_name, elapsed, attempt,
            )
            return response.text

        except Exception as exc:
            elapsed   = time.monotonic() - t0
            exc_type  = type(exc).__name__
            status_code = (
                getattr(exc, "status_code", None)
                or getattr(exc, "code", None)
            )
            is_retryable = (
                status_code in (500, 503, 429)
                or "timeout" in exc_type.lower()
                or isinstance(exc, concurrent.futures.TimeoutError)
            )

            log.warning(
                "Gemma API error for %s on attempt %d/%d after %.1f s: %s%s",
                doc_name, attempt, _MAX_RETRIES + 1, elapsed, exc_type,
                f" (HTTP {status_code})" if status_code else "",
            )
            last_exc = exc

            if is_retryable and attempt <= _MAX_RETRIES:
                log.info("Retrying %s in %s s…", doc_name, _RETRY_WAIT_SECS)
                time.sleep(_RETRY_WAIT_SECS)
            else:
                break

    raise last_exc  # type: ignore[misc]


def _parse_gemma_json(raw_text: str, retry_once: bool = True) -> _GemmaResponse:
    """Strip ```json fence, parse, validate with Pydantic.  Retries once."""
    cleaned = _strip_json_fence(raw_text)
    try:
        return _GemmaResponse.model_validate(_json.loads(cleaned))
    except (_json.JSONDecodeError, ValidationError) as exc:
        if retry_once:
            log.warning("Gemma JSON parse failed (%s); retrying with aggressive strip",
                        type(exc).__name__)
            # look for a JSON fence anywhere in the raw text
            fence_m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_text,
                                 re.DOTALL | re.IGNORECASE)
            if fence_m:
                try:
                    return _GemmaResponse.model_validate(_json.loads(fence_m.group(1)))
                except (_json.JSONDecodeError, ValidationError):
                    pass
            obj_m = re.search(r"\{.*\}", raw_text, re.DOTALL)
            if obj_m:
                try:
                    return _GemmaResponse.model_validate(_json.loads(obj_m.group(0)))
                except (_json.JSONDecodeError, ValidationError):
                    pass
        raise


# ─────────────────────────────────────────────────────────────────────────────
# Disagreement detection
# ─────────────────────────────────────────────────────────────────────────────

def _normalise_for_compare(value: str) -> str:
    """Lowercase, strip whitespace and currency prefix for comparison."""
    v = value.lower().strip()
    v = re.sub(r"^rs\.?\s*", "", v)   # strip "Rs." prefix for amounts
    v = re.sub(r"[,\s]", "", v)       # strip commas and spaces
    return v


def _apply_disagreement_check(
    gemma_fields:  list[FieldResult],
    regex_fields:  list[FieldResult],
) -> list[FieldResult]:
    """
    Compare key fields between gemma and regex results.  Where both have a
    non-empty value but they differ, lower the gemma field's confidence to
    CONF_DISAGREE and append a note so reviewers see it.
    """
    regex_map = {f["name"]: f["value"] for f in regex_fields}

    updated: list[FieldResult] = []
    for fld in gemma_fields:
        name = fld["name"]
        if name not in _COMPARE_FIELDS:
            updated.append(fld)
            continue

        gemma_val = (fld["value"] or "").strip()
        regex_val = (regex_map.get(name) or "").strip()

        if gemma_val and regex_val:
            gn = _normalise_for_compare(gemma_val)
            rn = _normalise_for_compare(regex_val)
            if gn != rn:
                log.info(
                    "Disagreement on %s: gemma=%r regex=%r — marking confidence 0.5",
                    name, gemma_val, regex_val,
                )
                updated.append(FieldResult(
                    name=name,
                    value=f"{gemma_val} [Gemma and regex disagree: regex={regex_val}]",
                    confidence=CONF_DISAGREE,
                    page_number=fld["page_number"],
                ))
                continue

        updated.append(fld)
    return updated


# ─────────────────────────────────────────────────────────────────────────────
# Gemma extraction (single document)
# ─────────────────────────────────────────────────────────────────────────────

def _gemma_extract(path: Path) -> ExtractionResult:
    """
    Run Gemma 4 extraction for a single file with retry/fallback logic.
    Also runs regex in parallel for disagreement detection.
    """
    from google.genai import types

    suffix     = path.suffix.lower()
    model_name = os.environ.get("GEMMA_MODEL", "gemma-4-26b-a4b-it")
    doc_name   = path.name

    # ── always run regex (needed for disagreement check + fast fallback) ──
    regex_result = _regex_extract(path)

    # ── build image parts ─────────────────────────────────────────────────
    image_parts: list[Any] = []
    page_texts:  list[str] = []

    if suffix in IMAGE_EXTS:
        mime = _MIME_MAP.get(suffix, "image/png")
        image_parts.append(types.Part.from_bytes(data=path.read_bytes(), mime_type=mime))
        page_texts.append("[IMAGE FILE]")
    elif suffix == PDF_EXT:
        doc = pymupdf.open(str(path))
        for pg in doc:
            mat    = pymupdf.Matrix(100 / 72, 100 / 72)
            pixmap = pg.get_pixmap(matrix=mat, alpha=False)
            image_parts.append(types.Part.from_bytes(
                data=pixmap.tobytes("png"), mime_type="image/png"
            ))
            page_texts.append(pg.get_text())
        doc.close()
    else:
        log.warning("Unsupported file type for Gemma: %s; using regex", suffix)
        return regex_result

    pages = [PageText(page=i + 1, text=t) for i, t in enumerate(page_texts)]

    # ── call Gemma ────────────────────────────────────────────────────────
    try:
        client   = _load_gemini_client()
        raw_text = _call_gemma_with_retry(client, model_name, image_parts, doc_name)
        gr       = _parse_gemma_json(raw_text, retry_once=True)
    except Exception as exc:
        log.error(
            "Gemma extraction failed for %s (%s); using regex fallback",
            doc_name, type(exc).__name__,
        )
        fallback = dict(regex_result)
        fallback["extraction_method"] = "regex-fallback"
        fallback["model_name"]        = model_name
        return ExtractionResult(**fallback)

    # ── resolve document type ─────────────────────────────────────────────
    raw_dtype = (gr.document_type or "").lower().replace(" ", "_")
    doc_type  = raw_dtype if raw_dtype in (
        DOCTYPE_HOSPITAL_BILL, DOCTYPE_CLAIM_FORM,
        DOCTYPE_DISCHARGE_SUMMARY, DOCTYPE_PRESCRIPTION,
    ) else DOCTYPE_UNKNOWN

    gemma_fields = _gemma_response_to_fields(gr)
    # Cross-check with regex and flag disagreements
    final_fields = _apply_disagreement_check(gemma_fields, regex_result["fields"])

    return ExtractionResult(
        document_type=doc_type,
        fields=final_fields,
        pages=pages,
        extraction_method="gemma-4",
        model_name=model_name,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def extract_document(file_path: str | Path) -> ExtractionResult:
    """
    Extract structured information from a single PDF or image file.

    EXTRACTION_MODE=regex (default) – fast, no API calls
    EXTRACTION_MODE=gemma           – Gemma 4 with regex fallback

    Results are cached on disk (backend/cache/extraction_cache.json) keyed
    by SHA-256(file) + model name, so re-uploads are instant.
    """
    path       = Path(file_path)
    mode       = os.environ.get("EXTRACTION_MODE", "regex").lower()
    model_name = os.environ.get("GEMMA_MODEL", "gemma-4-26b-a4b-it") if mode == "gemma" else ""
    file_hash  = _file_sha256(path)

    # ── cache lookup ──────────────────────────────────────────────────────
    cached = _cache_get(file_hash, model_name)
    if cached is not None:
        log.debug("Cache hit for %s (%s…)", path.name, file_hash[:8])
        return cached

    # ── dispatch ──────────────────────────────────────────────────────────
    t0 = time.monotonic()
    result = _gemma_extract(path) if mode == "gemma" else _regex_extract(path)
    elapsed = time.monotonic() - t0
    log.info(
        "Extracted %s via %s in %.1f s",
        path.name, result["extraction_method"], elapsed,
    )

    _cache_set(file_hash, model_name, result)
    return result


def extract_documents_parallel(
    file_paths: list[str | Path],
    max_workers: int = 3,
) -> list[ExtractionResult]:
    """
    Extract multiple documents concurrently using a ThreadPoolExecutor.

    Parameters
    ----------
    file_paths :  list of paths (order preserved in returned list)
    max_workers : maximum concurrent extraction threads (default 3)

    Returns results in the same order as file_paths.  If one document fails
    after all retries it returns a regex-fallback result (never raises).
    """
    if not file_paths:
        return []

    results: list[ExtractionResult | None] = [None] * len(file_paths)

    def _extract_one(idx: int, fp: str | Path) -> None:
        try:
            results[idx] = extract_document(fp)
        except Exception as exc:
            log.error(
                "Unexpected error extracting %s (%s); using regex fallback",
                Path(fp).name, type(exc).__name__,
            )
            try:
                results[idx] = _regex_extract(Path(fp))
            except Exception:
                results[idx] = ExtractionResult(
                    document_type=DOCTYPE_UNKNOWN,
                    fields=[],
                    pages=[],
                    extraction_method="regex-fallback",
                    model_name="",
                )

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_extract_one, i, fp): i
            for i, fp in enumerate(file_paths)
        }
        for future in concurrent.futures.as_completed(futures):
            future.result()   # propagate unexpected exceptions (already caught above)

    return [r for r in results if r is not None]
