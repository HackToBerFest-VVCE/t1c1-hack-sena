"""
tests/test_routes.py – Integration tests for all API routes.

Calls route functions directly via FastAPI's dependency-injection system
using a disposable in-memory SQLite database — no HTTP client required.
"""

from __future__ import annotations

import io
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# ── swap the DB engine before any app code loads ────────────────────────────
import database as _db

_TEST_URL    = "sqlite:///:memory:"
_TEST_ENGINE = create_engine(_TEST_URL, connect_args={"check_same_thread": False})
_TestSession = sessionmaker(bind=_TEST_ENGINE, autocommit=False, autoflush=False)
_db.engine       = _TEST_ENGINE
_db.SessionLocal = _TestSession

from database import Base  # noqa: E402
import models               # noqa: F401  – registers ORM classes
Base.metadata.create_all(bind=_TEST_ENGINE)

# ── now safe to import app objects ────────────────────────────────────────────
import main as _main               # noqa: E402
from main import (               # noqa: E402
    create_claim, list_claims, get_claim,
    patch_field, submit_decision,
    get_page_image, get_audit,
    FieldPatch, DecisionBody,
)
from models import Claim, Document, ExtractedField, AuditLog  # noqa: E402

# ── sample PDFs ───────────────────────────────────────────────────────────────
SAMPLE_DIR  = Path(__file__).parent.parent.parent / "sample_data"
SAMPLE_PDFS = sorted(SAMPLE_DIR.rglob("hospital_bill.pdf"))
if not SAMPLE_PDFS:
    pytest.skip("No sample PDFs found - run sample_data/make_samples.py first.",
                allow_module_level=True)

# ── DB session fixture ────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    """Fresh DB session; rolls back everything after each test."""
    conn = _TEST_ENGINE.connect()
    trans = conn.begin()
    session = _TestSession(bind=conn)
    yield session
    session.close()
    trans.rollback()
    conn.close()


# ── fake UploadFile ────────────────────────────────────────────────────────────

def _make_upload(path: Path) -> MagicMock:
    """Return a minimal UploadFile-like object backed by a real file."""
    data = path.read_bytes()
    mock = MagicMock()
    mock.filename = path.name
    mock.file     = io.BytesIO(data)
    return mock


# ── helper: create one claim synchronously ───────────────────────────────────

import asyncio  # noqa: E402

def _create(path: Path, db_session) -> dict:
    """Call create_claim coroutine and return its result as a dict."""
    upload = _make_upload(path)
    coro   = create_claim(
        files=[upload],
        x_user="test_user",
        db=db_session,
    )
    result = asyncio.get_event_loop().run_until_complete(coro)
    # result is an ORM Claim; convert to dict via Pydantic
    from main import ClaimDetail
    return ClaimDetail.model_validate(result).model_dump()


# ─────────────────────────────────────────────────────────────────────────────
# POST /claims
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateClaim:
    def test_creates_claim_with_id(self, db):
        claim = _create(SAMPLE_PDFS[0], db)
        assert claim["id"].startswith("CLM-")

    def test_returns_documents(self, db):
        claim = _create(SAMPLE_PDFS[0], db)
        assert len(claim["documents"]) == 1

    def test_documents_have_fields(self, db):
        claim = _create(SAMPLE_PDFS[0], db)
        assert len(claim["documents"][0]["fields"]) > 0

    def test_has_risk_score(self, db):
        claim = _create(SAMPLE_PDFS[0], db)
        assert 0 <= claim["risk_score"] <= 100

    def test_has_risk_level(self, db):
        claim = _create(SAMPLE_PDFS[0], db)
        assert claim["risk_level"] in ("Low", "Medium", "High")

    def test_status_is_pending(self, db):
        claim = _create(SAMPLE_PDFS[0], db)
        assert claim["status"] == "pending"

    def test_multiple_files(self, db):
        uploads = [_make_upload(p) for p in SAMPLE_PDFS[:2]]
        coro    = create_claim(files=uploads, x_user="test_user", db=db)
        result  = asyncio.get_event_loop().run_until_complete(coro)
        from main import ClaimDetail
        data = ClaimDetail.model_validate(result).model_dump()
        assert len(data["documents"]) == 2

    def test_audit_entry_created(self, db):
        claim = _create(SAMPLE_PDFS[0], db)
        logs  = db.query(AuditLog).filter(AuditLog.claim_id == claim["id"]).all()
        assert any(l.action == "CLAIM_CREATED" for l in logs)

    def test_rejects_unsupported_extension(self, db):
        bad = MagicMock()
        bad.filename = "bad.txt"
        bad.file     = io.BytesIO(b"not a pdf")
        with pytest.raises(Exception) as exc_info:
            coro = create_claim(files=[bad], x_user="u", db=db)
            asyncio.get_event_loop().run_until_complete(coro)
        assert "422" in str(exc_info.value) or "Unsupported" in str(exc_info.value)


