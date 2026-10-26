"""
normalise.py – Field-value normalisation for ClaimLens rule engine.

Before any rule compares extracted values, call the helpers here to strip
currency prefixes, remove formatting noise, and canonicalise dates and names.
This prevents "INR 32,071/-" from failing to match "Rs. 32,071.00" simply
because different hospital templates use different amount styles.

Public helpers
--------------
    normalise_amount(raw) -> float | None
        Strips "Rs.", "INR", commas, "/–" and converts to float.

    normalise_date(raw) -> datetime | None
        Parses Indian date strings (dd-Mon-yyyy, dd/mm/yyyy, ISO …).
        Returns a timezone-naive datetime or None.

    normalise_name(raw) -> str
        Lowercases, collapses whitespace, strips leading/trailing spaces.
        Does NOT expand initials – callers use fuzzy matching for that.

    patch_doc_fields(doc) -> DocResult
        Returns a *copy* of a DocResult with every field value replaced by
        its normalised form (amounts → "Rs. X,XXX.XX"; dates → "dd-Mon-YYYY";
        names trimmed/casefolded).  The original dict is not mutated.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.rules.validation import DocResult


# ── amount normalisation ──────────────────────────────────────────────────────

# Matches the numeric part of strings like:
#   "Rs. 32,071.25"   "INR 1,40,651.25"   "32071/-"   "12,450/–"
#   "₹12,450.75"      "32000"
_AMOUNT_RE = re.compile(
    r"""
    (?:Rs\.?|INR|₹)?          # optional currency prefix
    \s*
    ([\d,]+                   # integer part (may have commas)
     (?:\.\d{1,2})?)          # optional decimal
    \s*
    (?:/-|/–)?                # optional slash suffix (Indian notation)
    """,
    re.VERBOSE | re.IGNORECASE,
)


def normalise_amount(raw: str) -> float | None:
    """
    Parse any common Indian rupee amount format to a Python float.

    >>> normalise_amount("Rs. 32,071.25")
    32071.25
    >>> normalise_amount("INR 1,40,651.25")
    140651.25
    >>> normalise_amount("32071/-")
    32071.0
    >>> normalise_amount("₹12,450.75")
    12450.75
    """
    if not raw:
        return None
    raw = raw.strip()
    m = _AMOUNT_RE.search(raw)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


# ── date normalisation ────────────────────────────────────────────────────────

_DATE_FORMATS = (
    "%d-%b-%Y",    # 14-Nov-2024
    "%d/%b/%Y",
    "%d-%b-%y",    # 14-Nov-24
    "%d/%b/%y",
    "%d-%m-%Y",    # 14-11-2024
    "%d/%m/%Y",
    "%d-%m-%y",
    "%d/%m/%y",
    "%Y-%m-%d",    # ISO 8601
)

# Normalise common separators to "-" before trying strptime
_SEP_RE = re.compile(r"[./]")


def normalise_date(raw: str) -> datetime | None:
    """
    Parse an Indian date string to a timezone-naive datetime.

    Accepts separators '-', '/', '.'.  Returns None when the string cannot
    be parsed with any known format.
    """
    if not raw:
        return None
    normalised = _SEP_RE.sub("-", raw.strip())
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(normalised, fmt)
        except ValueError:
            continue
    # Try ISO fromisoformat as a last resort
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.replace(tzinfo=None)
    except ValueError:
        return None


def date_to_iso(raw: str) -> str:
    """Return "YYYY-MM-DD" for the input, or the original string on failure."""
    dt = normalise_date(raw)
    return dt.strftime("%Y-%m-%d") if dt else raw


# ── name normalisation ────────────────────────────────────────────────────────

_WHITESPACE_RE = re.compile(r"\s+")


def normalise_name(raw: str) -> str:
    """
    Casefolded, whitespace-collapsed name suitable for fuzzy comparison.

    >>> normalise_name("  Vikram  R.  Mehta  ")
    'vikram r. mehta'
    """
    if not raw:
        return ""
    return _WHITESPACE_RE.sub(" ", raw.strip()).casefold()


# ── per-document normalisation ────────────────────────────────────────────────

# Fields whose values should be normalised as amounts
_AMOUNT_FIELDS = {"total_amount", "claimed_amount", "billed_total"}

# Fields whose values should be normalised as dates
_DATE_FIELDS = {
    "admission_date", "discharge_date", "bill_date",
    "policy_start_date", "date_of_birth",
}

# Fields whose values should be normalised as names
_NAME_FIELDS = {"patient_name", "hospital_name"}


def _normalise_field_value(name: str, value: str) -> str:
    """Return a cleaned value based on the field's semantic type."""
    if not value:
        return value

    if name in _AMOUNT_FIELDS:
        amt = normalise_amount(value)
        return f"Rs. {amt:,.2f}" if amt is not None else value

    if name in _DATE_FIELDS:
        dt = normalise_date(value)
        return dt.strftime("%d-%b-%Y") if dt is not None else value

    if name in _NAME_FIELDS:
        # Preserve display case; only strip/collapse whitespace (not casefold)
        return _WHITESPACE_RE.sub(" ", value.strip())

    return value.strip()


def patch_doc_fields(doc: "DocResult") -> "DocResult":
    """
    Return a deep copy of *doc* with every field's value replaced by its
    normalised form.  The original is never mutated.

    This should be called once per document before any rule runs.
    """
    patched = copy.deepcopy(doc)
    for field in patched["fields"]:
        old_value = field.get("value") or ""
        field["value"] = _normalise_field_value(field["name"], old_value)
    return patched


def patch_all_docs(docs: list["DocResult"]) -> list["DocResult"]:
    """
    Convenience wrapper: normalise every document in a list and return
    the patched list.  The originals are not mutated.
    """
    return [patch_doc_fields(d) for d in docs]
