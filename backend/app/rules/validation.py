"""
validation.py – Claim validation rules for ClaimLens AI.

Each rule is a standalone function with the signature:

    def rule_VN(docs: list[DocResult]) -> list[Finding]

where DocResult is the dict returned by extract_document() enriched with
the document filename:

    {
        "filename":      str,
        "document_type": str,          # DOCTYPE_* constant
        "fields":        list[FieldResult],
        "pages":         list[PageText],
    }

and Finding matches the Finding ORM model's columns:

    {
        "rule_id":          str,
        "severity":         "low" | "medium" | "high",
        "weight":           int,
        "what":             str,
        "evidence":         [{"document": str, "page": int}, ...],
        "why_it_matters":   str,
        "suggested_action": str,
    }

All rules are collected in ALL_RULES and can be run in one call via
    run_all_rules(docs) -> list[Finding]
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TypedDict

from rapidfuzz import fuzz

# ── shared type hints ─────────────────────────────────────────────────────────

class Evidence(TypedDict):
    document: str
    page:     int


class Finding(TypedDict):
    rule_id:          str
    severity:         str   # "low" | "medium" | "high"
    weight:           int
    what:             str
    evidence:         list[Evidence]
    why_it_matters:   str
    suggested_action: str


class DocResult(TypedDict):
    filename:      str
    document_type: str
    fields:        list[dict]   # FieldResult dicts
    pages:         list[dict]   # PageText dicts


# ── document-type constants (mirror extraction.py, no circular import) ────────

DOCTYPE_HOSPITAL_BILL     = "hospital_bill"
DOCTYPE_CLAIM_FORM        = "claim_form"
DOCTYPE_DISCHARGE_SUMMARY = "discharge_summary"
DOCTYPE_PRESCRIPTION      = "prescription"


# ── helpers ───────────────────────────────────────────────────────────────────

def _get(doc: DocResult, field_name: str) -> tuple[str, int]:
    """Return (value, page_number) for the first matching field, or ('', 1)."""
    for f in doc["fields"]:
        if f["name"] == field_name:
            return f["value"].strip(), f["page_number"]
    return "", 1


def _amount(raw: str) -> float | None:
    """Parse 'Rs. 1,23,456.78' or '1,23,456.78' → float, or None on failure."""
    import re
    m = re.search(r"([\d,]+(?:\.\d{1,2})?)", raw)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_date(raw: str) -> datetime | None:
    """Try a handful of common Indian date formats, return datetime or None."""
    import re
    raw = raw.strip()
    formats = [
        "%d-%b-%Y",   # 14-Nov-2024
        "%d/%b/%Y",
        "%d-%m-%Y",   # 14-11-2024
        "%d/%m/%Y",
        "%d-%b-%y",   # 14-Nov-24
        "%d/%m/%y",
    ]
    # normalise separator
    raw = re.sub(r"[./]", "-", raw)
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _doc_by_type(docs: list[DocResult], dtype: str) -> list[DocResult]:
    return [d for d in docs if d["document_type"] == dtype]


def _evidence(doc: DocResult, page: int = 1) -> Evidence:
    return Evidence(document=doc["filename"], page=page)


# ─────────────────────────────────────────────────────────────────────────────
# V1 – Line-item sum vs bill total
# ─────────────────────────────────────────────────────────────────────────────

def rule_V1(docs: list[DocResult]) -> list[Finding]:
    """
    V1: The sum of extracted line items does not match the printed total
    on a hospital bill.

    Weight 25 – financial integrity check.
    """
    findings: list[Finding] = []

    for doc in _doc_by_type(docs, DOCTYPE_HOSPITAL_BILL):
        items_raw, items_page = _get(doc, "line_items")
        total_raw, total_page = _get(doc, "total_amount")

        if not items_raw or not total_raw:
            continue

        try:
            items = json.loads(items_raw)
        except (json.JSONDecodeError, TypeError):
            continue

        if not items:
            continue

        computed = sum(it.get("amount", 0) for it in items)
        billed   = _amount(total_raw)

        if billed is None:
            continue

        diff = abs(computed - billed)
        if diff < 0.02:           # within 2 paise – rounding, ignore
            continue

        pct = (diff / billed * 100) if billed else 0

        # severity: high >5 %, medium >1 %, low otherwise
        severity = "low"
        if pct > 5:
            severity = "high"
        elif pct > 1:
            severity = "medium"

        findings.append(Finding(
            rule_id="V1-LINE-ITEM-TOTAL-MISMATCH",
            severity=severity,
            weight=25,
            what=(
                f"Line-item sum (Rs.\u00a0{computed:,.2f}) differs from "
                f"printed total (Rs.\u00a0{billed:,.2f}) "
                f"by Rs.\u00a0{diff:,.2f} ({pct:.1f}\u00a0%)"
            ),
            evidence=[
                _evidence(doc, items_page),
                _evidence(doc, total_page),
            ],
            why_it_matters=(
                "A discrepancy between the itemised charges and the printed "
                "total may indicate arithmetic fraud, phantom charges, or a "
                "data-entry error. The insurer cannot verify the correct "
                "reimbursement amount without reconciliation."
            ),
            suggested_action=(
                "Request the original signed bill and verify each line item. "
                "Reimburse only the lower verified amount until reconciled."
            ),
        ))

    return findings


# ─────────────────────────────────────────────────────────────────────────────
# V2 – Claimed amount vs billed total
# ─────────────────────────────────────────────────────────────────────────────

def rule_V2(docs: list[DocResult]) -> list[Finding]:
    """
    V2: The amount claimed on the claim form differs from the billed total
    on the hospital bill.

    Weight 25 – direct financial integrity check.
    """
    findings: list[Finding] = []

    bills  = _doc_by_type(docs, DOCTYPE_HOSPITAL_BILL)
    claims = _doc_by_type(docs, DOCTYPE_CLAIM_FORM)

    if not bills or not claims:
        return findings

    # Use the first bill and first claim form found
    bill        = bills[0]
    claim_form  = claims[0]

    billed_raw,  billed_page  = _get(bill,       "total_amount")
    claimed_raw, claimed_page = _get(claim_form, "claimed_amount")

    if not billed_raw or not claimed_raw:
        return findings

    billed  = _amount(billed_raw)
    claimed = _amount(claimed_raw)

    if billed is None or claimed is None:
        return findings

    diff = claimed - billed       # positive = over-claimed
    if abs(diff) < 0.02:
        return findings

    pct = (abs(diff) / billed * 100) if billed else 0

    severity = "low"
    if pct > 10 or diff > 0:     # any over-claim is at least medium
        severity = "high" if pct > 15 else "medium"

    direction = "exceeds" if diff > 0 else "is less than"
    findings.append(Finding(
        rule_id="V2-CLAIMED-VS-BILLED-MISMATCH",
        severity=severity,
        weight=25,
        what=(
            f"Claimed amount (Rs.\u00a0{claimed:,.2f}) {direction} "
            f"billed total (Rs.\u00a0{billed:,.2f}) "
            f"by Rs.\u00a0{abs(diff):,.2f} ({pct:.1f}\u00a0%)"
        ),
        evidence=[
            _evidence(claim_form, claimed_page),
            _evidence(bill,       billed_page),
        ],
        why_it_matters=(
            "The insured may only claim up to the amount actually billed. "
            "An over-claim could indicate fraud or inflate the loss ratio. "
            "An under-claim is unusual and may warrant verification."
        ),
        suggested_action=(
            "Obtain the original signed bill and cross-check. "
            "Cap reimbursement at the verified billed total."
        ),
    ))

    return findings


# ─────────────────────────────────────────────────────────────────────────────
# V3 – Patient name mismatch across documents
# ─────────────────────────────────────────────────────────────────────────────

# Names with similarity below this threshold are flagged.
_NAME_SIMILARITY_THRESHOLD = 85   # rapidfuzz token_sort_ratio score 0–100


def rule_V3(docs: list[DocResult]) -> list[Finding]:
    """
    V3: Patient name appears differently across two or more documents.
    Uses rapidfuzz token_sort_ratio for fuzzy comparison (handles
    middle-name omissions, initials, etc.).

    Weight 20.
    """
    findings: list[Finding] = []

    # Collect (name, doc, page) for every document that has a patient_name
    name_sources: list[tuple[str, DocResult, int]] = []
    for doc in docs:
        val, page = _get(doc, "patient_name")
        if val:
            name_sources.append((val, doc, page))

    if len(name_sources) < 2:
        return findings

    # Compare every pair; report the worst mismatch per pair of documents
    reported: set[frozenset[str]] = set()
    for i in range(len(name_sources)):
        for j in range(i + 1, len(name_sources)):
            name_a, doc_a, page_a = name_sources[i]
            name_b, doc_b, page_b = name_sources[j]

            pair_key = frozenset({doc_a["filename"], doc_b["filename"]})
            if pair_key in reported:
                continue

            score = fuzz.token_sort_ratio(name_a.lower(), name_b.lower())
            if score >= _NAME_SIMILARITY_THRESHOLD:
                continue

            reported.add(pair_key)
            severity = "high" if score < 60 else "medium"
            findings.append(Finding(
                rule_id="V3-PATIENT-NAME-MISMATCH",
                severity=severity,
                weight=20,
                what=(
                    f"Patient name differs: \u201c{name_a}\u201d "
                    f"({doc_a['filename']}) vs "
                    f"\u201c{name_b}\u201d ({doc_b['filename']}) "
                    f"[similarity {score}\u00a0/\u00a0100]"
                ),
                evidence=[
                    _evidence(doc_a, page_a),
                    _evidence(doc_b, page_b),
                ],
                why_it_matters=(
                    "Inconsistent patient names across documents may indicate "
                    "that documents belong to different individuals, or a data "
                    "entry error. Either case requires identity verification "
                    "before the claim can be processed."
                ),
                suggested_action=(
                    "Request a government-issued photo ID matching the insured "
                    "name on the policy. Verify the name on all documents "
                    "before approval."
                ),
            ))

    return findings


# ─────────────────────────────────────────────────────────────────────────────
# V4 – Date logic problems
# ─────────────────────────────────────────────────────────────────────────────

def rule_V4(docs: list[DocResult]) -> list[Finding]:
    """
    V4a: Admission date is after discharge date.
    V4b: Bill date falls outside the admission–discharge window.

    Weight 15 per violation.
    """
    findings: list[Finding] = []

    # Gather dates from all docs; prefer hospital bill, fall back to any doc
    def _first_date(field: str) -> tuple[datetime | None, DocResult | None, int]:
        for dtype in (DOCTYPE_HOSPITAL_BILL, DOCTYPE_DISCHARGE_SUMMARY,
                      DOCTYPE_CLAIM_FORM, DOCTYPE_PRESCRIPTION, ""):
            candidates = _doc_by_type(docs, dtype) if dtype else docs
            for doc in candidates:
                raw, page = _get(doc, field)
                if raw:
                    dt = _parse_date(raw)
                    if dt:
                        return dt, doc, page
        return None, None, 1

    admission_dt, adm_doc, adm_page = _first_date("admission_date")
    discharge_dt, dis_doc, dis_page = _first_date("discharge_date")
    bill_dt,      bill_doc, bill_page = _first_date("bill_date")

    # V4a – admission after discharge
    if admission_dt and discharge_dt and admission_dt > discharge_dt:
        evid: list[Evidence] = []
        if adm_doc:
            evid.append(_evidence(adm_doc, adm_page))
        if dis_doc:
            evid.append(_evidence(dis_doc, dis_page))
        findings.append(Finding(
            rule_id="V4A-ADMISSION-AFTER-DISCHARGE",
            severity="high",
            weight=15,
            what=(
                f"Admission date ({admission_dt.strftime('%d-%b-%Y')}) is "
                f"after discharge date ({discharge_dt.strftime('%d-%b-%Y')})"
            ),
            evidence=evid,
            why_it_matters=(
                "An admission date that follows the discharge date is "
                "logically impossible and is a strong indicator of "
                "document tampering or data entry fraud."
            ),
            suggested_action=(
                "Reject the claim pending re-submission of original documents "
                "with verified admission and discharge dates."
            ),
        ))

    # V4b – bill date outside treatment window
    if bill_dt and admission_dt and discharge_dt:
        if not (admission_dt <= bill_dt <= discharge_dt):
            evid = []
            if bill_doc:
                evid.append(_evidence(bill_doc, bill_page))
            if adm_doc:
                evid.append(_evidence(adm_doc, adm_page))
            findings.append(Finding(
                rule_id="V4B-BILL-DATE-OUTSIDE-TREATMENT",
                severity="medium",
                weight=15,
                what=(
                    f"Bill date ({bill_dt.strftime('%d-%b-%Y')}) is outside "
                    f"treatment window "
                    f"{admission_dt.strftime('%d-%b-%Y')} – "
                    f"{discharge_dt.strftime('%d-%b-%Y')}"
                ),
                evidence=evid,
                why_it_matters=(
                    "A bill dated before admission or after discharge may "
                    "relate to a different episode of care and could be "
                    "ineligible for reimbursement under this claim."
                ),
                suggested_action=(
                    "Verify that the bill covers the inpatient episode. "
                    "Request a clarification letter from the hospital."
                ),
            ))

    return findings


# ─────────────────────────────────────────────────────────────────────────────
# V6 – Admission or discharge date outside the active policy period
# ─────────────────────────────────────────────────────────────────────────────

def rule_V6(docs: list[DocResult]) -> list[Finding]:
    """
    V6: Admission date or discharge date is before the policy start date,
    meaning the treatment occurred before the policy was active.

    Requires `policy_start_date` from the claim form and at least one of
    `admission_date` / `discharge_date` from any document.

    Weight 25 – high-severity coverage eligibility issue.
    """
    findings: list[Finding] = []

    # ── find policy_start_date (claim form preferred) ────────────────────
    policy_start: datetime | None = None
    policy_doc:   DocResult | None = None
    policy_page:  int = 1

    for dtype in (DOCTYPE_CLAIM_FORM, DOCTYPE_DISCHARGE_SUMMARY, ""):
        candidates = _doc_by_type(docs, dtype) if dtype else docs
        for doc in candidates:
            raw, page = _get(doc, "policy_start_date")
            if raw:
                dt = _parse_date(raw)
                if dt:
                    policy_start = dt
                    policy_doc   = doc
                    policy_page  = page
                    break
        if policy_start:
            break

    if not policy_start:
        return findings

    # ── helper: check a date field against policy start ──────────────────
    def _check_date_field(field_name: str, label: str) -> None:
        for dtype in (DOCTYPE_HOSPITAL_BILL, DOCTYPE_DISCHARGE_SUMMARY,
                      DOCTYPE_CLAIM_FORM, DOCTYPE_PRESCRIPTION, ""):
            candidates = _doc_by_type(docs, dtype) if dtype else docs
            for doc in candidates:
                raw, page = _get(doc, field_name)
                if not raw:
                    continue
                dt = _parse_date(raw)
                if dt is None:
                    continue
                if dt < policy_start:  # type: ignore[operator]
                    evid: list[Evidence] = [_evidence(doc, page)]
                    if policy_doc:
                        evid.append(_evidence(policy_doc, policy_page))
                    findings.append(Finding(
                        rule_id="V6-DATE-OUTSIDE-POLICY-PERIOD",
                        severity="high",
                        weight=25,
                        what=(
                            f"{label} ({dt.strftime('%d-%b-%Y')}) is before "
                            f"policy start date "
                            f"({policy_start.strftime('%d-%b-%Y')})"
                        ),
                        evidence=evid,
                        why_it_matters=(
                            "Treatment that began before the policy inception "
                            "date is not covered. Reimbursement of this claim "
                            "would be outside the policy terms and conditions."
                        ),
                        suggested_action=(
                            "Verify the policy start date on the policy "
                            "document. If treatment pre-dates the policy, "
                            "decline the claim citing the policy inception "
                            "clause."
                        ),
                    ))
                return  # report at most once per field across all docs

    _check_date_field("admission_date",  "Admission date")
    _check_date_field("discharge_date",  "Discharge date")

    return findings




# Per document-type: list of (field_name, human_label) that must be present
_REQUIRED: dict[str, list[tuple[str, str]]] = {
    DOCTYPE_CLAIM_FORM: [
        ("patient_name",    "Patient Name"),
        ("policy_number",   "Policy Number"),
        ("claimed_amount",  "Claimed Amount"),
        ("signature_present", "Claimant Signature"),
    ],
    DOCTYPE_HOSPITAL_BILL: [
        ("patient_name",    "Patient Name"),
        ("hospital_name",   "Hospital Name"),
        ("bill_date",       "Bill Date"),
        ("total_amount",    "Total Amount"),
    ],
}

_SIGNATURE_ABSENT_VALUES = {"missing", "unknown", ""}


def rule_V5(docs: list[DocResult]) -> list[Finding]:
    """
    V5: Required fields are blank or flagged as missing.

    Claim form:   patient_name, policy_number, claimed_amount, signature_present
    Hospital bill: patient_name, hospital_name, bill_date, total_amount

    Weight 15.
    """
    findings: list[Finding] = []

    for dtype, required_fields in _REQUIRED.items():
        for doc in _doc_by_type(docs, dtype):
            missing: list[str] = []
            evidence: list[Evidence] = []

            for field_name, label in required_fields:
                val, page = _get(doc, field_name)

                absent = False
                if field_name == "signature_present":
                    absent = val.lower() in _SIGNATURE_ABSENT_VALUES
                else:
                    absent = not val

                if absent:
                    missing.append(label)
                    evidence.append(_evidence(doc, page))

            if not missing:
                continue

            severity = "high" if len(missing) >= 3 else "medium" if len(missing) >= 2 else "low"
            label_list = ", ".join(missing)
            findings.append(Finding(
                rule_id="V5-MISSING-REQUIRED-FIELDS",
                severity=severity,
                weight=15,
                what=f"Missing required field(s) in {dtype.replace('_', ' ')}: {label_list}",
                evidence=evidence,
                why_it_matters=(
                    f"The following mandatory field(s) are absent: {label_list}. "
                    "An incomplete document cannot be processed under IRDAI "
                    "claim-settlement guidelines."
                ),
                suggested_action=(
                    f"Return the document to the insured / hospital and request "
                    f"a corrected version with the missing field(s) filled: "
                    f"{label_list}."
                ),
            ))

    return findings


# ─────────────────────────────────────────────────────────────────────────────
# Public runner
# ─────────────────────────────────────────────────────────────────────────────

ALL_RULES = [rule_V1, rule_V2, rule_V3, rule_V4, rule_V5, rule_V6]


def run_all_rules(docs: list[DocResult]) -> list[Finding]:
    """Run every validation rule and return the combined list of findings."""
    results: list[Finding] = []
    for rule in ALL_RULES:
        results.extend(rule(docs))
    return results
