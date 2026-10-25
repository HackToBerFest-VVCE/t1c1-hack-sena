"""
make_samples.py – Generate 8 synthetic Indian hospital-claim bundles.

Each bundle lives in  sample_data/bundle_NNN/  and contains:
    claim_form.pdf
    hospital_bill.pdf
    discharge_summary.pdf

Planted defects (one per bundle, except bundle_001 which is clean):
    bundle_001  CLEAN           – correct in every field
    bundle_002  AMOUNT_OVER     – claimed amount > billed total
    bundle_003  WRONG_TOTAL     – line items do not sum to the printed total
    bundle_004  MISSING_SIG     – claim form has no claimant signature
    bundle_005  NAME_MISMATCH   – patient name differs across documents
    bundle_006  DATE_OUTSIDE    – admission date outside policy period
    bundle_007  ROUND_BILLING   – every line item ends in .00
    bundle_008  DUPLICATE       – exact copy of bundle_001

ground_truth.json is written to sample_data/ listing the defect for each bundle.

Run with:
    python sample_data/make_samples.py
"""

from __future__ import annotations

import copy
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

# ── output root ───────────────────────────────────────────────────────────────
OUT_DIR = Path(__file__).parent

W, H = A4
STYLES = getSampleStyleSheet()

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

BLUE = colors.HexColor("#1a56db")
LIGHT_BLUE = colors.HexColor("#e8f0fe")
DARK_BLUE  = colors.HexColor("#1240ab")


# ── data structures ───────────────────────────────────────────────────────────

@dataclass
class LineItem:
    description: str
    amount: float


@dataclass
class BundleData:
    """All the logical data for one claim bundle."""
    bundle_id:          str
    patient_name_bill:  str          # name on the bill / discharge
    patient_name_form:  str          # name on the claim form (may differ)
    dob:                str
    policy_number:      str
    policy_start:       str          # dd-Mon-YYYY
    hospital_name:      str
    hospital_address:   str
    bill_number:        str
    bill_date:          str
    admission_date:     str
    discharge_date:     str
    diagnosis:          str
    items:              list[LineItem]
    true_total:         float        # actual sum of items
    printed_total:      float        # what goes on the bill (may differ)
    claimed_amount:     float        # what the form claims
    signature:          str          # "Present" | "Missing"
    defect:             str
    defect_description: str


# ── helpers ───────────────────────────────────────────────────────────────────

def rs(amount: float) -> str:
    """Format as Indian rupee string."""
    return f"Rs. {amount:,.2f}"


def _kv_table(rows: list[tuple[str, str]]) -> Table:
    """Two-column key-value table."""
    data = [[Paragraph(f"<b>{k}</b>", SMALL), Paragraph(v, NORMAL)]
            for k, v in rows]
    t = Table(data, colWidths=[4.5 * cm, 12 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (0, -1), LIGHT_BLUE),
        ("TEXTCOLOR",   (0, 0), (0, -1), DARK_BLUE),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#f4f7fc")]),
        ("GRID",        (0, 0), (-1, -1), 0.5, colors.HexColor("#d1daea")),
        ("LEFTPADDING",  (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
    ]))
    return t


def _section(title: str) -> Paragraph:
    return Paragraph(title, HEADING_STYLE)


def _doc(path: Path) -> SimpleDocTemplate:
    return SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm,  bottomMargin=2 * cm,
    )


# ── document builders ─────────────────────────────────────────────────────────

