"""
tests/test_fraud.py – pytest tests for all five fraud / risk-indicator rules
and the compute_risk scorer.
"""

from __future__ import annotations

import json
import hashlib
import struct
import tempfile
import zlib
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.rules.fraud import (
    rule_F1, rule_F2, rule_F3, rule_F4, rule_F5,
    compute_risk, run_fraud_rules,
    PriorClaim,
)

# ── fixture helpers ───────────────────────────────────────────────────────────

def _field(name, value, page=1, confidence=0.9):
    return {"name": name, "value": value, "confidence": confidence, "page_number": page}


def _bill(patient="Rajesh Kumar Sharma", hospital="Apollo Hospitals",
          admission="08-Nov-2024", discharge="13-Nov-2024",
          bill_date="13-Nov-2024", total="Rs. 98,450.50",
          items=None, filename="bill.pdf"):
    if items is None:
        items = [
            {"description": "Room charges",   "amount": 22500.00},
            {"description": "OT charges",     "amount": 35000.00},
            {"description": "Surgeon fees",   "amount": 28000.00},
            {"description": "Medicines",      "amount":  8750.50},
            {"description": "Diagnostics",    "amount":  4200.00},
        ]
    return {
        "filename":      filename,
        "document_type": "hospital_bill",
        "fields": [
            _field("patient_name",   patient),
            _field("hospital_name",  hospital),
            _field("admission_date", admission),
            _field("discharge_date", discharge),
            _field("bill_date",      bill_date),
            _field("total_amount",   total),
            _field("line_items",     json.dumps(items)),
        ],
        "pages": [],
    }


def _prior(
    claim_id="CLM-2024-0001",
    patient="Rajesh Kumar Sharma",
    hospital="Apollo Hospitals",
    admission="08-Nov-2024",
    total="Rs. 98,450.50",
    file_hash=None,
) -> PriorClaim:
    return PriorClaim(
        claim_id=claim_id,
        patient_name=patient,
        hospital_name=hospital,
        admission_date=admission,
        total_amount=total,
        file_hash=file_hash,
    )


# ── minimal valid PDF builder (no pymupdf, pure bytes) ───────────────────────
# Used for F5 metadata tests.  Produces a real PDF with controllable
# creationDate / modDate in the document info dictionary.

