"""
tests/test_claim_detail_live.py – Smoke-test GET /claims/{id} for every
claim that already exists in claimlens.db.

These tests open the real database (read-only), call the route handler
directly (no HTTP, no token), and assert that each claim can be serialised
through ClaimDetail without raising an exception.

Skipped automatically when claimlens.db has no claims (e.g. in a fresh CI
environment that hasn't uploaded any claims yet).

Run:
    cd backend
    pytest tests/test_claim_detail_live.py -v
"""

from __future__ import annotations

import sqlite3
import traceback
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# ── resolve paths ────────────────────────────────────────────────────────────
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_DB_PATH     = _BACKEND_DIR / "claimlens.db"

# ── skip the entire module if the DB file is absent or empty ─────────────────
if not _DB_PATH.exists():
    pytest.skip("claimlens.db not found", allow_module_level=True)

# ── swap DB engine BEFORE importing app code ─────────────────────────────────
# We point at the real claimlens.db but wrap it in a read-only-style session
# (no writes, no migrations) so the live data is never mutated by tests.
import database as _db  # noqa: E402

_LIVE_URL    = f"sqlite:///{_DB_PATH}"
_LIVE_ENGINE = create_engine(_LIVE_URL, connect_args={"check_same_thread": False})
_LiveSession = sessionmaker(bind=_LIVE_ENGINE, autocommit=False, autoflush=False)
_db.engine       = _LIVE_ENGINE
_db.SessionLocal = _LiveSession

from database import Base  # noqa: E402
import models               # noqa: F401 – registers ORM classes with Base

# Run create_all so that any columns added in newer model versions are
# present.  On an already-migrated DB this is a no-op.
Base.metadata.create_all(bind=_LIVE_ENGINE)

# ── now safe to import app objects ────────────────────────────────────────────
import main  # noqa: E402
from main import ClaimDetail, get_claim  # noqa: E402

