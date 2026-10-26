import { Component, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useAuth } from '../auth'
import { getClaim, updateField, submitDecision, getPageImageBlob } from '../api'
import type { Claim, Document, Field, Finding, RiskLevel, ClaimStatus } from '../types'
import './ClaimDetailPage.css'

// ── helpers ───────────────────────────────────────────────────────────────────

const DOC_TYPE_LABEL: Record<string, string> = {
  hospital_bill:     'Hospital Bill',
  claim_form:        'Claim Form',
  discharge_summary: 'Discharge Summary',
  prescription:      'Prescription',
}

// ── small shared UI pieces ────────────────────────────────────────────────────

function RiskBadge({ level, score }: { level: RiskLevel; score: number }) {
  return (
    <span className={`cd-risk-badge cd-risk-${level.toLowerCase()}`}>
      <span className="cd-risk-dot" />
      {level} · {score}
    </span>
  )
}

function StatusChip({ status }: { status: ClaimStatus }) {
  return (
    <span className={`cd-status-chip cd-status-${status}`}>
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </span>
  )
}

function SeverityPip({ sev }: { sev: string }) {
  return <span className={`cd-sev cd-sev-${sev}`}>{sev.toUpperCase()}</span>
}

function ExtractionBadge({ method, model }: { method?: string; model?: string }) {
  if (!method) return null
  const isGemma = method === 'gemma-4'
  const label   = isGemma
    ? `Extracted by Gemma 4${model ? ` (${model})` : ''}`
    : 'Regex fallback'
  return (
    <span
      className={`cd-extraction-badge ${isGemma ? 'cd-extraction-gemma' : 'cd-extraction-regex'}`}
      title={label}
    >
      {isGemma ? '✦ Gemma 4' : '⚙ Regex fallback'}
    </span>
  )
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100)
  const cls = value < 0.6 ? 'conf-low' : value < 0.85 ? 'conf-mid' : 'conf-ok'
  return (
    <span className="cd-conf" title={`Confidence: ${pct}%`}>
      <span className={`cd-conf-bar ${cls}`} style={{ width: `${pct}%` }} />
      <span className={`cd-conf-pct${value < 0.6 ? ' conf-low-text' : ''}`}>{pct}%</span>
    </span>
  )
}

// ── blob cache ────────────────────────────────────────────────────────────────
// Keyed by "claimId/docId/page". Object URLs are stored here so Prev/Next
// reuse them without refetching and they are revoked on unmount.

type BlobCacheEntry = { objectUrl: string }
const _blobCache = new Map<string, BlobCacheEntry>()

function _blobKey(claimId: string, docId: number, page: number) {
  return `${claimId}/${docId}/${page}`
}

// ── document viewer (left pane) ───────────────────────────────────────────────

interface ViewerProps {
  claimId:     string
  docs:        Document[]
  activeDocId: number | null
  activePage:  number          // 1-based
  onDocChange:  (id: number) => void
  onPageChange: (page: number) => void
}

type ImgState =
  | { phase: 'loading' }
  | { phase: 'ready'; objectUrl: string }
  | { phase: 'error'; status: number | null }

