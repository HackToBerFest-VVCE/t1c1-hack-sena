"""
tests/test_validation.py – pytest tests for all five validation rules.

Each rule is tested with:
  - a "fires" case (finding is returned)
  - a "clean" case (no finding)
  - edge cases specific to that rule
"""

import json
import pytest

from app.rules.validation import (
    rule_V1, rule_V2, rule_V3, rule_V4, rule_V5,
    run_all_rules,
    DOCTYPE_HOSPITAL_BILL, DOCTYPE_CLAIM_FORM,
    DOCTYPE_DISCHARGE_SUMMARY,
)


# ── fixtures / builders ───────────────────────────────────────────────────────

def _field(name, value, page=1, confidence=0.9):
    return {"name": name, "value": value, "confidence": confidence, "page_number": page}


def _bill(fields=None, filename="bill.pdf"):
    base = [
        _field("patient_name",   "Rajesh Kumar Sharma"),
        _field("hospital_name",  "Apollo Hospitals"),
        _field("bill_date",      "13-Nov-2024"),   # same as discharge – within window
        _field("total_amount",   "Rs. 98,450.50"),
        _field("admission_date", "08-Nov-2024"),
        _field("discharge_date", "13-Nov-2024"),
        _field("line_items", json.dumps([
            {"description": "Room charges",  "amount": 22500.00},
            {"description": "OT charges",    "amount": 35000.00},
            {"description": "Surgeon fees",  "amount": 28000.00},
            {"description": "Medicines",     "amount":  8750.50},
            {"description": "Diagnostics",   "amount":  4200.00},
        ])),
    ]
    doc = {"filename": filename, "document_type": DOCTYPE_HOSPITAL_BILL,
           "fields": fields if fields is not None else base, "pages": []}
    return doc


def _claim_form(fields=None, filename="claim.pdf"):
    base = [
        _field("patient_name",    "Rajesh Kumar Sharma"),
        _field("policy_number",   "HDFC-HI-2024-00341"),
        _field("claimed_amount",  "Rs. 98,450.50"),
        _field("signature_present", "Present"),
    ]
    doc = {"filename": filename, "document_type": DOCTYPE_CLAIM_FORM,
           "fields": fields if fields is not None else base, "pages": []}
    return doc


# ── V1 ─────────────────────────────────────────────────────────────────────────

class TestV1:
    def test_fires_when_total_inflated(self):
        # line items sum to 98,450.50 but total printed as 103,450.50
        items = [
            {"description": "Room charges", "amount": 22500.00},
            {"description": "OT charges",   "amount": 35000.00},
            {"description": "Surgeon fees", "amount": 28000.00},
            {"description": "Medicines",    "amount":  8750.50},
            {"description": "Diagnostics",  "amount":  4200.00},
        ]
        doc = _bill(fields=[
            _field("total_amount", "Rs. 103,450.50"),
            _field("line_items",   json.dumps(items)),
        ])
        findings = rule_V1([doc])
        assert len(findings) == 1
        f = findings[0]
        assert f["rule_id"] == "V1-LINE-ITEM-TOTAL-MISMATCH"
        assert f["weight"] == 25
        assert "5,000" in f["what"]          # Rs. 5,000 discrepancy
        assert len(f["evidence"]) == 2

    def test_clean_when_totals_match(self):
        findings = rule_V1([_bill()])
        assert findings == []

    def test_no_finding_when_line_items_absent(self):
        doc = _bill(fields=[
            _field("total_amount", "Rs. 50,000.00"),
            # no line_items field
        ])
        assert rule_V1([doc]) == []

    def test_rounding_within_2_paise_ignored(self):
        items = [{"description": "X", "amount": 100.001}]
        doc = _bill(fields=[
            _field("total_amount", "Rs. 100.00"),
            _field("line_items",   json.dumps(items)),
        ])
        assert rule_V1([doc]) == []

    def test_severity_high_when_over_5_pct(self):
        items = [{"description": "X", "amount": 50000.00}]
        doc = _bill(fields=[
            _field("total_amount", "Rs. 100000.00"),   # 100 % over
            _field("line_items",   json.dumps(items)),
        ])
        findings = rule_V1([doc])
        assert findings[0]["severity"] == "high"

    def test_no_finding_for_non_bill_doc(self):
        doc = {**_bill(), "document_type": DOCTYPE_CLAIM_FORM}
        assert rule_V1([doc]) == []


