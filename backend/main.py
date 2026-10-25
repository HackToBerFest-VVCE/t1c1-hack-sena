"""
ClaimLens AI – FastAPI backend
"""

from __future__ import annotations

import hashlib
import io
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymupdf
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from database import Base, engine, get_db
import models
from models import (
    AuditLog, Claim, ClaimStatus, Document, DocumentType,
    ExtractedField, Finding, RiskLevel, Severity,
)
from app.extraction import extract_document
from app.rules.validation import run_all_rules
from app.rules.fraud import compute_risk, run_fraud_rules, PriorClaim

# ── paths ─────────────────────────────────────────────────────────────────────

BASE_DIR    = Path(__file__).parent
UPLOADS_DIR = BASE_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}
PDF_EXT    = ".pdf"
ALLOWED_EXTS = {PDF_EXT} | IMAGE_EXTS

# ── startup ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(title="ClaimLens AI", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Pydantic response / request models ───────────────────────────────────────

class FieldOut(BaseModel):
    id:          int
    name:        str
    value:       str
    confidence:  float
    page_number: int

    model_config = ConfigDict(from_attributes=True)


class DocumentOut(BaseModel):
    id:            int
    filename:      str
    document_type: str
    page_count:    int
    fields:        list[FieldOut] = []

    model_config = ConfigDict(from_attributes=True)


class FindingOut(BaseModel):
    id:               int
    rule_id:          str
    severity:         str
    weight:           int
    what:             str
    evidence:         list[dict]
    why_it_matters:   str
    suggested_action: str

    model_config = ConfigDict(from_attributes=True)


class ClaimSummary(BaseModel):
    id:            str
    patient_name:  str
    hospital_name: str
    status:        str
    risk_level:    str
    risk_score:    int
    risk_reasons:  list[str]
    created_at:    datetime

    model_config = ConfigDict(from_attributes=True)


class ClaimDetail(ClaimSummary):
    documents: list[DocumentOut] = []
    findings:  list[FindingOut]  = []


class AuditOut(BaseModel):
    id:        int
    user:      str
    action:    str
    claim_id:  str
    before:    dict | None
    after:     dict | None
    timestamp: datetime

    model_config = ConfigDict(from_attributes=True)


class FieldPatch(BaseModel):
    value: str
    user:  str = "reviewer"


class DecisionBody(BaseModel):
    decision: str   # "approved" | "queried" | "escalated"
    comment:  str = ""
    user:     str = "reviewer"


# ── helpers ───────────────────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _audit(db: Session, user: str, action: str, claim_id: str,
           before: dict | None = None, after: dict | None = None) -> None:
    db.add(AuditLog(
        user=user, action=action, claim_id=claim_id,
        before=before, after=after,
        timestamp=datetime.now(timezone.utc),
    ))


def _doc_type_enum(raw: str) -> DocumentType:
    try:
        return DocumentType(raw)
    except ValueError:
        return DocumentType.hospital_bill


def _severity_enum(raw: str) -> Severity:
    try:
        return Severity(raw)
    except ValueError:
        return Severity.low


def _build_doc_results(docs: list[Document]) -> list[dict]:
    """Convert ORM Document + ExtractedField objects to DocResult dicts."""
    results = []
    for doc in docs:
        fields = [
            {"name": f.name, "value": f.value or "",
             "confidence": f.confidence, "page_number": f.page_number}
            for f in doc.fields
        ]
        results.append({
            "filename":      doc.filename,
            "document_type": doc.document_type.value,
            "fields":        fields,
            "pages":         [],          # raw text not needed for re-scoring
        })
    return results


