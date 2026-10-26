"""
tests/test_auth.py – Comprehensive unit & integration tests for authentication and RBAC.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Setup test in-memory SQLite database
import database as _db
_TEST_URL = "sqlite:///:memory:"
_TEST_ENGINE = create_engine(_TEST_URL, connect_args={"check_same_thread": False})
_TestSession = sessionmaker(bind=_TEST_ENGINE, autocommit=False, autoflush=False)
_db.engine = _TEST_ENGINE
_db.SessionLocal = _TestSession

from database import Base
import models
from models import User, Claim, ClaimStatus, AuditLog
Base.metadata.create_all(bind=_TEST_ENGINE)

import auth
from auth import (
    hash_password,
    verify_password,
    validate_email_format,
    validate_password_strength,
    create_access_token,
    decode_access_token,
    login_limiter,
    get_current_user,
    require_supervisor,
    seed_demo_accounts_if_empty,
)
import main
from main import (
    signup,
    login,
    get_me,
    submit_decision,
    SignupRequest,
    LoginRequest,
    DecisionBody,
)


@pytest.fixture(autouse=True)
def setup_env_and_limiter(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-12345678901234567890-32bytes")
    monkeypatch.setenv("SUPERVISOR_INVITE_CODE", "super-secret-code")
    login_limiter.reset()


@pytest.fixture()
def db():
    conn = _TEST_ENGINE.connect()
    trans = conn.begin()
    session = _TestSession(bind=conn)
    yield session
    session.close()
    trans.rollback()
    conn.close()


# ── 1. Validation & Cryptography Unit Tests ───────────────────────────────────

class TestCryptoAndValidation:
    def test_email_validation(self):
        assert validate_email_format("user@example.com") is True
        assert validate_email_format("user.name+tag@sub.domain.org") is True
        assert validate_email_format("invalid-email") is False
        assert validate_email_format("@missingusername.com") is False
        assert validate_email_format("user@.com") is False
        assert validate_email_format("") is False

    def test_password_strength_validation(self):
        assert validate_password_strength("Abcdef12") is True
        assert validate_password_strength("short1A") is False   # < 8 chars
        assert validate_password_strength("alllettersonly") is False
        assert validate_password_strength("1234567890") is False

    def test_password_hashing_and_verification(self):
        pwd = "SecurePassword123"
        hashed = hash_password(pwd)
        assert hashed != pwd
        assert verify_password(pwd, hashed) is True
        assert verify_password("WrongPassword123", hashed) is False

    def test_jwt_creation_and_decoding(self, db):
        user = User(
            id=42,
            full_name="Alice Smith",
            email="alice@claimlens.demo",
            password_hash=hash_password("Password123"),
            role="supervisor",
        )
        token = create_access_token(user)
        payload = decode_access_token(token)
        assert payload["id"] == 42
        assert payload["name"] == "Alice Smith"
        assert payload["email"] == "alice@claimlens.demo"
        assert payload["role"] == "supervisor"
        assert payload["sub"] == "42"
        assert payload["exp"] > payload["iat"]


# ── 2. POST /auth/signup ──────────────────────────────────────────────────────

class TestSignup:
    def test_signup_reviewer_default(self, db):
        req = SignupRequest(
            full_name="John Doe",
            email="John.Doe@Example.com",
            password="Password123",
            confirm_password="Password123",
        )
        res = signup(req, db=db)
        assert res["access_token"]
        assert res["token_type"] == "bearer"
        assert res["user"]["name"] == "John Doe"
        assert res["user"]["email"] == "john.doe@example.com"  # Lowercase
        assert res["user"]["role"] == "reviewer"

        # Check DB directly
        db_user = db.query(User).filter(User.email == "john.doe@example.com").first()
        assert db_user is not None
        assert db_user.role == "reviewer"
        assert verify_password("Password123", db_user.password_hash)

    def test_signup_supervisor_with_valid_invite_code(self, db):
        req = SignupRequest(
            full_name="Super User",
            email="supervisor@example.com",
            password="Password123",
            confirm_password="Password123",
            invite_code="super-secret-code",
        )
        res = signup(req, db=db)
        assert res["user"]["role"] == "supervisor"

    def test_signup_with_invalid_invite_code_becomes_reviewer(self, db):
        req = SignupRequest(
            full_name="Regular User",
            email="regular@example.com",
            password="Password123",
            confirm_password="Password123",
            invite_code="wrong-code",
        )
        res = signup(req, db=db)
        assert res["user"]["role"] == "reviewer"

    def test_signup_password_mismatch(self, db):
        req = SignupRequest(
            full_name="Mismatch User",
            email="mismatch@example.com",
            password="Password123",
            confirm_password="Password456",
        )
        with pytest.raises(HTTPException) as exc_info:
            signup(req, db=db)
        assert exc_info.value.status_code == 422
        assert "Passwords do not match" in exc_info.value.detail

    def test_signup_weak_password(self, db):
        req = SignupRequest(
            full_name="Weak User",
            email="weak@example.com",
            password="weak",
            confirm_password="weak",
        )
        with pytest.raises(HTTPException) as exc_info:
            signup(req, db=db)
        assert exc_info.value.status_code == 422
        assert "at least 8 characters" in exc_info.value.detail

    def test_signup_duplicate_email(self, db):
        req = SignupRequest(
            full_name="User One",
            email="dupe@example.com",
            password="Password123",
            confirm_password="Password123",
        )
        signup(req, db=db)

        # Attempt same email with different casing
        req2 = SignupRequest(
            full_name="User Two",
            email="DUPE@example.com",
            password="Password123",
            confirm_password="Password123",
        )
        with pytest.raises(HTTPException) as exc_info:
            signup(req2, db=db)
        assert exc_info.value.status_code == 400
        assert "already registered" in exc_info.value.detail


# ── 3. POST /auth/login & Rate Limiting ───────────────────────────────────────

class TestLogin:
    def test_login_success(self, db):
        # Create user
        signup(SignupRequest(
            full_name="Active User",
            email="active@example.com",
            password="Password123",
            confirm_password="Password123",
        ), db=db)

        res = login(LoginRequest(email="Active@Example.com", password="Password123"), db=db)
        assert res["access_token"]
        assert res["user"]["email"] == "active@example.com"

    def test_login_unknown_email_generic_error(self, db):
        with pytest.raises(HTTPException) as exc_info:
            login(LoginRequest(email="unknown@example.com", password="Password123"), db=db)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid email or password"

    def test_login_wrong_password_generic_error(self, db):
        signup(SignupRequest(
            full_name="Active User",
            email="active2@example.com",
            password="Password123",
            confirm_password="Password123",
        ), db=db)

        with pytest.raises(HTTPException) as exc_info:
            login(LoginRequest(email="active2@example.com", password="WrongPassword999"), db=db)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid email or password"

    def test_login_rate_limiting_after_5_failures(self, db):
        email = "brute@example.com"
        # 4 failed attempts
        for _ in range(4):
            with pytest.raises(HTTPException) as exc:
                login(LoginRequest(email=email, password="BadPassword1"), db=db)
            assert exc.value.status_code == 401

        # 5th failed attempt triggers 429
        with pytest.raises(HTTPException) as exc:
            login(LoginRequest(email=email, password="BadPassword1"), db=db)
        assert exc.value.status_code == 429
        assert "Too many failed login attempts" in exc.value.detail

        # 6th attempt is also locked out with 429
        with pytest.raises(HTTPException) as exc:
            login(LoginRequest(email=email, password="BadPassword1"), db=db)
        assert exc.value.status_code == 429


# ── 4. GET /auth/me & get_current_user ────────────────────────────────────────

class TestAuthMeAndDependency:
    def test_get_current_user_valid_token(self, db):
        user = User(
            full_name="Token User",
            email="token@example.com",
            password_hash=hash_password("Password123"),
            role="reviewer",
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        token = create_access_token(user)
        resolved_user = get_current_user(authorization=f"Bearer {token}", db=db)
        assert resolved_user.id == user.id
        assert resolved_user.email == "token@example.com"

    def test_get_current_user_missing_or_invalid_token(self, db):
        with pytest.raises(HTTPException) as exc:
            get_current_user(authorization=None, db=db)
        assert exc.value.status_code == 401

        with pytest.raises(HTTPException) as exc:
            get_current_user(authorization="InvalidHeader", db=db)
        assert exc.value.status_code == 401

        with pytest.raises(HTTPException) as exc:
            get_current_user(authorization="Bearer bad.token.here", db=db)
        assert exc.value.status_code == 401

    def test_get_me_route(self, db):
        user = User(
            id=10,
            full_name="Me User",
            email="me@example.com",
            password_hash="hash",
            role="supervisor",
        )
        res = get_me(current_user=user)
        assert res == {
            "name": "Me User",
            "email": "me@example.com",
            "role": "supervisor",
        }


# ── 5. RBAC & Audit Log on Decision ──────────────────────────────────────────

class TestRBACAndDecision:
    def test_require_supervisor_passes_for_supervisor(self):
        sup_user = User(id=1, full_name="Sup", email="s@demo.com", password_hash="h", role="supervisor")
        assert require_supervisor(sup_user) == sup_user

    def test_require_supervisor_fails_for_reviewer(self):
        rev_user = User(id=2, full_name="Rev", email="r@demo.com", password_hash="h", role="reviewer")
        with pytest.raises(HTTPException) as exc:
            require_supervisor(rev_user)
        assert exc.value.status_code == 403

    def test_reviewer_can_query_claim(self, db):
        claim = Claim(
            id="CLM-TEST-001",
            patient_name="Patient A",
            hospital_name="Hospital B",
            status=ClaimStatus.pending,
            risk_score=10,
            risk_reasons=[],
            created_at=datetime.now(timezone.utc),
        )
        db.add(claim)
        db.commit()

        rev_user = User(id=3, full_name="Reviewer Bob", email="bob@demo.com", password_hash="h", role="reviewer")
        res = submit_decision(
            claim_id="CLM-TEST-001",
            body=DecisionBody(decision="queried", comment="Needs check", user="spoofed_name"),
            current_user=rev_user,
            db=db,
        )
        assert res.status == ClaimStatus.queried

        # Check audit log records actor from token ('Reviewer Bob'), NOT 'spoofed_name'
        audit = db.query(AuditLog).filter(AuditLog.claim_id == "CLM-TEST-001").first()
        assert audit is not None
        assert audit.user == "Reviewer Bob"

    def test_reviewer_blocked_from_approve_and_escalate(self, db):
        claim = Claim(
            id="CLM-TEST-002",
            patient_name="Patient A",
            hospital_name="Hospital B",
            status=ClaimStatus.pending,
            risk_score=10,
            risk_reasons=[],
            created_at=datetime.now(timezone.utc),
        )
        db.add(claim)
        db.commit()

        rev_user = User(id=4, full_name="Reviewer Bob", email="bob@demo.com", password_hash="h", role="reviewer")

        with pytest.raises(HTTPException) as exc:
            submit_decision(
                claim_id="CLM-TEST-002",
                body=DecisionBody(decision="approved"),
                current_user=rev_user,
                db=db,
            )
        assert exc.value.status_code == 403

        with pytest.raises(HTTPException) as exc:
            submit_decision(
                claim_id="CLM-TEST-002",
                body=DecisionBody(decision="escalated"),
                current_user=rev_user,
                db=db,
            )
        assert exc.value.status_code == 403

    def test_supervisor_can_approve_and_escalate(self, db):
        claim = Claim(
            id="CLM-TEST-003",
            patient_name="Patient A",
            hospital_name="Hospital B",
            status=ClaimStatus.pending,
            risk_score=10,
            risk_reasons=[],
            created_at=datetime.now(timezone.utc),
        )
        db.add(claim)
        db.commit()

        sup_user = User(id=5, full_name="Supervisor Carol", email="carol@demo.com", password_hash="h", role="supervisor")
        res = submit_decision(
            claim_id="CLM-TEST-003",
            body=DecisionBody(decision="approved", comment="Looks good"),
            current_user=sup_user,
            db=db,
        )
        assert res.status == ClaimStatus.approved

        # Check audit actor
        audit = db.query(AuditLog).filter(AuditLog.claim_id == "CLM-TEST-003").first()
        assert audit.user == "Supervisor Carol"


# ── 6. Demo Account Seeder ───────────────────────────────────────────────────

class TestDemoAccountSeeder:
    def test_seeds_accounts_when_empty(self, db):
        assert db.query(User).count() == 0
        seed_demo_accounts_if_empty(db)
        assert db.query(User).count() == 2

        reviewer = db.query(User).filter(User.email == "reviewer@claimlens.demo").first()
        supervisor = db.query(User).filter(User.email == "supervisor@claimlens.demo").first()

        assert reviewer is not None
        assert reviewer.role == "reviewer"
        assert supervisor is not None
        assert supervisor.role == "supervisor"

        # Calling again should not re-seed or duplicate
        seed_demo_accounts_if_empty(db)
        assert db.query(User).count() == 2