def build_hospital_bill(path: Path, b: BundleData) -> None:
    doc   = _doc(path)
    story = []

    story.append(Paragraph(b.hospital_name, TITLE_STYLE))
    story.append(Paragraph(b.hospital_address, SMALL))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph("HOSPITAL BILL / TAX INVOICE", HEADING_STYLE))

    story.append(_kv_table([
        ("Patient Name",   b.patient_name_bill),
        ("Policy Number",  b.policy_number),
        ("Bill Number",    b.bill_number),
        ("Bill Date",      b.bill_date),
        ("Admission Date", b.admission_date),
        ("Discharge Date", b.discharge_date),
        ("Diagnosis",      b.diagnosis),
    ]))

    story.append(Spacer(1, 0.4 * cm))
    story.append(_section("Bill Particulars"))

    # line items table
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
            Paragraph(rs(item.amount), NORMAL),
        ])
    # total row
    rows.append([
        Paragraph("", NORMAL),
        Paragraph("<b>TOTAL AMOUNT</b>", NORMAL),
        Paragraph(f"<b>{rs(b.printed_total)}</b>", NORMAL),
    ])

    items_table = Table(rows, colWidths=[1.2 * cm, 12.3 * cm, 4 * cm])
    items_table.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, 0),   BLUE),
        ("TEXTCOLOR",   (0, 0), (-1, 0),   colors.white),
        ("BACKGROUND",  (0, -1),(-1, -1),  BLUE),
        ("TEXTCOLOR",   (0, -1),(-1, -1),  colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2),
         [colors.white, colors.HexColor("#f4f7fc")]),
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
    doc   = _doc(path)
    story = []

    story.append(Paragraph("HEALTH INSURANCE CLAIM FORM", TITLE_STYLE))
    story.append(Paragraph(
        "To be completed by the insured and submitted to the TPA.", SMALL))
    story.append(Spacer(1, 0.3 * cm))

    story.append(_section("Insured Details"))
    story.append(_kv_table([
        ("Insured Name",    b.patient_name_form),
        ("Date of Birth",   b.dob),
        ("Policy Number",   b.policy_number),
        ("Policy Start Date", b.policy_start),
    ]))

    story.append(_section("Hospitalisation Details"))
    story.append(_kv_table([
        ("Hospital Name",   b.hospital_name),
        ("Admission Date",  b.admission_date),
        ("Discharge Date",  b.discharge_date),
        ("Diagnosis",       b.diagnosis),
    ]))

    story.append(_section("Claim Details"))
    story.append(_kv_table([
        ("Claimed Amount",     rs(b.claimed_amount)),
        ("Original Bill Ref.", b.bill_number),
    ]))

    story.append(Spacer(1, 0.5 * cm))
    story.append(_section("Declaration"))

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
    doc   = _doc(path)
    story = []

    story.append(Paragraph(b.hospital_name, TITLE_STYLE))
    story.append(Paragraph("DISCHARGE SUMMARY", HEADING_STYLE))
    story.append(Spacer(1, 0.3 * cm))

    story.append(_kv_table([
        ("Patient Name",   b.patient_name_bill),
        ("Date of Birth",  b.dob),
        ("Admission Date", b.admission_date),
        ("Discharge Date", b.discharge_date),
        ("Diagnosis",      b.diagnosis),
        ("Treating Doctor","Dr. Ramesh Nair, MBBS, MD (General Medicine)"),
    ]))

    story.append(_section("Clinical Summary"))
    story.append(Paragraph(
        f"Patient {b.patient_name_bill} was admitted on {b.admission_date} with "
        f"a presenting complaint of {b.diagnosis.lower()}. The patient was managed "
        "conservatively with appropriate medications and monitoring. "
        "Condition improved satisfactorily and the patient was discharged on "
        f"{b.discharge_date} in a stable condition with advice to follow up in "
        "2 weeks.",
        NORMAL,
    ))

    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph(
        "<b>Signature of Treating Doctor :</b>  [ Signed ]", NORMAL
    ))
    doc.build(story)


# ── bundle definitions ────────────────────────────────────────────────────────

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


def _sum(items: list[LineItem]) -> float:
    return round(sum(i.amount for i in items), 2)