def _recompute_risk(claim: Claim, db: Session) -> None:
    """Delete existing findings, re-run all rules, update risk on the claim."""
    # clear old findings
    for f in list(claim.findings):
        db.delete(f)
    db.flush()

    doc_results = _build_doc_results(claim.documents)

    # validation rules
    val_findings = run_all_rules(doc_results)

    # fraud rules – pull prior claims for F1/F2
    prior_rows = (
        db.query(Claim)
          .filter(Claim.id != claim.id)
          .all()
    )
    prior_claims: list[PriorClaim] = [
        PriorClaim(
            claim_id=c.id,
            patient_name=c.patient_name or "",
            hospital_name=c.hospital_name or "",
            admission_date=c.admission_date or "",
            total_amount=c.billed_total or "",
            file_hash=c.file_hash or "",
            bill_number=c.bill_number or "",
            billed_total=c.billed_total or "",
        )
        for c in prior_rows
    ]
    file_paths = [
        str(UPLOADS_DIR / d.filename) for d in claim.documents
    ]
    file_hashes = [_sha256(Path(p)) if Path(p).exists() else "" for p in file_paths]

    # Extract policy_start_date from the current claim's doc_results
    policy_start_date: str | None = None
    for dr in doc_results:
        for f in dr["fields"]:
            if f["name"] == "policy_start_date" and f["value"]:
                policy_start_date = f["value"]
                break
        if policy_start_date:
            break

    fraud_findings = run_fraud_rules(
        doc_results,
        prior_claims=prior_claims,
        file_hashes=file_hashes,
        file_paths=file_paths,
        policy_start_date=policy_start_date,
    )

    all_findings = val_findings + fraud_findings

    # persist new findings
    for fd in all_findings:
        # normalise evidence key: rules use "document" but schema uses "document_filename"
        evidence = [
            {"document_filename": e.get("document", e.get("document_filename", "")),
             "page_number": e.get("page", e.get("page_number", 1))}
            for e in fd.get("evidence", [])
        ]
        db.add(Finding(
            claim_id=claim.id,
            rule_id=fd["rule_id"],
            severity=_severity_enum(fd["severity"]),
            weight=fd["weight"],
            what=fd["what"],
            evidence=evidence,
            why_it_matters=fd.get("why_it_matters", ""),
            suggested_action=fd.get("suggested_action", ""),
        ))

    risk = compute_risk(all_findings)
    claim.risk_score   = risk["risk_score"]
    claim.risk_level   = RiskLevel(risk["risk_level"])
    claim.risk_reasons = risk["risk_reasons"]