function DocumentViewer({
  claimId, docs, activeDocId, activePage, onDocChange, onPageChange,
}: ViewerProps) {
  const [imgState, setImgState] = useState<ImgState>({ phase: 'loading' })
  // Track the key we're currently showing so stale responses are discarded.
  const pendingKey = useRef<string>('')

  const doc     = docs.find(d => d.id === activeDocId) ?? docs[0] ?? null
  const maxPage = doc?.page_count ?? 1
  const safePage = Math.min(Math.max(activePage, 1), maxPage)

  useEffect(() => {
    if (!doc) return

    const key = _blobKey(claimId, doc.id, safePage)

    // Cache hit — use the existing object URL immediately.
    const cached = _blobCache.get(key)
    if (cached) {
      setImgState({ phase: 'ready', objectUrl: cached.objectUrl })
      return
    }

    // Cache miss — fetch via authenticated axios.
    setImgState({ phase: 'loading' })
    pendingKey.current = key

    getPageImageBlob(claimId, doc.id, safePage)
      .then(blob => {
        // Discard if the user navigated away while this was in-flight.
        if (pendingKey.current !== key) return
        const objectUrl = URL.createObjectURL(blob)
        _blobCache.set(key, { objectUrl })
        setImgState({ phase: 'ready', objectUrl })
      })
      .catch((err: unknown) => {
        if (pendingKey.current !== key) return
        // Try to pull the HTTP status from an axios error.
        const status: number | null =
          (err && typeof err === 'object' && 'response' in err &&
            (err as { response?: { status?: number } }).response?.status != null)
            ? (err as { response: { status: number } }).response.status
            : null
        setImgState({ phase: 'error', status })
      })

    // On unmount or before the next effect run, mark the pending key stale.
    return () => { pendingKey.current = '' }
  }, [claimId, doc?.id, safePage])   // eslint-disable-line react-hooks/exhaustive-deps

  // Revoke all cached object URLs when the viewer unmounts (claim page left).
  useEffect(() => {
    return () => {
      _blobCache.forEach(entry => URL.revokeObjectURL(entry.objectUrl))
      _blobCache.clear()
    }
  }, [])   // run only on mount/unmount

  if (docs.length === 0) {
    return (
      <div className="cd-viewer">
        <div className="cd-page-placeholder">
          <span>📂</span>
          <span>No documents attached to this claim.</span>
        </div>
      </div>
    )
  }

  return (
    <div className="cd-viewer">
      {/* document tabs */}
      <div className="cd-doc-tabs" role="tablist" aria-label="Documents">
        {docs.map(d => (
          <button
            key={d.id}
            role="tab"
            aria-selected={d.id === (doc?.id ?? -1)}
            className={`cd-doc-tab${d.id === (doc?.id ?? -1) ? ' active' : ''}`}
            onClick={() => { onDocChange(d.id); onPageChange(1) }}
            title={d.filename}
          >
            {DOC_TYPE_LABEL[d.document_type] ?? d.document_type}
          </button>
        ))}
      </div>

      {/* filename sub-label */}
      {doc && (
        <div className="cd-doc-filename" title={doc.filename}>
          📄 {doc.filename}
          <span className="cd-doc-pages">
            {doc.page_count} page{doc.page_count !== 1 ? 's' : ''}
          </span>
          <ExtractionBadge method={doc.extraction_method} model={doc.model_name} />
        </div>
      )}

      {/* page image */}
      <div className="cd-page-frame">
        {imgState.phase === 'loading' && (
          <div className="cd-page-skeleton" aria-label="Loading page image…" />
        )}

        {imgState.phase === 'ready' && (
          <img
            src={imgState.objectUrl}
            alt={`${doc?.filename ?? 'document'} – page ${safePage}`}
            className="cd-page-img"
          />
        )}

        {imgState.phase === 'error' && (
          <div className="cd-page-placeholder">
            <span>⚠️</span>
            <span>Image not available</span>
            <span className="cd-page-placeholder-sub">
              Could not load page {safePage} from the server.
              {imgState.status != null && (
                <span className="cd-page-err-status"> (HTTP {imgState.status})</span>
              )}
            </span>
          </div>
        )}
      </div>

      {/* pager */}
      <div className="cd-pager">
        <button
          className="cd-pager-btn"
          disabled={safePage <= 1}
          onClick={() => onPageChange(safePage - 1)}
          aria-label="Previous page"
        >
          ← Prev
        </button>
        <span className="cd-pager-info">Page {safePage} / {maxPage}</span>
        <button
          className="cd-pager-btn"
          disabled={safePage >= maxPage}
          onClick={() => onPageChange(safePage + 1)}
          aria-label="Next page"
        >
          Next →
        </button>
      </div>
    </div>
  )
}

