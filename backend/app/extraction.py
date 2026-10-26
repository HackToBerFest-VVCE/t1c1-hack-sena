"""
extraction.py – Document extraction for ClaimLens AI.

Public API
----------
    result = extract_document(file_path)

Returns
-------
    {
        "document_type": str,          # one of the DOCTYPE_* constants
        "fields":        list[dict],   # see FieldResult TypedDict
        "pages":         list[dict],   # {"page": int, "text": str}
        "extraction_method": str,      # "gemma-4" | "regex-fallback"
        "model_name":    str,          # model identifier used, or ""
    }

Extraction modes
----------------
    EXTRACTION_MODE=regex  (default)   – existing regex / next-line-label path
    EXTRACTION_MODE=gemma              – Gemma 4 via google-genai SDK

    GEMMA_MODEL (default "gemma-4-26b-a4b-it")
    GEMINI_API_KEY – loaded from backend/.env (never hard-coded or logged)

Image files (jpg, png, bmp, tiff, webp) are sent directly to Gemma when
gemma mode is active; PDFs are rendered to PNG pages at 100 dpi.

Caching
-------
Results are cached in memory keyed by SHA-256 of the file content so that
re-uploading the same file does not trigger another API call.
"""

from __future__ import annotations

import hashlib
import io
import json as _json
import logging
import os
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, TypedDict

import pymupdf  # PyMuPDF 2.x  (import name changed from 'fitz')
from pydantic import BaseModel, ValidationError

# ── logging ───────────────────────────────────────────────────────────────────

log = logging.getLogger(__name__)

# ── document type constants ───────────────────────────────────────────────────

DOCTYPE_HOSPITAL_BILL      = "hospital_bill"
DOCTYPE_CLAIM_FORM         = "claim_form"
DOCTYPE_DISCHARGE_SUMMARY  = "discharge_summary"
DOCTYPE_PRESCRIPTION       = "prescription"
DOCTYPE_UNKNOWN            = "unknown"

# ── confidence levels ─────────────────────────────────────────────────────────

CONF_MATCH   = 0.9   # regex found a value
CONF_MISSING = 0.3   # field expected but not found

# ── file-extension sets ───────────────────────────────────────────────────────

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}
PDF_EXT    = ".pdf"

# MIME type map for direct image uploads to Gemma
_MIME_MAP: dict[str, str] = {
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".bmp":  "image/bmp",
    ".tiff": "image/tiff",
    ".tif":  "image/tiff",
    ".webp": "image/webp",
}


# ── typed return shapes ───────────────────────────────────────────────────────

class FieldResult(TypedDict):
    name:        str
    value:       str        # empty string when missing
    confidence:  float      # CONF_MATCH or CONF_MISSING
    page_number: int        # 1-based; 0 for image placeholders


class PageText(TypedDict):
    page: int
    text: str


class ExtractionResult(TypedDict):
    document_type:      str
    fields:             list[FieldResult]
    pages:              list[PageText]
    extraction_method:  str   # "gemma-4" | "regex-fallback"
    model_name:         str   # model identifier or ""


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic model for Gemma JSON output validation
# ─────────────────────────────────────────────────────────────────────────────

class _LineItem(BaseModel):
    description: str | None = None
    amount: float | str | None = None


class _GemmaResponse(BaseModel):
    document_type:    str | None = None
    patient_name:     Any        = None
    policy_number:    Any        = None
    policy_start:     Any        = None
    hospital_name:    Any        = None
    bill_number:      Any        = None
    bill_date:        Any        = None
    admission_date:   Any        = None
    discharge_date:   Any        = None
    diagnosis:        Any        = None
    claimed_amount:   Any        = None
    total_amount:     Any        = None
    line_items:       list[_LineItem] | None = None
    signature_present: Any       = None
    # Per-field confidence maps – optional
    confidence:       dict[str, float] | float | None = None


# ─────────────────────────────────────────────────────────────────────────────
# In-memory extraction cache  {sha256_hex: ExtractionResult}
# ─────────────────────────────────────────────────────────────────────────────

_extraction_cache: dict[str, ExtractionResult] = {}


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
# Regex / label-based extraction helpers (preserved from original)
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
    """Return the best matching document type string, or DOCTYPE_UNKNOWN."""
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
    "policy number", "policy no",
    "policy start date",
    "hospital name",
    "admission date", "discharge date", "bill date",
    "claimed amount", "amount claimed",
    "bill number", "original bill ref.",
    "diagnosis",
    "date of birth",
    "insured details",
    "hospitalisation details",
    "claim details",
    "declaration",
    "treating doctor",
    "clinical summary",
    "bill particulars",
    "sr.",
    "description",
    "amount",
    "total amount",
    "grand total",
})


