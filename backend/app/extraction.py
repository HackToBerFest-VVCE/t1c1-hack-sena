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
    }

The function signature is intentionally stable: a Gemma-4 / LLM-based
implementation will replace only the body of extract_document() while
callers and the DB layer remain unchanged.

Image files (jpg, png, bmp, tiff, webp) return a placeholder result so
the rest of the pipeline can continue without OCR.
"""

from __future__ import annotations

import json as _json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, TypedDict

import pymupdf  # PyMuPDF 2.x  (import name changed from 'fitz')

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
    document_type: str
    fields:        list[FieldResult]
    pages:         list[PageText]


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

# ── document-type classifier ──────────────────────────────────────────────────

# Each type maps to (required_keywords, bonus_keywords).
# required: ALL must appear for the type to match.
# bonus:    extra supporting keywords – tiebreak when multiple types match.
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


# ── next-line label helper ────────────────────────────────────────────────────

# Labels that are themselves field headings (so we don't confuse them as values)
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
    """
    Given a list of text lines from a PDF page, find a line that matches one
    of *label_variants* (case-insensitive, ignoring a trailing colon) and
    return the next non-empty line — unless that line is itself a known label,
    in which case return None.

    Also supports "Label: value" on a single line as a fallback.

    Parameters
    ----------
    lines:
        Raw lines from ``page.get_text()``, one element per line.
    label_variants:
        The label spellings to try, e.g. ["Policy Number", "Policy No"].
        Matching is done case-insensitively after normalisation.
    """
    norm_variants = {v.lower().rstrip(":").strip() for v in label_variants}

    for i, raw_line in enumerate(lines):
        norm = _normalise_line(raw_line).lower().rstrip(":").strip()
        if norm not in norm_variants:
            continue

        # --- "Label: value" on the same line ---
        # Check if the original line already contains ": something"
        colon_m = re.match(
            r"^[^:]+:\s*(.+)$",
            _normalise_line(raw_line),
            re.IGNORECASE,
        )
        if colon_m:
            candidate = colon_m.group(1).strip()
            if candidate:
                return candidate

        # --- value is on the NEXT non-empty line ---
        for j in range(i + 1, len(lines)):
            candidate = _normalise_line(lines[j])
            if not candidate:
                continue
            # reject if it's itself a known label
            candidate_lower = candidate.lower().rstrip(":").strip()
            if candidate_lower in _KNOWN_LABELS:
                return None
            return candidate

    return None


# ── amount / date parsers ─────────────────────────────────────────────────────

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


# ── regex helpers (kept for backward compat with _extract_line_items) ─────────

def _first(pattern: str, text: str, flags: int = re.IGNORECASE) -> str | None:
    """Return the first capture group of pattern, or None."""
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def _date_pattern(label: str) -> str:
    """
    Build a regex that matches '<label>[: ]<date>' where label and date may
    be on the same line OR on consecutive lines (common in fpdf2 output).
    Non-breaking spaces (\\xa0) are treated like ordinary spaces.
    """
    date_rx = r"(\d{1,2}[-/]\w{2,9}[-/]\d{2,4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})"
    return rf"{label}[\s\xa0]*[:\-]?[\s\xa0]*{date_rx}"


# ── line-item extractor ───────────────────────────────────────────────────────

def _extract_line_items(text: str) -> list[dict[str, Any]]:
    """
    Extract bill line items handling two layouts:

    Layout A (fpdf2 / cell-per-line):
        1
        Room charges (5 days @ Rs. 4,500/day)
        Rs. 22,500.00

    Layout B (inline):
        1  Room charges (5 days @ Rs. 4,500/day)  Rs. 22,500.00
    """
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

    # Layout A – scan between "Bill Particulars" and "TOTAL"
    block_m = re.search(
        r"Bill\s+Particulars.*?\n(.*?)(?:TOTAL\s+AMOUNT|GRAND\s+TOTAL|Total\b)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if block_m:
        lines = [ln.strip() for ln in block_m.group(1).splitlines() if ln.strip()]
        # skip column-header lines
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

    # Layout B – inline  <sr?>  <description>  Rs. <amount>
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


# ── signature detector ────────────────────────────────────────────────────────

def _detect_signature(text: str) -> str:
    """
    Return 'Present', 'Missing', or 'Unknown'.

    Reads the text inside the brackets on the "Claimant Signature" line:

        Claimant Signature : [ Signed ]               → Present
        Claimant Signature : [ Missing — not signed ] → Missing
        Claimant Signature : [  ]                     → Missing (empty brackets)

    Rules (applied in order so "not signed" wins over bare "signed"):
    1. Look for the Claimant Signature line (case-insensitive).
    2. Extract the content between "[" and "]".
    3. If the bracket content is exactly "Signed" (case-insensitive) → Present.
    4. If it contains "Missing" or "not signed" (case-insensitive), or the
       brackets are empty/whitespace-only → Missing.
    5. Fallback on the whole text if the dedicated line is absent.
    """
    # --- dedicated "Claimant Signature" line ---
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
        # Some other text inside brackets → treat as present
        return "Present"

    # --- fallback: prose or other layouts ---
    lower = text.lower()
    # Check "missing" / "not signed" / "unsigned" before checking "signed"
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


# ─────────────────────────────────────────────────────────────────────────────
# Core field extractor (next-line label helper + targeted regex)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_fields_from_text(
    full_text: str,
    page_texts: list[str],
) -> list[FieldResult]:
    """
    Extract named fields from the raw PDF text.

    Header fields (patient_name, policy_number, etc.) are extracted with the
    ``value_after_label`` helper that handles the "label on one line, value
    on the next" layout produced by fpdf2, with an inline "Label: value"
    fallback.

    Line-item and total extraction use the existing regex-based helpers that
    already work correctly.
    """

    # Split every page into individual lines once; we search across all lines.
    all_lines: list[str] = []
    for pt in page_texts:
        all_lines.extend(pt.splitlines())

    def _page_of(value: str) -> int:
        """Return the 1-based page number where *value* first appears."""
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

    # ── patient_name ──────────────────────────────────────────────────────
    patient = _value_after_label(all_lines, [
        "Patient Name",
        "Insured Name",
        "Name of Patient",
        "Claimant Name",
    ])
    results.append(_field("patient_name", patient))

    # ── hospital_name ─────────────────────────────────────────────────────
    # On hospital bills the hospital is the first line of the page.
    # On claim forms the label "Hospital Name" appears in the body.
    # We first try the label-based approach (for claim forms / discharge
    # summaries); for bills the label also appears so this works universally.
    hosp = _value_after_label(all_lines, ["Hospital Name"])
    if not hosp:
        # Fallback: first non-empty line of the document
        hosp = next((ln.strip() for ln in all_lines if ln.strip()), None)
    results.append(_field("hospital_name", hosp, page=1))

    # ── policy_number ─────────────────────────────────────────────────────
    policy = _value_after_label(all_lines, ["Policy Number", "Policy No"])
    results.append(_field("policy_number", policy))

    # ── policy_start_date ─────────────────────────────────────────────────
    raw_start = _value_after_label(all_lines, ["Policy Start Date"])
    if raw_start:
        dt = _parse_date_str(raw_start)
        start_val = dt.strftime("%d-%b-%Y") if dt else raw_start
    else:
        start_val = None
    results.append(_field("policy_start_date", start_val))

    # ── bill_date ─────────────────────────────────────────────────────────
    raw_bd = _value_after_label(all_lines, ["Bill Date"])
    if raw_bd:
        dt = _parse_date_str(raw_bd)
        bd_val = dt.strftime("%d-%b-%Y") if dt else raw_bd
    else:
        bd_val = None
    results.append(_field("bill_date", bd_val))

    # ── admission_date ────────────────────────────────────────────────────
    raw_ad = _value_after_label(all_lines, ["Admission Date"])
    if raw_ad:
        dt = _parse_date_str(raw_ad)
        ad_val = dt.strftime("%d-%b-%Y") if dt else raw_ad
    else:
        ad_val = None
    results.append(_field("admission_date", ad_val))

    # ── discharge_date ────────────────────────────────────────────────────
    raw_dd = _value_after_label(all_lines, ["Discharge Date"])
    if raw_dd:
        dt = _parse_date_str(raw_dd)
        dd_val = dt.strftime("%d-%b-%Y") if dt else raw_dd
    else:
        dd_val = None
    results.append(_field("discharge_date", dd_val))

    # ── bill_number ───────────────────────────────────────────────────────
    bill_num = _value_after_label(all_lines, ["Bill Number", "Original Bill Ref."])
    results.append(_field("bill_number", bill_num))

    # ── diagnosis ─────────────────────────────────────────────────────────
    diag = _value_after_label(all_lines, ["Diagnosis"])
    results.append(_field("diagnosis", diag))

    # ── claimed_amount ────────────────────────────────────────────────────
    raw_ca = _value_after_label(all_lines, ["Claimed Amount", "Amount Claimed"])
    if raw_ca:
        amt = _parse_amount(raw_ca)
        ca_val = f"Rs. {amt:,.2f}" if amt is not None else raw_ca
    else:
        ca_val = None
    results.append(_field("claimed_amount", ca_val))

    # ── total_amount (from bill) ──────────────────────────────────────────
    # The total appears as "TOTAL AMOUNT" on one line, then the value on the
    # next.  We still keep the regex approach here because:
    #  (a) it already works correctly for all 8 bundles, and
    #  (b) the label "TOTAL AMOUNT" does NOT appear in _KNOWN_LABELS, so the
    #      next-line helper would work too – but regex is more robust when the
    #      total can be inline ("TOTAL AMOUNT   Rs. 32,071.25").
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

    # ── signature_present ─────────────────────────────────────────────────
    sig = _detect_signature(full_text)
    results.append(_field("signature_present", sig))

    # ── line_items (JSON-serialised list) ─────────────────────────────────
    items = _extract_line_items(full_text)
    results.append(FieldResult(
        name="line_items",
        value=_json.dumps(items),
        confidence=CONF_MATCH if items else CONF_MISSING,
        page_number=1,
    ))

    return results


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
            "document_type": str,
            "fields":        list[FieldResult],
            "pages":         list[PageText],
        }

    Notes
    -----
    * This implementation uses PyMuPDF text extraction + regex / next-line
      label helpers.
    * Image files return a clearly labelled placeholder — no OCR is run.
    * The function signature is intentionally stable: a model-based version
      (e.g. Gemma 4) will replace only this function body.
    """
    path   = Path(file_path)
    suffix = path.suffix.lower()

    # ── image placeholder ──────────────────────────────────────────────────
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
        )

    # ── PDF extraction ─────────────────────────────────────────────────────
    doc        = pymupdf.open(str(path))
    page_texts = [page.get_text() for page in doc]
    doc.close()

    full_text = "\n".join(page_texts)

    document_type = _classify_document_type(full_text)
    fields        = _extract_fields_from_text(full_text, page_texts)
    pages         = [
        PageText(page=i + 1, text=t)
        for i, t in enumerate(page_texts)
    ]

    return ExtractionResult(
        document_type=document_type,
        fields=fields,
        pages=pages,
    )