// ── editable field row ────────────────────────────────────────────────────────

interface FieldRowProps {
  field:        Field
  docId:        number
  claimId:      string
  onJumpToPage: (page: number) => void
  onSaved:      (fieldId: number, newVal: string) => void
}

function FieldRow({ field, docId, claimId, onJumpToPage, onSaved }: FieldRowProps) {
  const [editing, setEditing] = useState(false)
  const [draft,   setDraft]   = useState(field.value)
  const [saving,  setSaving]  = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { if (editing) inputRef.current?.focus() }, [editing])

  function startEdit() { setDraft(field.value); setEditing(true) }

  async function save() {
    if (draft === field.value) { setEditing(false); return }
    setSaving(true); setSaveErr(null)
    try {
      // updateField expects fieldId as string; field.id is number
      await updateField(claimId, String(docId), String(field.id), draft)
      onSaved(field.id, draft)
      setEditing(false)
    } catch (e) {
      setSaveErr(String(e))
    } finally {
      setSaving(false)
    }
  }

  function cancel() { setDraft(field.value); setEditing(false); setSaveErr(null) }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter')  save()
    if (e.key === 'Escape') cancel()
  }

  const isLowConf = field.confidence < 0.6

  return (
    <tr className={`cd-field-row${isLowConf ? ' low-conf-row' : ''}`}>
      <td className="cd-field-name">
        {field.name}
        {isLowConf && (
          <span className="cd-low-conf-flag" title="Low confidence – review carefully">⚠</span>
        )}
      </td>
      <td className="cd-field-val">
        {editing ? (
          <span className="cd-edit-wrap">
            <input
              ref={inputRef}
              className="cd-edit-input"
              value={draft}
              onChange={e => setDraft(e.target.value)}
              onKeyDown={onKeyDown}
              disabled={saving}
            />
            <button className="cd-save-btn"   onClick={save}   disabled={saving} aria-label="Save">✓</button>
            <button className="cd-cancel-btn" onClick={cancel} disabled={saving} aria-label="Cancel">✕</button>
          </span>
        ) : (
          <span
            className="cd-field-value-text"
            onClick={startEdit}
            title="Click to edit"
            role="button"
            tabIndex={0}
            onKeyDown={e => e.key === 'Enter' && startEdit()}
          >
            {field.value || <span className="cd-field-empty">—</span>}
            <span className="cd-edit-hint" aria-hidden>✎</span>
          </span>
        )}
        {saveErr && <span className="cd-save-err"> {saveErr}</span>}
      </td>
      <td className="cd-field-conf">
        <ConfidenceBar value={field.confidence} />
      </td>
      <td className="cd-field-jump">
        <button
          className="cd-jump-btn"
          onClick={() => onJumpToPage(field.page_number)}
          title={`Jump to page ${field.page_number}`}
        >
          p.{field.page_number} ↗
        </button>
      </td>
    </tr>
  )
}

// ── finding card ──────────────────────────────────────────────────────────────

interface FindingCardProps {
  finding:     Finding
  docs:        Document[]
  onJumpToDoc: (docId: number, page: number) => void
}