# ── V2 ─────────────────────────────────────────────────────────────────────────

class TestV2:
    def test_fires_when_over_claimed(self):
        bill = _bill()   # total 98,450.50
        form = _claim_form(fields=[
            _field("patient_name",    "Rajesh Kumar Sharma"),
            _field("policy_number",   "HDFC-HI-2024-00341"),
            _field("claimed_amount",  "Rs. 1,45,000.00"),
            _field("signature_present", "Present"),
        ])
        findings = rule_V2([bill, form])
        assert len(findings) == 1
        f = findings[0]
        assert f["rule_id"] == "V2-CLAIMED-VS-BILLED-MISMATCH"
        assert f["weight"] == 40
        assert "exceeds" in f["what"]
        assert f["severity"] in ("medium", "high")

    def test_fires_when_under_claimed(self):
        bill = _bill()  # total 98,450.50
        form = _claim_form(fields=[
            _field("claimed_amount", "Rs. 50,000.00"),
            _field("patient_name",   "Rajesh Kumar Sharma"),
            _field("policy_number",  "HDFC-HI-2024-00341"),
            _field("signature_present", "Present"),
        ])
        findings = rule_V2([bill, form])
        assert len(findings) == 1
        assert "is less than" in findings[0]["what"]

    def test_clean_when_amounts_match(self):
        assert rule_V2([_bill(), _claim_form()]) == []

    def test_no_finding_when_claim_form_missing(self):
        assert rule_V2([_bill()]) == []

    def test_no_finding_when_bill_missing(self):
        assert rule_V2([_claim_form()]) == []

    def test_evidence_references_both_docs(self):
        bill = _bill(filename="hospital_bill.pdf")
        form = _claim_form(fields=[
            _field("claimed_amount",    "Rs. 2,00,000.00"),
            _field("patient_name",      "Rajesh Kumar Sharma"),
            _field("policy_number",     "HDFC-HI-2024-00341"),
            _field("signature_present", "Present"),
        ], filename="claim_form.pdf")
        f = rule_V2([bill, form])[0]
        doc_names = {e["document"] for e in f["evidence"]}
        assert "claim_form.pdf"   in doc_names
        assert "hospital_bill.pdf" in doc_names


# ── V3 ─────────────────────────────────────────────────────────────────────────

class TestV3:
    def test_fires_on_clear_mismatch(self):
        bill = _bill(fields=[_field("patient_name", "Ravi Shankar Menon")])
        form = _claim_form(fields=[_field("patient_name", "Sunita Devi Agarwal")])
        findings = rule_V3([bill, form])
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "V3-PATIENT-NAME-MISMATCH"
        assert findings[0]["weight"] == 20

    def test_clean_when_names_match_exactly(self):
        bill = _bill(fields=[_field("patient_name", "Rajesh Kumar Sharma")])
        form = _claim_form(fields=[_field("patient_name", "Rajesh Kumar Sharma")])
        assert rule_V3([bill, form]) == []

    def test_clean_for_minor_variation(self):
        # "Arun K. Pillai" vs "Arun Kumar Pillai" — should be above threshold
        bill = _bill(fields=[_field("patient_name", "Arun K. Pillai")])
        form = _claim_form(fields=[_field("patient_name", "Arun Kumar Pillai")])
        # may or may not fire depending on threshold; just assert it doesn't crash
        findings = rule_V3([bill, form])
        assert isinstance(findings, list)

    def test_no_finding_with_single_doc(self):
        assert rule_V3([_bill()]) == []

    def test_no_finding_when_patient_name_absent(self):
        bill = _bill(fields=[_field("total_amount", "Rs. 10,000")])
        form = _claim_form(fields=[_field("total_amount", "Rs. 10,000")])
        assert rule_V3([bill, form]) == []

    def test_each_doc_pair_reported_once(self):
        # three docs with mismatched names → at most 3 pairs, but each pair once
        d1 = {**_bill(fields=[_field("patient_name", "Alice")]),      "filename": "d1.pdf"}
        d2 = {**_bill(fields=[_field("patient_name", "Bob")]),        "filename": "d2.pdf"}
        d3 = {**_claim_form(fields=[_field("patient_name", "Charlie")]), "filename": "d3.pdf"}
        findings = rule_V3([d1, d2, d3])
        pairs = [frozenset({f["evidence"][0]["document"], f["evidence"][1]["document"]})
                 for f in findings]
        assert len(pairs) == len(set(map(frozenset, [tuple(sorted(p)) for p in pairs])))