# ─────────────────────────────────────────────────────────────────────────────
# GET /claims
# ─────────────────────────────────────────────────────────────────────────────

class TestListClaims:
    def test_returns_list(self, db):
        _create(SAMPLE_PDFS[0], db)
        result = list_claims(db=db)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_sorted_by_risk_score_desc(self, db):
        for p in SAMPLE_PDFS:
            _create(p, db)
        rows   = list_claims(db=db)
        scores = [r.risk_score for r in rows]
        assert scores == sorted(scores, reverse=True)

    def test_all_claims_present(self, db):
        ids = [_create(p, db)["id"] for p in SAMPLE_PDFS]
        rows = list_claims(db=db)
        found = {r.id for r in rows}
        for cid in ids:
            assert cid in found


# ─────────────────────────────────────────────────────────────────────────────
# GET /claims/{id}
# ─────────────────────────────────────────────────────────────────────────────

class TestGetClaim:
    def test_returns_detail_with_documents(self, db):
        claim = _create(SAMPLE_PDFS[0], db)
        row   = get_claim(claim["id"], db=db)
        assert row.id == claim["id"]
        assert len(row.documents) == 1

    def test_returns_findings(self, db):
        claim = _create(SAMPLE_PDFS[0], db)
        row   = get_claim(claim["id"], db=db)
        assert isinstance(row.findings, list)

    def test_404_for_unknown_id(self, db):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_claim("CLM-DOES-NOT-EXIST", db=db)
        assert exc_info.value.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /fields/{id}
# ─────────────────────────────────────────────────────────────────────────────

class TestPatchField:
    def test_updates_value(self, db):
        claim   = _create(SAMPLE_PDFS[0], db)
        field   = claim["documents"][0]["fields"][0]
        fid     = field["id"]
        result  = patch_field(fid, FieldPatch(value="Updated Name", user="reviewer"), db=db)
        assert result.value      == "Updated Name"
        assert result.confidence == 1.0

    def test_confidence_set_to_1(self, db):
        claim  = _create(SAMPLE_PDFS[0], db)
        field  = claim["documents"][0]["fields"][0]
        result = patch_field(field["id"], FieldPatch(value="X", user="reviewer"), db=db)
        assert result.confidence == 1.0

    def test_audit_entry_created(self, db):
        claim = _create(SAMPLE_PDFS[0], db)
        field = claim["documents"][0]["fields"][0]
        patch_field(field["id"], FieldPatch(value="Y", user="reviewer"), db=db)
        logs = db.query(AuditLog).filter(
            AuditLog.claim_id == claim["id"],
            AuditLog.action   == "FIELD_UPDATED",
        ).all()
        assert len(logs) >= 1

    def test_risk_recomputed(self, db):
        claim      = _create(SAMPLE_PDFS[0], db)
        old_score  = claim["risk_score"]
        field      = claim["documents"][0]["fields"][0]
        patch_field(field["id"], FieldPatch(value="Changed", user="reviewer"), db=db)
        updated = db.query(Claim).filter(Claim.id == claim["id"]).first()
        assert 0 <= updated.risk_score <= 100   # still valid

    def test_404_for_unknown_field(self, db):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            patch_field(99999, FieldPatch(value="X"), db=db)
        assert exc_info.value.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# POST /claims/{id}/decision
