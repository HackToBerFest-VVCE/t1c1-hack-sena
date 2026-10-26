"""
make_samples.py – Generate synthetic Indian hospital-claim bundles.

Each bundle lives in  sample_data/bundle_NNN/  and contains:
    claim_form.pdf        (or claim_form.jpg  for scanned bundles)
    hospital_bill.pdf
    discharge_summary.pdf (or discharge_summary.jpg for scanned bundles)
    prescription.pdf      (only for bundles that include one)

Bundles 001-008  – original set, unchanged
Bundles 009-020  – extended set with more variety

Planted defects in new bundles:
    bundle_009  CLEAN               – correct in every field (layout B)
    bundle_010  MISSING_DIAGNOSIS   – diagnosis field blank on bill and form
    bundle_011  EARLY_CLAIM         – claim filed 10 days after policy start
    bundle_012  NAME_INITIAL_MISMATCH – "Vikram R. Mehta" vs "Vikram Rajesh Mehta"
    bundle_013  BILL_TOTAL_3X       – bill total is ~3× normal amount for treatment
    bundle_014  AMOUNT_OVER         – over-claimed (layout C, with prescription)
    bundle_015  CLEAN               – correct, with prescription (layout D)
    bundle_016  WRONG_TOTAL         – phantom charge, scanned-look JPGs
    bundle_017  MISSING_SIG         – missing signature, scanned-look JPGs
    bundle_018  DATE_OUTSIDE        – admission before policy start, layout B
    bundle_019  ROUND_BILLING       – all round amounts, with prescription, layout C
    bundle_020  NAME_MISMATCH       – name mismatch, INR amount format, layout D

Run with:
    python sample_data/make_samples.py
"""

from __future__ import annotations

import copy
import io
import json
import math
import os
import random
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ── ensure stdout handles Unicode on Windows ──────────────────────────────────
if hasattr(sys.stdout, "buffer") and sys.stdout.encoding and \
        sys.stdout.encoding.lower().replace("-", "") not in ("utf8", "utf_8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from typing import Any, Callable

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, HRFlowable,
)

# ── output root ───────────────────────────────────────────────────────────────
OUT_DIR = Path(__file__).parent

W, H = A4
STYLES = getSampleStyleSheet()

# ── shared paragraph styles ───────────────────────────────────────────────────
TITLE_STYLE = ParagraphStyle(
    "CL_Title",
    parent=STYLES["Heading1"],
    fontSize=14,
    spaceAfter=4,
    textColor=colors.HexColor("#1a56db"),
)
HEADING_STYLE = ParagraphStyle(
    "CL_Heading",
    parent=STYLES["Heading2"],
    fontSize=11,
    spaceBefore=8,
    spaceAfter=4,
    textColor=colors.HexColor("#1240ab"),
)
NORMAL = STYLES["Normal"]
SMALL  = ParagraphStyle("CL_Small", parent=STYLES["Normal"], fontSize=8,
                        textColor=colors.HexColor("#555555"))

BLUE       = colors.HexColor("#1a56db")
LIGHT_BLUE = colors.HexColor("#e8f0fe")
DARK_BLUE  = colors.HexColor("#1240ab")


# ── LAYOUT THEMES (4 distinct looks) ─────────────────────────────────────────

class Theme:
    """Encapsulates colour + label choices for one hospital layout."""

    def __init__(
        self,
        header_color: str,
        header_text_color: str,
        row_alt_color: str,
        section_color: str,
        title_font_size: int,
        patient_label: str,   # label for the patient name row
        bill_no_label: str,   # label for the bill number row
        amount_formatter: Callable[[float], str],
    ):
        self.header_color       = colors.HexColor(header_color)
        self.header_text_color  = colors.HexColor(header_text_color)
        self.row_alt_color      = colors.HexColor(row_alt_color)
        self.section_color      = colors.HexColor(section_color)
        self.title_font_size    = title_font_size
        self.patient_label      = patient_label
        self.bill_no_label      = bill_no_label
        self.amount_formatter   = amount_formatter


def _fmt_rs(amount: float) -> str:
    """Format A: Rs. 12,450.75"""
    return f"Rs. {amount:,.2f}"


def _fmt_inr(amount: float) -> str:
    """Format B: INR 12,450.75"""
    return f"INR {amount:,.2f}"


def _fmt_slash(amount: float) -> str:
    """Format C: 12450/- (integer slash notation)"""
    return f"{int(amount):,}/-"


def _fmt_rupee_sign(amount: float) -> str:
    """Format D: ₹12,450.75"""
    return f"\u20b9{amount:,.2f}"


THEME_A = Theme(
    header_color="#1a56db", header_text_color="#ffffff",
    row_alt_color="#f4f7fc", section_color="#1240ab",
    title_font_size=14,
    patient_label="Patient Name",
    bill_no_label="Bill Number",
    amount_formatter=_fmt_rs,
)

THEME_B = Theme(
    header_color="#0f766e", header_text_color="#ffffff",
    row_alt_color="#f0fdf4", section_color="#065f46",
    title_font_size=15,
    patient_label="Name of Patient",
    bill_no_label="Bill No.",
    amount_formatter=_fmt_inr,
)

THEME_C = Theme(
    header_color="#7c3aed", header_text_color="#ffffff",
    row_alt_color="#faf5ff", section_color="#5b21b6",
    title_font_size=13,
    patient_label="Patient",
    bill_no_label="Invoice No.",
    amount_formatter=_fmt_slash,
)

THEME_D = Theme(
    header_color="#b91c1c", header_text_color="#ffffff",
    row_alt_color="#fff1f2", section_color="#991b1b",
    title_font_size=14,
    patient_label="Insured",
    bill_no_label="Bill Ref.",
    amount_formatter=_fmt_rupee_sign,
)

# map bundle_id prefix → theme (A for original bundles)
THEME_MAP: dict[str, Theme] = {}   # filled per-bundle below


# ── data structures ───────────────────────────────────────────────────────────

@dataclass
class LineItem:
    description: str
    amount: float


@dataclass
class BundleData:
    """All the logical data for one claim bundle."""
    bundle_id:          str
    patient_name_bill:  str
    patient_name_form:  str
    dob:                str
    policy_number:      str
    policy_start:       str
    hospital_name:      str
    hospital_address:   str
    bill_number:        str
    bill_date:          str
    admission_date:     str
    discharge_date:     str
    diagnosis:          str          # "" means diagnosis is intentionally missing
    items:              list[LineItem]
    true_total:         float
    printed_total:      float
    claimed_amount:     float
    signature:          str
    defect:             str
    defect_description: str
    theme:              Theme        = field(default_factory=lambda: THEME_A)
    include_prescription: bool       = False
    scan_docs:          bool         = False  # render claim_form + discharge as scanned JPG


