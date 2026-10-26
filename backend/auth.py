"""
auth.py – Authentication, JWT management, and RBAC utilities for ClaimLens AI.
"""

from __future__ import annotations

import os
import re
import secrets
import string
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from models import User

JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 8

# Regex for email validation
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


# ── Configuration Helpers ─────────────────────────────────────────────────────

def get_jwt_secret() -> str:
    secret = os.environ.get("JWT_SECRET", "").strip()
    if not secret:
        # Fallback for dev/testing if not specified in env
        return "claimlens-dev-secret-key-change-in-production"
    return secret


def get_supervisor_invite_code() -> str:
    return os.environ.get("SUPERVISOR_INVITE_CODE", "").strip()


# ── Validation Helpers ────────────────────────────────────────────────────────

def validate_email_format(email: str) -> bool:
    if not email or len(email) > 256:
        return False
    return bool(EMAIL_REGEX.match(email.strip()))


def validate_password_strength(password: str) -> bool:
    """Password must be at least 8 characters and contain at least one letter and one number."""
    if not password or len(password) < 8:
        return False
    has_letter = any(c.isalpha() for c in password)
    has_digit = any(c.isdigit() for c in password)
    return has_letter and has_digit


# ── Password Hashing ──────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


# ── JWT Creation & Decoding ───────────────────────────────────────────────────

def create_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=JWT_EXPIRATION_HOURS)
    payload = {
        "sub": str(user.id),
        "id": user.id,
        "name": user.full_name,
        "email": user.email,
        "role": user.role,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])


# ── Rate Limiter for Login ────────────────────────────────────────────────────
# After 5 failed attempts for an email within 10 minutes, return 429 for 10 minutes.

class LoginRateLimiter:
    def __init__(self, max_attempts: int = 5, window_minutes: int = 10, lockout_minutes: int = 10):
        self.max_attempts = max_attempts
        self.window = timedelta(minutes=window_minutes)
        self.lockout_duration = timedelta(minutes=lockout_minutes)
        self._lock = threading.Lock()
        self._failed_attempts: dict[str, list[datetime]] = {}
        self._lockout_until: dict[str, datetime] = {}

    def check_allowed(self, email: str) -> None:
        clean_email = email.strip().lower()
        now = datetime.now(timezone.utc)
        with self._lock:
            # Check active lockout
            locked_until = self._lockout_until.get(clean_email)
            if locked_until:
                if now < locked_until:
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="Too many failed login attempts. Please try again in 10 minutes.",
                    )
                else:
                    # Lockout expired
                    del self._lockout_until[clean_email]
                    self._failed_attempts[clean_email] = []

            # Prune old attempts
            cutoff = now - self.window
            recent = [t for t in self._failed_attempts.get(clean_email, []) if t > cutoff]
            self._failed_attempts[clean_email] = recent

            if len(recent) >= self.max_attempts:
                self._lockout_until[clean_email] = now + self.lockout_duration
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many failed login attempts. Please try again in 10 minutes.",
                )

    def record_failure(self, email: str) -> None:
        clean_email = email.strip().lower()
        now = datetime.now(timezone.utc)
        with self._lock:
            cutoff = now - self.window
            recent = [t for t in self._failed_attempts.get(clean_email, []) if t > cutoff]
            recent.append(now)
            self._failed_attempts[clean_email] = recent

            if len(recent) >= self.max_attempts:
                self._lockout_until[clean_email] = now + self.lockout_duration
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many failed login attempts. Please try again in 10 minutes.",
                )

    def record_success(self, email: str) -> None:
        clean_email = email.strip().lower()
        with self._lock:
            self._failed_attempts.pop(clean_email, None)
            self._lockout_until.pop(clean_email, None)

    def reset(self) -> None:
        """Helper to clear state during testing."""
        with self._lock:
            self._failed_attempts.clear()
            self._lockout_until.clear()


login_limiter = LoginRateLimiter()


# ── FastAPI Dependencies ──────────────────────────────────────────────────────

def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """
    Extracts Bearer token from Authorization header and returns the User.
    Raises 401 if token is missing, invalid, expired, or user not found.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = parts[1]
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User no longer exists",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


def require_supervisor(
    current_user: User = Depends(get_current_user),
) -> User:
    """Enforces that the current authenticated user has the supervisor role."""
    if current_user.role != "supervisor":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Supervisor role required",
        )
    return current_user


# ── Demo Account Seeder ───────────────────────────────────────────────────────

def _generate_random_password(length: int = 14) -> str:
    """Generate a cryptographically secure random password with letters and digits."""
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        pwd = "".join(secrets.choice(chars) for _ in range(length))
        if any(c.isalpha() for c in pwd) and any(c.isdigit() for c in pwd):
            return pwd


def seed_demo_accounts_if_empty(db: Session) -> None:
    """
    If the users table is empty, seeds reviewer@claimlens.demo and supervisor@claimlens.demo
    with random passwords and prints them once directly to the server console.
    Never logs passwords.
    """
    count = db.query(User).count()
    if count > 0:
        return

    reviewer_pwd = _generate_random_password()
    supervisor_pwd = _generate_random_password()

    reviewer = User(
        full_name="Demo Reviewer",
        email="reviewer@claimlens.demo",
        password_hash=hash_password(reviewer_pwd),
        role="reviewer",
    )
    supervisor = User(
        full_name="Demo Supervisor",
        email="supervisor@claimlens.demo",
        password_hash=hash_password(supervisor_pwd),
        role="supervisor",
    )

    db.add(reviewer)
    db.add(supervisor)
    db.commit()

    # Printed once to server console (sys.stdout) - NOT through logging
    print("\n" + "=" * 65)
    print(" ClaimLens AI Demo Accounts Initialized")
    print("=" * 65)
    print(f" Reviewer:   reviewer@claimlens.demo   | Password: {reviewer_pwd}")
    print(f" Supervisor: supervisor@claimlens.demo | Password: {supervisor_pwd}")
    print(" Note: Demo system, synthetic data only.")
    print("=" * 65 + "\n", flush=True)