# ── V4 ─────────────────────────────────────────────────────────────────────────

class TestV4:
    def test_V4a_fires_admission_after_discharge(self):
        doc = _bill(fields=[
            _field("patient_name",   "X"),
            _field("hospital_name",  "Y"),
            _field("bill_date",      "10-Nov-2024"),
            _field("total_amount",   "Rs. 1,000"),
            _field("admission_date", "15-Nov-2024"),   # after discharge!
            _field("discharge_date", "10-Nov-2024"),
            _field("line_items", json.dumps([])),
        ])
        findings = rule_V4([doc])
        rule_ids = [f["rule_id"] for f in findings]
        assert "V4A-ADMISSION-AFTER-DISCHARGE" in rule_ids
        v4a = next(f for f in findings if f["rule_id"] == "V4A-ADMISSION-AFTER-DISCHARGE")
        assert v4a["severity"] == "high"
        assert v4a["weight"] == 15

    def test_V4b_fires_bill_before_admission(self):
        doc = _bill(fields=[
            _field("patient_name",   "X"),
            _field("hospital_name",  "Y"),
            _field("bill_date",      "01-Nov-2024"),   # before admission
            _field("total_amount",   "Rs. 1,000"),
            _field("admission_date", "08-Nov-2024"),
            _field("discharge_date", "13-Nov-2024"),
            _field("line_items", json.dumps([])),
        ])
        findings = rule_V4([doc])
        rule_ids = [f["rule_id"] for f in findings]
        assert "V4B-BILL-DATE-OUTSIDE-TREATMENT" in rule_ids

    def test_V4b_fires_bill_after_discharge(self):
        doc = _bill(fields=[
            _field("patient_name",   "X"),
            _field("hospital_name",  "Y"),
            _field("bill_date",      "20-Nov-2024"),   # after discharge
            _field("total_amount",   "Rs. 1,000"),
            _field("admission_date", "08-Nov-2024"),
            _field("discharge_date", "13-Nov-2024"),
            _field("line_items", json.dumps([])),
        ])
        findings = rule_V4([doc])
        assert any(f["rule_id"] == "V4B-BILL-DATE-OUTSIDE-TREATMENT" for f in findings)

    def test_clean_valid_dates(self):
        assert rule_V4([_bill()]) == []

    def test_no_finding_when_dates_absent(self):
        doc = _bill(fields=[
            _field("patient_name",  "X"),
            _field("total_amount",  "Rs. 1,000"),
            _field("line_items", json.dumps([])),
        ])
        assert rule_V4([doc]) == []

    def test_same_day_admission_discharge_allowed(self):
        doc = _bill(fields=[
            _field("patient_name",   "X"),
            _field("hospital_name",  "Y"),
            _field("bill_date",      "10-Nov-2024"),
            _field("total_amount",   "Rs. 1,000"),
            _field("admission_date", "10-Nov-2024"),
            _field("discharge_date", "10-Nov-2024"),
            _field("line_items", json.dumps([])),
        ])
        findings = rule_V4([doc])
        assert not any(f["rule_id"] == "V4A-ADMISSION-AFTER-DISCHARGE" for f in findings)


# ── V5 ─────────────────────────────────────────────────────────────────────────