def _normalise_line(line: str) -> str:
    """Replace non-breaking spaces, collapse whitespace, strip."""
    return re.sub(r"\s+", " ", line.replace("\xa0", " ")).strip()


def _value_after_label(lines: list[str], label_variants: list[str]) -> str | None:
    norm_variants = {v.lower().rstrip(":").strip() for v in label_variants}

    for i, raw_line in enumerate(lines):
        norm = _normalise_line(raw_line).lower().rstrip(":").strip()
        if norm not in norm_variants:
            continue

        colon_m = re.match(
            r"^[^:]+:\s*(.+)$",
            _normalise_line(raw_line),
            re.IGNORECASE,
        )
        if colon_m:
            candidate = colon_m.group(1).strip()
            if candidate:
                return candidate

        for j in range(i + 1, len(lines)):
            candidate = _normalise_line(lines[j])
            if not candidate:
                continue
            candidate_lower = candidate.lower().rstrip(":").strip()
            if candidate_lower in _KNOWN_LABELS:
                return None
            return candidate

    return None


def _parse_amount(raw: str) -> float | None:
    """Parse 'Rs. 32,071.25' or '32,071.25' → float, or None."""
    m = re.search(r"([\d,]+(?:\.\d{1,2})?)", raw)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_date_str(raw: str) -> datetime | None:
    """Try common Indian date formats, return datetime or None."""
    raw = re.sub(r"[./]", "-", raw.strip())
    for fmt in (
        "%d-%b-%Y",   # 14-Nov-2024
        "%d/%b/%Y",
        "%d-%m-%Y",   # 14-11-2024
        "%d/%m/%Y",
        "%d-%b-%y",   # 14-Nov-24
        "%d/%m/%y",
        "%Y-%m-%d",   # ISO
    ):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _first(pattern: str, text: str, flags: int = re.IGNORECASE) -> str | None:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def _date_pattern(label: str) -> str:
    date_rx = r"(\d{1,2}[-/]\w{2,9}[-/]\d{2,4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})"
    return rf"{label}[\s\xa0]*[:\-]?[\s\xa0]*{date_rx}"