# ─────────────────────────────────────────────────────────────────────────────
# POST /claims
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/claims", response_model=ClaimDetail, status_code=201)
async def create_claim(
    files: list[UploadFile] = File(...),
    x_user: str = Header(default="system"),
    db: Session = Depends(get_db),
):
    """
    Upload one or more PDF / image files. For each file:
      1. Save to /uploads
      2. Extract text and fields (extraction.py)
      3. Store Document + ExtractedField rows
    Then run validation + fraud rules and persist findings.
    Returns the full ClaimDetail.
    """
    if not files:
        raise HTTPException(422, "No files provided.")

    # ── 1. save & extract every file ────────────────────────────────────────
    claim_id  = f"CLM-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
    saved_paths: list[Path] = []
    doc_results: list[dict] = []
    orm_docs:    list[Document] = []

    for upload in files:
        raw_name = Path(upload.filename or "file").name
        suffix   = Path(raw_name).suffix.lower()
        if suffix not in ALLOWED_EXTS:
            raise HTTPException(422, f"Unsupported file type: {suffix}")

        # avoid filename collisions across claims
        dest_name = f"{claim_id}_{raw_name}"
        dest      = UPLOADS_DIR / dest_name
        with dest.open("wb") as fh:
            shutil.copyfileobj(upload.file, fh)
        saved_paths.append(dest)

        extraction = extract_document(dest)

        raw_text  = "\n\n".join(p["text"] for p in extraction["pages"])
        doc_type  = _doc_type_enum(extraction["document_type"])
        page_cnt  = len(extraction["pages"])

        orm_doc = Document(
            claim_id=claim_id,
            filename=dest_name,
            document_type=doc_type,
            page_count=page_cnt,
            raw_text=raw_text,
        )

        for fld in extraction["fields"]:
            orm_doc.fields.append(ExtractedField(
                name=fld["name"],
                value=fld["value"] or "",
                confidence=fld["confidence"],
                page_number=fld["page_number"],
            ))

        orm_docs.append(orm_doc)
        doc_results.append({
            "filename":      dest_name,
            "document_type": extraction["document_type"],
            "fields":        extraction["fields"],
            "pages":         extraction["pages"],
        })

    # ── 2. pick patient / hospital from extracted fields ─────────────────────
    def _first_val(field_name: str) -> str:
        for dr in doc_results:
            for f in dr["fields"]:
                if f["name"] == field_name and f["value"]:
                    return f["value"]
        return "Unknown"

    patient_name  = _first_val("patient_name")
    hospital_name = _first_val("hospital_name")
    admission_date = _first_val("admission_date")
    discharge_date = _first_val("discharge_date")
    bill_number    = _first_val("bill_number")
    billed_total   = _first_val("total_amount")
    primary_hash  = _sha256(saved_paths[0]) if saved_paths else None

    # ── 3. create Claim row ──────────────────────────────────────────────────
    claim = Claim(
        id=claim_id,
        patient_name=patient_name,
        hospital_name=hospital_name,
        admission_date=admission_date or None,
        discharge_date=discharge_date or None,
        bill_number=bill_number or None,
        billed_total=billed_total or None,
        status=ClaimStatus.pending,
        risk_level=RiskLevel.Low,
        risk_score=0,
        risk_reasons=[],
        file_hash=primary_hash,
        created_at=datetime.now(timezone.utc),
    )
    for orm_doc in orm_docs:
        claim.documents.append(orm_doc)

    db.add(claim)
    db.flush()   # assign IDs before running rules

    # ── 4. run rules & persist findings ─────────────────────────────────────
    _recompute_risk(claim, db)

    # ── 5. audit ─────────────────────────────────────────────────────────────
    _audit(db, x_user, "CLAIM_CREATED", claim_id,
           before=None,
           after={"patient": patient_name, "hospital": hospital_name,
                  "files": [p.name for p in saved_paths]})
    db.commit()
    db.refresh(claim)
    return claim


