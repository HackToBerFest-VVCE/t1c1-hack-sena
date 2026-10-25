// ── ClaimLens AI – API layer ───────────────────────────────────────────────────
//
// All calls hit the real FastAPI backend at http://localhost:8000.
// Every mutating request carries x-user: reviewer so the audit log is populated.
// Mock paths are kept behind USE_MOCK = false for emergency fallback only.

import axios from 'axios'
import { MOCK_CLAIMS, MOCK_AUDIT } from './mockData'
import type { Claim, AuditEntry, Field } from './types'

const USE_MOCK    = false
const BACKEND_URL = 'http://localhost:8000'
const X_USER      = 'reviewer'

// ── axios instance with default x-user header ─────────────────────────────────

const api = axios.create({
  baseURL: BACKEND_URL,
  headers: { 'x-user': X_USER },
})

// ── helpers ───────────────────────────────────────────────────────────────────

function delay(ms = 400): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}

function notFound(id: string): never {
  throw new Error(`Claim "${id}" not found.`)
}

// Tiny in-memory store so mutations persist within a session when using mocks
let _claims: Claim[] = structuredClone(MOCK_CLAIMS)

// ── getClaims ─────────────────────────────────────────────────────────────────

/** Return all claims (summary list). Sorted by risk_score desc by the backend. */
export async function getClaims(): Promise<Claim[]> {
  if (USE_MOCK) {
    await delay()
    return structuredClone(_claims)
  }
  // GET /claims  →  list[ClaimSummary]
  const { data } = await api.get<Claim[]>('/claims')
  return data
}

// ── getClaim ──────────────────────────────────────────────────────────────────

/** Return a single claim with full documents and findings. */
export async function getClaim(id: string): Promise<Claim> {
  if (USE_MOCK) {
    await delay()
    const claim = _claims.find(c => c.id === id)
    if (!claim) notFound(id)
    return structuredClone(claim)
  }
  // GET /claims/{id}  →  ClaimDetail
  const { data } = await api.get<Claim>(`/claims/${id}`)
  return data
}

// ── updateField ───────────────────────────────────────────────────────────────

/**
 * Overwrite a single extracted field value (human correction).
 *
 * Backend endpoint: PATCH /fields/{fieldId}
 * Body: { value: string, user: string }
 * The field id is an integer PK from the DB.
 */
export async function updateField(
  claimId:    string,   // kept for mock compatibility; not used in real path
  documentId: string,   // kept for mock compatibility; not used in real path
  fieldId:    string,   // numeric id as string (Field.id is number in types)
  newValue:   string,
): Promise<Field> {
  if (USE_MOCK) {
    await delay(200)
    const claim = _claims.find(c => c.id === claimId)
    if (!claim) notFound(claimId)
    const doc = claim.documents?.find(d => String(d.id) === documentId)
    if (!doc) throw new Error(`Document "${documentId}" not found.`)
    const field = doc.fields.find(f => String(f.id) === fieldId)
    if (!field) throw new Error(`Field "${fieldId}" not found.`)
    field.value = newValue
    return structuredClone(field)
  }
  // PATCH /fields/{fieldId}  →  FieldOut
  const { data } = await api.patch<Field>(`/fields/${fieldId}`, {
    value: newValue,
    user:  X_USER,
  })
  return data
}

// ── submitDecision ────────────────────────────────────────────────────────────

/**
 * Submit an adjudication decision for a claim.
 *
 * Backend endpoint: POST /claims/{claimId}/decision
 * Body: { decision: string, comment: string, user: string }
 * Note: the backend calls the optional note field "comment", not "note".
 */
export async function submitDecision(
  claimId:  string,
  decision: 'approved' | 'queried' | 'escalated',
  comment?: string,
): Promise<Claim> {
  if (USE_MOCK) {
    await delay(300)
    const claim = _claims.find(c => c.id === claimId)
    if (!claim) notFound(claimId)
    claim.status = decision
    return structuredClone(claim)
  }
  // POST /claims/{claimId}/decision  →  ClaimDetail
  const { data } = await api.post<Claim>(`/claims/${claimId}/decision`, {
    decision,
    comment: comment ?? '',
    user:    X_USER,
  })
  return data
}

// ── uploadClaim ───────────────────────────────────────────────────────────────

/**
 * Upload one or more files to create a new claim.
 *
 * Backend endpoint: POST /claims  (multipart/form-data)
 * Each file is appended under the key "files".
 * Do NOT set Content-Type manually — axios sets the correct multipart boundary.
 */
export async function uploadClaim(files: File[]): Promise<Claim> {
  if (USE_MOCK) {
    await delay(800)
    const stub: Claim = {
      id:            `CLM-MOCK-${Date.now()}`,
      patient_name:  'Unknown (mock upload)',
      hospital_name: 'Unknown',
      status:        'pending',
      risk_level:    'Low',
      risk_score:    0,
      risk_reasons:  ['Mock upload – extraction not run'],
      created_at:    new Date().toISOString(),
      documents: files.map((f, i) => ({
        id:            i,
        filename:      f.name,
        document_type: 'hospital_bill',
        page_count:    1,
        fields:        [],
      })),
      findings: [],
    }
    _claims = [stub, ..._claims]
    return structuredClone(stub)
  }
  // POST /claims  →  ClaimDetail (status 201)
  const form = new FormData()
  files.forEach(f => form.append('files', f))
  // axios will set the correct multipart/form-data Content-Type with boundary;
  // do not override it here.
  const { data } = await api.post<Claim>('/claims', form)
  return data
}

// ── getPageImageUrl ───────────────────────────────────────────────────────────

/**
 * Build the URL for a single document page image.
 *
 * Backend endpoint: GET /claims/{claimId}/pages/{docId}/{pageNumber}
 * Returns image/png directly — no JSON wrapper.
 * We return the URL string so <img src={...}> can load it natively.
 */
export function getPageImageUrl(
  claimId:    string,
  docId:      number,
  pageNumber: number,
): string {
  return `${BACKEND_URL}/claims/${claimId}/pages/${docId}/${pageNumber}`
}

// ── getAudit ──────────────────────────────────────────────────────────────────

/**
 * Return the audit trail, optionally filtered to a single claim.
 *
 * Backend endpoint: GET /audit?claim_id=...  →  list[AuditOut]
 */
export async function getAudit(claimId?: string): Promise<AuditEntry[]> {
  if (USE_MOCK) {
    await delay(250)
    const entries = claimId
      ? MOCK_AUDIT.filter(e => e.claim_id === claimId)
      : MOCK_AUDIT
    return structuredClone(entries)
  }
  // GET /audit  →  list[AuditOut]
  const { data } = await api.get<AuditEntry[]>('/audit', {
    params: claimId ? { claim_id: claimId } : undefined,
  })
  return data
}