function FindingCard({ finding, docs, onJumpToDoc }: FindingCardProps) {
  function jump(filename: string, page: number) {
    const doc = docs.find(d => d.filename === filename)
    if (doc) onJumpToDoc(doc.id, page)
  }

  return (
    <div className={`cd-finding cd-finding-${finding.severity}`}>
      <div className="cd-finding-head">
        <SeverityPip sev={finding.severity} />
        <span className="cd-finding-what">{finding.what}</span>
        <span className="cd-finding-rule">{finding.rule_id}</span>
      </div>

      {(finding.evidence ?? []).length > 0 && (
        <div className="cd-finding-evidence">
          <span className="cd-finding-label">Evidence</span>
          <div className="cd-evidence-links">
            {(finding.evidence ?? []).map((ev, i) => (
              <button
                key={i}
                className="cd-evidence-link"
                onClick={() => jump(ev.document_filename, ev.page_number)}
                title={`Jump to ${ev.document_filename} p.${ev.page_number}`}
              >
                📄 {ev.document_filename} · p.{ev.page_number}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="cd-finding-why">
        <span className="cd-finding-label">Why it matters</span>
        <p>{finding.why_it_matters}</p>
      </div>

      {finding.suggested_action && (
        <div className="cd-finding-action">
          <span className="cd-finding-label">Suggested action</span>
          <p>{finding.suggested_action}</p>
        </div>
      )}
    </div>
  )
}

// ── risk panel ────────────────────────────────────────────────────────────────

function RiskPanel({
  level, score, reasons,
}: { level: RiskLevel; score: number; reasons: string[] }) {
  return (
    <div className="cd-risk-panel">
      <div className="cd-risk-panel-top">
        <RiskBadge level={level} score={score} />
        <div className="cd-risk-score-bar-wrap" title={`Risk score: ${score}/100`}>
          <div
            className={`cd-risk-score-bar cd-risk-score-bar-${level.toLowerCase()}`}
            style={{ width: `${score}%` }}
          />
        </div>
        <span className="cd-risk-score-label">{score} / 100</span>
      </div>

      {(reasons ?? []).length > 0 && (
        <div className="cd-risk-reasons">
          <span className="cd-risk-reasons-label">Risk indicators</span>
          <ul className="cd-risk-reasons-list">
            {(reasons ?? []).map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </div>
      )}
    </div>
  )
}

// ── decision bar ──────────────────────────────────────────────────────────────

type Decision = 'approved' | 'queried' | 'escalated'

const DECISIONS: { key: Decision; label: string; cls: string }[] = [
  { key: 'approved',  label: '✓ Approve',  cls: 'dec-approve'  },
  { key: 'queried',   label: '? Query',    cls: 'dec-query'    },
  { key: 'escalated', label: '↑ Escalate', cls: 'dec-escalate' },
]

interface DecisionBarProps {
  claimId:   string
  status:    ClaimStatus
  onDecided: (updated: Claim) => void
}

function DecisionBar({ claimId, status, onDecided }: DecisionBarProps) {
  const { user } = useAuth()
  const isSupervisor = user?.role === 'Supervisor'
  const visibleDecisions = DECISIONS.filter(d => isSupervisor || d.key === 'queried')

  const [pending, setPending] = useState<Decision | null>(null)
  const [comment, setComment] = useState('')
  const [saving,  setSaving]  = useState(false)
  const [err,     setErr]     = useState<string | null>(null)
  const textRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => { if (pending) textRef.current?.focus() }, [pending])

  async function confirm() {
    if (!pending) return
    setSaving(true); setErr(null)
    try {
      const updated = await submitDecision(claimId, pending, comment.trim() || undefined)
      onDecided(updated)
      setPending(null); setComment('')
    } catch (e) {
      setErr(String(e))
    } finally {
      setSaving(false)
    }
  }

  function cancel() { setPending(null); setComment(''); setErr(null) }

  return (
    <div className="cd-decision-bar">
      <div className="cd-decision-current">
        <span className="cd-decision-label">Current status</span>
        <StatusChip status={status} />
      </div>

      <div className="cd-decision-btns" role="group" aria-label="Adjudication actions">
        {visibleDecisions.map(d => (
          <button
            key={d.key}
            className={`cd-dec-btn ${d.cls}${status === d.key ? ' is-current' : ''}`}
            onClick={() => { setPending(d.key); setErr(null) }}
            disabled={saving}
            aria-pressed={status === d.key}
          >
            {d.label}
          </button>
        ))}
      </div>

      {pending && (
        <div className="cd-confirm-panel" role="region" aria-label="Confirm decision">
          <p className="cd-confirm-title">
            Set status to <strong>{pending}</strong> — add a comment (optional):
          </p>
          <textarea
            ref={textRef}
            className="cd-confirm-textarea"
            rows={2}
            placeholder="Short note for the audit trail…"
            value={comment}
            onChange={e => setComment(e.target.value)}
            disabled={saving}
          />
          {err && <p className="cd-confirm-err" role="alert">{err}</p>}
          <div className="cd-confirm-actions">
            <button
              className={`cd-dec-btn ${DECISIONS.find(d => d.key === pending)!.cls}`}
              onClick={confirm}
              disabled={saving}
            >
              {saving ? 'Saving…' : `Confirm ${pending}`}
            </button>
            <button className="cd-cancel-decision" onClick={cancel} disabled={saving}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ── ErrorBoundary (class component — hooks can't catch render errors) ─────────

interface EBState { error: Error | null }

export class ErrorBoundary extends Component<
  { children: React.ReactNode; fallback?: React.ReactNode },
  EBState
> {
  state: EBState = { error: null }

  static getDerivedStateFromError(error: Error): EBState {
    return { error }
  }

  render() {
    if (this.state.error) {
      return this.props.fallback ?? (
        <div style={{
          padding: '2rem', maxWidth: 600, margin: '3rem auto',
          fontFamily: 'Inter, system-ui, sans-serif',
        }}>
          <h2 style={{ color: '#dc2626', marginBottom: '0.75rem' }}>Something went wrong</h2>
          <pre style={{
            background: '#fef2f2', border: '1px solid #fca5a5', borderRadius: 8,
            padding: '1rem', fontSize: '0.85rem', overflowX: 'auto',
            whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: '#7f1d1d',
          }}>
            {this.state.error.message}
            {this.state.error.stack ? '\n\n' + this.state.error.stack : ''}
          </pre>
          <button
            onClick={() => this.setState({ error: null })}
            style={{
              marginTop: '1rem', padding: '0.5rem 1.2rem',
              background: '#1a56db', color: '#fff', border: 'none',
              borderRadius: 7, cursor: 'pointer', fontSize: '0.9rem', fontWeight: 700,
            }}
          >
            Try again
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

// ── main page ─────────────────────────────────────────────────────────────────

export default function ClaimDetailPage() {
  const { id }   = useParams<{ id: string }>()
  const navigate = useNavigate()

  const [claim,   setClaim]   = useState<Claim | null>(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  // viewer state — doc id is number (DB PK), null until claim loads
  const [activeDocId, setActiveDocId] = useState<number | null>(null)
  const [activePage,  setActivePage]  = useState(1)

  useEffect(() => {
    if (!id) return
    getClaim(id)
      .then(c => {
        setClaim(c)
        const firstDoc = (c.documents ?? [])[0]
        if (firstDoc) setActiveDocId(firstDoc.id)
      })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [id])

  function jumpToDoc(docId: number, page: number) {
    setActiveDocId(docId)
    setActivePage(page)
  }

  function handleFieldSaved(docId: number, fieldId: number, newVal: string) {
    setClaim(prev => {
      if (!prev) return prev
      return {
        ...prev,
        documents: (prev.documents ?? []).map(d =>
          d.id !== docId ? d : {
            ...d,
            fields: (d.fields ?? []).map(f =>
              f.id !== fieldId ? f : { ...f, value: newVal }
            ),
          }
        ),
      }
    })
  }

  // ── loading / error ────────────────────────────────────────────────────────
  if (loading) return (
    <div className="cd-shell">
      <div className="cd-loading"><span className="cd-spinner" /> Loading claim…</div>
    </div>
  )
  if (error || !claim) return (
    <div className="cd-shell">
      <button className="cd-back" onClick={() => navigate(-1)}>← Back</button>
      <p className="cd-err-msg">{error ?? 'Claim not found.'}</p>
    </div>
  )

  const docs     = claim.documents ?? []
  const findings = claim.findings  ?? []
  const activeDoc = docs.find(d => d.id === activeDocId) ?? docs[0] ?? null

  return (
    <div className="cd-shell">

      {/* ── slim header ─────────────────────────────────────────────────── */}
      <div className="cd-header">
        <button className="cd-back" onClick={() => navigate(-1)}>← Queue</button>

        <div className="cd-header-main">
          <div className="cd-header-left">
            <h2 className="cd-claim-id">{claim.id}</h2>
            <span className="cd-patient">{claim.patient_name}</span>
            <span className="cd-sep">·</span>
            <span className="cd-hospital">{claim.hospital_name}</span>
          </div>
          <div className="cd-header-right">
            <RiskBadge level={claim.risk_level} score={claim.risk_score} />
            <StatusChip status={claim.status} />
          </div>
        </div>
      </div>

      {/* ── split body ──────────────────────────────────────────────────── */}
      <div className="cd-body">

        {/* LEFT – sticky document viewer */}
        <aside className="cd-left" aria-label="Document viewer">
          <DocumentViewer
            claimId={claim.id}
            docs={docs}
            activeDocId={activeDocId}
            activePage={activePage}
            onDocChange={docId => { setActiveDocId(docId); setActivePage(1) }}
            onPageChange={setActivePage}
          />
        </aside>

        {/* RIGHT – scrollable detail panel */}
        <section className="cd-right" aria-label="Claim detail">

          {/* ── Risk ─────────────────────────────────────────────────── */}
          <div className="cd-section">
            <h3 className="cd-section-title">Risk Assessment</h3>
            <RiskPanel
              level={claim.risk_level}
              score={claim.risk_score}
              reasons={claim.risk_reasons ?? []}
            />
          </div>

          {/* ── Extracted fields ──────────────────────────────────────── */}
          <div className="cd-section">
            <h3 className="cd-section-title">
              Extracted Fields
              {activeDoc && (
                <span className="cd-section-doc-hint">
                  — {DOC_TYPE_LABEL[activeDoc.document_type] ?? activeDoc.filename}
                </span>
              )}
            </h3>
            <p className="cd-section-hint">
              Switch documents with the tabs on the left. Click any value to correct it.
            </p>

            {docs.length === 0 ? (
              <p className="cd-empty">No documents on this claim.</p>
            ) : !activeDoc || (activeDoc.fields ?? []).length === 0 ? (
              <p className="cd-empty">No fields extracted for this document.</p>
            ) : (
              <div className="cd-fields-table-wrap">
                <table className="cd-fields-table">
                  <thead>
                    <tr>
                      <th>Field</th>
                      <th>Value</th>
                      <th>Confidence</th>
                      <th>Page</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(activeDoc.fields ?? []).map(field => (
                      <FieldRow
                        key={field.id}
                        field={field}
                        docId={activeDoc.id}
                        claimId={claim.id}
                        onJumpToPage={page => setActivePage(page)}
                        onSaved={(fid, val) => handleFieldSaved(activeDoc.id, fid, val)}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* ── Findings ─────────────────────────────────────────────── */}
          <div className="cd-section">
            <h3 className="cd-section-title">
              Findings
              {findings.length > 0 && (
                <span className="cd-findings-count">{findings.length}</span>
              )}
            </h3>

            {findings.length === 0 ? (
              <p className="cd-empty cd-empty-green">✓ No findings flagged for this claim.</p>
            ) : (
              <div className="cd-findings-list">
                {findings.map(f => (
                  <FindingCard
                    key={f.id}
                    finding={f}
                    docs={docs}
                    onJumpToDoc={jumpToDoc}
                  />
                ))}
              </div>
            )}
          </div>

          {/* ── Decision ─────────────────────────────────────────────── */}
          <div className="cd-section cd-section-last">
            <h3 className="cd-section-title">Decision</h3>
            <DecisionBar
              claimId={claim.id}
              status={claim.status}
              onDecided={updated => setClaim(updated)}
            />
          </div>

        </section>
      </div>
    </div>
  )
}