def _extract_line_items(text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen:  set[str]             = set()

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
            items.append({
                "description": desc,
                "amount":      float(raw_amt.replace(",", "")),
            })
        except ValueError:
            pass

    block_m = re.search(
        r"Bill\s+Particulars.*?\n(.*?)(?:TOTAL\s+AMOUNT|GRAND\s+TOTAL|Total\b)",
        text,
        re.IGNORECASE | re.DOTALL,
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
                    desc   = lines[i + 1]
                    amt_ln = lines[i + 2]
                    amt_m  = re.search(r"([\d,]+(?:\.\d{1,2})?)", amt_ln)
                    if amt_m and _keep(desc):
                        _add(desc, amt_m.group(1))
                i += 3
                continue
            i += 1

    for m in re.finditer(
        r"^\s*(?:\d{1,3}\s+)?([A-Za-z][^\n]{5,100}?)\s+"
        r"(?:Rs\.?\s*|INR\s*)([\d,]+(?:\.\d{1,2})?)\s*$",
        text,
        re.MULTILINE | re.IGNORECASE,
    ):
        desc = m.group(1).strip()
        if _keep(desc):
            _add(desc, m.group(2))

    return items


def _detect_signature(text: str) -> str:
    sig_line_m = re.search(
        r"claimant\s+signature\s*:?\s*\[([^\]]*)\]",
        text,
        re.IGNORECASE,
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
    if re.search(
        r"\bsignature\b.*\bmissing\b"
        r"|\bnot\s+signed\b"
        r"|\bunsigned\b"
        r"|\bno\s+signature\b",
        lower,
    ):
        return "Missing"
    if re.search(r"\bsignature\b.*\bpresent\b|\bsigned\b", lower):
        return "Present"
    if re.search(r"\bsignature\b", lower):
        return "Present"
    return "Unknown"


def _extract_fields_from_text(
    full_text: str,
    page_texts: list[str],
) -> list[FieldResult]:
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
        return FieldResult(
            name=name,
            value=v,
            confidence=CONF_MATCH if v else CONF_MISSING,
            page_number=pg,
        )

    results: list[FieldResult] = []

    patient = _value_after_label(all_lines, [
        "Patient Name", "Insured Name", "Name of Patient", "Claimant Name",
    ])
    results.append(_field("patient_name", patient))

    hosp = _value_after_label(all_lines, ["Hospital Name"])
    if not hosp:
        hosp = next((ln.strip() for ln in all_lines if ln.strip()), None)
    results.append(_field("hospital_name", hosp, page=1))

    policy = _value_after_label(all_lines, ["Policy Number", "Policy No"])
    results.append(_field("policy_number", policy))

    raw_start = _value_after_label(all_lines, ["Policy Start Date"])
    if raw_start:
        dt = _parse_date_str(raw_start)
        start_val = dt.strftime("%d-%b-%Y") if dt else raw_start
    else:
        start_val = None
    results.append(_field("policy_start_date", start_val))

    raw_bd = _value_after_label(all_lines, ["Bill Date"])
    if raw_bd:
        dt = _parse_date_str(raw_bd)
        bd_val = dt.strftime("%d-%b-%Y") if dt else raw_bd
    else:
        bd_val = None
    results.append(_field("bill_date", bd_val))

    raw_ad = _value_after_label(all_lines, ["Admission Date"])
    if raw_ad:
        dt = _parse_date_str(raw_ad)
        ad_val = dt.strftime("%d-%b-%Y") if dt else raw_ad
    else:
        ad_val = None
    results.append(_field("admission_date", ad_val))

    raw_dd = _value_after_label(all_lines, ["Discharge Date"])
    if raw_dd:
        dt = _parse_date_str(raw_dd)
        dd_val = dt.strftime("%d-%b-%Y") if dt else raw_dd
    else:
        dd_val = None
    results.append(_field("discharge_date", dd_val))

    bill_num = _value_after_label(all_lines, ["Bill Number", "Original Bill Ref."])
    results.append(_field("bill_number", bill_num))

    diag = _value_after_label(all_lines, ["Diagnosis"])
    results.append(_field("diagnosis", diag))

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
        r"(?:Rs\.?[\s\xa0]*|INR[\s\xa0]*)?([\d,]+(?:\.\d{1,2})?)",
        full_text,
        re.IGNORECASE,
    )
    if total_m:
        t_amt = _parse_amount(total_m.group(1))
        total_val: str | None = f"Rs. {t_amt:,.2f}" if t_amt is not None else total_m.group(1)
    else:
        total_val = None
    results.append(_field("total_amount", total_val))

    sig = _detect_signature(full_text)
    results.append(_field("signature_present", sig))

    items = _extract_line_items(full_text)
    results.append(FieldResult(
        name="line_items",
        value=_json.dumps(items),
        confidence=CONF_MATCH if items else CONF_MISSING,
        page_number=1,
    ))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Regex-based public extraction (unchanged logic, new return fields)
# ─────────────────────────────────────────────────────────────────────────────

def _regex_extract(path: Path) -> ExtractionResult:
    """Run the original regex / next-line-label extraction path."""
    suffix = path.suffix.lower()

    if suffix in IMAGE_EXTS:
        placeholder_note = (
            f"Image extraction pending model integration. File: {path.name}"
        )
        return ExtractionResult(
            document_type=DOCTYPE_UNKNOWN,
            fields=[
                FieldResult(
                    name="extraction_note",
                    value=placeholder_note,
                    confidence=0.0,
                    page_number=0,
                )
            ],
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
# Gemma 4 extraction path
# ─────────────────────────────────────────────────────────────────────────────

def _load_gemini_client():
    """
    Load the Gemini API key from backend/.env and return a google.genai.Client.
    Raises RuntimeError if the key is absent.
    Never logs the key value.
    """
    from dotenv import load_dotenv

    env_file = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(dotenv_path=env_file, override=False)

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY not found. "
            "Set it in backend/.env or as an environment variable."
        )

    from google import genai
    return genai.Client(api_key=api_key)


def _strip_json_fence(text: str) -> str:
    """Remove a leading ```json ... ``` code fence if present."""
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return text


def _coerce_amount(raw: Any) -> str:
    """Convert an amount value (string like 'Rs. 32,071.25' or float) to 'Rs. X.XX'."""
    if raw is None:
        return ""
    if isinstance(raw, (int, float)):
        return f"Rs. {float(raw):,.2f}"
    parsed = _parse_amount(str(raw))
    if parsed is not None:
        return f"Rs. {parsed:,.2f}"
    return str(raw)


def _coerce_date(raw: Any) -> str:
    """Convert a date value to 'DD-Mon-YYYY' string, or return as-is."""
    if raw is None:
        return ""
    dt = _parse_date_str(str(raw))
    if dt:
        return dt.strftime("%d-%b-%Y")
    return str(raw)


def _coerce_bool_to_str(raw: Any) -> str:
    """Convert signature_present (bool/str) to 'Present'/'Missing'/'Unknown'."""
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
    """Convert a validated _GemmaResponse into the standard FieldResult list."""

    def _conf(field_name: str) -> float:
        if isinstance(gr.confidence, dict):
            return float(gr.confidence.get(field_name, CONF_MATCH))
        if isinstance(gr.confidence, float):
            return gr.confidence
        return CONF_MATCH

    def _f(name: str, value: str) -> FieldResult:
        v = (value or "").strip()
        return FieldResult(
            name=name,
            value=v,
            confidence=_conf(name) if v else CONF_MISSING,
            page_number=1,
        )

    results: list[FieldResult] = []

    results.append(_f("patient_name",    str(gr.patient_name)    if gr.patient_name    is not None else ""))
    results.append(_f("hospital_name",   str(gr.hospital_name)   if gr.hospital_name   is not None else ""))
    results.append(_f("policy_number",   str(gr.policy_number)   if gr.policy_number   is not None else ""))
    results.append(_f("policy_start_date", _coerce_date(gr.policy_start)))
    results.append(_f("bill_date",       _coerce_date(gr.bill_date)))
    results.append(_f("admission_date",  _coerce_date(gr.admission_date)))
    results.append(_f("discharge_date",  _coerce_date(gr.discharge_date)))
    results.append(_f("bill_number",     str(gr.bill_number)     if gr.bill_number     is not None else ""))
    results.append(_f("diagnosis",       str(gr.diagnosis)       if gr.diagnosis       is not None else ""))
    results.append(_f("claimed_amount",  _coerce_amount(gr.claimed_amount)))
    results.append(_f("total_amount",    _coerce_amount(gr.total_amount)))
    results.append(_f("signature_present", _coerce_bool_to_str(gr.signature_present)))

    # line_items
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
) -> str:
    """
    Call client.models.generate_content with up to 3 attempts on transient
    API errors (500, 503, 429, timeouts).  Returns the raw text of the response.
    Raises the last exception if all attempts fail.
    Never logs API key values.
    """
    from google.genai import types

    _RETRYABLE = (500, 503, 429)
    _MAX_ATTEMPTS = 3
    _RETRY_WAIT   = 3  # seconds

    config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_level="minimal"),
    )

    last_exc: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=image_parts + [_GEMMA_PROMPT],
                config=config,
            )
            return response.text
        except Exception as exc:
            exc_type = type(exc).__name__
            # Check for retryable status codes
            status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
            is_retryable = (
                status_code in _RETRYABLE
                or "timeout" in exc_type.lower()
                or "timeout" in str(exc).lower()
                or (status_code is None and attempt < _MAX_ATTEMPTS)
            )
            log.warning(
                "Gemma API error on attempt %d/%d: %s",
                attempt, _MAX_ATTEMPTS, exc_type,
                # intentionally NOT logging exc message to avoid key leakage
            )
            last_exc = exc
            if not is_retryable or attempt == _MAX_ATTEMPTS:
                break
            time.sleep(_RETRY_WAIT)

    raise last_exc  # type: ignore[misc]


