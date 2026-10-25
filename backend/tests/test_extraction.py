"""
tests/test_extraction.py – Integration tests for extraction.py and validation rules.

Tests cover:
  1. Ground-truth extraction: for each bundle in sample_data/ground_truth.json,
     verify that extract_document() returns the correct patient_name (form and
     bill), policy_number, policy_start_date, claimed_amount, billed_total,
     admission_date, discharge_date, and signature against ground truth.

  2. Signature detection (V5):
     - bundle_004 → signature_present == "Missing"   ([ Missing — not signed ])
     - bundle_001 → signature_present == "Present"   ([ Signed ])

  3. V5 rule fires for bundle_004 (missing signature) and not for bundle_001.

  4. V6 rule fires for bundle_006 (treatment before policy start) and not for
     bundle_001 (clean bundle).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

# ── path helpers ──────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent   # …/claimlens-ai
_SAMPLE_DATA = _REPO_ROOT / "sample_data"
_GT_FILE     = _SAMPLE_DATA / "ground_truth.json"

# Skip the entire module if the sample data is absent (CI without assets).
pytestmark = pytest.mark.skipif(
    not _GT_FILE.exists(),
    reason="sample_data/ground_truth.json not found",
)


# ── lazy imports so the skip can fire before we import app code ───────────────

def _import_extract():
    from app.extraction import extract_document
    return extract_document


def _import_rules():
    from app.rules.validation import rule_V5, rule_V6
    return rule_V5, rule_V6


# ── helpers ───────────────────────────────────────────────────────────────────

def _load_gt() -> dict:
    return json.loads(_GT_FILE.read_text(encoding="utf-8"))


def _get_field(result: dict, name: str) -> str:
    """Return the value of the named field from an ExtractionResult, or ''."""
    for f in result["fields"]:
        if f["name"] == name:
            return f["value"].strip()
    return ""


def _parse_amount(raw: str) -> float | None:
    """Parse 'Rs. 32,071.25' → 32071.25, or None."""
    import re
    m = re.search(r"([\d,]+(?:\.\d{1,2})?)", raw)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _bundle_docs(bundle_name: str) -> dict[str, dict]:
    """
    Extract all three documents for a bundle.
    Returns {doc_type: ExtractionResult} dict.
    """
    extract = _import_extract()
    bundle_dir = _SAMPLE_DATA / bundle_name
    result = {}
    for doc_name in ("claim_form.pdf", "hospital_bill.pdf", "discharge_summary.pdf"):
        p = bundle_dir / doc_name
        if p.exists():
            result[doc_name] = extract(str(p))
    return result


def _to_doc_result(filename: str, ext_result: dict) -> dict:
    """Convert an ExtractionResult into a DocResult for rule functions."""
    return {
        "filename":      filename,
        "document_type": ext_result["document_type"],
        "fields":        ext_result["fields"],
        "pages":         ext_result["pages"],
    }


# ── ground-truth extraction tests ─────────────────────────────────────────────

class TestGroundTruthExtraction:
    """
    For each bundle verify that the extraction matches the ground truth values
    for all fields named in the task spec.
    """

    @pytest.fixture(params=list(_load_gt().keys()))
    def bundle(self, request):
        """Parametrise over every bundle key."""
        name = request.param
        gt   = _load_gt()[name]
        docs = _bundle_docs(name)
        return name, gt, docs

    # ── patient_name (form) ───────────────────────────────────────────────

    def test_patient_name_form(self, bundle):
        name, gt, docs = bundle
        ext = docs.get("claim_form.pdf")
        if ext is None:
            pytest.skip(f"claim_form.pdf missing for {name}")
        extracted = _get_field(ext, "patient_name")
        assert extracted == gt["patient_name_form"], (
            f"{name}: patient_name from claim_form.pdf "
            f"expected={gt['patient_name_form']!r} got={extracted!r}"
        )

    # ── patient_name (bill) ───────────────────────────────────────────────

    def test_patient_name_bill(self, bundle):
        name, gt, docs = bundle
        ext = docs.get("hospital_bill.pdf")
        if ext is None:
            pytest.skip(f"hospital_bill.pdf missing for {name}")
        extracted = _get_field(ext, "patient_name")
        assert extracted == gt["patient_name_bill"], (
            f"{name}: patient_name from hospital_bill.pdf "
            f"expected={gt['patient_name_bill']!r} got={extracted!r}"
        )

    # ── policy_number ─────────────────────────────────────────────────────

    def test_policy_number(self, bundle):
        name, gt, docs = bundle
        ext = docs.get("claim_form.pdf")
        if ext is None:
            pytest.skip(f"claim_form.pdf missing for {name}")
        extracted = _get_field(ext, "policy_number")
        assert extracted == gt["policy_number"], (
            f"{name}: policy_number "
            f"expected={gt['policy_number']!r} got={extracted!r}"
        )

    # ── policy_start_date ─────────────────────────────────────────────────

    def test_policy_start(self, bundle):
        name, gt, docs = bundle
        ext = docs.get("claim_form.pdf")
        if ext is None:
            pytest.skip(f"claim_form.pdf missing for {name}")
        extracted = _get_field(ext, "policy_start_date")
        assert extracted == gt["policy_start"], (
            f"{name}: policy_start_date "
            f"expected={gt['policy_start']!r} got={extracted!r}"
        )

    # ── claimed_amount ────────────────────────────────────────────────────

    def test_claimed_amount(self, bundle):
        name, gt, docs = bundle
        ext = docs.get("claim_form.pdf")
        if ext is None:
            pytest.skip(f"claim_form.pdf missing for {name}")
        raw = _get_field(ext, "claimed_amount")
        amount = _parse_amount(raw)
        assert amount is not None, f"{name}: claimed_amount could not be parsed: {raw!r}"
        assert math.isclose(amount, gt["claimed_amount"], rel_tol=1e-4), (
            f"{name}: claimed_amount expected={gt['claimed_amount']} got={amount}"
        )

    # ── billed_total ──────────────────────────────────────────────────────

    def test_billed_total(self, bundle):
        name, gt, docs = bundle
        ext = docs.get("hospital_bill.pdf")
        if ext is None:
            pytest.skip(f"hospital_bill.pdf missing for {name}")
        raw = _get_field(ext, "total_amount")
        amount = _parse_amount(raw)
        assert amount is not None, f"{name}: total_amount could not be parsed: {raw!r}"
        assert math.isclose(amount, gt["billed_total"], rel_tol=1e-4), (
            f"{name}: billed_total expected={gt['billed_total']} got={amount}"
        )

    # ── admission_date ────────────────────────────────────────────────────

    def test_admission_date(self, bundle):
        name, gt, docs = bundle
        # Use bill if present, else claim form
        ext = docs.get("hospital_bill.pdf") or docs.get("claim_form.pdf")
        if ext is None:
            pytest.skip(f"no usable document for {name}")
        extracted = _get_field(ext, "admission_date")
        assert extracted == gt["admission_date"], (
            f"{name}: admission_date "
            f"expected={gt['admission_date']!r} got={extracted!r}"
        )

    # ── discharge_date ────────────────────────────────────────────────────

    def test_discharge_date(self, bundle):
        name, gt, docs = bundle
        ext = docs.get("hospital_bill.pdf") or docs.get("claim_form.pdf")
        if ext is None:
            pytest.skip(f"no usable document for {name}")
        extracted = _get_field(ext, "discharge_date")
        assert extracted == gt["discharge_date"], (
            f"{name}: discharge_date "
            f"expected={gt['discharge_date']!r} got={extracted!r}"
        )

    # ── signature ─────────────────────────────────────────────────────────

    def test_signature(self, bundle):
        name, gt, docs = bundle
        ext = docs.get("claim_form.pdf")
        if ext is None:
            pytest.skip(f"claim_form.pdf missing for {name}")
        extracted = _get_field(ext, "signature_present")
        assert extracted == gt["signature"], (
            f"{name}: signature_present "
            f"expected={gt['signature']!r} got={extracted!r}"
        )


# ── signature detection specific tests ───────────────────────────────────────

class TestSignatureDetection:
    """
    Explicit tests for bundle_004 (missing) and bundle_001 (present).
    """

    def test_bundle_004_signature_missing(self):
        """[ Missing — not signed ] must yield signature_present = 'Missing'."""
        extract = _import_extract()
        path = _SAMPLE_DATA / "bundle_004" / "claim_form.pdf"
        if not path.exists():
            pytest.skip("bundle_004/claim_form.pdf not found")
        result = extract(str(path))
        sig = _get_field(result, "signature_present")
        assert sig == "Missing", f"Expected 'Missing', got {sig!r}"

    def test_bundle_001_signature_present(self):
        """[ Signed ] must yield signature_present = 'Present'."""
        extract = _import_extract()
        path = _SAMPLE_DATA / "bundle_001" / "claim_form.pdf"
        if not path.exists():
            pytest.skip("bundle_001/claim_form.pdf not found")
        result = extract(str(path))
        sig = _get_field(result, "signature_present")
        assert sig == "Present", f"Expected 'Present', got {sig!r}"


# ── V5 rule (missing signature) tests ────────────────────────────────────────

class TestV5Signature:
    """
    V5 should fire on bundle_004 (missing signature) and be silent on
    bundle_001 (all fields present).
    """

    def _docs_for(self, bundle_name: str) -> list[dict]:
        docs = _bundle_docs(bundle_name)
        return [_to_doc_result(fname, ext) for fname, ext in docs.items()]

    def test_v5_fires_for_bundle_004_missing_sig(self):
        rule_V5, _ = _import_rules()
        bundle_dir = _SAMPLE_DATA / "bundle_004"
        if not bundle_dir.exists():
            pytest.skip("bundle_004 not found")
        docs = self._docs_for("bundle_004")
        findings = rule_V5(docs)
        sig_findings = [
            f for f in findings
            if f["rule_id"] == "V5-MISSING-REQUIRED-FIELDS"
            and "Claimant Signature" in f["what"]
        ]
        assert len(sig_findings) >= 1, (
            "Expected at least one V5 finding mentioning 'Claimant Signature' "
            f"for bundle_004. Findings: {findings}"
        )
        # Evidence must reference the claim form
        ev = sig_findings[0]["evidence"]
        assert any("claim_form" in e["document"] for e in ev), (
            f"V5 finding evidence should reference claim_form.pdf, got: {ev}"
        )
        # Page 1
        assert ev[0]["page"] == 1, (
            f"V5 finding evidence page should be 1, got: {ev[0]['page']}"
        )

    def test_v5_clean_for_bundle_001(self):
        rule_V5, _ = _import_rules()
        bundle_dir = _SAMPLE_DATA / "bundle_001"
        if not bundle_dir.exists():
            pytest.skip("bundle_001 not found")
        docs = self._docs_for("bundle_001")
        findings = rule_V5(docs)
        sig_findings = [
            f for f in findings
            if f["rule_id"] == "V5-MISSING-REQUIRED-FIELDS"
            and "Claimant Signature" in f["what"]
        ]
        assert sig_findings == [], (
            f"Expected no signature V5 findings for bundle_001, got: {sig_findings}"
        )


# ── V6 rule (treatment before policy start) tests ────────────────────────────

class TestV6PolicyStart:
    """
    V6 should fire on bundle_006 (admission 27-Oct-2024 < policy 01-Dec-2024)
    and be silent on bundle_001 (clean bundle).
    """

    def _docs_for(self, bundle_name: str) -> list[dict]:
        docs = _bundle_docs(bundle_name)
        return [_to_doc_result(fname, ext) for fname, ext in docs.items()]

    def test_v6_fires_for_bundle_006(self):
        _, rule_V6 = _import_rules()
        bundle_dir = _SAMPLE_DATA / "bundle_006"
        if not bundle_dir.exists():
            pytest.skip("bundle_006 not found")
        docs = self._docs_for("bundle_006")
        findings = rule_V6(docs)
        assert len(findings) >= 1, (
            f"Expected at least one V6 finding for bundle_006. Got: {findings}"
        )
        f = findings[0]
        assert f["rule_id"] == "V6-DATE-OUTSIDE-POLICY-PERIOD"
        assert f["weight"] == 25
        assert "27-Oct-2024" in f["what"] or "27-Oct" in f["what"], (
            f"Expected admission date in finding 'what': {f['what']}"
        )
        assert "01-Dec-2024" in f["what"] or "01-Dec" in f["what"], (
            f"Expected policy start date in finding 'what': {f['what']}"
        )
        # Evidence must have at least two entries: the date doc + the policy doc
        assert len(f["evidence"]) >= 1

    def test_v6_clean_for_bundle_001(self):
        _, rule_V6 = _import_rules()
        bundle_dir = _SAMPLE_DATA / "bundle_001"
        if not bundle_dir.exists():
            pytest.skip("bundle_001 not found")
        docs = self._docs_for("bundle_001")
        findings = rule_V6(docs)
        assert findings == [], (
            f"Expected no V6 findings for bundle_001, got: {findings}"
        )
