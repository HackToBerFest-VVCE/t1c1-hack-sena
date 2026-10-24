// ── ClaimLens AI – API layer ───────────────────────────────────────────────────
//
// Set USE_MOCK = false and fill in BACKEND_URL to switch to the real backend.

import axios from 'axios'
import { MOCK_CLAIMS, MOCK_AUDIT } from './mockData'
import type { Claim, AuditEntry, Field } from './types'

const USE_MOCK    = true
const BACKEND_URL = 'http://localhost:8000'

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

/** Return all claims (summary list). */
export async function getClaims(): Promise<Claim[]> {
  if (USE_MOCK) {
    await delay()
    return structuredClone(_claims)
  }
  const { data } = await axios.get<Claim[]>(`${BACKEND_URL}/claims`)
  return data
}

// ── getClaim ──────────────────────────────────────────────────────────────────

/** Return a single claim with full document and findings detail. */
export async function getClaim(id: string): Promise<Claim> {
  if (USE_MOCK) {
    await delay()
    const claim = _claims.find(c => c.id === id)
    if (!claim) notFound(id)
    return structuredClone(claim)
  }
  const { data } = await axios.get<Claim>(`${BACKEND_URL}/claims/${id}`)
  return data
}

// ── updateField ───────────────────────────────────────────────────────────────

/** Overwrite a single extracted field value (human correction). */
export async function updateField(
  claimId:    string,
  documentId: string,
  fieldId:    string,
  newValue:   string,
): Promise<Field> {
  if (USE_MOCK) {
    await delay(200)
    const claim = _claims.find(c => c.id === claimId)
    if (!claim) notFound(claimId)
    const doc = claim.documents.find(d => d.id === documentId)
    if (!doc) throw new Error(`Document "${documentId}" not found.`)
    const field = doc.fields.find(f => f.id === fieldId)
    if (!field) throw new Error(`Field "${fieldId}" not found.`)
    field.value = newValue
    return structuredClone(field)
  }
  const { data } = await axios.patch<Field>(
    `${BACKEND_URL}/claims/${claimId}/documents/${documentId}/fields/${fieldId}`,
    { value: newValue },
  )
  return data
}

// ── submitDecision ────────────────────────────────────────────────────────────

/**
 * Submit an adjudication decision for a claim.
 * @param decision - 'approved' | 'queried' | 'escalated'
 * @param note     - Optional reviewer note attached to the decision.
 */
export async function submitDecision(
  claimId:  string,
  decision: 'approved' | 'queried' | 'escalated',
  note?:    string,
): Promise<Claim> {
  if (USE_MOCK) {
    await delay(300)
    const claim = _claims.find(c => c.id === claimId)
    if (!claim) notFound(claimId)
    claim.status = decision
    if (note) {
      // Surface note as a low-severity finding so the UI can display it
      claim.findings.push({
        id:               `FND-decision-${Date.now()}`,
        rule_id:          'REVIEWER-NOTE',
        severity:         'low',
        what:             `Reviewer decision: ${decision}`,
        evidence:         [],
        why_it_matters:   note,
        suggested_action: '',
      })
    }
    return structuredClone(claim)
  }
  const { data } = await axios.post<Claim>(
    `${BACKEND_URL}/claims/${claimId}/decision`,
    { decision, note },
  )
  return data
}

// ── uploadClaim ───────────────────────────────────────────────────────────────

/**
 * Upload one or more files to create a new claim.
 * In mock mode returns a minimal stub; the real backend runs full extraction.
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
        id:               `DOC-mock-${i}`,
        filename:         f.name,
        document_type:    'hospital_bill',
        page_count:       1,
        page_image_urls:  [],
        fields:           [],
      })),
      findings: [],
    }
    _claims = [stub, ..._claims]
    return structuredClone(stub)
  }
  const form = new FormData()
  files.forEach(f => form.append('files', f))
  const { data } = await axios.post<Claim>(`${BACKEND_URL}/upload`, form)
  return data
}

// ── getAudit ──────────────────────────────────────────────────────────────────

/** Return the audit trail, optionally filtered to a single claim. */
export async function getAudit(claimId?: string): Promise<AuditEntry[]> {
  if (USE_MOCK) {
    await delay(250)
    const entries = claimId
      ? MOCK_AUDIT.filter(e => e.claim_id === claimId)
      : MOCK_AUDIT
    return structuredClone(entries)
  }
  const url = claimId
    ? `${BACKEND_URL}/audit?claim_id=${claimId}`
    : `${BACKEND_URL}/audit`
  const { data } = await axios.get<AuditEntry[]>(url)
  return data
}