def _parse_gemma_json(raw_text: str, retry_once: bool = True) -> _GemmaResponse:
    """
    Strip the ```json fence, parse JSON, validate with Pydantic.
    Retries once on validation failure (retry_once=True).

    The retry scans the raw text for any embedded JSON code fence (the model
    may prefix its answer with prose like "Here is the result:").
    """
    cleaned = _strip_json_fence(raw_text)
    try:
        data = _json.loads(cleaned)
        return _GemmaResponse.model_validate(data)
    except (_json.JSONDecodeError, ValidationError) as exc:
        if retry_once:
            log.warning("Gemma JSON parse/validation failed (%s); retrying once", type(exc).__name__)
            # Aggressive retry: scan for any ```json ... ``` block anywhere in raw_text
            fence_m = re.search(
                r"```(?:json)?\s*(\{.*?\})\s*```",
                raw_text,
                re.DOTALL | re.IGNORECASE,
            )
            if fence_m:
                try:
                    data2 = _json.loads(fence_m.group(1))
                    return _GemmaResponse.model_validate(data2)
                except (_json.JSONDecodeError, ValidationError):
                    pass
            # Also try extracting the first {...} JSON object from raw_text
            obj_m = re.search(r"\{.*\}", raw_text, re.DOTALL)
            if obj_m:
                try:
                    data3 = _json.loads(obj_m.group(0))
                    return _GemmaResponse.model_validate(data3)
                except (_json.JSONDecodeError, ValidationError):
                    pass
        raise


