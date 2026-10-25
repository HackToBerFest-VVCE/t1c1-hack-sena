// ── ClaimLens AI – shared types ───────────────────────────────────────────────
// Field names and id types match the backend Pydantic schemas exactly.

export type ClaimStatus   = 'pending' | 'queried' | 'escalated' | 'approved'
export type RiskLevel     = 'Low' | 'Medium' | 'High'
export type DocumentType  = 'claim_form' | 'hospital_bill' | 'discharge_summary' | 'prescription'
export type Severity      = 'low' | 'medium' | 'high'

// ── Evidence reference inside a Finding ───────────────────────────────────────
// Shape returned by the backend: {"document_filename": "...", "page_number": 1}

export interface EvidenceRef {
  document_filename: string
  page_number:       number
}

// ── Field: one extracted data point from a document page ──────────────────────
// id is an integer (autoincrement PK in the DB).

export interface Field {
  id:          number
  name:        string
  value:       string
  confidence:  number   // 0 – 1
  page_number: number
}

// ── Document: a single uploaded file attached to a claim ──────────────────────
// id is an integer (autoincrement PK in the DB).
// page_image_urls is NOT returned by the backend; page images are fetched via
//   GET /claims/{claimId}/pages/{docId}/{pageNumber}
// The UI constructs those URLs at render time.

export interface Document {
  id:            number
  filename:      string
  document_type: DocumentType
  page_count:    number
  fields:        Field[]
}

// ── Finding: one risk/anomaly flag raised by a rule ───────────────────────────
// id is an integer (autoincrement PK in the DB).
// weight is included in the backend response (used for risk scoring).

export interface Finding {
  id:               number
  rule_id:          string
  severity:         Severity
  weight:           number
  what:             string
  evidence:         EvidenceRef[]
  why_it_matters:   string
  suggested_action: string
}

// ── Claim: top-level entity ───────────────────────────────────────────────────

export interface Claim {
  id:            string
  patient_name:  string
  hospital_name: string
  status:        ClaimStatus
  risk_level:    RiskLevel
  risk_score:    number      // 0 – 100
  risk_reasons:  string[]
  created_at:    string      // ISO-8601
  // present on ClaimDetail responses only (not ClaimSummary from GET /claims)
  documents?:    Document[]
  findings?:     Finding[]
}

// ── AuditEntry: immutable log of every state change ──────────────────────────
// id is an integer (autoincrement PK in the DB).

export interface AuditEntry {
  id:        number
  user:      string
  action:    string
  claim_id:  string
  before:    Record<string, unknown> | null
  after:     Record<string, unknown> | null
  timestamp: string   // ISO-8601
}
