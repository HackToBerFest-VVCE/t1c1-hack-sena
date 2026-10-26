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
import logging
from datetime import datetime
from typing import TypedDict

from rapidfuzz import fuzz

log = logging.getLogger(__name__)

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
    """Parse amount strings to float via the shared normaliser."""
    from app.rules.normalise import normalise_amount
    return normalise_amount(raw)


def _parse_date(raw: str) -> datetime | None:
    """Parse Indian date strings via the shared normaliser."""
    from app.rules.normalise import normalise_date
    return normalise_date(raw)


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
        weight=40,
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
        ("diagnosis",       "Diagnosis"),
    ],
    DOCTYPE_HOSPITAL_BILL: [
        ("patient_name",    "Patient Name"),
        ("hospital_name",   "Hospital Name"),
        ("bill_date",       "Bill Date"),
        ("total_amount",    "Total Amount"),
        ("diagnosis",       "Diagnosis"),
    ],
}

_SIGNATURE_ABSENT_VALUES = {"missing", "unknown", ""}

# Values that mean "this field is functionally empty"
_EMPTY_VALUES = {"", "[not specified]", "not specified", "n/a", "unknown", "none"}


_V5_SIGNATURE_WEIGHT = 30   # missing signature carries higher weight than other gaps
_V5_MISSING_WEIGHT   = 15   # other missing required fields


def rule_V5(docs: list[DocResult]) -> list[Finding]:
    """
    V5: Required fields are blank or flagged as missing.

    Claim form:   patient_name, policy_number, claimed_amount, signature_present
    Hospital bill: patient_name, hospital_name, bill_date, total_amount

    The missing-signature finding is emitted separately at weight 30 because
    an unsigned claim form is a standalone compliance blocker.  All other
    missing-field combinations use weight 15.
    """
    findings: list[Finding] = []

    for dtype, required_fields in _REQUIRED.items():
        for doc in _doc_by_type(docs, dtype):
            sig_absent:   bool            = False
            sig_evidence: Evidence | None = None

            other_missing: list[str]     = []
            other_evidence: list[Evidence] = []

            for field_name, label in required_fields:
                val, page = _get(doc, field_name)
                val_lower = val.lower().strip()

                if field_name == "signature_present":
                    absent = val_lower in _SIGNATURE_ABSENT_VALUES
                    if absent:
                        sig_absent   = True
                        sig_evidence = _evidence(doc, page)
                else:
                    absent = val_lower in _EMPTY_VALUES
                    if absent:
                        other_missing.append(label)
                        other_evidence.append(_evidence(doc, page))

            # ── signature finding (weight 30) ────────────────────────────
            if sig_absent:
                findings.append(Finding(
                    rule_id="V5-MISSING-REQUIRED-FIELDS",
                    severity="low",
                    weight=_V5_SIGNATURE_WEIGHT,
                    what=(
                        f"Missing required field in {dtype.replace('_', ' ')}: "
                        f"Claimant Signature"
                    ),
                    evidence=[sig_evidence] if sig_evidence else [],
                    why_it_matters=(
                        "Claimant Signature is mandatory on the claim form. "
                        "An unsigned claim form cannot be processed under IRDAI "
                        "claim-settlement guidelines."
                    ),
                    suggested_action=(
                        "Return the claim form to the insured and request a "
                        "duly signed version before processing."
                    ),
                ))

            # ── other missing fields (weight 15) ─────────────────────────
            if other_missing:
                severity = (
                    "high"   if len(other_missing) >= 3 else
                    "medium" if len(other_missing) >= 2 else
                    "low"
                )
                label_list = ", ".join(other_missing)
                findings.append(Finding(
                    rule_id="V5-MISSING-REQUIRED-FIELDS",
                    severity=severity,
                    weight=_V5_MISSING_WEIGHT,
                    what=(
                        f"Missing required field(s) in "
                        f"{dtype.replace('_', ' ')}: {label_list}"
                    ),
                    evidence=other_evidence,
                    why_it_matters=(
                        f"The following mandatory field(s) are absent: "
                        f"{label_list}. "
                        "An incomplete document cannot be processed under IRDAI "
                        "claim-settlement guidelines."
                    ),
                    suggested_action=(
                        f"Return the document to the insured / hospital and "
                        f"request a corrected version with the missing field(s) "
                        f"filled: {label_list}."
                    ),
                ))

    return findings