def _make_pdf_bytes(creation: str, mod: str) -> bytes:
    """
    Build a minimal 1-page PDF with explicit creationDate / modDate.
    Dates must be PDF format strings, e.g. 'D:20241101120000'.
    """
    # We use a hand-crafted minimal PDF so we don't need reportlab/fpdf2 here.
    content = b"BT /F1 12 Tf 100 700 Td (ClaimLens test) Tj ET"
    stream  = zlib.compress(content)

    info_obj = (
        f"<< /Title (Test) "
        f"/CreationDate ({creation}) "
        f"/ModDate ({mod}) >>"
    ).encode()

    def obj(n, s):
        return f"{n} 0 obj\n".encode() + s + b"\nendobj\n"

    o1 = obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    o2 = obj(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    o3 = obj(3, (
        b"<< /Type /Page /Parent 2 0 R "
        b"/MediaBox [0 0 595 842] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
    ))
    o4 = obj(4, (
        f"<< /Filter /FlateDecode /Length {len(stream)} >>\n".encode()
        + b"stream\n" + stream + b"\nendstream"
    ))
    o5 = obj(5, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    o6 = obj(6, info_obj)

    header  = b"%PDF-1.4\n"
    body    = o1 + o2 + o3 + o4 + o5 + o6

    offsets = []
    pos     = len(header)
    for chunk in [o1, o2, o3, o4, o5, o6]:
        offsets.append(pos)
        pos += len(chunk)

    xref_pos = len(header) + len(body)
    xref = b"xref\n0 7\n0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n".encode()

    trailer = (
        f"trailer\n<< /Size 7 /Root 1 0 R /Info 6 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()

    return header + body + xref + trailer


# ── F1 ────────────────────────────────────────────────────────────────────────

class TestF1:
    def test_fires_on_near_duplicate(self):
        docs = [_bill()]
        prior = [_prior()]   # exact match
        findings = rule_F1(docs, prior)
        assert len(findings) == 1
        f = findings[0]
        assert f["rule_id"] == "F1-NEAR-DUPLICATE-CLAIM"
        assert f["weight"] == 40
        assert "risk indicator" in f["what"].lower()
        assert "CLM-2024-0001" in f["what"]

    def test_fires_on_fuzzy_name_match(self):
        # "Rajesh K Sharma" vs "Rajesh Kumar Sharma" – should still match
        docs   = [_bill(patient="Rajesh K Sharma")]
        prior  = [_prior(patient="Rajesh Kumar Sharma")]
        findings = rule_F1(docs, prior)
        assert len(findings) == 1

    def test_no_finding_different_patient(self):
        docs  = [_bill(patient="Sunita Agarwal")]
        prior = [_prior(patient="Ravi Shankar Menon")]
        assert rule_F1(docs, prior) == []

    def test_no_finding_different_hospital(self):
        docs  = [_bill(hospital="Max Hospital Delhi")]
        prior = [_prior(hospital="AIIMS Bhopal")]
        assert rule_F1(docs, prior) == []

    def test_no_finding_when_neither_date_nor_amount_match(self):
        # Patient and hospital match but amount is wildly different
        docs  = [_bill(total="Rs. 10,000.00",  admission="01-Jan-2024")]
        prior = [_prior(total="Rs. 5,00,000.00", admission="01-Jul-2024")]
        assert rule_F1(docs, prior) == []

    def test_no_finding_with_empty_prior_list(self):
        assert rule_F1([_bill()], []) == []

    def test_amount_match_within_tolerance(self):
        docs  = [_bill(total="Rs. 98,450.50")]
        prior = [_prior(total="Rs. 97,000.00")]   # ~1.5 % diff – within 5 %
        findings = rule_F1(docs, prior)
        assert len(findings) == 1

    def test_no_duplicate_finding_per_distinct_prior(self):
        docs  = [_bill()]
        prior = [_prior(claim_id="CLM-A"), _prior(claim_id="CLM-B")]
        findings = rule_F1(docs, prior)
        assert len(findings) == 2


# ── F2 ────────────────────────────────────────────────────────────────────────

class TestF2:
    def test_fires_on_matching_hash(self):
        h     = "a" * 64
        docs  = [_bill()]
        prior = [_prior(file_hash=h)]
        findings = rule_F2(docs, prior, [h])
        assert len(findings) == 1
        f = findings[0]
        assert f["rule_id"] == "F2-DUPLICATE-FILE-HASH"
        assert f["weight"] == 40
        assert "risk indicator" in f["what"].lower()

    def test_no_finding_different_hash(self):
        prior = [_prior(file_hash="a" * 64)]
        assert rule_F2([_bill()], prior, ["b" * 64]) == []

    def test_no_finding_empty_hashes(self):
        prior = [_prior(file_hash="a" * 64)]
        assert rule_F2([_bill()], prior, []) == []

    def test_no_finding_prior_has_no_hash(self):
        prior = [_prior(file_hash=None)]
        assert rule_F2([_bill()], prior, ["a" * 64]) == []

    def test_multiple_hash_matches_reported_separately(self):
        h1, h2 = "a" * 64, "b" * 64
        prior  = [_prior(claim_id="A", file_hash=h1), _prior(claim_id="B", file_hash=h2)]
        docs   = [_bill(filename="d1.pdf"), _bill(filename="d2.pdf")]
        findings = rule_F2(docs, prior, [h1, h2])
        assert len(findings) == 2


# ── F3 ────────────────────────────────────────────────────────────────────────

class TestF3:
    def _all_round(self, count=5):
        return [{"description": f"Item {i}", "amount": float(i * 10000)} for i in range(1, count + 1)]

    def _mixed(self):
        return [
            {"description": "Medicines",   "amount": 8750.50},
            {"description": "Room",        "amount": 22500.00},
            {"description": "Lab",         "amount": 3150.00},
            {"description": "OT",          "amount": 35000.00},
            {"description": "Surgeon",     "amount": 28000.00},
        ]

    def test_fires_when_all_round(self):
        doc = _bill(items=self._all_round())
        findings = rule_F3([doc])
        assert len(findings) == 1
        f = findings[0]
        assert f["rule_id"] == "F3-ROUND-NUMBER-BILLING"
        assert f["weight"] == 10
        assert "risk indicator" in f["what"].lower()

    def test_fires_when_4_of_5_round(self):
        items = [
            {"description": "A", "amount": 10000.00},
            {"description": "B", "amount": 20000.00},
            {"description": "C", "amount": 30000.00},
            {"description": "D", "amount": 40000.00},
            {"description": "E", "amount":  1234.56},  # not round
        ]
        doc = _bill(items=items)
        findings = rule_F3([doc])
        assert len(findings) == 1   # 4/5 = 80 % > 60 %

    def test_no_finding_when_mostly_fractional(self):
        # _mixed() has 4/5 round (22500, 35000, 28000, 3150) → 80% → fires
        # Use a dataset that is genuinely mostly fractional instead
        items = [
            {"description": "Medicines",   "amount": 8750.50},
            {"description": "Lab test A",  "amount": 1234.75},
            {"description": "Lab test B",  "amount":  987.25},
            {"description": "Room",        "amount": 22500.00},
            {"description": "Dressing",    "amount":  430.50},
        ]
        # 1 of 5 = 20 % round – below threshold
        doc = _bill(items=items)
        assert rule_F3([doc]) == []

    def test_no_finding_for_fewer_than_3_items(self):
        items = [{"description": "X", "amount": 10000.00},
                 {"description": "Y", "amount": 20000.00}]
        doc = _bill(items=items)
        assert rule_F3([doc]) == []

    def test_no_finding_for_non_bill_doc(self):
        doc = {**_bill(items=self._all_round()), "document_type": "claim_form"}
        assert rule_F3([doc]) == []

    def test_no_finding_when_line_items_absent(self):
        doc = _bill()
        doc["fields"] = [f for f in doc["fields"] if f["name"] != "line_items"]
        assert rule_F3([doc]) == []


# ── F4 ────────────────────────────────────────────────────────────────────────

class TestF4:
    def test_fires_within_30_days(self):
        docs = [_bill(admission="15-Nov-2024")]
        findings = rule_F4(docs, policy_start_date="01-Nov-2024")
        assert len(findings) == 1
        f = findings[0]
        assert f["rule_id"] == "F4-EARLY-TREATMENT-RISK"
        assert f["weight"] == 15
        assert "risk indicator" in f["what"].lower()
        assert "14 day" in f["what"] or "14" in f["what"]

    def test_fires_on_day_zero(self):
        findings = rule_F4([_bill(admission="01-Nov-2024")], "01-Nov-2024")
        assert len(findings) == 1

    def test_fires_on_day_30(self):
        findings = rule_F4([_bill(admission="01-Dec-2024")], "01-Nov-2024")
        assert len(findings) == 1

    def test_no_finding_on_day_31(self):
        findings = rule_F4([_bill(admission="02-Dec-2024")], "01-Nov-2024")
        assert findings == []

    def test_no_finding_when_policy_date_missing(self):
        assert rule_F4([_bill()], None) == []

    def test_no_finding_when_admission_missing(self):
        doc = _bill()
        doc["fields"] = [f for f in doc["fields"] if f["name"] != "admission_date"]
        assert rule_F4([doc], "01-Nov-2024") == []

    def test_accepts_iso_date(self):
        findings = rule_F4([_bill(admission="10-Nov-2024")], "2024-11-01")
        assert len(findings) == 1

    def test_admission_before_policy_start_ignored(self):
        # Logically impossible but should not raise
        findings = rule_F4([_bill(admission="01-Oct-2024")], "01-Nov-2024")
        assert findings == []


# ── F5 ────────────────────────────────────────────────────────────────────────

class TestF5:
    def _pdf_file(self, creation: str, mod: str) -> Path:
        data = _make_pdf_bytes(creation, mod)
        tmp  = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(data)
        tmp.close()
        return Path(tmp.name)

    def test_fires_when_mod_after_creation(self):
        path = self._pdf_file("D:20241101120000", "D:20241115093000")
        try:
            docs     = [_bill(filename=path.name)]
            findings = rule_F5(docs, [str(path)])
            assert len(findings) == 1
            f = findings[0]
            assert f["rule_id"] == "F5-PDF-MODIFIED-AFTER-CREATION"
            assert f["weight"] == 10
            assert "risk indicator" in f["what"].lower()
        finally:
            path.unlink(missing_ok=True)

    def test_no_finding_when_dates_equal(self):
        path = self._pdf_file("D:20241101120000", "D:20241101120000")
        try:
            assert rule_F5([_bill()], [str(path)]) == []
        finally:
            path.unlink(missing_ok=True)

    def test_no_finding_within_10_second_tolerance(self):
        path = self._pdf_file("D:20241101120000", "D:20241101120008")
        try:
            assert rule_F5([_bill()], [str(path)]) == []
        finally:
            path.unlink(missing_ok=True)

    def test_no_finding_for_image_file(self):
        # pass a .jpg path – rule should skip it without error
        assert rule_F5([_bill()], ["photo.jpg"]) == []

    def test_no_finding_when_file_missing(self):
        assert rule_F5([_bill()], ["/nonexistent/path/bill.pdf"]) == []

    def test_no_finding_when_no_paths(self):
        assert rule_F5([_bill()], []) == []


# ── compute_risk ──────────────────────────────────────────────────────────────

class TestComputeRisk:
    def _finding(self, weight, what="test finding"):
        return {
            "rule_id": "TEST", "severity": "low", "weight": weight,
            "what": what, "evidence": [], "why_it_matters": "",
            "suggested_action": "",
        }

    def test_empty_findings_gives_zero_low(self):
        r = compute_risk([])
        assert r["risk_score"] == 0
        assert r["risk_level"] == "Low"
        assert r["risk_reasons"] == []

    def test_low_below_30(self):
        r = compute_risk([self._finding(10), self._finding(10)])
        assert r["risk_score"] == 20
        assert r["risk_level"] == "Low"

    def test_medium_at_30(self):
        r = compute_risk([self._finding(30)])
        assert r["risk_score"] == 30
        assert r["risk_level"] == "Medium"

    def test_medium_at_60(self):
        r = compute_risk([self._finding(60)])
        assert r["risk_score"] == 60
        assert r["risk_level"] == "Medium"   # 60 is within Medium (30–60 inclusive)

    def test_high_above_60(self):
        r = compute_risk([self._finding(40), self._finding(40)])
        assert r["risk_score"] == 80
        assert r["risk_level"] == "High"

    def test_capped_at_100(self):
        r = compute_risk([self._finding(40), self._finding(40), self._finding(40)])
        assert r["risk_score"] == 100
        assert r["risk_level"] == "High"

    def test_risk_reasons_match_what_fields(self):
        findings = [self._finding(25, "first reason"), self._finding(25, "second reason")]
        r = compute_risk(findings)
        assert r["risk_reasons"] == ["first reason", "second reason"]

    def test_f1_plus_f2_equals_high(self):
        # F1 weight=40 + F2 weight=40 = 80 → High
        f1 = self._finding(40, "F1 finding")
        f2 = self._finding(40, "F2 finding")
        r  = compute_risk([f1, f2])
        assert r["risk_level"] == "High"
        assert r["risk_score"]  == 80

    def test_f3_plus_f4_plus_f5_is_medium(self):
        # F3=10 + F4=15 + F5=10 = 35 → Medium
        r = compute_risk([self._finding(10), self._finding(15), self._finding(10)])
        assert r["risk_level"] == "Medium"


# ── run_fraud_rules integration ───────────────────────────────────────────────

class TestRunFraudRules:
    def test_returns_list(self):
        assert isinstance(run_fraud_rules([_bill()]), list)

    def test_clean_claim_no_findings(self):
        # Use fractional items so F3 doesn't fire
        fractional_items = [
            {"description": "Medicines",  "amount": 8750.50},
            {"description": "Lab A",      "amount": 1234.75},
            {"description": "Lab B",      "amount":  987.25},
            {"description": "Room",       "amount": 22500.00},
            {"description": "Dressing",   "amount":  430.50},
        ]
        clean_bill = _bill(items=fractional_items)
        findings = run_fraud_rules(
            [clean_bill],
            prior_claims=[],
            policy_start_date="01-Jan-2020",
        )
        assert findings == []

    def test_all_rules_fire_together(self):
        h = "c" * 64
        prior = [_prior(claim_id="CLM-PREV", file_hash=h)]
        items = [{"description": f"Item {i}", "amount": float(i * 10000)} for i in range(1, 6)]
        docs  = [_bill(admission="05-Nov-2024", items=items)]

        findings = run_fraud_rules(
            docs,
            prior_claims=prior,
            file_hashes=[h],
            file_paths=[],
            policy_start_date="01-Nov-2024",  # F4: 4 days in
        )
        rule_ids = {f["rule_id"] for f in findings}
        assert "F1-NEAR-DUPLICATE-CLAIM"  in rule_ids
        assert "F2-DUPLICATE-FILE-HASH"   in rule_ids
        assert "F3-ROUND-NUMBER-BILLING"  in rule_ids
        assert "F4-EARLY-TREATMENT-RISK"  in rule_ids