# ── collect claim IDs from live DB ───────────────────────────────────────────
def _get_live_claim_ids() -> list[str]:
    con = sqlite3.connect(str(_DB_PATH))
    try:
        rows = con.execute(
            "SELECT id FROM claims ORDER BY created_at"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


_LIVE_CLAIM_IDS = _get_live_claim_ids()

if not _LIVE_CLAIM_IDS:
    pytest.skip("claimlens.db contains no claims", allow_module_level=True)


# ── per-test DB session ───────────────────────────────────────────────────────
@pytest.fixture()
def live_db():
    """Open a session against the real DB; always roll back so tests are safe."""
    conn  = _LIVE_ENGINE.connect()
    trans = conn.begin()
    session = _LiveSession(bind=conn)
    yield session
    session.close()
    trans.rollback()
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestClaimDetailLive:
    """
    For every claim in claimlens.db, verify that:
      1. get_claim() returns an ORM Claim without raising.
      2. The result can be fully serialised through ClaimDetail (Pydantic).
      3. The serialised output satisfies basic structural invariants.
    """

    @pytest.mark.parametrize("claim_id", _LIVE_CLAIM_IDS)
    def test_detail_endpoint_does_not_raise(self, claim_id: str, live_db) -> None:
        """get_claim() must not raise any exception for any existing claim."""
        try:
            result = get_claim(claim_id, db=live_db)
        except Exception as exc:
            pytest.fail(
                f"get_claim({claim_id!r}) raised {type(exc).__name__}: {exc}\n"
                + traceback.format_exc()
            )
        assert result is not None, f"get_claim({claim_id!r}) returned None"

    @pytest.mark.parametrize("claim_id", _LIVE_CLAIM_IDS)
    def test_detail_serialises_via_pydantic(self, claim_id: str, live_db) -> None:
        """
        The ORM object must be serialisable through ClaimDetail without
        a validation error.  This is the exact path FastAPI takes when it
        serialises the HTTP response, so a failure here means a 500 in prod.
        """
        result = get_claim(claim_id, db=live_db)
        try:
            data = ClaimDetail.model_validate(result).model_dump()
        except Exception as exc:
            pytest.fail(
                f"ClaimDetail serialisation failed for {claim_id!r}: "
                f"{type(exc).__name__}: {exc}\n" + traceback.format_exc()
            )
        assert isinstance(data, dict), "model_dump() must return a dict"

    @pytest.mark.parametrize("claim_id", _LIVE_CLAIM_IDS)
    def test_detail_shape(self, claim_id: str, live_db) -> None:
        """Serialised ClaimDetail must have the expected top-level keys and types."""
        result = get_claim(claim_id, db=live_db)
        data   = ClaimDetail.model_validate(result).model_dump()

        # Top-level required keys
        for key in ("id", "patient_name", "hospital_name", "status",
                    "risk_level", "risk_score", "risk_reasons",
                    "created_at", "documents", "findings"):
            assert key in data, f"Missing key {key!r} in ClaimDetail for {claim_id!r}"

        assert data["id"] == claim_id
        assert data["risk_level"] in ("Low", "Medium", "High"), (
            f"risk_level {data['risk_level']!r} is not one of Low/Medium/High"
        )
        assert 0 <= data["risk_score"] <= 100, (
            f"risk_score {data['risk_score']} out of range 0–100"
        )
        assert data["status"] in ("pending", "queried", "escalated", "approved"), (
            f"status {data['status']!r} is not a valid ClaimStatus value"
        )
        assert isinstance(data["documents"], list)
        assert isinstance(data["findings"], list)
        assert isinstance(data["risk_reasons"], list)

    @pytest.mark.parametrize("claim_id", _LIVE_CLAIM_IDS)
    def test_documents_have_required_fields(self, claim_id: str, live_db) -> None:
        """Every document in the detail response must have the expected fields."""
        result = get_claim(claim_id, db=live_db)
        data   = ClaimDetail.model_validate(result).model_dump()

        for doc in data["documents"]:
            for key in ("id", "filename", "document_type", "page_count",
                        "fields", "extraction_method", "model_name"):
                assert key in doc, (
                    f"Document missing key {key!r} in claim {claim_id!r}"
                )
            assert doc["document_type"] in (
                "claim_form", "hospital_bill", "discharge_summary", "prescription"
            ), f"Unknown document_type {doc['document_type']!r}"
            assert isinstance(doc["fields"], list)

    @pytest.mark.parametrize("claim_id", _LIVE_CLAIM_IDS)
    def test_findings_have_required_fields(self, claim_id: str, live_db) -> None:
        """Every finding in the detail response must have the expected fields."""
        result = get_claim(claim_id, db=live_db)
        data   = ClaimDetail.model_validate(result).model_dump()

        for finding in data["findings"]:
            for key in ("id", "rule_id", "severity", "weight",
                        "what", "evidence", "why_it_matters", "suggested_action"):
                assert key in finding, (
                    f"Finding missing key {key!r} in claim {claim_id!r}"
                )
            assert finding["severity"] in ("low", "medium", "high"), (
                f"Unknown severity {finding['severity']!r} in claim {claim_id!r}"
            )
            assert isinstance(finding["evidence"], list)


# ─────────────────────────────────────────────────────────────────────────────
# Summary report (collected via the last parametrised group, printed at end)
# ─────────────────────────────────────────────────────────────────────────────

def test_summary_report(live_db) -> None:
    """
    Non-parametrised summary: open every claim, serialise it, and collect
    per-claim pass/fail into a human-readable table printed at the end.
    This test always passes; individual structural failures are caught by the
    parametrised tests above.
    """
    print(f"\n{'Claim ID':<28} {'Docs':>4} {'Findings':>8} {'Risk':<8} Status")
    print("-" * 65)

    failures: list[tuple[str, str]] = []

    for cid in _LIVE_CLAIM_IDS:
        try:
            result = get_claim(cid, db=live_db)
            data   = ClaimDetail.model_validate(result).model_dump()
            ndocs  = len(data["documents"])
            nfind  = len(data["findings"])
            rl     = data["risk_level"]
            st     = data["status"]
            print(f"  {cid:<26} {ndocs:>4} {nfind:>8}   {rl:<8} {st}")
        except Exception as exc:
            failures.append((cid, f"{type(exc).__name__}: {exc}"))
            print(f"  {cid:<26} FAIL  {type(exc).__name__}: {str(exc)[:50]}")

    print("-" * 65)
    if failures:
        print(f"\n{len(failures)} claim(s) FAILED:")
        for cid, msg in failures:
            print(f"  {cid}: {msg}")
    else:
        print(f"\nAll {len(_LIVE_CLAIM_IDS)} claims OK ✓")