# ─────────────────────────────────────────────────────────────────────────────
# GET /claims
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/claims", response_model=list[ClaimSummary])
def list_claims(db: Session = Depends(get_db)):
    """Return all claims sorted by risk_score descending."""
    return (
        db.query(Claim)
          .order_by(Claim.risk_score.desc(), Claim.created_at.desc())
          .all()
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /claims/{id}
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/claims/{claim_id}", response_model=ClaimDetail)
def get_claim(claim_id: str, db: Session = Depends(get_db)):
    claim = db.query(Claim).filter(Claim.id == claim_id).first()
    if not claim:
        raise HTTPException(404, f"Claim {claim_id!r} not found.")
    return claim


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /fields/{id}
# ─────────────────────────────────────────────────────────────────────────────

@app.patch("/fields/{field_id}", response_model=FieldOut)
def patch_field(
    field_id: int,
    body: FieldPatch,
    db: Session = Depends(get_db),
):
    """
    Overwrite an extracted field value (reviewer correction).
    Re-runs all rules on the parent claim and updates risk score.
    """
    field = db.query(ExtractedField).filter(ExtractedField.id == field_id).first()
    if not field:
        raise HTTPException(404, f"Field {field_id} not found.")

    old_value = field.value
    field.value      = body.value
    field.confidence = 1.0   # human-corrected → full confidence

    claim = field.document.claim
    _audit(db, body.user, "FIELD_UPDATED", claim.id,
           before={"field_id": field_id, "name": field.name, "value": old_value},
           after={"field_id":  field_id, "name": field.name, "value": body.value})

    db.flush()
    _recompute_risk(claim, db)
    db.commit()
    db.refresh(field)
    return field


# ─────────────────────────────────────────────────────────────────────────────
# POST /claims/{id}/decision
# ─────────────────────────────────────────────────────────────────────────────

ALLOWED_DECISIONS = {"approved", "queried", "escalated"}


@app.post("/claims/{claim_id}/decision", response_model=ClaimDetail)
def submit_decision(
    claim_id: str,
    body: DecisionBody,
    db: Session = Depends(get_db),
):
    """
    Set the adjudication decision. Only 'approved', 'queried', 'escalated'
    are accepted. 'rejected' is explicitly forbidden.
    """
    if body.decision not in ALLOWED_DECISIONS:
        raise HTTPException(
            422,
            f"Decision '{body.decision}' is not allowed. "
            f"Valid values: {sorted(ALLOWED_DECISIONS)}."
        )

    claim = db.query(Claim).filter(Claim.id == claim_id).first()
    if not claim:
        raise HTTPException(404, f"Claim {claim_id!r} not found.")

    old_status = claim.status.value
    claim.status = ClaimStatus(body.decision)

    action_label = f"CLAIM_{body.decision.upper()}"
    _audit(db, body.user, action_label, claim_id,
           before={"status": old_status},
           after={"status": body.decision, "comment": body.comment})

    db.commit()
    db.refresh(claim)
    return claim


# ─────────────────────────────────────────────────────────────────────────────
# GET /claims/{id}/pages/{doc_id}/{page}
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/claims/{claim_id}/pages/{doc_id}/{page_number}")
def get_page_image(
    claim_id: str,
    doc_id:   int,
    page_number: int,
    db: Session = Depends(get_db),
):
    """
    Render a single PDF page as a PNG image using PyMuPDF.
    Returns Content-Type: image/png.
    Image files return a 1×1 placeholder PNG.
    """
    doc = db.query(Document).filter(
        Document.id == doc_id,
        Document.claim_id == claim_id,
    ).first()
    if not doc:
        raise HTTPException(404, "Document not found.")

    if page_number < 1 or page_number > doc.page_count:
        raise HTTPException(404, f"Page {page_number} out of range (1–{doc.page_count}).")

    file_path = UPLOADS_DIR / doc.filename
    suffix    = Path(doc.filename).suffix.lower()

    # ── image file: return a tiny placeholder PNG ──────────────────────────
    if suffix in IMAGE_EXTS:
        # 1×1 grey PNG
        placeholder = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x00\x00\x00\x00:~\x9bU\x00\x00\x00\n"
            b"IDAT\x08\xd7c\xf8\x0f\x00\x00\x01\x01\x00\x18\xdd\x8d\xb4"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        return Response(content=placeholder, media_type="image/png")

    if not file_path.exists():
        raise HTTPException(404, f"File {doc.filename!r} not found on disk.")

    try:
        pdf     = pymupdf.open(str(file_path))
        pg      = pdf[page_number - 1]          # 0-indexed
        mat     = pymupdf.Matrix(2, 2)           # 2× zoom → ~150 dpi
        pixmap  = pg.get_pixmap(matrix=mat, alpha=False)
        png_bytes = pixmap.tobytes("png")
        pdf.close()
    except Exception as exc:
        raise HTTPException(500, f"Failed to render page: {exc}") from exc

    return Response(content=png_bytes, media_type="image/png")


# ─────────────────────────────────────────────────────────────────────────────
# GET /audit
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/audit", response_model=list[AuditOut])
def get_audit(
    claim_id: str | None = None,
    db: Session = Depends(get_db),
):
    """
    Return audit log entries, newest first.
    Optionally filter by claim_id query parameter.
    """
    q = db.query(AuditLog)
    if claim_id:
        q = q.filter(AuditLog.claim_id == claim_id)
    return q.order_by(AuditLog.timestamp.desc()).all()