# ── helpers ───────────────────────────────────────────────────────────────────

def rs(amount: float) -> str:
    return _fmt_rs(amount)


def _kv_table(rows: list[tuple[str, str]], theme: Theme | None = None) -> Table:
    th = theme or THEME_A
    lbl_style  = ParagraphStyle("lbl",  parent=SMALL,   textColor=th.section_color)
    val_style  = NORMAL

    data = [[Paragraph(f"<b>{k}</b>", lbl_style), Paragraph(v, val_style)]
            for k, v in rows]
    t = Table(data, colWidths=[4.5 * cm, 12 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (0, -1), colors.HexColor("#e8f0fe")),
        ("TEXTCOLOR",   (0, 0), (0, -1), th.section_color),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, th.row_alt_color]),
        ("GRID",        (0, 0), (-1, -1), 0.5, colors.HexColor("#d1daea")),
        ("LEFTPADDING",  (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
    ]))
    return t


def _section(title: str, theme: Theme | None = None) -> Paragraph:
    th = theme or THEME_A
    style = ParagraphStyle(
        "sec",
        parent=STYLES["Heading2"],
        fontSize=11,
        spaceBefore=8,
        spaceAfter=4,
        textColor=th.section_color,
    )
    return Paragraph(title, style)


def _doc(path: Path) -> SimpleDocTemplate:
    return SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm,  bottomMargin=2 * cm,
    )


def _title_para(text: str, theme: Theme) -> Paragraph:
    style = ParagraphStyle(
        "th_title",
        parent=STYLES["Heading1"],
        fontSize=theme.title_font_size,
        spaceAfter=4,
        textColor=theme.header_color,
    )
    return Paragraph(text, style)


# ── document builders (theme-aware) ───────────────────────────────────────────

def build_hospital_bill(path: Path, b: BundleData) -> None:
    th    = b.theme
    doc   = _doc(path)
    story = []

    story.append(_title_para(b.hospital_name, th))
    story.append(Paragraph(b.hospital_address, SMALL))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph("HOSPITAL BILL / TAX INVOICE",
                            ParagraphStyle("hd", parent=STYLES["Heading2"],
                                           fontSize=11, textColor=th.section_color)))

    diag_val = b.diagnosis if b.diagnosis else "<i>[Not specified]</i>"

    story.append(_kv_table([
        (th.patient_label,    b.patient_name_bill),
        ("Policy Number",     b.policy_number),
        (th.bill_no_label,    b.bill_number),
        ("Bill Date",         b.bill_date),
        ("Admission Date",    b.admission_date),
        ("Discharge Date",    b.discharge_date),
        ("Diagnosis",         diag_val),
    ], th))

    story.append(Spacer(1, 0.4 * cm))
    story.append(_section("Bill Particulars", th))

    header = [
        Paragraph("<b>Sr.</b>", SMALL),
        Paragraph("<b>Description</b>", SMALL),
        Paragraph("<b>Amount</b>", SMALL),
    ]
    rows = [header]
    for i, item in enumerate(b.items, 1):
        rows.append([
            Paragraph(str(i), NORMAL),
            Paragraph(item.description, NORMAL),
            Paragraph(th.amount_formatter(item.amount), NORMAL),
        ])
    rows.append([
        Paragraph("", NORMAL),
        Paragraph("<b>TOTAL AMOUNT</b>", NORMAL),
        Paragraph(f"<b>{th.amount_formatter(b.printed_total)}</b>", NORMAL),
    ])

    items_table = Table(rows, colWidths=[1.2 * cm, 12.3 * cm, 4 * cm])
    items_table.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, 0),   th.header_color),
        ("TEXTCOLOR",   (0, 0), (-1, 0),   th.header_text_color),
        ("BACKGROUND",  (0, -1),(-1, -1),  th.header_color),
        ("TEXTCOLOR",   (0, -1),(-1, -1),  th.header_text_color),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2),
         [colors.white, th.row_alt_color]),
        ("GRID",        (0, 0), (-1, -1), 0.5, colors.HexColor("#d1daea")),
        ("ALIGN",       (2, 0), (2, -1),  "RIGHT"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
    ]))
    story.append(items_table)

    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph(
        "This is a computer-generated invoice. All amounts are in Indian Rupees (INR).",
        SMALL,
    ))
    doc.build(story)


def build_claim_form(path: Path, b: BundleData) -> None:
    th    = b.theme
    doc   = _doc(path)
    story = []

    story.append(_title_para("HEALTH INSURANCE CLAIM FORM", th))
    story.append(Paragraph(
        "To be completed by the insured and submitted to the TPA.", SMALL))
    story.append(Spacer(1, 0.3 * cm))

    diag_val = b.diagnosis if b.diagnosis else "<i>[Not specified]</i>"

    story.append(_section("Insured Details", th))
    story.append(_kv_table([
        ("Insured Name",      b.patient_name_form),
        ("Date of Birth",     b.dob),
        ("Policy Number",     b.policy_number),
        ("Policy Start Date", b.policy_start),
    ], th))

    story.append(_section("Hospitalisation Details", th))
    story.append(_kv_table([
        ("Hospital Name",  b.hospital_name),
        ("Admission Date", b.admission_date),
        ("Discharge Date", b.discharge_date),
        ("Diagnosis",      diag_val),
    ], th))

    story.append(_section("Claim Details", th))
    story.append(_kv_table([
        ("Claimed Amount",     th.amount_formatter(b.claimed_amount)),
        ("Original Bill Ref.", b.bill_number),
    ], th))

    story.append(Spacer(1, 0.5 * cm))
    story.append(_section("Declaration", th))
    declaration = (
        "I / We hereby declare that the information furnished above is true "
        "and correct to the best of my / our knowledge. I / We undertake to "
        "refund the excess amount paid, if any."
    )
    story.append(Paragraph(declaration, NORMAL))
    story.append(Spacer(1, 0.6 * cm))

    if b.signature == "Present":
        sig_text = "<b>Claimant Signature :</b>  [ Signed ]"
    else:
        sig_text = "<b>Claimant Signature :</b>  [ Missing — not signed ]"
    story.append(Paragraph(sig_text, NORMAL))
    doc.build(story)


