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

import re
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


# ── regex helpers ─────────────────────────────────────────────────────────────

def _first(pattern: str, text: str, flags: int = re.IGNORECASE) -> str | None:
    """Return the first capture group of pattern, or None."""
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def _date_pattern(label: str) -> str:
    """
    Build a regex that matches '<label> : <date>' where the date and label
    may be on the same line or on consecutive lines (common in fpdf2 output).
    """
    date_rx = r"(\d{1,2}[-/]\w{2,9}[-/]\d{2,4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})"
    return rf"{label}\s*[:\-]?\s*\n?\s*{date_rx}"


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
    """Return 'Present', 'Missing', or 'Unknown'."""
    lower = text.lower()
    if re.search(r"\bsignature\b.*\bpresent\b|\bsigned\b", lower):
        return "Present"
    if re.search(r"\bsignature\b.*\bmissing\b|\bunsigned\b|\bno\s+signature\b", lower):
        return "Missing"
    if re.search(r"\bsignature\b", lower):
        return "Present"   # signature field exists → assume present unless flagged
    return "Unknown"


# ─────────────────────────────────────────────────────────────────────────────
# Core field extractor (regex / heuristics)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_fields_from_text(
    full_text: str,
    page_texts: list[str],
) -> list[FieldResult]:
    """
    Run all regex extractors against full_text.
    page_number is approximated by scanning each page for the matched value.
    """

    def _page_of(value: str) -> int:
        """Return the 1-based page number where value first appears."""
        for i, pt in enumerate(page_texts, start=1):
            if value.lower() in pt.lower():
                return i
        return 1

    def _field(name: str, value: str | None, page: int | None = None) -> FieldResult:
        v   = (value or "").strip()
        pg  = page if page is not None else (_page_of(v) if v else 1)
        return FieldResult(
            name=name,
            value=v,
            confidence=CONF_MATCH if v else CONF_MISSING,
            page_number=pg,
        )

    results: list[FieldResult] = []

    # ── scalar fields ──────────────────────────────────────────────────────

    # patient_name: label on one line, value on next (fpdf2) or same line
    patient = (
        _first(r"Patient\s+Name\s*[:\-]\s*\n?\s*(.+)", full_text)
        or _first(r"Patient\s*[:\-]\s*(.+)", full_text)
    )
    results.append(_field("patient_name", patient))

    # hospital_name: first non-empty line of the document
    first_line = next(
        (ln.strip() for ln in full_text.splitlines() if ln.strip()), None
    )
    results.append(_field("hospital_name", first_line, page=1))

    # policy_number
    policy = _first(
        r"Policy\s+(?:Number|No\.?)\s*[:\-]\s*\n?\s*(\S+)", full_text
    )
    results.append(_field("policy_number", policy))

    # dates
    results.append(_field("bill_date",
        _first(_date_pattern(r"Bill\s+Date"), full_text)))
    results.append(_field("admission_date",
        _first(_date_pattern(r"Admission\s+Date"), full_text)))
    results.append(_field("discharge_date",
        _first(_date_pattern(r"Discharge\s+Date"), full_text)))

    # claimed_amount
    claimed = _first(
        r"Claimed\s+Amount\s*[:\-]\s*\n?\s*(?:Rs\.?\s*|INR\s*)?([\d,]+(?:\.\d{1,2})?)",
        full_text,
    )
    results.append(_field("claimed_amount", f"Rs. {claimed}" if claimed else ""))

    # total_amount – last labelled total (handles wrong-total scenario too)
    total_m = re.search(
        r"(?:TOTAL\s+AMOUNT|GRAND\s+TOTAL|Total\b)[^\d\n]*\n?"
        r"(?:Rs\.?\s*|INR\s*)?([\d,]+(?:\.\d{1,2})?)",
        full_text,
        re.IGNORECASE,
    )
    total = total_m.group(1) if total_m else None
    results.append(_field("total_amount", f"Rs. {total}" if total else ""))

    # signature_present
    sig = _detect_signature(full_text)
    results.append(_field("signature_present", sig))

    # ── line_items: stored as a single JSON-serialisable field ────────────
    # We store the serialised list as the value string so it fits the
    # FieldResult shape; callers that need structured data can json.loads it.
    import json as _json
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
    * This implementation uses PyMuPDF text extraction + regex.
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
