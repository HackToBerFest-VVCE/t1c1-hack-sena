"""
make_samples.py
Generates 3 synthetic Indian hospital bill PDFs using fpdf2.
Bill #3 intentionally carries a wrong total for testing purposes.
"""

from fpdf import FPDF
from datetime import date
import os

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))


# ── helper ────────────────────────────────────────────────────────────────────

def rupee(amount: float) -> str:
    """Format a float as an Indian-style rupee string."""
    return f"Rs. {amount:,.2f}"


# ── bill data ─────────────────────────────────────────────────────────────────

BILLS = [
    {
        "filename": "bill_001_sharma.pdf",
        "hospital": "Apollo Hospitals, Bengaluru",
        "hospital_addr": "154, Bannerghatta Road, Bengaluru - 560 076",
        "patient": "Rajesh Kumar Sharma",
        "dob": "12-Mar-1978",
        "policy_no": "HDFC-HI-2024-00341",
        "bill_no": "APB/2024/007821",
        "bill_date": date(2024, 11, 14),
        "admission": date(2024, 11, 8),
        "discharge": date(2024, 11, 13),
        "diagnosis": "Acute Appendicitis - Laparoscopic Appendectomy",
        "line_items": [
            ("Room charges (5 days @ Rs. 4,500/day)", 22500.00),
            ("Operation Theatre charges", 35000.00),
            ("Surgeon & Anaesthesia fees", 28000.00),
            ("Medicines & Consumables", 8750.50),
            ("Diagnostic tests (Blood, USG, ECG)", 4200.00),
        ],
        "wrong_total": False,
    },
    {
        "filename": "bill_002_iyer.pdf",
        "hospital": "Manipal Hospital, Chennai",
        "hospital_addr": "No. 1, New Tank Street, Nungambakkam, Chennai - 600 034",
        "patient": "Priya Venkataraman Iyer",
        "dob": "05-Jul-1990",
        "policy_no": "ICICI-LOM-2023-88210",
        "bill_no": "MHC/2024/015443",
        "bill_date": date(2024, 10, 22),
        "admission": date(2024, 10, 18),
        "discharge": date(2024, 10, 21),
        "diagnosis": "Dengue Fever with Thrombocytopenia",
        "line_items": [
            ("Room charges (4 days @ Rs. 3,800/day)", 15200.00),
            ("ICU monitoring (1 day)", 9500.00),
            ("Platelet transfusion (2 units)", 12000.00),
            ("IV fluids & Medicines", 5480.75),
            ("CBC, NS1, LFT and other labs", 3150.00),
        ],
        "wrong_total": False,
    },
    {
        "filename": "bill_003_patel.pdf",
        "hospital": "Kokilaben Dhirubhai Ambani Hospital, Mumbai",
        "hospital_addr": "Rao Saheb Achutrao Patwardhan Marg, Four Bungalows, Andheri (W), Mumbai - 400 053",
        "patient": "Suresh Hasmukhlal Patel",
        "dob": "29-Sep-1965",
        "policy_no": "STAR-HI-2024-56789",
        "bill_no": "KDA/2024/029984",
        "bill_date": date(2024, 12, 3),
        "admission": date(2024, 11, 27),
        "discharge": date(2024, 12, 2),
        "diagnosis": "Type-2 Diabetes Mellitus - Foot Ulcer debridement",
        "line_items": [
            ("Room charges (6 days @ Rs. 5,200/day)", 31200.00),
            ("Minor OT - wound debridement", 18500.00),
            ("Antibiotics & wound dressings", 11320.00),
            ("Diabetologist consultation (3 visits)", 4500.00),
            ("HbA1c, RBS, CBC, culture & sensitivity", 3800.00),
        ],
        "wrong_total": True,   # ← intentionally wrong
    },
]


# ── PDF builder ───────────────────────────────────────────────────────────────

class BillPDF(FPDF):
    """Custom FPDF subclass with header/footer branding."""

    def __init__(self, hospital: str, hospital_addr: str):
        super().__init__()
        self._hospital = hospital
        self._hospital_addr = hospital_addr

    def header(self):
        self.set_font("Helvetica", "B", 15)
        self.set_text_color(25, 80, 160)
        self.cell(0, 10, self._hospital, align="C", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(80, 80, 80)
        self.cell(0, 6, self._hospital_addr, align="C", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(25, 80, 160)
        self.set_line_width(0.6)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)
        self.set_text_color(0, 0, 0)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(130, 130, 130)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def kv_row(pdf: FPDF, label: str, value: str):
    """Print a key-value pair in two columns."""
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(55, 7, label, new_x="RIGHT", new_y="TOP")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 7, value, new_x="LMARGIN", new_y="NEXT")


