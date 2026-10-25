"""
fraud.py – Risk-indicator rules for ClaimLens AI.

Same Finding format as validation.py:
    {
        "rule_id":          str,
        "severity":         "low" | "medium" | "high",
        "weight":           int,
        "what":             str,
        "evidence":         [{"document": str, "page": int}, ...],
        "why_it_matters":   str,
        "suggested_action": str,
    }

IMPORTANT: findings describe risk indicators only. None of these rules
confirms fraud. Wording deliberately avoids the phrase "fraud confirmed".

Public helpers
--------------
    run_fraud_rules(docs, prior_claims, file_paths, policy_start_date)
        -> list[Finding]

    compute_risk(findings) -> {"risk_score": int, "risk_level": str, "risk_reasons": list[str]}
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

import pymupdf
from rapidfuzz import fuzz

# ── re-use shared types from validation ──────────────────────────────────────

from app.rules.validation import Evidence, Finding, DocResult


# ── additional input types ────────────────────────────────────────────────────

class PriorClaim(TypedDict):
    """Minimal summary of a previously processed claim used for F1 / F2."""
    claim_id:     str
    patient_name: str
    hospital_name: str
    admission_date: str       # raw string as extracted
    total_amount:  str        # raw string e.g. "Rs. 98,450.50"
    file_hash:     str | None


class RiskResult(TypedDict):
    risk_score:   int         # 0 – 100
    risk_level:   str         # "Low" | "Medium" | "High"
    risk_reasons: list[str]


# ── constants ─────────────────────────────────────────────────────────────────

RISK_LOW_THRESHOLD    = 30
RISK_MEDIUM_THRESHOLD = 60

# F1 similarity thresholds (rapidfuzz token_sort_ratio, 0-100)
F1_PATIENT_THRESHOLD  = 85
F1_HOSPITAL_THRESHOLD = 80
# Claim is considered a near-duplicate when patient AND hospital match
# AND at least one of date/amount also matches.
F1_AMOUNT_TOLERANCE_PCT = 5.0   # amounts within 5 % are considered matching

# F3 round-number threshold
F3_ROUND_RATIO_THRESHOLD = 0.60  # > 60 % of items ending in 00


# ── internal helpers ──────────────────────────────────────────────────────────

def _get(doc: DocResult, field_name: str) -> tuple[str, int]:
    for f in doc["fields"]:
        if f["name"] == field_name:
            return f["value"].strip(), f["page_number"]
    return "", 1


def _amount(raw: str) -> float | None:
    m = re.search(r"([\d,]+(?:\.\d{1,2})?)", raw)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_date(raw: str) -> datetime | None:
    raw = re.sub(r"[./]", "-", raw.strip())
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%d-%b-%y", "%d-%m-%y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _doc_by_type(docs: list[DocResult], dtype: str) -> list[DocResult]:
    return [d for d in docs if d["document_type"] == dtype]


def _evidence(doc: DocResult, page: int = 1) -> Evidence:
    return Evidence(document=doc["filename"], page=page)


def _first_field(docs: list[DocResult], field_name: str) -> tuple[str, DocResult | None, int]:
    """Search all docs for a field; return (value, doc, page) or ('', None, 1)."""
    for doc in docs:
        val, page = _get(doc, field_name)
        if val:
            return val, doc, page
    return "", None, 1


# ─────────────────────────────────────────────────────────────────────────────
# F1 – Near-duplicate of a prior claim
# ─────────────────────────────────────────────────────────────────────────────

def rule_F1(
    docs: list[DocResult],
    prior_claims: list[PriorClaim],
) -> list[Finding]:
    """
    F1: Patient name, hospital name, admission date and total amount are all
    similar to a previously processed claim.

    Matching logic:
      - patient  : token_sort_ratio >= F1_PATIENT_THRESHOLD
      - hospital : token_sort_ratio >= F1_HOSPITAL_THRESHOLD
      - Plus at least one of:
            * admission dates within 3 days of each other, OR
            * total amounts within F1_AMOUNT_TOLERANCE_PCT %

    Weight 40.
    """
    findings: list[Finding] = []
    if not prior_claims:
        return findings

    patient_val,  patient_doc,  patient_page  = _first_field(docs, "patient_name")
    hospital_val, hospital_doc, hospital_page = _first_field(docs, "hospital_name")
    adm_val,      adm_doc,      adm_page      = _first_field(docs, "admission_date")
    total_val,    total_doc,    total_page    = _first_field(docs, "total_amount")

    if not patient_val or not hospital_val:
        return findings

    current_adm    = _parse_date(adm_val)   if adm_val   else None
    current_amount = _amount(total_val)      if total_val else None

    for prior in prior_claims:
        p_score = fuzz.token_sort_ratio(
            patient_val.lower(), prior["patient_name"].lower()
        )
        h_score = fuzz.token_sort_ratio(
            hospital_val.lower(), prior["hospital_name"].lower()
        )

        if p_score < F1_PATIENT_THRESHOLD or h_score < F1_HOSPITAL_THRESHOLD:
            continue

        # Check supporting signals
        date_match   = False
        amount_match = False

        prior_adm = _parse_date(prior["admission_date"]) if prior.get("admission_date") else None
        if current_adm and prior_adm:
            date_match = abs((current_adm - prior_adm).days) <= 3

        prior_amount = _amount(prior.get("total_amount", ""))
        if current_amount and prior_amount and prior_amount != 0:
            pct_diff = abs(current_amount - prior_amount) / prior_amount * 100
            amount_match = pct_diff <= F1_AMOUNT_TOLERANCE_PCT

        if not (date_match or amount_match):
            continue

        # Build evidence list from whichever fields contributed
        evidence: list[Evidence] = []
        if patient_doc:
            evidence.append(_evidence(patient_doc, patient_page))
        if hospital_doc and hospital_doc is not patient_doc:
            evidence.append(_evidence(hospital_doc, hospital_page))

        match_details = []
        if date_match:
            match_details.append(f"admission date ({adm_val})")
        if amount_match:
            match_details.append(f"total amount ({total_val})")

        findings.append(Finding(
            rule_id="F1-NEAR-DUPLICATE-CLAIM",
            severity="high",
            weight=40,
            what=(
                f"Risk indicator: current claim closely resembles prior claim "
                f"{prior['claim_id']} — patient ({p_score}/100), "
                f"hospital ({h_score}/100), matching {' and '.join(match_details)}"
            ),
            evidence=evidence,
            why_it_matters=(
                f"The patient name, hospital name, and {' and '.join(match_details)} "
                f"strongly resemble those of claim {prior['claim_id']}, which was "
                f"previously processed. Submitting the same hospitalisation episode "
                f"twice for reimbursement is a known risk pattern."
            ),
            suggested_action=(
                f"Cross-reference with claim {prior['claim_id']}. "
                f"Verify original discharge summary dates and confirm this is a "
                f"separate episode of care before any payment."
            ),
        ))

    return findings


# ─────────────────────────────────────────────────────────────────────────────
# F2 – Identical file hash to a prior claim
# ─────────────────────────────────────────────────────────────────────────────

def rule_F2(
    docs:          list[DocResult],
    prior_claims:  list[PriorClaim],
    file_hashes:   list[str],          # SHA-256 hex digests of current uploads
) -> list[Finding]:
    """
    F2: The SHA-256 hash of an uploaded file matches that of a document
    attached to a previously processed claim.

    Weight 40.
    """
    findings: list[Finding] = []

    prior_hashes = {
        p["file_hash"]: p
        for p in prior_claims
        if p.get("file_hash")
    }
    if not prior_hashes:
        return findings

    for i, fhash in enumerate(file_hashes):
        if not fhash:
            continue
        if fhash not in prior_hashes:
            continue

        prior = prior_hashes[fhash]
        # Map hash index back to the corresponding doc if possible
        doc      = docs[i] if i < len(docs) else docs[0] if docs else None
        evidence = [_evidence(doc, 1)] if doc else []

        findings.append(Finding(
            rule_id="F2-DUPLICATE-FILE-HASH",
            severity="high",
            weight=40,
            what=(
                f"Risk indicator: uploaded file hash matches a document "
                f"already submitted under claim {prior['claim_id']}"
            ),
            evidence=evidence,
            why_it_matters=(
                f"The exact same file (SHA-256: {fhash[:12]}…) was previously "
                f"received as part of claim {prior['claim_id']}. Identical files "
                f"across separate claims indicate the same document is being "
                f"reused, which is a strong risk indicator."
            ),
            suggested_action=(
                f"Immediately suspend processing. Compare this claim with "
                f"{prior['claim_id']} and request the insured to produce "
                f"original documents for manual verification."
            ),
        ))

    return findings


# ─────────────────────────────────────────────────────────────────────────────
# F3 – Round-number billing
# ─────────────────────────────────────────────────────────────────────────────

def rule_F3(docs: list[DocResult]) -> list[Finding]:
    """
    F3: More than 60 % of the bill's line-item amounts end in .00
    (i.e. are exact round numbers), which is statistically unusual for
    legitimate itemised medical invoices.

    Weight 10.
    """
    findings: list[Finding] = []

    for doc in _doc_by_type(docs, "hospital_bill"):
        items_raw, items_page = _get(doc, "line_items")
        if not items_raw:
            continue

        try:
            items = json.loads(items_raw)
        except (json.JSONDecodeError, TypeError):
            continue

        if len(items) < 3:   # too few items to be statistically meaningful
            continue

        round_count = sum(
            1 for it in items
            if isinstance(it.get("amount"), (int, float))
            and abs(it["amount"] - round(it["amount"])) < 0.01
        )
        ratio = round_count / len(items)

        if ratio <= F3_ROUND_RATIO_THRESHOLD:
            continue

        pct_str = f"{ratio * 100:.0f}%"
        findings.append(Finding(
            rule_id="F3-ROUND-NUMBER-BILLING",
            severity="low",
            weight=10,
            what=(
                f"Risk indicator: {round_count} of {len(items)} line items "
                f"({pct_str}) are round-number amounts ending in .00"
            ),
            evidence=[_evidence(doc, items_page)],
            why_it_matters=(
                "Legitimate itemised hospital invoices typically include "
                "fractional amounts for medications, consumables and lab tests. "
                "A high proportion of round numbers may indicate fabricated or "
                "estimated charges rather than actual costs."
            ),
            suggested_action=(
                "Request supporting purchase receipts or pharmacy invoices for "
                "each line item to verify the actual amounts charged."
            ),
        ))

    return findings


# ─────────────────────────────────────────────────────────────────────────────
# F4 – Treatment within 30 days of policy start date
# ─────────────────────────────────────────────────────────────────────────────

def rule_F4(
    docs:               list[DocResult],
    policy_start_date:  str | None,       # ISO-8601 or dd-Mon-yyyy
) -> list[Finding]:
    """
    F4: Admission date falls within 30 days of the policy start date,
    suggesting the insured may have taken out cover specifically to claim
    for a pre-existing or pre-planned condition.

    Weight 15.
    """
    findings: list[Finding] = []

    if not policy_start_date:
        return findings

    start_dt = _parse_date(policy_start_date)
    if not start_dt:
        # try ISO-8601
        try:
            start_dt = datetime.fromisoformat(policy_start_date.replace("Z", "+00:00"))
            start_dt = start_dt.replace(tzinfo=None)
        except ValueError:
            return findings

    adm_val, adm_doc, adm_page = _first_field(docs, "admission_date")
    if not adm_val:
        return findings

    adm_dt = _parse_date(adm_val)
    if not adm_dt:
        return findings

    days_since_start = (adm_dt - start_dt).days

    if not (0 <= days_since_start <= 30):
        return findings

    evidence = [_evidence(adm_doc, adm_page)] if adm_doc else []
    findings.append(Finding(
        rule_id="F4-EARLY-TREATMENT-RISK",
        severity="medium",
        weight=15,
        what=(
            f"Risk indicator: admission date ({adm_val}) is only "
            f"{days_since_start} day(s) after policy start "
            f"({policy_start_date})"
        ),
        evidence=evidence,
        why_it_matters=(
            "Claims arising within 30 days of policy inception are a known "
            "risk pattern associated with pre-existing conditions being "
            "claimed under a newly issued policy. Most policies include a "
            "waiting-period clause for this scenario."
        ),
        suggested_action=(
            "Verify the policy's waiting-period clause. Check medical "
            "history for evidence of a pre-existing condition. Consult the "
            "underwriting team before approving."
        ),
    ))

    return findings


# ─────────────────────────────────────────────────────────────────────────────
# F5 – PDF modification date after creation date
# ─────────────────────────────────────────────────────────────────────────────

def _parse_pdf_date(raw: str | None) -> datetime | None:
    """Parse a PDF metadata date string (D:YYYYMMDDHHmmSSOHH'mm')."""
    if not raw:
        return None
    # strip leading "D:" and timezone suffix
    raw = re.sub(r"^D:", "", raw)
    raw = re.sub(r"[Z+\-]\d{2}'\d{2}'.*$", "", raw)
    raw = raw.strip()
    for fmt in ("%Y%m%d%H%M%S", "%Y%m%d%H%M", "%Y%m%d"):
        try:
            return datetime.strptime(raw[:len(fmt.replace("%", "XX"))], fmt)
        except ValueError:
            continue
    return None


def rule_F5(
    docs:        list[DocResult],
    file_paths:  list[str | Path],    # absolute paths to the uploaded files
) -> list[Finding]:
    """
    F5: A PDF's modification date (XMP/DocInfo modDate) is later than its
    creation date, suggesting the document was altered after it was generated.

    Weight 10.
    """
    findings: list[Finding] = []

    for i, path in enumerate(file_paths):
        path = Path(path)
        if path.suffix.lower() != ".pdf":
            continue
        if not path.exists():
            continue

        try:
            pdf_doc  = pymupdf.open(str(path))
            metadata = pdf_doc.metadata or {}
            pdf_doc.close()
        except Exception:
            continue

        creation_dt     = _parse_pdf_date(metadata.get("creationDate"))
        modification_dt = _parse_pdf_date(metadata.get("modDate"))

        if not creation_dt or not modification_dt:
            continue

        # Allow up to 10-second tolerance for tools that write both dates
        delta_seconds = (modification_dt - creation_dt).total_seconds()
        if delta_seconds <= 10:
            continue

        # Map file index to doc
        doc      = docs[i] if i < len(docs) else docs[0] if docs else None
        evidence = [_evidence(doc, 1)] if doc else []
        filename = path.name

        findings.append(Finding(
            rule_id="F5-PDF-MODIFIED-AFTER-CREATION",
            severity="medium",
            weight=10,
            what=(
                f"Risk indicator: '{filename}' was modified "
                f"{int(delta_seconds // 60)} minute(s) after it was created "
                f"(created: {creation_dt.strftime('%d-%b-%Y %H:%M')}, "
                f"modified: {modification_dt.strftime('%d-%b-%Y %H:%M')})"
            ),
            evidence=evidence,
            why_it_matters=(
                "A PDF that was edited after its initial creation date may "
                "have had its content altered. This is a risk indicator for "
                "document tampering such as changed amounts, dates or "
                "patient details."
            ),
            suggested_action=(
                "Request the original, unmodified document directly from the "
                "hospital's billing system. Compare metadata and content with "
                "the submitted file."
            ),
        ))

    return findings


# ─────────────────────────────────────────────────────────────────────────────
# Risk scorer
# ─────────────────────────────────────────────────────────────────────────────

def compute_risk(findings: list[Finding]) -> RiskResult:
    """
    Combine all finding weights into a risk_score (0–100), map to a
    risk_level, and produce human-readable risk_reasons.

    Scoring
    -------
    risk_score = min(sum(finding["weight"] for finding in findings), 100)

    Thresholds
    ----------
    Low    : score < 30
    Medium : 30 <= score <= 60
    High   : score > 60
    """
    score = min(sum(f["weight"] for f in findings), 100)

    if score > RISK_MEDIUM_THRESHOLD:
        level = "High"
    elif score >= RISK_LOW_THRESHOLD:
        level = "Medium"
    else:
        level = "Low"

    reasons = [f["what"] for f in findings]

    return RiskResult(
        risk_score=score,
        risk_level=level,
        risk_reasons=reasons,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public runner
# ─────────────────────────────────────────────────────────────────────────────

ALL_FRAUD_RULES = [rule_F1, rule_F2, rule_F3, rule_F4, rule_F5]


def run_fraud_rules(
    docs:               list[DocResult],
    prior_claims:       list[PriorClaim]  | None = None,
    file_hashes:        list[str]         | None = None,
    file_paths:         list[str | Path]  | None = None,
    policy_start_date:  str | None               = None,
) -> list[Finding]:
    """
    Run all fraud / risk-indicator rules and return the combined findings.

    Parameters
    ----------
    docs               : extracted document results (with filename)
    prior_claims       : list of PriorClaim dicts from the database
    file_hashes        : SHA-256 hex digests of the current uploads (same order as docs)
    file_paths         : absolute paths to the uploaded files (same order as docs)
    policy_start_date  : policy inception date as a parseable date string
    """
    results: list[Finding] = []
    results.extend(rule_F1(docs, prior_claims or []))
    results.extend(rule_F2(docs, prior_claims or [], file_hashes or []))
    results.extend(rule_F3(docs))
    results.extend(rule_F4(docs, policy_start_date))
    results.extend(rule_F5(docs, file_paths or []))
    return results