BUNDLES_DEF: list[BundleData] = [

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
        claimed_amount=_sum(_items_normal()) + 46680.00,   # PLANTED: over-claim
        signature="Present",
        defect="AMOUNT_OVER",
        defect_description=(
            f"Claimed amount on form (Rs. {_sum(_items_normal()) + 46680:.2f}) "
            f"exceeds billed total (Rs. {_sum(_items_normal()):.2f}) by Rs. 46,680.00."
        ),
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
        printed_total=_sum(_items_wrong_total()) + 5000.00,   # PLANTED: wrong total
        claimed_amount=_sum(_items_wrong_total()) + 5000.00,
        signature="Present",
        defect="WRONG_TOTAL",
        defect_description=(
            f"Line items sum to Rs. {_sum(_items_wrong_total()):.2f} but bill "
            f"total printed as Rs. {_sum(_items_wrong_total()) + 5000:.2f} "
            "(Rs. 5,000 phantom charge)."
        ),
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
        signature="Missing",                                  # PLANTED
        defect="MISSING_SIG",
        defect_description="Claimant signature field is blank on the claim form.",
    ),

    # ── 005 NAME_MISMATCH ──────────────────────────────────────────────────────
    BundleData(
        bundle_id="bundle_005",
        patient_name_bill="Meera Subramanian Nair",          # name on bill & discharge
        patient_name_form="Meera S. Pillai",                 # PLANTED: different on form
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
    ),

    # ── 006 DATE_OUTSIDE ───────────────────────────────────────────────────────
    BundleData(
        bundle_id="bundle_006",
        patient_name_bill="Suresh Hasmukhlal Patel",
        patient_name_form="Suresh Hasmukhlal Patel",
        dob="29-Sep-1965",
        policy_number="STAR-HI-2024-77412",
        policy_start="01-Dec-2024",                # policy starts Dec 2024
        hospital_name="Kokilaben Dhirubhai Ambani Hospital, Mumbai",
        hospital_address="Andheri (W), Mumbai - 400 053",
        bill_number="KDA/2024/029984",
        bill_date="02-Nov-2024",                   # PLANTED: bill/admission before policy start
        admission_date="27-Oct-2024",              # PLANTED
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
        items=_items_round(),                      # PLANTED: all amounts end in .00
        true_total=_sum(_items_round()),
        printed_total=_sum(_items_round()),
        claimed_amount=_sum(_items_round()),
        signature="Present",
        defect="ROUND_BILLING",
        defect_description=(
            "All 5 line items have round-number amounts ending in .00 "
            "(100% round-number ratio, threshold > 60%)."
        ),
    ),

    # ── 008 DUPLICATE (exact copy of bundle_001) ──────────────────────────────
    BundleData(
        bundle_id="bundle_008",
        patient_name_bill="Rajesh Kumar Sharma",
        patient_name_form="Rajesh Kumar Sharma",
        dob="12-Mar-1978",
        policy_number="HDFC-HI-2024-00341",
        policy_start="01-Jan-2024",
        hospital_name="Apollo Hospitals, Bengaluru",
        hospital_address="154, Bannerghatta Road, Bengaluru - 560 076",
        bill_number="APB/2024/007821",            # PLANTED: same bill number as bundle_001
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
    ),
]


# ── build all bundles ─────────────────────────────────────────────────────────

def build_bundle(b: BundleData) -> Path:
    bundle_dir = OUT_DIR / b.bundle_id
    bundle_dir.mkdir(exist_ok=True)

    build_hospital_bill(bundle_dir / "hospital_bill.pdf", b)
    build_claim_form(bundle_dir / "claim_form.pdf", b)
    build_discharge_summary(bundle_dir / "discharge_summary.pdf", b)

    return bundle_dir


def write_ground_truth(bundles: list[BundleData]) -> Path:
    truth = {
        b.bundle_id: {
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
        for b in bundles
    }
    gt_path = OUT_DIR / "ground_truth.json"
    gt_path.write_text(json.dumps(truth, indent=2, ensure_ascii=False))
    return gt_path


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Generating 8 claim bundles ...\n")

    for b in BUNDLES_DEF:
        path = build_bundle(b)
        files = sorted(path.glob("*.pdf"))
        print(f"  [{b.defect:<12}]  {b.bundle_id}/")
        for f in files:
            print(f"                  {f.name}  ({f.stat().st_size:,} bytes)")
        print(f"    Patient  : {b.patient_name_bill}", end="")
        if b.patient_name_bill != b.patient_name_form:
            print(f"  /  form: {b.patient_name_form}", end="")
        print()
        print(f"    Hospital : {b.hospital_name}")
        print(f"    Billed   : Rs. {b.printed_total:,.2f}  |  Claimed: Rs. {b.claimed_amount:,.2f}")
        if b.defect_description:
            print(f"    Defect   : {b.defect_description}")
        print()

    gt = write_ground_truth(BUNDLES_DEF)
    print(f"ground_truth.json written to: {gt}\n")
    print("Done.")