def _gemma_extract(path: Path) -> ExtractionResult:
    """
    Run the Gemma 4 extraction path for a single file.
    Falls back to regex extraction if all API attempts fail.
    """
    from google.genai import types

    suffix    = path.suffix.lower()
    model_name = os.environ.get("GEMMA_MODEL", "gemma-4-26b-a4b-it")

    # ── build image parts ─────────────────────────────────────────────────
    image_parts: list[Any] = []
    page_texts:  list[str] = []

    if suffix in IMAGE_EXTS:
        mime = _MIME_MAP.get(suffix, "image/png")
        data = path.read_bytes()
        image_parts.append(types.Part.from_bytes(data=data, mime_type=mime))
        page_texts.append("[IMAGE FILE]")
    elif suffix == PDF_EXT:
        doc = pymupdf.open(str(path))
        for page in doc:
            # render at 100 dpi
            mat    = pymupdf.Matrix(100 / 72, 100 / 72)
            pixmap = page.get_pixmap(matrix=mat, alpha=False)
            png_bytes = pixmap.tobytes("png")
            image_parts.append(types.Part.from_bytes(data=png_bytes, mime_type="image/png"))
            page_texts.append(page.get_text())
        doc.close()
    else:
        log.warning("Unsupported file type for Gemma extraction: %s; falling back to regex", suffix)
        return _regex_extract(path)

    pages = [PageText(page=i + 1, text=t) for i, t in enumerate(page_texts)]

    # ── call Gemma with retry ─────────────────────────────────────────────
    try:
        client    = _load_gemini_client()
        raw_text  = _call_gemma_with_retry(client, model_name, image_parts)
        gr        = _parse_gemma_json(raw_text, retry_once=True)
    except Exception as exc:
        log.error(
            "Gemma extraction failed for %s (%s); falling back to regex",
            path.name, type(exc).__name__,
        )
        fallback = _regex_extract(path)
        fallback["extraction_method"] = "regex-fallback"
        fallback["model_name"]        = model_name
        return fallback

    # ── resolve document type ─────────────────────────────────────────────
    raw_dtype = (gr.document_type or "").lower().replace(" ", "_")
    doc_type  = raw_dtype if raw_dtype in (
        DOCTYPE_HOSPITAL_BILL, DOCTYPE_CLAIM_FORM,
        DOCTYPE_DISCHARGE_SUMMARY, DOCTYPE_PRESCRIPTION,
    ) else DOCTYPE_UNKNOWN

    fields = _gemma_response_to_fields(gr)

    return ExtractionResult(
        document_type=doc_type,
        fields=fields,
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

    Parameters
    ----------
    file_path : str | Path
        Absolute path to the uploaded file.

    Returns
    -------
    ExtractionResult
        {
            "document_type":     str,
            "fields":            list[FieldResult],
            "pages":             list[PageText],
            "extraction_method": str,   # "gemma-4" | "regex-fallback"
            "model_name":        str,   # model identifier or ""
        }

    Modes
    -----
    Set the ``EXTRACTION_MODE`` environment variable:
      * ``regex``  (default) – regex / next-line-label extraction; no API calls.
      * ``gemma``            – Gemma 4 via google-genai; falls back to regex on
                               persistent API failure.

    Results are cached by file SHA-256 so re-uploading the same file is free.
    """
    path = Path(file_path)
    mode = os.environ.get("EXTRACTION_MODE", "regex").lower()

    # ── cache lookup ──────────────────────────────────────────────────────
    file_hash = _file_sha256(path)
    if file_hash in _extraction_cache:
        log.debug("Cache hit for %s (%s)", path.name, file_hash[:8])
        return _extraction_cache[file_hash]

    # ── dispatch ──────────────────────────────────────────────────────────
    if mode == "gemma":
        result = _gemma_extract(path)
    else:
        result = _regex_extract(path)

    _extraction_cache[file_hash] = result
    return result
