"""
models.py – SQLAlchemy ORM models for ClaimLens AI.

Tables
------
  Claim          – top-level claim record
  Document       – one uploaded file per row
  ExtractedField – one data point extracted from a document page
  Finding        – one risk / anomaly flag
  AuditLog       – immutable change log
"""

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime, Enum, Float, ForeignKey,
    Integer, String, Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from database import Base


# ── enum types ────────────────────────────────────────────────────────────────

class ClaimStatus(str, enum.Enum):
    pending   = "pending"
    queried   = "queried"
    escalated = "escalated"
    approved  = "approved"


class RiskLevel(str, enum.Enum):
    Low    = "Low"
    Medium = "Medium"
    High   = "High"


class DocumentType(str, enum.Enum):
    claim_form         = "claim_form"
    hospital_bill      = "hospital_bill"
    discharge_summary  = "discharge_summary"
    prescription       = "prescription"


class Severity(str, enum.Enum):
    low    = "low"
    medium = "medium"
    high   = "high"


# ── Claim ─────────────────────────────────────────────────────────────────────

class Claim(Base):
    """
    Top-level claim entity.

    risk_reasons  – stored as a JSON array of strings, e.g.
                    ["Duplicate bill number", "Policy limit 92 % exhausted"]
    file_hash     – SHA-256 of the first (or primary) uploaded file, used to
                    detect duplicate submissions before extraction runs.
    """
    __tablename__ = "claims"

    id:             Mapped[str]        = mapped_column(String(64),  primary_key=True)
    patient_name:   Mapped[str]        = mapped_column(String(256), nullable=False)
    hospital_name:  Mapped[str]        = mapped_column(String(256), nullable=False)
    # Key clinical/billing fields stored at the claim level for fast duplicate checks
    admission_date: Mapped[str | None] = mapped_column(String(32),  nullable=True)
    discharge_date: Mapped[str | None] = mapped_column(String(32),  nullable=True)
    bill_number:    Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    billed_total:   Mapped[str | None] = mapped_column(String(64),  nullable=True)
    status:        Mapped[ClaimStatus] = mapped_column(
                       Enum(ClaimStatus), nullable=False, default=ClaimStatus.pending
                   )
    risk_level:    Mapped[RiskLevel]  = mapped_column(
                       Enum(RiskLevel), nullable=False, default=RiskLevel.Low
                   )
    risk_score:    Mapped[int]        = mapped_column(Integer, nullable=False, default=0)
    # JSON array of plain-text reason strings
    risk_reasons:  Mapped[list]       = mapped_column(JSON, nullable=False, default=list)
    # SHA-256 hex digest of the primary uploaded file (duplicate detection)
    file_hash:     Mapped[str | None] = mapped_column(String(64),  nullable=True, index=True)
    created_at:    Mapped[datetime]   = mapped_column(
                       DateTime(timezone=True),
                       nullable=False,
                       default=lambda: datetime.now(timezone.utc),
                   )

    # relationships
    documents:  Mapped[list["Document"]] = relationship(
        "Document", back_populates="claim", cascade="all, delete-orphan"
    )
    findings:   Mapped[list["Finding"]]  = relationship(
        "Finding", back_populates="claim", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        "AuditLog", back_populates="claim", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Claim id={self.id!r} status={self.status!r} risk={self.risk_level!r}>"


# ── Document ──────────────────────────────────────────────────────────────────

class Document(Base):
    """
    One uploaded file attached to a claim.

    raw_text – full concatenated text extracted from all pages (PDF) or the
               placeholder string for image files.
    """
    __tablename__ = "documents"

    id:            Mapped[int]          = mapped_column(Integer, primary_key=True, autoincrement=True)
    claim_id:      Mapped[str]          = mapped_column(
                       String(64), ForeignKey("claims.id", ondelete="CASCADE"),
                       nullable=False, index=True
                   )
    filename:      Mapped[str]          = mapped_column(String(512), nullable=False)
    document_type: Mapped[DocumentType] = mapped_column(
                       Enum(DocumentType), nullable=False, default=DocumentType.hospital_bill
                   )
    page_count:    Mapped[int]          = mapped_column(Integer, nullable=False, default=1)
    raw_text:      Mapped[str | None]   = mapped_column(Text, nullable=True)

    # relationships
    claim:  Mapped["Claim"]               = relationship("Claim", back_populates="documents")
    fields: Mapped[list["ExtractedField"]] = relationship(
        "ExtractedField", back_populates="document", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Document id={self.id} claim={self.claim_id!r} file={self.filename!r}>"


# ── ExtractedField ────────────────────────────────────────────────────────────

class ExtractedField(Base):
    """
    One structured data point extracted from a document page.

    confidence – 0.0 … 1.0; below 0.6 is flagged as low-confidence in the UI.
    """
    __tablename__ = "extracted_fields"

    id:          Mapped[int]   = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int]   = mapped_column(
                     Integer, ForeignKey("documents.id", ondelete="CASCADE"),
                     nullable=False, index=True
                 )
    name:        Mapped[str]   = mapped_column(String(256), nullable=False)
    value:       Mapped[str]   = mapped_column(Text, nullable=False, default="")
    confidence:  Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    page_number: Mapped[int]   = mapped_column(Integer, nullable=False, default=1)

    # relationship
    document: Mapped["Document"] = relationship("Document", back_populates="fields")

    def __repr__(self) -> str:
        return (
            f"<ExtractedField id={self.id} doc={self.document_id}"
            f" name={self.name!r} conf={self.confidence:.2f}>"
        )


# ── Finding ───────────────────────────────────────────────────────────────────

class Finding(Base):
    """
    One risk/anomaly flag raised by a detection rule.

    evidence – stored as a JSON array of objects, each with the shape:
               {"document_filename": "...", "page_number": 1}
    weight   – numeric importance weight used when computing the aggregate
               risk_score on the parent Claim (0 … 100).
    """
    __tablename__ = "findings"

    id:               Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    claim_id:         Mapped[str]      = mapped_column(
                          String(64), ForeignKey("claims.id", ondelete="CASCADE"),
                          nullable=False, index=True
                      )
    rule_id:          Mapped[str]      = mapped_column(String(128), nullable=False)
    severity:         Mapped[Severity] = mapped_column(
                          Enum(Severity), nullable=False, default=Severity.low
                      )
    weight:           Mapped[int]      = mapped_column(Integer, nullable=False, default=10)
    what:             Mapped[str]      = mapped_column(String(512), nullable=False)
    # JSON array: [{"document_filename": str, "page_number": int}, ...]
    evidence:         Mapped[list]     = mapped_column(JSON, nullable=False, default=list)
    why_it_matters:   Mapped[str]      = mapped_column(Text, nullable=False, default="")
    suggested_action: Mapped[str]      = mapped_column(Text, nullable=False, default="")

    # relationship
    claim: Mapped["Claim"] = relationship("Claim", back_populates="findings")

    def __repr__(self) -> str:
        return (
            f"<Finding id={self.id} claim={self.claim_id!r}"
            f" rule={self.rule_id!r} sev={self.severity!r}>"
        )


# ── AuditLog ──────────────────────────────────────────────────────────────────

class AuditLog(Base):
    """
    Immutable record of every state change made to a Claim.

    before / after – arbitrary JSON snapshots of the changed fields, e.g.
                     {"status": "pending"} → {"status": "approved"}
    """
    __tablename__ = "audit_logs"

    id:        Mapped[int]          = mapped_column(Integer, primary_key=True, autoincrement=True)
    user:      Mapped[str]          = mapped_column(String(256), nullable=False)
    action:    Mapped[str]          = mapped_column(String(128), nullable=False)
    claim_id:  Mapped[str]          = mapped_column(
                   String(64), ForeignKey("claims.id", ondelete="CASCADE"),
                   nullable=False, index=True
               )
    before:    Mapped[dict | None]  = mapped_column(JSON, nullable=True)
    after:     Mapped[dict | None]  = mapped_column(JSON, nullable=True)
    timestamp: Mapped[datetime]     = mapped_column(
                   DateTime(timezone=True),
                   nullable=False,
                   default=lambda: datetime.now(timezone.utc),
               )

    # relationship
    claim: Mapped["Claim"] = relationship("Claim", back_populates="audit_logs")

    def __repr__(self) -> str:
        return (
            f"<AuditLog id={self.id} user={self.user!r}"
            f" action={self.action!r} claim={self.claim_id!r}>"
        )