def build_discharge_summary(path: Path, b: BundleData) -> None:
    th    = b.theme
    doc   = _doc(path)
    story = []

    story.append(_title_para(b.hospital_name, th))
    story.append(Paragraph("DISCHARGE SUMMARY",
                            ParagraphStyle("hd", parent=STYLES["Heading2"],
                                           fontSize=11, textColor=th.section_color)))
    story.append(Spacer(1, 0.3 * cm))

    diag_val = b.diagnosis if b.diagnosis else "<i>[Not specified]</i>"

    story.append(_kv_table([
        (th.patient_label,  b.patient_name_bill),
        ("Date of Birth",   b.dob),
        ("Admission Date",  b.admission_date),
        ("Discharge Date",  b.discharge_date),
        ("Diagnosis",       diag_val),
        ("Treating Doctor", "Dr. Ramesh Nair, MBBS, MD (General Medicine)"),
    ], th))

    story.append(_section("Clinical Summary", th))
    clinical_text = (
        f"Patient {b.patient_name_bill} was admitted on {b.admission_date} with "
        f"a presenting complaint of {b.diagnosis.lower() if b.diagnosis else 'undisclosed condition'}. "
        "The patient was managed conservatively with appropriate medications and monitoring. "
        "Condition improved satisfactorily and the patient was discharged on "
        f"{b.discharge_date} in a stable condition with advice to follow up in 2 weeks."
    )
    story.append(Paragraph(clinical_text, NORMAL))

    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph(
        "<b>Signature of Treating Doctor :</b>  [ Signed ]", NORMAL
    ))
    doc.build(story)


def build_prescription(path: Path, b: BundleData) -> None:
    """Fourth document – medication prescription slip."""
    th    = b.theme
    doc   = _doc(path)
    story = []

    story.append(_title_para(b.hospital_name, th))
    story.append(Paragraph("PRESCRIPTION / MEDICATION ADVICE", SMALL))
    story.append(HRFlowable(width="100%", thickness=1,
                             color=th.header_color, spaceAfter=6))

    story.append(_kv_table([
        (th.patient_label, b.patient_name_bill),
        ("Date of Discharge", b.discharge_date),
        ("Diagnosis",         b.diagnosis if b.diagnosis else "<i>[Not specified]</i>"),
    ], th))

    story.append(Spacer(1, 0.4 * cm))
    story.append(_section("Medications Prescribed", th))

    meds = [
        ["Sr.", "Drug Name",             "Dosage",     "Duration",  "Instructions"],
        ["1",   "Tab. Amoxicillin 500mg", "1-0-1",     "7 days",    "After meals"],
        ["2",   "Tab. Pantoprazole 40mg", "1-0-0",     "14 days",   "Before breakfast"],
        ["3",   "Syp. Electral",          "As needed", "5 days",    "Dilute in water"],
        ["4",   "Tab. Paracetamol 500mg", "SOS",       "3 days",    "For fever/pain"],
    ]
    med_table = Table(
        [[Paragraph(f"<b>{c}</b>" if r == 0 else c, SMALL if r == 0 else NORMAL)
          for c in row]
         for r, row in enumerate(meds)],
        colWidths=[0.8*cm, 4.5*cm, 2*cm, 2.2*cm, 5*cm],
    )
    med_table.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, 0),  th.header_color),
        ("TEXTCOLOR",   (0, 0), (-1, 0),  th.header_text_color),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, th.row_alt_color]),
        ("GRID",        (0, 0), (-1, -1), 0.5, colors.HexColor("#d1daea")),
        ("LEFTPADDING",  (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING",   (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
    ]))
    story.append(med_table)

    story.append(Spacer(1, 0.6 * cm))
    story.append(Paragraph(
        "<b>Doctor's Signature :</b>  [ Signed ] &nbsp;&nbsp; "
        "<b>Registration No. :</b>  MCI/KA/2019/12345", NORMAL))
    doc.build(story)


# ── scanned-look rendering ────────────────────────────────────────────────────

def _render_pdf_to_scanned_jpg(pdf_path: Path, out_jpg: Path,
                                 seed: int = 42) -> None:
    """
    Convert first page of a PDF to a JPG that looks like a scanned document:
      - slight rotation (±1.5°)
      - salt-and-pepper noise
      - mild Gaussian blur

    Requires Pillow.  Falls back silently to keeping the PDF if Pillow is absent.
    """
    try:
        from PIL import Image, ImageFilter, ImageDraw
        import struct
    except ImportError:
        # Pillow not installed – skip the scan effect
        shutil.copy(str(pdf_path), str(out_jpg.with_suffix(".pdf")))
        return

    # Try pdf2image / pypdfium2 / pymupdf for rasterisation, fall back to
    # a plain white canvas with a "SCANNED" watermark if none are available.
    img: Image.Image | None = None

    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(pdf_path))
        page = pdf[0]
        bitmap = page.render(scale=2)          # 144 dpi
        img = bitmap.to_pil()
    except Exception:
        pass

    if img is None:
        try:
            from pdf2image import convert_from_path
            pages = convert_from_path(str(pdf_path), dpi=144)
            img = pages[0]
        except Exception:
            pass

    if img is None:
        try:
            import fitz  # PyMuPDF
            doc_fitz = fitz.open(str(pdf_path))
            page_fitz = doc_fitz[0]
            mat = fitz.Matrix(2, 2)
            pix = page_fitz.get_pixmap(matrix=mat)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        except Exception:
            pass

    if img is None:
        # No rasteriser available – create a placeholder grey page
        img = Image.new("RGB", (1240, 1754), color=(245, 245, 245))
        draw = ImageDraw.Draw(img)
        draw.text((400, 800), f"[SCANNED – {pdf_path.name}]", fill=(180, 180, 180))

    rng = random.Random(seed)

    # Convert to RGB if needed
    img = img.convert("RGB")

    # Slight rotation
    angle = rng.uniform(-1.5, 1.5)
    img = img.rotate(angle, expand=False, fillcolor=(255, 255, 255))

    # Salt-and-pepper noise
    import array as arr
    pixels = img.load()
    w, h_ = img.size
    noise_count = int(w * h_ * 0.004)
    for _ in range(noise_count):
        x = rng.randint(0, w - 1)
        y = rng.randint(0, h_ - 1)
        val = rng.choice([0, 255])
        pixels[x, y] = (val, val, val)

    # Mild Gaussian blur
    img = img.filter(ImageFilter.GaussianBlur(radius=0.6))

    # Slight yellowing / paper tone
    img_arr = img.split()
    # boost red/green slightly for aged paper
    from PIL import ImageEnhance
    img = ImageEnhance.Color(img).enhance(0.85)

    img.save(str(out_jpg), "JPEG", quality=82)


# ── item sets ─────────────────────────────────────────────────────────────────

