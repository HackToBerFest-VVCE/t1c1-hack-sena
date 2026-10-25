"""
tests/test_extraction_ground_truth.py
──────────────────────────────────────
For every bundle in sample_data/ground_truth.json, extract the claim_form.pdf,
hospital_bill.pdf and discharge_summary.pdf, then assert that every critical
field matches the ground-truth values.

Fields asserted per bundle
  • patient_name       (claim_form  and hospital_bill)
  • policy_number      (claim_form)
  • claimed_amount     (claim_form)
  • billed_total       (hospital_bill  → total_amount field)
  • admission_date     (hospital_bill)
  • discharge_date     (hospital_bill)
  • signature_present  (claim_form)

The test also verifies Rule V6 fires for bundle_006 (DATE_OUTSIDE defect).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

# ── resolve paths ──────────────────────────────────────────────────────────
# tests/ is inside backend/, so project root is two levels up
_BACKEND_DIR    = Path(__file__).resolve().parent.parent        # …/backend
_PROJECT_ROOT   = _BACKEND_DIR.parent                           # …/claimlens-ai
_SAMPLE_DIR     = _PROJECT_ROOT / "sample_data"
_GROUND_TRUTH   = _SAMPLE_DIR / "ground_truth.json"

# Make sure the backend package is importable when pytest is run from backend/
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.extraction import extract_document                                   # noqa: E402
from app.rules.validation import rule_V6, DOCTYPE_CLAIM_FORM, DOCTYPE_HOSPITAL_BILL  # noqa: E402


# ── helpers ────────────────────────────────────────────────────────────────

def _get_field(result: dict, name: str) -> str:
    """Return the first matching field value, or '' if not found."""
    for f in result["fields"]:
        if f["name"] == name:
            return f["value"].strip()
    return ""


def _amount(raw: str) -> float:
    """Parse 'Rs. 32,071.25' → 32071.25."""
    m = re.search(r"([\d,]+(?:\.\d{1,2})?)", raw)
    assert m, f"Could not parse amount from: {raw!r}"
    return float(m.group(1).replace(",", ""))


def _date_str(raw: str) -> str:
    """Normalise minor whitespace in extracted date string."""
    return raw.strip()


# ── load ground truth ──────────────────────────────────────────────────────

def _load_ground_truth() -> dict:
    if not _GROUND_TRUTH.exists():
        pytest.skip(f"ground_truth.json not found at {_GROUND_TRUTH}")
    with _GROUND_TRUTH.open() as fh:
        return json.load(fh)


GROUND_TRUTH = _load_ground_truth()


# ── parametrised fixture ───────────────────────────────────────────────────

@pytest.fixture(params=sorted(GROUND_TRUTH.keys()))
def bundle_data(request):
    """Yields (bundle_name, gt_entry, claim_result, bill_result)."""
    bundle_name: str = request.param
    gt: dict = GROUND_TRUTH[bundle_name]

    bundle_dir = _SAMPLE_DIR / bundle_name
    if not bundle_dir.is_dir():
        pytest.skip(f"Bundle directory not found: {bundle_dir}")

    form_path = bundle_dir / "claim_form.pdf"
    bill_path = bundle_dir / "hospital_bill.pdf"

    if not form_path.exists():
        pytest.skip(f"claim_form.pdf missing in {bundle_dir}")
    if not bill_path.exists():
        pytest.skip(f"hospital_bill.pdf missing in {bundle_dir}")

    form_result = extract_document(str(form_path))
    bill_result = extract_document(str(bill_path))

    return bundle_name, gt, form_result, bill_result


# ── individual assertions ──────────────────────────────────────────────────

class TestGroundTruth:
    """One test method per field; pytest reports which field failed per bundle."""

    # ── patient_name on claim form ────────────────────────────────────────

    def test_patient_name_claim_form(self, bundle_data):
        bundle_name, gt, form_result, _ = bundle_data
        extracted = _get_field(form_result, "patient_name")
        expected  = gt["patient_name_form"]
        assert extracted == expected, (
            f"[{bundle_name}] claim_form patient_name: "
            f"got {extracted!r}, expected {expected!r}"
        )

    # ── patient_name on hospital bill ─────────────────────────────────────

    def test_patient_name_hospital_bill(self, bundle_data):
        bundle_name, gt, _, bill_result = bundle_data
        extracted = _get_field(bill_result, "patient_name")
        expected  = gt["patient_name_bill"]
        assert extracted == expected, (
            f"[{bundle_name}] hospital_bill patient_name: "
            f"got {extracted!r}, expected {expected!r}"
        )

    # ── policy_number (claim form) ────────────────────────────────────────

    def test_policy_number(self, bundle_data):
        bundle_name, gt, form_result, _ = bundle_data
        extracted = _get_field(form_result, "policy_number")
        expected  = gt["policy_number"]
        assert extracted == expected, (
            f"[{bundle_name}] policy_number: "
            f"got {extracted!r}, expected {expected!r}"
        )

    # ── claimed_amount (claim form) ───────────────────────────────────────

    def test_claimed_amount(self, bundle_data):
        bundle_name, gt, form_result, _ = bundle_data
        raw      = _get_field(form_result, "claimed_amount")
        assert raw, f"[{bundle_name}] claimed_amount is empty"
        extracted = _amount(raw)
        expected  = float(gt["claimed_amount"])
        assert abs(extracted - expected) < 0.02, (
            f"[{bundle_name}] claimed_amount: "
            f"got {extracted}, expected {expected}"
        )

    # ── billed_total (hospital bill → total_amount) ───────────────────────

    def test_billed_total(self, bundle_data):
        bundle_name, gt, _, bill_result = bundle_data
        raw      = _get_field(bill_result, "total_amount")
        assert raw, f"[{bundle_name}] total_amount (billed_total) is empty"
        extracted = _amount(raw)
        expected  = float(gt["billed_total"])
        assert abs(extracted - expected) < 0.02, (
            f"[{bundle_name}] billed_total: "
            f"got {extracted}, expected {expected}"
        )

    # ── admission_date (hospital bill) ────────────────────────────────────

    def test_admission_date(self, bundle_data):
        bundle_name, gt, _, bill_result = bundle_data
        extracted = _date_str(_get_field(bill_result, "admission_date"))
        expected  = gt["admission_date"]
        assert extracted == expected, (
            f"[{bundle_name}] admission_date: "
            f"got {extracted!r}, expected {expected!r}"
        )

    # ── discharge_date (hospital bill) ────────────────────────────────────

    def test_discharge_date(self, bundle_data):
        bundle_name, gt, _, bill_result = bundle_data
        extracted = _date_str(_get_field(bill_result, "discharge_date"))
        expected  = gt["discharge_date"]
        assert extracted == expected, (
            f"[{bundle_name}] discharge_date: "
            f"got {extracted!r}, expected {expected!r}"
        )

    # ── signature_present (claim form) ───────────────────────────────────

    def test_signature_present(self, bundle_data):
        bundle_name, gt, form_result, _ = bundle_data
        extracted = _get_field(form_result, "signature_present")
        expected  = gt["signature"]
        assert extracted == expected, (
            f"[{bundle_name}] signature_present: "
            f"got {extracted!r}, expected {expected!r}"
        )


# ── V6 rule fires for bundle_006 (DATE_OUTSIDE) ────────────────────────────

class TestV6DateOutsidePolicy:
    """Rule V6 should fire for bundle_006 and not for bundle_001."""

    def _make_doc_result(self, bundle_name: str, pdf_name: str,
                         dtype: str) -> dict:
        path = _SAMPLE_DIR / bundle_name / pdf_name
        if not path.exists():
            pytest.skip(f"PDF not found: {path}")
        result = extract_document(str(path))
        result["filename"]      = pdf_name
        result["document_type"] = dtype
        return result

    def test_v6_fires_for_date_outside_bundle(self):
        """bundle_006: admission 27-Oct-2024 < policy start 01-Dec-2024 → V6 fires."""
        form = self._make_doc_result("bundle_006", "claim_form.pdf",  DOCTYPE_CLAIM_FORM)
        bill = self._make_doc_result("bundle_006", "hospital_bill.pdf", DOCTYPE_HOSPITAL_BILL)
        findings = rule_V6([form, bill])
        assert len(findings) >= 1, "Expected V6 finding for bundle_006 DATE_OUTSIDE"
        f = findings[0]
        assert f["rule_id"] == "V6-DATE-OUTSIDE-POLICY-PERIOD"
        assert f["weight"] == 25
        assert f["severity"] == "high"
        assert "27-Oct-2024" in f["what"]
        assert "01-Dec-2024" in f["what"]
        assert len(f["evidence"]) >= 1

    def test_v6_does_not_fire_for_clean_bundle(self):
        """bundle_001: admission 08-Nov-2024 is well after policy start 01-Jan-2024."""
        form = self._make_doc_result("bundle_001", "claim_form.pdf",  DOCTYPE_CLAIM_FORM)
        bill = self._make_doc_result("bundle_001", "hospital_bill.pdf", DOCTYPE_HOSPITAL_BILL)
        findings = rule_V6([form, bill])
        assert findings == [], (
            "V6 should not fire for bundle_001 (clean dates); "
            f"got: {findings}"
        )

    def test_v6_evidence_includes_document_and_page(self):
        """Evidence entries must have 'document' and 'page' keys."""
        form = self._make_doc_result("bundle_006", "claim_form.pdf",  DOCTYPE_CLAIM_FORM)
        bill = self._make_doc_result("bundle_006", "hospital_bill.pdf", DOCTYPE_HOSPITAL_BILL)
        findings = rule_V6([form, bill])
        assert findings, "Expected at least one V6 finding"
        for ev in findings[0]["evidence"]:
            assert "document" in ev, f"Evidence missing 'document' key: {ev}"
            assert "page" in ev,     f"Evidence missing 'page' key: {ev}"

    def test_v6_no_finding_when_policy_start_absent(self):
        """No policy_start_date field → V6 must not crash or fire."""
        bill = self._make_doc_result("bundle_006", "hospital_bill.pdf", DOCTYPE_HOSPITAL_BILL)
        # hospital_bill has no policy_start_date field → V6 should return []
        findings = rule_V6([bill])
        assert findings == []


# ── V5 fires with signature field name when signature is missing ───────────

class TestV5SignatureNaming:
    """V5 must name 'Claimant Signature' in its what string when sig is Missing."""

    def test_v5_names_signature_field_bundle_004(self):
        """bundle_004 MISSING_SIG: V5 finding must mention 'Claimant Signature'."""
        from app.rules.validation import rule_V5

        form_path = _SAMPLE_DIR / "bundle_004" / "claim_form.pdf"
        if not form_path.exists():
            pytest.skip(f"PDF not found: {form_path}")

        result = extract_document(str(form_path))
        result["filename"]      = "claim_form.pdf"
        result["document_type"] = DOCTYPE_CLAIM_FORM

        findings = rule_V5([result])
        sig_findings = [f for f in findings if "Claimant Signature" in f["what"]]
        assert sig_findings, (
            "Expected a V5 finding mentioning 'Claimant Signature' for bundle_004; "
            f"V5 findings: {[f['what'] for f in findings]}"
        )
        assert sig_findings[0]["rule_id"] == "V5-MISSING-REQUIRED-FIELDS"