def section_title(pdf: FPDF, title: str):
    pdf.set_fill_color(230, 240, 255)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, f"  {title}", fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)


def build_bill(bill: dict) -> str:
    pdf = BillPDF(hospital=bill["hospital"], hospital_addr=bill["hospital_addr"])
    pdf.add_page()

    # ── Title
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(25, 80, 160)
    pdf.cell(0, 12, "HOSPITAL BILL / TAX INVOICE", align="C",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)

    # ── Patient & Policy details
    section_title(pdf, "Patient & Insurance Details")
    kv_row(pdf, "Patient Name :", bill["patient"])
    kv_row(pdf, "Date of Birth :", bill["dob"])
    kv_row(pdf, "Policy Number :", bill["policy_no"])
    kv_row(pdf, "Bill Number :", bill["bill_no"])
    kv_row(pdf, "Bill Date :", bill["bill_date"].strftime("%d-%b-%Y"))
    kv_row(pdf, "Admission Date :", bill["admission"].strftime("%d-%b-%Y"))
    kv_row(pdf, "Discharge Date :", bill["discharge"].strftime("%d-%b-%Y"))
    kv_row(pdf, "Diagnosis :", bill["diagnosis"])
    pdf.ln(4)

    # ── Line items table
    section_title(pdf, "Bill Particulars")

    # Table header
    pdf.set_fill_color(25, 80, 160)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(10, 8, "Sr.", border=1, fill=True, align="C")
    pdf.cell(140, 8, "Description", border=1, fill=True)
    pdf.cell(40, 8, "Amount", border=1, fill=True, align="R",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)

    # Table rows - alternating shading
    true_total = 0.0
    for idx, (desc, amt) in enumerate(bill["line_items"], start=1):
        fill_color = (245, 248, 255) if idx % 2 == 0 else (255, 255, 255)
        pdf.set_fill_color(*fill_color)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(10, 7, str(idx), border=1, fill=True, align="C")
        pdf.cell(140, 7, desc, border=1, fill=True)
        pdf.cell(40, 7, rupee(amt), border=1, fill=True, align="R",
                 new_x="LMARGIN", new_y="NEXT")
        true_total += amt

    # ── Total row
    if bill["wrong_total"]:
        # Introduce a deliberate error: add Rs. 5,000 phantom charge
        displayed_total = true_total + 5000.00
        note = "  *** Total is intentionally incorrect for QA testing ***"
    else:
        displayed_total = true_total
        note = ""

    pdf.set_fill_color(25, 80, 160)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(150, 9, "TOTAL AMOUNT", border=1, fill=True, align="R")
    pdf.cell(40, 9, rupee(displayed_total), border=1, fill=True, align="R",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)

    if note:
        pdf.ln(3)
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(200, 0, 0)
        pdf.cell(0, 6, note, new_x="LMARGIN", new_y="NEXT")
        # Also print true total so a reader can see the discrepancy
        pdf.set_font("Helvetica", "I", 9)
        pdf.cell(0, 6,
                 f"  (Sum of line items: {rupee(true_total)}  |  "
                 f"Displayed total: {rupee(displayed_total)}  |  "
                 f"Discrepancy: {rupee(displayed_total - true_total)})",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)

    pdf.ln(6)

    # ── Footer note
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(100, 100, 100)
    pdf.multi_cell(
        0, 5,
        "This is a computer-generated invoice. For billing queries, contact the "
        "hospital billing department. All amounts are in Indian Rupees (INR).",
    )

    out_path = os.path.join(OUTPUT_DIR, bill["filename"])
    pdf.output(out_path)
    return out_path


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Generating sample hospital bills …\n")
    for bill in BILLS:
        path = build_bill(bill)
        items_total = sum(a for _, a in bill["line_items"])
        flag = "  *** WRONG TOTAL" if bill["wrong_total"] else ""
        print(f"  [OK]  {bill['filename']}{flag}")
        print(f"     Patient : {bill['patient']}")
        print(f"     Policy  : {bill['policy_no']}")
        print(f"     True sum: Rs. {items_total:,.2f}")
        if bill["wrong_total"]:
            print(f"     Printed : Rs. {items_total + 5000:,.2f}  (Rs. 5,000 phantom charge)")
        print(f"     Saved   : {path}\n")
    print("Done. 3 PDFs written to sample_data/")
