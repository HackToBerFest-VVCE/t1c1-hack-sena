// ── ClaimLens AI – shared types ───────────────────────────────────────────────

export type ClaimStatus   = 'pending' | 'queried' | 'escalated' | 'approved'
export type RiskLevel     = 'Low' | 'Medium' | 'High'
export type DocumentType  = 'claim_form' | 'hospital_bill' | 'discharge_summary' | 'prescription'
export type Severity      = 'low' | 'medium' | 'high'

// ── Evidence reference inside a Finding ───────────────────────────────────────

export interface EvidenceRef {
  document_filename: string
  page_number:       number
}

// ── Field: one extracted data point from a document page ──────────────────────

export interface Field {
  id:          string
  name:        string
  value:       string
  confidence:  number   // 0 – 1
  page_number: number
}

// ── Document: a single uploaded file attached to a claim ──────────────────────

export interface Document {
  id:               string
  filename:         string
  document_type:    DocumentType
  page_count:       number
  page_image_urls:  string[]   // one URL per page
  fields:           Field[]
}

// ── Finding: one risk/anomaly flag raised by a rule ───────────────────────────

export interface Finding {
  id:               string
  rule_id:          string
  severity:         Severity
  what:             string           // short headline
  evidence:         EvidenceRef[]
  why_it_matters:   string
  suggested_action: string
}

// ── Claim: top-level entity ───────────────────────────────────────────────────

export interface Claim {
  id:           string
  patient_name: string
  hospital_name: string
  status:       ClaimStatus
  risk_level:   RiskLevel
  risk_score:   number      // 0 – 100
  risk_reasons: string[]
  created_at:   string      // ISO-8601
  documents:    Document[]
  findings:     Finding[]
}

// ── AuditEntry: immutable log of every state change ──────────────────────────

export interface AuditEntry {
  id:        string
  user:      string
  action:    string
  claim_id:  string
  before:    Record<string, unknown> | null
  after:     Record<string, unknown> | null
  timestamp: string   // ISO-8601
}
