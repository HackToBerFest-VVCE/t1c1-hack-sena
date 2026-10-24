"""
ClaimLens AI – FastAPI backend
POST /upload  →  extract structured claim info from hospital bill PDFs / images
"""

import re
import shutil
from pathlib import Path
from typing import Any

import pymupdf                          # PyMuPDF 2.x  (was 'fitz' in 1.x)
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

# ── paths ──────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
UPLOADS_DIR = BASE_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}
PDF_EXT    = ".pdf"

# ── app setup ─────────────────────────────────────────────────────────────────
app = FastAPI(title="ClaimLens AI", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── regex helpers ─────────────────────────────────────────────────────────────

def _first(pattern: str, text: str, flags: int = re.IGNORECASE) -> str | None:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def _extract_line_items(text: str) -> list[dict[str, Any]]:
    """
    Extract line items from bill text.

    Handles two common PDF text-extraction layouts:

    Layout A (each field on its own line – typical of fpdf2/cell tables):
        1
        Room charges (5 days @ Rs. 4,500/day)
        Rs. 22,500.00

    Layout B (all on one line – typical of word-processor PDFs):
        1  Room charges (5 days @ Rs. 4,500/day)  Rs. 22,500.00
    """
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    SKIP = {"total", "bill no", "bill number", "patient", "policy",
            "admission", "discharge", "diagnosis", "hospital",
            "date of birth", "invoice", "description", "amount", "sr."}

    def _keep(desc: str) -> bool:
        low = desc.lower()
        return not any(w in low for w in SKIP) and len(desc) >= 6

    def _add(desc: str, raw_amt: str) -> None:
        key = desc.lower()[:50]
        if key in seen:
            return
        seen.add(key)
        try:
            items.append({"description": desc, "amount": float(raw_amt.replace(",", ""))})
        except ValueError:
            pass

    # ── Layout A: <sr_no_line> <desc_line> <amount_line> ──────────────────
    # After the "Bill Particulars" section header, rows come as groups of 3 lines.
    # We look for the block between "Bill Particulars" and "TOTAL".
    particulars_block = re.search(
        r"Bill\s+Particulars.*?\n(.*?)(?:TOTAL\s+AMOUNT|GRAND\s+TOTAL)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if particulars_block:
        block = particulars_block.group(1)
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        # skip the header row (Sr. / Description / Amount)
        start = 0
        for i, ln in enumerate(lines):
            if re.fullmatch(r"Sr\.?", ln, re.IGNORECASE) or ln.lower() in ("description", "amount"):
                start = i + 1   # skip past column-header lines
        lines = lines[start:]

        i = 0
        while i < len(lines):
            ln = lines[i]
            # serial number line: a bare integer
            if re.fullmatch(r"\d{1,3}", ln):
                if i + 2 < len(lines):
                    desc   = lines[i + 1]
                    amt_ln = lines[i + 2]
                    amt_m  = re.search(r"([\d,]+(?:\.\d{1,2})?)", amt_ln)
                    if amt_m and _keep(desc):
                        _add(desc, amt_m.group(1))
                    i += 3
                    continue
            i += 1

    # ── Layout B: inline  <sr?>  <description>  Rs. <amount> ─────────────
    inline = re.compile(
        r"^\s*(?:\d{1,3}\s+)?"                       # optional sr.no
        r"([A-Za-z][^\n]{5,100}?)\s+"                # description
        r"(?:Rs\.?\s*|INR\s*)([\d,]+(?:\.\d{1,2})?)"  # Rs. amount
        r"\s*$",
        re.MULTILINE | re.IGNORECASE,
    )
    for m in inline.finditer(text):
        desc = m.group(1).strip()
        if _keep(desc):
            _add(desc, m.group(2))

    return items


def _extract_dates(text: str) -> dict[str, str | None]:
    """Pull bill_date, admission_date, discharge_date by labelled context."""
    date_pattern = r"\n?\s*(\d{1,2}[-/]\w{2,9}[-/]\d{2,4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})"

    bill_date      = _first(rf"Bill\s+Date\s*[:\-]\s*{date_pattern}", text)
    admission_date = _first(rf"Admission\s+Date\s*[:\-]\s*{date_pattern}", text)
    discharge_date = _first(rf"Discharge\s+Date\s*[:\-]\s*{date_pattern}", text)

    return {
        "bill_date":       bill_date,
        "admission_date":  admission_date,
        "discharge_date":  discharge_date,
    }


# ── main extraction function ───────────────────────────────────────────────────

def extract_claim_info(file_path: Path) -> dict[str, Any]:
    """
    Extract structured claim fields from a PDF using PyMuPDF + regex.

    Returns a dict with:
        page_count, pages (list of raw text), extracted_fields

    Swap out or extend this function when a model-based version is ready.
    """
    doc        = pymupdf.open(str(file_path))
    page_texts = [page.get_text() for page in doc]
    full_text  = "\n".join(page_texts)
    doc.close()

    dates = _extract_dates(full_text)

    # Total: last occurrence of a labelled total line (may be on the next line)
    total_match = re.search(
        r"(?:TOTAL\s+AMOUNT|GRAND\s+TOTAL|Total)[^\d\n]*\n?"
        r"(?:Rs\.?\s*|INR\s*)?([\d,]+(?:\.\d{1,2})?)",
        full_text,
        re.IGNORECASE,
    )
    total_amount = (
        float(total_match.group(1).replace(",", "")) if total_match else None
    )

    extracted_fields = {
        "patient_name":    _first(r"Patient\s+Name\s*[:\-]\s*\n?(.+)", full_text),
        "hospital_name":   _first(r"^(.+?)\n", full_text, re.MULTILINE),  # first line
        "policy_number":   _first(r"Policy\s+(?:Number|No\.?)\s*[:\-]\s*\n?(\S+)", full_text),
        "bill_date":       dates["bill_date"],
        "admission_date":  dates["admission_date"],
        "discharge_date":  dates["discharge_date"],
        "line_items":      _extract_line_items(full_text),
        "total_amount":    total_amount,
    }

    return {
        "page_count":        len(page_texts),
        "pages":             [{"page": i + 1, "text": t} for i, t in enumerate(page_texts)],
        "extracted_fields":  extracted_fields,
    }


def _image_placeholder(file_path: Path) -> dict[str, Any]:
    """Placeholder result for image files pending model integration."""
    return {
        "page_count": 1,
        "pages": [
            {
                "page": 1,
                "text": "[IMAGE FILE – no raw text available]",
            }
        ],
        "extracted_fields": {
            "patient_name":    None,
            "hospital_name":   None,
            "policy_number":   None,
            "bill_date":       None,
            "admission_date":  None,
            "discharge_date":  None,
            "line_items":      [],
            "total_amount":    None,
            "_note": (
                f"Image extraction pending model integration. "
                f"File: {file_path.name}"
            ),
        },
    }


# ── route ─────────────────────────────────────────────────────────────────────

@app.post("/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    """
    Accept one or more PDF / image uploads.

    Returns a JSON list, one entry per file, with:
      filename, page_count, extracted_fields, pages (raw text per page)
    """
    if not files:
        raise HTTPException(status_code=422, detail="No files provided.")

    results = []

    for upload in files:
        name   = Path(upload.filename).name        # strip any path traversal
        suffix = Path(name).suffix.lower()

        if suffix not in {PDF_EXT} | IMAGE_EXTS:
            results.append({
                "filename": name,
                "error":    f"Unsupported file type '{suffix}'. "
                            f"Accepted: pdf, jpg, jpeg, png, bmp, tiff, webp.",
            })
            continue

        dest = UPLOADS_DIR / name
        with dest.open("wb") as f:
            shutil.copyfileobj(upload.file, f)

        try:
            if suffix == PDF_EXT:
                info = extract_claim_info(dest)
            else:
                info = _image_placeholder(dest)
        except Exception as exc:
            results.append({"filename": name, "error": str(exc)})
            continue

        results.append(
            {
                "filename":         name,
                "page_count":       info["page_count"],
                "extracted_fields": info["extracted_fields"],
                "pages":            info["pages"],
            }
        )

    return results