# ─────────────────────────────────────────────────────────────────────────────
# Public runner
# ─────────────────────────────────────────────────────────────────────────────

ALL_RULES = [rule_V1, rule_V2, rule_V3, rule_V4, rule_V5, rule_V6]

# Fields whose absence causes a rule to be skipped (rule_id → required fields)
# An entry of [] means "always run" (the rule handles its own missing-data guard).
# For rules that need a specific document type, the check is done inline below.
_RULE_REQUIRED_FIELDS: dict[str, list[str]] = {
    "V1": [],   # checked inline: needs hospital bill with line_items + total_amount
    "V2": [],   # checked inline: needs bill total_amount + form claimed_amount
    "V3": ["patient_name"],
    "V4": [],   # V4 skips internally when dates are missing
    "V5": [],   # V5 always runs (checks for missing fields itself)
    "V6": ["policy_start_date"],
}


def _has_field_on_doctype(docs: list[DocResult], doctype: str, field_name: str) -> bool:
    """Return True if any doc of *doctype* has a non-empty value for *field_name*."""
    for doc in docs:
        if doc.get("document_type") != doctype:
            continue
        for f in doc["fields"]:
            if f["name"] == field_name and (f.get("value") or "").strip() not in ("", "[]"):
                return True
    return False


def _collect_all_field_values(docs: list[DocResult]) -> dict[str, str]:
    """Flatten all non-empty field values across all docs into one dict."""
    seen: dict[str, str] = {}
    for doc in docs:
        for f in doc["fields"]:
            name = f["name"]
            val  = (f.get("value") or "").strip()
            if val and name not in seen:
                seen[name] = val
    return seen


def run_all_rules(docs: list[DocResult]) -> list[Finding]:
    """
    Normalise field values, then run every validation rule.

    Logs per claim:
      - which rules ran
      - which were skipped (and why – missing required field)
      - which fired (produced findings)
    """
    from app.rules.normalise import patch_all_docs

    # ── 1. Normalise all extracted field values before rules run ─────────────
    docs = patch_all_docs(docs)

    all_fields = _collect_all_field_values(docs)

    results: list[Finding] = []

    for rule_fn in ALL_RULES:
        rule_key = rule_fn.__name__.replace("rule_", "").upper()  # e.g. "V1"
        required = _RULE_REQUIRED_FIELDS.get(rule_key, [])

        # Per-rule skip logic for rules that need a specific document type
        if rule_key == "V1":
            if not _has_field_on_doctype(docs, "hospital_bill", "line_items"):
                log.info("RULE %s SKIPPED – line_items missing on hospital bill", rule_fn.__name__)
                continue
            if not _has_field_on_doctype(docs, "hospital_bill", "total_amount"):
                log.info("RULE %s SKIPPED – total_amount missing on hospital bill", rule_fn.__name__)
                continue
        elif rule_key == "V2":
            if not _has_field_on_doctype(docs, "hospital_bill", "total_amount"):
                log.info("RULE %s SKIPPED – total_amount missing on hospital bill", rule_fn.__name__)
                continue
            if not _has_field_on_doctype(docs, "claim_form", "claimed_amount"):
                log.info("RULE %s SKIPPED – claimed_amount missing on claim form", rule_fn.__name__)
                continue
        else:
            # Generic field check against any document
            missing_fields = [f for f in required if not all_fields.get(f)]
            if missing_fields:
                log.info(
                    "RULE %s SKIPPED – missing required field(s): %s",
                    rule_fn.__name__,
                    ", ".join(missing_fields),
                )
                continue

        log.info("RULE %s running", rule_fn.__name__)
        new_findings = rule_fn(docs)

        if new_findings:
            log.info(
                "RULE %s FIRED – %d finding(s): %s",
                rule_fn.__name__,
                len(new_findings),
                "; ".join(f["rule_id"] for f in new_findings),
            )
        else:
            log.info("RULE %s passed (no findings)", rule_fn.__name__)

        results.extend(new_findings)

    return results