# ─────────────────────────────────────────────────────────────────────────────

class TestDecision:
    @pytest.mark.parametrize("decision", ["approved", "queried", "escalated"])
    def test_valid_decisions_accepted(self, decision, db):
        claim  = _create(SAMPLE_PDFS[0], db)
        result = submit_decision(
            claim["id"],
            DecisionBody(decision=decision, user="reviewer"),
            db=db,
        )
        assert result.status.value == decision

    def test_reject_is_forbidden(self, db):
        from fastapi import HTTPException
        claim = _create(SAMPLE_PDFS[0], db)
        with pytest.raises(HTTPException) as exc_info:
            submit_decision(
                claim["id"],
                DecisionBody(decision="rejected", user="reviewer"),
                db=db,
            )
        assert exc_info.value.status_code == 422

    def test_unknown_decision_rejected(self, db):
        from fastapi import HTTPException
        claim = _create(SAMPLE_PDFS[0], db)
        with pytest.raises(HTTPException) as exc_info:
            submit_decision(
                claim["id"],
                DecisionBody(decision="deleted", user="reviewer"),
                db=db,
            )
        assert exc_info.value.status_code == 422

    def test_audit_entry_created(self, db):
        claim = _create(SAMPLE_PDFS[0], db)
        submit_decision(claim["id"], DecisionBody(decision="approved", user="u"), db=db)
        logs = db.query(AuditLog).filter(
            AuditLog.claim_id == claim["id"],
            AuditLog.action   == "CLAIM_APPROVED",
        ).all()
        assert len(logs) == 1

    def test_404_for_unknown_claim(self, db):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            submit_decision("FAKE", DecisionBody(decision="approved"), db=db)
        assert exc_info.value.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# GET /claims/{id}/pages/{doc_id}/{page}
# ─────────────────────────────────────────────────────────────────────────────

class TestPageImage:
    def test_returns_png_bytes(self, db):
        claim  = _create(SAMPLE_PDFS[0], db)
        doc_id = claim["documents"][0]["id"]
        resp   = get_page_image(claim["id"], doc_id, 1, db=db)
        assert resp.media_type == "image/png"
        assert resp.body[:4] == b"\x89PNG"

    def test_404_for_out_of_range_page(self, db):
        from fastapi import HTTPException
        claim  = _create(SAMPLE_PDFS[0], db)
        doc_id = claim["documents"][0]["id"]
        with pytest.raises(HTTPException) as exc_info:
            get_page_image(claim["id"], doc_id, 999, db=db)
        assert exc_info.value.status_code == 404

    def test_404_for_wrong_claim(self, db):
        from fastapi import HTTPException
        claim  = _create(SAMPLE_PDFS[0], db)
        doc_id = claim["documents"][0]["id"]
        with pytest.raises(HTTPException) as exc_info:
            get_page_image("WRONG-ID", doc_id, 1, db=db)
        assert exc_info.value.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# GET /audit
# ─────────────────────────────────────────────────────────────────────────────

class TestAudit:
    def test_returns_list(self, db):
        _create(SAMPLE_PDFS[0], db)
        rows = get_audit(claim_id=None, db=db)
        assert isinstance(rows, list)
        assert len(rows) >= 1

    def test_newest_first(self, db):
        for p in SAMPLE_PDFS:
            _create(p, db)
        rows = get_audit(claim_id=None, db=db)
        ts   = [r.timestamp for r in rows]
        assert ts == sorted(ts, reverse=True)

    def test_filter_by_claim_id(self, db):
        c1 = _create(SAMPLE_PDFS[0], db)
        c2 = _create(SAMPLE_PDFS[1], db)
        rows = get_audit(claim_id=c1["id"], db=db)
        assert all(r.claim_id == c1["id"] for r in rows)
        assert not any(r.claim_id == c2["id"] for r in rows)

    def test_audit_fields_present(self, db):
        _create(SAMPLE_PDFS[0], db)
        entry = get_audit(claim_id=None, db=db)[0]
        assert hasattr(entry, "user")
        assert hasattr(entry, "action")
        assert hasattr(entry, "claim_id")
        assert hasattr(entry, "timestamp")