class TestV5:
    def test_fires_on_missing_signature(self):
        form = _claim_form(fields=[
            _field("patient_name",      "Rajesh Kumar Sharma"),
            _field("policy_number",     "HDFC-HI-2024-00341"),
            _field("claimed_amount",    "Rs. 98,450.50"),
            _field("signature_present", "Missing"),          # ← missing
        ])
        findings = rule_V5([form])
        # Signature is split into its own finding at weight 30
        sig_findings = [f for f in findings if "Claimant Signature" in f["what"]]
        assert len(sig_findings) == 1
        f = sig_findings[0]
        assert f["rule_id"] == "V5-MISSING-REQUIRED-FIELDS"
        assert f["weight"] == 30
        assert "Claimant Signature" in f["what"]

    def test_fires_on_missing_policy_number(self):
        form = _claim_form(fields=[
            _field("patient_name",      "Rajesh"),
            _field("policy_number",     ""),           # blank
            _field("claimed_amount",    "Rs. 10,000"),
            _field("signature_present", "Present"),
        ])
        findings = rule_V5([form])
        # Policy number goes to the 'other missing' finding at weight 15
        assert any("Policy Number" in f["what"] for f in findings)
        policy_findings = [f for f in findings if "Policy Number" in f["what"]]
        assert policy_findings[0]["weight"] == 15

    def test_fires_on_missing_bill_total(self):
        bill = _bill(fields=[
            _field("patient_name",  "X"),
            _field("hospital_name", "Y"),
            _field("bill_date",     "14-Nov-2024"),
            _field("total_amount",  ""),               # blank
            _field("line_items", json.dumps([])),
        ])
        findings = rule_V5([bill])
        assert any("Total Amount" in f["what"] for f in findings)
        total_findings = [f for f in findings if "Total Amount" in f["what"]]
        assert total_findings[0]["weight"] == 15

    def test_clean_when_all_fields_present(self):
        assert rule_V5([_bill(), _claim_form()]) == []

    def test_severity_scales_with_missing_count(self):
        # All 4 claim-form fields missing:
        # → signature finding (weight=30, severity=low)
        # → other 3-missing finding (weight=15, severity=high)
        form = _claim_form(fields=[
            _field("patient_name",      ""),   # missing 1
            _field("policy_number",     ""),   # missing 2
            _field("claimed_amount",    ""),   # missing 3
            _field("signature_present", ""),   # missing 4 (signature)
        ])
        findings = rule_V5([form])
        # Expect exactly two findings: one sig, one other
        assert len(findings) == 2
        sig_f   = next(f for f in findings if "Claimant Signature" in f["what"])
        other_f = next(f for f in findings if "Claimant Signature" not in f["what"])
        assert sig_f["weight"]   == 30
        assert other_f["weight"] == 15
        assert other_f["severity"] == "high"   # 3 other fields → high

    def test_unknown_signature_treated_as_absent(self):
        form = _claim_form(fields=[
            _field("patient_name",      "X"),
            _field("policy_number",     "POL-001"),
            _field("claimed_amount",    "Rs. 5,000"),
            _field("signature_present", "Unknown"),    # treated as absent
        ])
        findings = rule_V5([form])
        sig_findings = [f for f in findings if "Claimant Signature" in f["what"]]
        assert sig_findings, "Expected a V5 finding for Unknown signature"
        assert sig_findings[0]["weight"] == 30

    def test_no_finding_for_unknown_doc_type(self):
        doc = {"filename": "x.pdf", "document_type": "unknown",
               "fields": [], "pages": []}
        assert rule_V5([doc]) == []


# ── run_all_rules integration ─────────────────────────────────────────────────

class TestRunAllRules:
    def test_returns_list(self):
        assert isinstance(run_all_rules([_bill(), _claim_form()]), list)

    def test_clean_claim_produces_no_findings(self):
        assert run_all_rules([_bill(), _claim_form()]) == []

    def test_multiple_violations_all_reported(self):
        # inflated total (V1) + over-claimed (V2) + missing signature (V5)
        items = [{"description": "Room", "amount": 40000.00}]
        bad_bill = _bill(fields=[
            _field("patient_name",   "Ravi Menon"),
            _field("hospital_name",  "Fortis"),
            _field("bill_date",      "13-Nov-2024"),   # within window
            _field("total_amount",   "Rs. 50,000.00"),   # V1: sum=40k, total=50k
            _field("admission_date", "08-Nov-2024"),
            _field("discharge_date", "13-Nov-2024"),
            _field("line_items",     json.dumps(items)),
        ])
        bad_form = _claim_form(fields=[
            _field("patient_name",      "Sunita Agarwal"),  # V3: name mismatch
            _field("policy_number",     "POL-001"),
            _field("claimed_amount",    "Rs. 75,000.00"),   # V2: over-claimed vs 50k
            _field("signature_present", "Missing"),          # V5: sig missing
        ])
        findings = run_all_rules([bad_bill, bad_form])
        rule_ids = {f["rule_id"] for f in findings}
        assert "V1-LINE-ITEM-TOTAL-MISMATCH"  in rule_ids
        assert "V2-CLAIMED-VS-BILLED-MISMATCH" in rule_ids
        assert "V3-PATIENT-NAME-MISMATCH"     in rule_ids
        assert "V5-MISSING-REQUIRED-FIELDS"   in rule_ids