def _items_normal() -> list[LineItem]:
    return [
        LineItem("Room charges (4 days @ Rs. 3,800/day)", 15200.00),
        LineItem("Consultant visit charges",               4500.00),
        LineItem("Medicines & IV fluids",                  6820.75),
        LineItem("Diagnostic tests (CBC, LFT, USG)",       3150.00),
        LineItem("Nursing & miscellaneous",                2400.50),
    ]


def _items_round() -> list[LineItem]:
    return [
        LineItem("Room charges",       15000.00),
        LineItem("Consultant charges",  5000.00),
        LineItem("Medicines",           7000.00),
        LineItem("Diagnostic tests",    3000.00),
        LineItem("Nursing charges",     2000.00),
    ]


def _items_wrong_total() -> list[LineItem]:
    return [
        LineItem("Room charges (5 days @ Rs. 4,500/day)", 22500.00),
        LineItem("Operation Theatre charges",             35000.00),
        LineItem("Surgeon & Anaesthesia fees",            28000.00),
        LineItem("Medicines & Consumables",                8750.50),
        LineItem("Diagnostic tests",                       4200.00),
    ]


def _items_ortho() -> list[LineItem]:
    """Typical orthopedic (knee replacement) – moderate cost, realistic fractional amounts."""
    return [
        LineItem("Room charges (6 days @ Rs. 4,200/day)", 25200.00),
        LineItem("Operation Theatre & implant charges",   58750.50),
        LineItem("Surgeon & Anaesthesia fees",            34620.00),
        LineItem("Physiotherapy (6 sessions)",             4875.00),
        LineItem("Medicines & Consumables",                9350.75),
        LineItem("Diagnostic tests (X-ray, MRI)",          5180.50),
        LineItem("Nursing & miscellaneous",                3100.50),
    ]


def _items_cardiac() -> list[LineItem]:
    """Cardiac stenting – higher cost, realistic fractional amounts."""
    return [
        LineItem("ICU charges (3 days @ Rs. 12,000/day)",  36000.00),
        LineItem("Cath Lab procedure charges",             85420.50),
        LineItem("Stent (drug-eluting) × 2",               62350.00),
        LineItem("Cardiologist & Anaesthesia fees",        28175.00),
        LineItem("Medicines & IV fluids",                  14320.50),
        LineItem("Diagnostic tests (ECG, Echo, Angio)",     8750.75),
        LineItem("Nursing & miscellaneous",                 5180.25),
    ]


def _items_maternity() -> list[LineItem]:
    """Normal delivery – lower cost."""
    return [
        LineItem("Room charges (3 days @ Rs. 2,800/day)",  8400.00),
        LineItem("Gynaecologist delivery charges",          12000.00),
        LineItem("Paediatrician visit charges",              1500.00),
        LineItem("Medicines & IV fluids",                   3120.50),
        LineItem("Nursing & miscellaneous",                  2050.00),
    ]


def _items_ent() -> list[LineItem]:
    """ENT (tonsillectomy) – moderate cost, realistic fractional amounts."""
    return [
        LineItem("Room charges (2 days @ Rs. 3,200/day)", 6400.00),
        LineItem("Operation Theatre charges",             17850.50),
        LineItem("ENT Surgeon fees",                      11750.00),
        LineItem("Anaesthesia charges",                    4320.75),
        LineItem("Medicines & Consumables",                2830.75),
        LineItem("Diagnostic tests (blood work)",          1420.50),
    ]


def _sum(items: list[LineItem]) -> float:
    return round(sum(i.amount for i in items), 2)


# ── bundle definitions ────────────────────────────────────────────────────────

BUNDLES_DEF: list[BundleData] = [

    # ═══════════════════════════════════════════════════════════════════════════
    #  ORIGINAL BUNDLES 001-008  (unchanged)
    # ═══════════════════════════════════════════════════════════════════════════

    # ── 001 CLEAN ──────────────────────────────────────────────────────────────
    BundleData(
        bundle_id="bundle_001",
        patient_name_bill="Rajesh Kumar Sharma",
        patient_name_form="Rajesh Kumar Sharma",
        dob="12-Mar-1978",
        policy_number="HDFC-HI-2024-00341",
        policy_start="01-Jan-2024",
        hospital_name="Apollo Hospitals, Bengaluru",
        hospital_address="154, Bannerghatta Road, Bengaluru - 560 076",
        bill_number="APB/2024/007821",
        bill_date="13-Nov-2024",
        admission_date="08-Nov-2024",
        discharge_date="13-Nov-2024",
        diagnosis="Acute Appendicitis - Laparoscopic Appendectomy",
        items=_items_normal(),
        true_total=_sum(_items_normal()),
        printed_total=_sum(_items_normal()),
        claimed_amount=_sum(_items_normal()),
        signature="Present",
        defect="CLEAN",
        defect_description="No defects planted. All fields correct.",
        theme=THEME_A,
    ),

    # ── 002 AMOUNT_OVER ────────────────────────────────────────────────────────
    BundleData(
        bundle_id="bundle_002",
        patient_name_bill="Sunita Devi Agarwal",
        patient_name_form="Sunita Devi Agarwal",
        dob="05-Jul-1985",
        policy_number="ICICI-LOM-2024-88210",
        policy_start="01-Feb-2024",
        hospital_name="Max Super Speciality Hospital, Delhi",
        hospital_address="1, Press Enclave Road, Saket, New Delhi - 110 017",
        bill_number="MAX/2024/009847",
        bill_date="05-Nov-2024",
        admission_date="01-Nov-2024",
        discharge_date="05-Nov-2024",
        diagnosis="Dengue Fever with Thrombocytopenia",
        items=_items_normal(),
        true_total=_sum(_items_normal()),
        printed_total=_sum(_items_normal()),
        claimed_amount=_sum(_items_normal()) + 46680.00,
        signature="Present",
        defect="AMOUNT_OVER",
        defect_description=(
            f"Claimed amount on form (Rs. {_sum(_items_normal()) + 46680:.2f}) "
            f"exceeds billed total (Rs. {_sum(_items_normal()):.2f}) by Rs. 46,680.00."
        ),
        theme=THEME_A,
    ),

    # ── 003 WRONG_TOTAL ────────────────────────────────────────────────────────
    BundleData(
        bundle_id="bundle_003",
        patient_name_bill="Priya Venkataraman Iyer",
        patient_name_form="Priya Venkataraman Iyer",
        dob="22-Sep-1990",
        policy_number="STAR-HI-2024-56789",
        policy_start="15-Mar-2024",
        hospital_name="Manipal Hospital, Chennai",
        hospital_address="No. 1, New Tank Street, Nungambakkam, Chennai - 600 034",
        bill_number="MHC/2024/015443",
        bill_date="21-Oct-2024",
        admission_date="18-Oct-2024",
        discharge_date="21-Oct-2024",
        diagnosis="Viral Fever with Dehydration",
        items=_items_wrong_total(),
        true_total=_sum(_items_wrong_total()),
        printed_total=_sum(_items_wrong_total()) + 5000.00,
        claimed_amount=_sum(_items_wrong_total()) + 5000.00,
        signature="Present",
        defect="WRONG_TOTAL",
        defect_description=(
            f"Line items sum to Rs. {_sum(_items_wrong_total()):.2f} but bill "
            f"total printed as Rs. {_sum(_items_wrong_total()) + 5000:.2f} "
            "(Rs. 5,000 phantom charge)."
        ),
        theme=THEME_A,
    ),

    # ── 004 MISSING_SIG ────────────────────────────────────────────────────────
    BundleData(
        bundle_id="bundle_004",
        patient_name_bill="Arun Kumar Pillai",
        patient_name_form="Arun Kumar Pillai",
        dob="15-Jun-1972",
        policy_number="BAJAJ-AL-2024-11203",
        policy_start="01-Apr-2024",
        hospital_name="Apollo Hospitals, Hyderabad",
        hospital_address="Jubilee Hills, Hyderabad - 500 033",
        bill_number="APH/2024/003021",
        bill_date="07-Nov-2024",
        admission_date="04-Nov-2024",
        discharge_date="07-Nov-2024",
        diagnosis="Type-2 Diabetes with Foot Infection",
        items=_items_normal(),
        true_total=_sum(_items_normal()),
        printed_total=_sum(_items_normal()),
        claimed_amount=_sum(_items_normal()),
        signature="Missing",
        defect="MISSING_SIG",
        defect_description="Claimant signature field is blank on the claim form.",
        theme=THEME_A,
    ),

    # ── 005 NAME_MISMATCH ──────────────────────────────────────────────────────
    BundleData(
        bundle_id="bundle_005",
        patient_name_bill="Meera Subramanian Nair",
        patient_name_form="Meera S. Pillai",
        dob="30-Jan-1995",
        policy_number="NIAC-HI-2024-33901",
        policy_start="01-Jun-2024",
        hospital_name="Fortis Malar Hospital, Chennai",
        hospital_address="52, 1st Main Road, Gandhi Nagar, Adyar, Chennai - 600 020",
        bill_number="FMC/2024/008812",
        bill_date="12-Nov-2024",
        admission_date="09-Nov-2024",
        discharge_date="12-Nov-2024",
        diagnosis="Acute Pyelonephritis",
        items=_items_normal(),
        true_total=_sum(_items_normal()),
        printed_total=_sum(_items_normal()),
        claimed_amount=_sum(_items_normal()),
        signature="Present",
        defect="NAME_MISMATCH",
        defect_description=(
            "Patient name on bill/discharge ('Meera Subramanian Nair') differs from "
            "name on claim form ('Meera S. Pillai')."
        ),
        theme=THEME_A,
    ),

    # ── 006 DATE_OUTSIDE ───────────────────────────────────────────────────────
    BundleData(
        bundle_id="bundle_006",
        patient_name_bill="Suresh Hasmukhlal Patel",
        patient_name_form="Suresh Hasmukhlal Patel",
        dob="29-Sep-1965",
        policy_number="STAR-HI-2024-77412",
        policy_start="01-Dec-2024",
        hospital_name="Kokilaben Dhirubhai Ambani Hospital, Mumbai",
        hospital_address="Andheri (W), Mumbai - 400 053",
        bill_number="KDA/2024/029984",
        bill_date="02-Nov-2024",
        admission_date="27-Oct-2024",
        discharge_date="02-Nov-2024",
        diagnosis="COPD Exacerbation",
        items=_items_normal(),
        true_total=_sum(_items_normal()),
        printed_total=_sum(_items_normal()),
        claimed_amount=_sum(_items_normal()),
        signature="Present",
        defect="DATE_OUTSIDE",
        defect_description=(
            "Admission date (27-Oct-2024) is before policy start date (01-Dec-2024). "
            "Treatment occurred outside the active policy period."
        ),
        theme=THEME_A,
    ),

    # ── 007 ROUND_BILLING ──────────────────────────────────────────────────────
    BundleData(
        bundle_id="bundle_007",
        patient_name_bill="Kavitha Ramachandran",
        patient_name_form="Kavitha Ramachandran",
        dob="14-Feb-1988",
        policy_number="HDFC-HI-2024-99123",
        policy_start="01-Jan-2024",
        hospital_name="Sri Ramachandra Medical Centre, Chennai",
        hospital_address="No. 1, Ramachandra Nagar, Porur, Chennai - 600 116",
        bill_number="SRM/2024/041200",
        bill_date="20-Nov-2024",
        admission_date="17-Nov-2024",
        discharge_date="20-Nov-2024",
        diagnosis="Urinary Tract Infection",
        items=_items_round(),
        true_total=_sum(_items_round()),
        printed_total=_sum(_items_round()),
        claimed_amount=_sum(_items_round()),
        signature="Present",
        defect="ROUND_BILLING",
        defect_description=(
            "All 5 line items have round-number amounts ending in .00 "
            "(100% round-number ratio, threshold > 60%)."
        ),
        theme=THEME_A,
    ),

    # ── 008 DUPLICATE ─────────────────────────────────────────────────────────
    BundleData(
        bundle_id="bundle_008",
        patient_name_bill="Rajesh Kumar Sharma",
        patient_name_form="Rajesh Kumar Sharma",
        dob="12-Mar-1978",
        policy_number="HDFC-HI-2024-00341",
        policy_start="01-Jan-2024",
        hospital_name="Apollo Hospitals, Bengaluru",
        hospital_address="154, Bannerghatta Road, Bengaluru - 560 076",
        bill_number="APB/2024/007821",
        bill_date="13-Nov-2024",
        admission_date="08-Nov-2024",
        discharge_date="13-Nov-2024",
        diagnosis="Acute Appendicitis - Laparoscopic Appendectomy",
        items=_items_normal(),
        true_total=_sum(_items_normal()),
        printed_total=_sum(_items_normal()),
        claimed_amount=_sum(_items_normal()),
        signature="Present",
        defect="DUPLICATE",
        defect_description=(
            "Identical patient, hospital, bill number, dates and amounts as bundle_001. "
            "This is an exact duplicate submission."
        ),
        theme=THEME_A,
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    #  NEW BUNDLES 009-020
    # ═══════════════════════════════════════════════════════════════════════════

    # ── 009 CLEAN  (Theme B – teal, INR format, "Name of Patient") ────────────
    BundleData(
        bundle_id="bundle_009",
        patient_name_bill="Aarav Suresh Menon",
        patient_name_form="Aarav Suresh Menon",
        dob="07-Apr-1992",
        policy_number="LIC-HI-2025-20456",
        policy_start="01-Apr-2025",
        hospital_name="Narayana Health City, Bengaluru",
        hospital_address="258/A, Bommasandra Industrial Area, Bengaluru - 560 099",
        bill_number="NHC/2025/003410",
        bill_date="15-Sep-2025",
        admission_date="10-Sep-2025",
        discharge_date="15-Sep-2025",
        diagnosis="Knee Ligament Repair - ACL Reconstruction",
        items=_items_ortho(),
        true_total=_sum(_items_ortho()),
        printed_total=_sum(_items_ortho()),
        claimed_amount=_sum(_items_ortho()),
        signature="Present",
        defect="CLEAN",
        defect_description="No defects planted. All fields correct.",
        theme=THEME_B,
    ),

    # ── 010 MISSING_DIAGNOSIS  (Theme C – purple, slash format) ───────────────
    BundleData(
        bundle_id="bundle_010",
        patient_name_bill="Pooja Krishnamurthy",
        patient_name_form="Pooja Krishnamurthy",
        dob="18-Nov-1987",
        policy_number="CARE-HI-2025-10234",
        policy_start="01-Jan-2025",
        hospital_name="Columbia Asia Hospital, Pune",
        hospital_address="Kharadi, Pune - 411 014",
        bill_number="CAP/2025/007788",
        bill_date="20-Jul-2025",
        admission_date="17-Jul-2025",
        discharge_date="20-Jul-2025",
        diagnosis="",                         # PLANTED: blank diagnosis
        items=_items_normal(),
        true_total=_sum(_items_normal()),
        printed_total=_sum(_items_normal()),
        claimed_amount=_sum(_items_normal()),
        signature="Present",
        defect="MISSING_DIAGNOSIS",
        defect_description=(
            "Diagnosis field is blank on both the hospital bill and the claim form. "
            "Required for processing any health insurance claim."
        ),
        theme=THEME_C,
    ),

    # ── 011 EARLY_CLAIM  (Theme D – red, ₹ format) ────────────────────────────
    # Policy start: 01-Aug-2025; admission: 11-Aug-2025 (10 days after start)
    BundleData(
        bundle_id="bundle_011",
        patient_name_bill="Deepak Ramesh Joshi",
        patient_name_form="Deepak Ramesh Joshi",
        dob="03-Dec-1980",
        policy_number="RELIANCE-HI-2025-55012",
        policy_start="01-Aug-2025",
        hospital_name="Wockhardt Hospital, Mumbai",
        hospital_address="1877, Dr. Anandrao Nair Road, Agripada, Mumbai - 400 011",
        bill_number="WKH/2025/012334",
        bill_date="18-Aug-2025",
        admission_date="11-Aug-2025",           # PLANTED: 10 days after policy start
        discharge_date="18-Aug-2025",
        diagnosis="Acute Myocardial Infarction - PTCA with Stenting",
        items=_items_cardiac(),
        true_total=_sum(_items_cardiac()),
        printed_total=_sum(_items_cardiac()),
        claimed_amount=_sum(_items_cardiac()),
        signature="Present",
        defect="EARLY_CLAIM",
        defect_description=(
            "Admission date (11-Aug-2025) is only 10 days after policy start "
            "(01-Aug-2025). Many policies have a 30-day initial waiting period for "
            "non-accidental hospitalisation."
        ),
        theme=THEME_D,
    ),

    # ── 012 NAME_INITIAL_MISMATCH  (Theme B – teal) ───────────────────────────
    # Bill: "Vikram Rajesh Mehta"  |  Form: "Vikram R. Mehta"
    BundleData(
        bundle_id="bundle_012",
        patient_name_bill="Vikram Rajesh Mehta",
        patient_name_form="Vikram R. Mehta",        # PLANTED: initial vs full name
        dob="25-Jun-1975",
        policy_number="ICICI-LOM-2025-71100",
        policy_start="01-Feb-2025",
        hospital_name="Hinduja Hospital, Mumbai",
        hospital_address="Veer Savarkar Marg, Mahim, Mumbai - 400 016",
        bill_number="HIN/2025/006522",
        bill_date="05-Oct-2025",
        admission_date="01-Oct-2025",
        discharge_date="05-Oct-2025",
        diagnosis="Tonsillitis - Bilateral Tonsillectomy",
        items=_items_ent(),
        true_total=_sum(_items_ent()),
        printed_total=_sum(_items_ent()),
        claimed_amount=_sum(_items_ent()),
        signature="Present",
        defect="NAME_INITIAL_MISMATCH",
        defect_description=(
            "Patient name on bill/discharge ('Vikram Rajesh Mehta') uses full middle "
            "name, but claim form uses initial only ('Vikram R. Mehta'). Middle name "
            "abbreviation inconsistency."
        ),
        theme=THEME_B,
    ),

    # ── 013 BILL_TOTAL_3X  (Theme C – purple, slash format) ───────────────────
    # Normal maternity bill ~₹27,070; planted total = ~₹81,210 (3×)
    # (pre-computed below; inserted as a BundleData entry in the list)
]

# Pre-compute for bundle_013 outside the list literal to avoid SyntaxError
_b013_items = _items_maternity()
_b013_true  = _sum(_b013_items)
_b013_bill  = round(_b013_true * 3, 2)

BUNDLES_DEF += [
    BundleData(
        bundle_id="bundle_013",
        patient_name_bill="Nandini Gopal Rao",
        patient_name_form="Nandini Gopal Rao",
        dob="11-Aug-1993",
        policy_number="STAR-HI-2025-31002",
        policy_start="01-Mar-2025",
        hospital_name="Cloudnine Hospital, Bengaluru",
        hospital_address="1533, 9th Main Rd, Sector 7, HSR Layout, Bengaluru - 560 102",
        bill_number="CLD/2025/009991",
        bill_date="08-Oct-2025",
        admission_date="05-Oct-2025",
        discharge_date="08-Oct-2025",
        diagnosis="Normal Vaginal Delivery",
        items=_b013_items,
        true_total=_b013_true,
        printed_total=_b013_bill,       # PLANTED: 3× the expected total
        claimed_amount=_b013_bill,
        signature="Present",
        defect="BILL_TOTAL_3X",
        defect_description=(
            f"Billed total ({_fmt_slash(_b013_bill)}) is approximately 3× the typical "
            f"amount ({_fmt_slash(_b013_true)}) for a normal delivery. Line items sum to "
            f"the standard amount; printed total is inflated."
        ),
        theme=THEME_C,
    ),

    # ── 014 AMOUNT_OVER  (Theme D, with prescription) ─────────────────────────
    BundleData(
        bundle_id="bundle_014",
        patient_name_bill="Sanjay Balkrishna Deshpande",
        patient_name_form="Sanjay Balkrishna Deshpande",
        dob="22-May-1969",
        policy_number="BAJAJ-AL-2025-40210",
        policy_start="01-Apr-2025",
        hospital_name="Sahyadri Super Speciality Hospital, Pune",
        hospital_address="Plot No. 30-C, Erandwane, Karve Road, Pune - 411 004",
        bill_number="SSH/2025/005530",
        bill_date="12-Sep-2025",
        admission_date="06-Sep-2025",
        discharge_date="12-Sep-2025",
        diagnosis="Coronary Artery Disease - Bypass Surgery (CABG)",
        items=_items_cardiac(),
        true_total=_sum(_items_cardiac()),
        printed_total=_sum(_items_cardiac()),
        claimed_amount=_sum(_items_cardiac()) + 55000.00,   # PLANTED: over-claim
        signature="Present",
        defect="AMOUNT_OVER",
        defect_description=(
            f"Claimed amount (₹{_sum(_items_cardiac()) + 55000:,.2f}) exceeds "
            f"billed total (₹{_sum(_items_cardiac()):,.2f}) by ₹55,000.00."
        ),
        theme=THEME_D,
        include_prescription=True,
    ),

    # ── 015 CLEAN  (Theme B, with prescription) ───────────────────────────────
    BundleData(
        bundle_id="bundle_015",
        patient_name_bill="Ananya Pradeep Nambiar",
        patient_name_form="Ananya Pradeep Nambiar",
        dob="09-Jan-2000",
        policy_number="HDFC-HI-2025-62345",
        policy_start="01-Jun-2025",
        hospital_name="KIMS Hospital, Hyderabad",
        hospital_address="1-8-31/1, Minister Road, Secunderabad - 500 003",
        bill_number="KIMS/2025/011241",
        bill_date="22-Sep-2025",
        admission_date="19-Sep-2025",
        discharge_date="22-Sep-2025",
        diagnosis="Acute Appendicitis - Open Appendectomy",
        items=_items_normal(),
        true_total=_sum(_items_normal()),
        printed_total=_sum(_items_normal()),
        claimed_amount=_sum(_items_normal()),
        signature="Present",
        defect="CLEAN",
        defect_description="No defects planted. Includes prescription as fourth document.",
        theme=THEME_B,
        include_prescription=True,
    ),

    # ── 016 WRONG_TOTAL  (Theme C, scanned-look JPGs) ─────────────────────────
    BundleData(
        bundle_id="bundle_016",
        patient_name_bill="Ravi Shankar Bhat",
        patient_name_form="Ravi Shankar Bhat",
        dob="30-Jul-1955",
        policy_number="NIC-HI-2025-80011",
        policy_start="15-Jan-2025",
        hospital_name="BGS Gleneagles Global Hospital, Bengaluru",
        hospital_address="67, Uttarahalli Road, Kengeri, Bengaluru - 560 060",
        bill_number="BGS/2025/009120",
        bill_date="14-Aug-2025",
        admission_date="09-Aug-2025",
        discharge_date="14-Aug-2025",
        diagnosis="Lumbar Disc Herniation - Microdiscectomy",
        items=_items_ortho(),
        true_total=_sum(_items_ortho()),
        printed_total=_sum(_items_ortho()) + 8500.00,   # PLANTED: phantom charge
        claimed_amount=_sum(_items_ortho()) + 8500.00,
        signature="Present",
        defect="WRONG_TOTAL",
        defect_description=(
            f"Line items sum to INR {_sum(_items_ortho()):,.2f} but bill total "
            f"printed as INR {_sum(_items_ortho()) + 8500:,.2f} "
            "(INR 8,500 phantom charge). Claim form and discharge summary "
            "rendered as scanned-look JPG images."
        ),
        theme=THEME_C,
        scan_docs=True,
    ),

    # ── 017 MISSING_SIG  (Theme D, scanned-look JPGs) ─────────────────────────
    BundleData(
        bundle_id="bundle_017",
        patient_name_bill="Fatima Begum Khan",
        patient_name_form="Fatima Begum Khan",
        dob="14-Mar-1978",
        policy_number="ORIENTAL-HI-2025-44001",
        policy_start="01-May-2025",
        hospital_name="Apollo DRDO Hospital, Hyderabad",
        hospital_address="DRDO Complex, Kanchanbagh, Hyderabad - 500 058",
        bill_number="ADH/2025/002910",
        bill_date="27-Sep-2025",
        admission_date="24-Sep-2025",
        discharge_date="27-Sep-2025",
        diagnosis="Dengue Haemorrhagic Fever",
        items=_items_normal(),
        true_total=_sum(_items_normal()),
        printed_total=_sum(_items_normal()),
        claimed_amount=_sum(_items_normal()),
        signature="Missing",                    # PLANTED
        defect="MISSING_SIG",
        defect_description=(
            "Claimant signature field is blank on the claim form. "
            "Claim form and discharge summary rendered as scanned-look JPG images."
        ),
        theme=THEME_D,
        scan_docs=True,
    ),

    # ── 018 DATE_OUTSIDE  (Theme B – teal) ────────────────────────────────────
    # Policy start: 01-Oct-2025; admission: 12-Sep-2025 (before start)
    BundleData(
        bundle_id="bundle_018",
        patient_name_bill="Geetanjali Mohan Kulkarni",
        patient_name_form="Geetanjali Mohan Kulkarni",
        dob="26-Feb-1971",
        policy_number="STAR-HI-2025-90123",
        policy_start="01-Oct-2025",
        hospital_name="Ruby Hall Clinic, Pune",
        hospital_address="40, Sassoon Road, Pune - 411 001",
        bill_number="RHC/2025/013400",
        bill_date="20-Sep-2025",
        admission_date="12-Sep-2025",           # PLANTED: before policy start
        discharge_date="20-Sep-2025",
        diagnosis="Cholecystitis - Laparoscopic Cholecystectomy",
        items=_items_ent(),
        true_total=_sum(_items_ent()),
        printed_total=_sum(_items_ent()),
        claimed_amount=_sum(_items_ent()),
        signature="Present",
        defect="DATE_OUTSIDE",
        defect_description=(
            "Admission date (12-Sep-2025) is before policy start date (01-Oct-2025). "
            "Treatment occurred outside the active policy period."
        ),
        theme=THEME_B,
    ),

    # ── 019 ROUND_BILLING  (Theme C, with prescription) ───────────────────────
    BundleData(
        bundle_id="bundle_019",
        patient_name_bill="Harish Chandra Tripathi",
        patient_name_form="Harish Chandra Tripathi",
        dob="02-Oct-1960",
        policy_number="UNITED-HI-2025-55678",
        policy_start="01-Jan-2025",
        hospital_name="Medanta - The Medicity, Gurugram",
        hospital_address="CH Baktawar Singh Road, Sector 38, Gurugram - 122 001",
        bill_number="MDT/2025/027000",
        bill_date="18-Oct-2025",
        admission_date="15-Oct-2025",
        discharge_date="18-Oct-2025",
        diagnosis="Hypertensive Emergency",
        items=_items_round(),                   # PLANTED: all round amounts
        true_total=_sum(_items_round()),
        printed_total=_sum(_items_round()),
        claimed_amount=_sum(_items_round()),
        signature="Present",
        defect="ROUND_BILLING",
        defect_description=(
            "All 5 line items have round-number amounts ending in .00 "
            "(100% round-number ratio, threshold > 60%). "
            "Includes prescription as fourth document."
        ),
        theme=THEME_C,
        include_prescription=True,
    ),

    # ── 020 NAME_MISMATCH  (Theme D, INR ₹ format) ────────────────────────────
    # Bill: "Lakshmi Narayanan Swaminathan"  |  Form: "L. N. Swaminathan"
    BundleData(
        bundle_id="bundle_020",
        patient_name_bill="Lakshmi Narayanan Swaminathan",
        patient_name_form="L. N. Swaminathan",          # PLANTED: abbreviated name
        dob="17-May-1982",
        policy_number="TATA-AIG-2025-31800",
        policy_start="01-Jul-2025",
        hospital_name="Kauvery Hospital, Chennai",
        hospital_address="199, Luz Church Road, Mylapore, Chennai - 600 004",
        bill_number="KAU/2025/016800",
        bill_date="10-Oct-2025",
        admission_date="05-Oct-2025",
        discharge_date="10-Oct-2025",
        diagnosis="Renal Calculi - Percutaneous Nephrolithotomy",
        items=_items_ortho(),
        true_total=_sum(_items_ortho()),
        printed_total=_sum(_items_ortho()),
        claimed_amount=_sum(_items_ortho()),
        signature="Present",
        defect="NAME_MISMATCH",
        defect_description=(
            "Patient name on bill/discharge ('Lakshmi Narayanan Swaminathan') "
            "differs from name on claim form ('L. N. Swaminathan'). "
            "Both initials abbreviated – first name and middle name missing."
        ),
        theme=THEME_D,
    ),
]


# ── build all bundles ─────────────────────────────────────────────────────────

def build_bundle(b: BundleData) -> Path:
    bundle_dir = OUT_DIR / b.bundle_id
    bundle_dir.mkdir(exist_ok=True)

    # Hospital bill always stays as PDF
    build_hospital_bill(bundle_dir / "hospital_bill.pdf", b)

    if b.scan_docs:
        # Build PDFs first, then convert claim_form + discharge_summary to scanned JPGs
        cf_pdf  = bundle_dir / "_claim_form_tmp.pdf"
        ds_pdf  = bundle_dir / "_discharge_summary_tmp.pdf"
        build_claim_form(cf_pdf, b)
        build_discharge_summary(ds_pdf, b)

        seed = int(b.bundle_id.split("_")[1])
        _render_pdf_to_scanned_jpg(cf_pdf,  bundle_dir / "claim_form.jpg",       seed)
        _render_pdf_to_scanned_jpg(ds_pdf,  bundle_dir / "discharge_summary.jpg", seed + 1)

        # Remove the temp PDFs
        cf_pdf.unlink(missing_ok=True)
        ds_pdf.unlink(missing_ok=True)
    else:
        build_claim_form(bundle_dir / "claim_form.pdf", b)
        build_discharge_summary(bundle_dir / "discharge_summary.pdf", b)

    if b.include_prescription:
        build_prescription(bundle_dir / "prescription.pdf", b)

    return bundle_dir


def write_ground_truth(bundles: list[BundleData]) -> Path:
    """
    Merge new bundles into the existing ground_truth.json without touching
    entries that already exist (bundles 001-008).
    """
    gt_path = OUT_DIR / "ground_truth.json"

    # Load existing truth if present
    if gt_path.exists():
        existing = json.loads(gt_path.read_text(encoding="utf-8"))
    else:
        existing = {}

    for b in bundles:
        existing[b.bundle_id] = {
            "patient_name_bill":  b.patient_name_bill,
            "patient_name_form":  b.patient_name_form,
            "hospital":           b.hospital_name,
            "policy_number":      b.policy_number,
            "policy_start":       b.policy_start,
            "admission_date":     b.admission_date,
            "discharge_date":     b.discharge_date,
            "billed_total":       b.printed_total,
            "claimed_amount":     b.claimed_amount,
            "signature":          b.signature,
            "defect":             b.defect,
            "defect_description": b.defect_description,
        }

    # Sort by bundle key for readability
    ordered = dict(sorted(existing.items()))
    gt_path.write_text(json.dumps(ordered, indent=2, ensure_ascii=False),
                       encoding="utf-8")
    return gt_path


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    total = len(BUNDLES_DEF)
    print(f"Generating {total} claim bundles ...\n")

    for b in BUNDLES_DEF:
        path = build_bundle(b)
        files = sorted(path.glob("*"))
        extras = []
        if b.include_prescription:
            extras.append("+prescription")
        if b.scan_docs:
            extras.append("+scanned-JPG")
        tag = "  ".join(extras)
        print(f"  [{b.defect:<22}]  {b.bundle_id}/  {tag}")
        for f in files:
            if f.is_file():
                print(f"                         {f.name}  ({f.stat().st_size:,} bytes)")
        print(f"    Patient  : {b.patient_name_bill}", end="")
        if b.patient_name_bill != b.patient_name_form:
            print(f"  /  form: {b.patient_name_form}", end="")
        print()
        print(f"    Hospital : {b.hospital_name}")
        print(f"    Billed   : {b.theme.amount_formatter(b.printed_total)}"
              f"  |  Claimed: {b.theme.amount_formatter(b.claimed_amount)}".encode("ascii", "replace").decode())
        if b.defect_description:
            print(f"    Defect   : {b.defect_description}")
        print()

    gt = write_ground_truth(BUNDLES_DEF)
    print(f"ground_truth.json written to: {gt}\n")
    print("Done.")
