import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { getClaim, updateField, submitDecision } from '../api'
import type { Claim, Document, Field, Finding, RiskLevel, ClaimStatus } from '../types'
import './ClaimDetailPage.css'

// ─── tiny helpers ────────────────────────────────────────────────────────────

const DOC_TYPE_LABEL: Record<string, string> = {
  hospital_bill:      'Hospital Bill',
  claim_form:         'Claim Form',
  discharge_summary:  'Discharge Summary',
  prescription:       'Prescription',
}

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

function ConfidenceBar({ value }: { value: number }) {
  const pct  = Math.round(value * 100)
  const low  = value < 0.6
  const mid  = value >= 0.6 && value < 0.85
  return (
    <span className="cd-conf" title={`Confidence: ${pct}%`}>
      <span
        className={`cd-conf-bar ${low ? 'conf-low' : mid ? 'conf-mid' : 'conf-ok'}`}
        style={{ width: `${pct}%` }}
      />
      <span className={`cd-conf-pct ${low ? 'conf-low-text' : ''}`}>{pct}%</span>
    </span>
  )
}

// ─── document viewer (left pane) ─────────────────────────────────────────────

interface ViewerProps {
  docs:       Document[]
  activeDock: string          // doc id
  activePage: number          // 1-based
  onDocChange: (id: string) => void
  onPageChange: (page: number) => void
}

function DocumentViewer({ docs, activeDock, activePage, onDocChange, onPageChange }: ViewerProps) {
  const doc = docs.find(d => d.id === activeDock) ?? docs[0]
  if (!doc) return null

  const pageUrl  = doc.page_image_urls[activePage - 1] ?? null
  const maxPage  = doc.page_count

  return (
    <div className="cd-viewer">
      {/* document tabs */}
      <div className="cd-doc-tabs" role="tablist">
        {docs.map(d => (
          <button
            key={d.id}
            role="tab"
            aria-selected={d.id === activeDock}
            className={`cd-doc-tab${d.id === activeDock ? ' active' : ''}`}
            onClick={() => { onDocChange(d.id); onPageChange(1) }}
            title={d.filename}
          >
            {DOC_TYPE_LABEL[d.document_type] ?? d.document_type}
          </button>
        ))}
      </div>

      {/* page image */}
      <div className="cd-page-frame">
        {pageUrl ? (
          <img src={pageUrl} alt={`${doc.filename} page ${activePage}`} className="cd-page-img" />
        ) : (
          <div className="cd-page-placeholder">
            <span>📄</span>
            <span>{doc.filename}</span>
            <span className="cd-page-placeholder-sub">No preview available</span>
          </div>
        )}
      </div>

      {/* pager */}
      <div className="cd-pager">
        <button
          className="cd-pager-btn"
          disabled={activePage <= 1}
          onClick={() => onPageChange(activePage - 1)}
          aria-label="Previous page"
        >
          ← Prev
        </button>
        <span className="cd-pager-info">
          Page {activePage} of {maxPage}
        </span>
        <button
          className="cd-pager-btn"
          disabled={activePage >= maxPage}
          onClick={() => onPageChange(activePage + 1)}
          aria-label="Next page"
        >
          Next →
        </button>
      </div>
    </div>
  )
}

// ─── editable field row ───────────────────────────────────────────────────────

interface FieldRowProps {
  field:        Field
  docId:        string
  claimId:      string
  onJumpToPage: (page: number) => void
  onSaved:      (fieldId: string, newVal: string) => void
}

function FieldRow({ field, docId, claimId, onJumpToPage, onSaved }: FieldRowProps) {
  const [editing,  setEditing]  = useState(false)
  const [draft,    setDraft]    = useState(field.value)
  const [saving,   setSaving]   = useState(false)
  const [saveErr,  setSaveErr]  = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  function startEdit() { setDraft(field.value); setEditing(true) }

  useEffect(() => {
    if (editing) inputRef.current?.focus()
  }, [editing])

  async function save() {
    if (draft === field.value) { setEditing(false); return }
    setSaving(true); setSaveErr(null)
    try {
      await updateField(claimId, docId, field.id, draft)
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

  return (
    <tr className={`cd-field-row${field.confidence < 0.6 ? ' low-conf-row' : ''}`}>
      <td className="cd-field-name">{field.name}</td>
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
            <button className="cd-save-btn" onClick={save}  disabled={saving}>✓</button>
            <button className="cd-cancel-btn" onClick={cancel} disabled={saving}>✕</button>
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
            {field.value}
            <span className="cd-edit-hint">✎</span>
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
          title={`Go to page ${field.page_number}`}
        >
          p.{field.page_number} ↗
        </button>
      </td>
    </tr>
  )
}

// ─── finding card ─────────────────────────────────────────────────────────────

interface FindingCardProps {
  finding:      Finding
  docs:         Document[]
  onJumpToDoc:  (docId: string, page: number) => void
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

      {finding.evidence.length > 0 && (
        <div className="cd-finding-evidence">
          <span className="cd-finding-label">Evidence</span>
          {finding.evidence.map((ev, i) => (
            <button
              key={i}
              className="cd-evidence-link"
              onClick={() => jump(ev.document_filename, ev.page_number)}
              title={`Open ${ev.document_filename} p.${ev.page_number}`}
            >
              📄 {ev.document_filename} · p.{ev.page_number}
            </button>
          ))}
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

// ─── decision bar ─────────────────────────────────────────────────────────────

type Decision = 'approved' | 'queried' | 'escalated'

interface DecisionBarProps {
  claimId:   string
  status:    ClaimStatus
  onDecided: (updated: Claim) => void
}

function DecisionBar({ claimId, status, onDecided }: DecisionBarProps) {
  const [pending,  setPending]  = useState<Decision | null>(null)
  const [comment,  setComment]  = useState('')
  const [saving,   setSaving]   = useState(false)
  const [err,      setErr]      = useState<string | null>(null)
  const textRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (pending) textRef.current?.focus()
  }, [pending])

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

  const DECISIONS: { key: Decision; label: string; cls: string }[] = [
    { key: 'approved',  label: 'Approve',  cls: 'dec-approve'  },
    { key: 'queried',   label: 'Query',    cls: 'dec-query'    },
    { key: 'escalated', label: 'Escalate', cls: 'dec-escalate' },
  ]

  return (
    <div className="cd-decision-bar">
      <div className="cd-decision-current">
        <span className="cd-decision-label">Current status</span>
        <StatusChip status={status} />
      </div>

      <div className="cd-decision-btns">
        {DECISIONS.map(d => (
          <button
            key={d.key}
            className={`cd-dec-btn ${d.cls}${status === d.key ? ' is-current' : ''}`}
            onClick={() => { setPending(d.key); setErr(null) }}
            disabled={saving}
          >
            {d.label}
          </button>
        ))}
      </div>

      {pending && (
        <div className="cd-confirm-panel">
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
          {err && <p className="cd-confirm-err">{err}</p>}
          <div className="cd-confirm-actions">
            <button className={`cd-dec-btn ${DECISIONS.find(d=>d.key===pending)!.cls}`}
              onClick={confirm} disabled={saving}>
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

// ─── main page ────────────────────────────────────────────────────────────────

export default function ClaimDetailPage() {
  const { id }   = useParams<{ id: string }>()
  const navigate = useNavigate()

  const [claim,   setClaim]   = useState<Claim | null>(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  // viewer state
  const [activeDocId, setActiveDocId] = useState<string>('')
  const [activePage,  setActivePage]  = useState(1)

  useEffect(() => {
    if (!id) return
    getClaim(id)
      .then(c => {
        setClaim(c)
        if (c.documents[0]) setActiveDocId(c.documents[0].id)
      })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [id])

  function jumpToDoc(docId: string, page: number) {
    setActiveDocId(docId)
    setActivePage(page)
  }

  function handleFieldSaved(docId: string, fieldId: string, newVal: string) {
    setClaim(prev => {
      if (!prev) return prev
      return {
        ...prev,
        documents: prev.documents.map(d =>
          d.id !== docId ? d : {
            ...d,
            fields: d.fields.map(f =>
              f.id !== fieldId ? f : { ...f, value: newVal }
            ),
          }
        ),
      }
    })
  }

  // ── loading / error states ─────────────────────────────────────────────────
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

  const activeDoc = claim.documents.find(d => d.id === activeDocId) ?? claim.documents[0]

  return (
    <div className="cd-shell">

      {/* ── breadcrumb + header ──────────────────────────────────────────── */}
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

        {/* risk indicators */}
        {claim.risk_reasons.length > 0 && (
          <div className="cd-risk-indicators">
            <span className="cd-risk-ind-label">Risk indicators</span>
            <ul className="cd-risk-ind-list">
              {claim.risk_reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* ── split layout ────────────────────────────────────────────────── */}
      <div className="cd-body">

        {/* LEFT – document viewer */}
        <aside className="cd-left">
          <DocumentViewer
            docs={claim.documents}
            activeDock={activeDocId}
            activePage={activePage}
            onDocChange={docId => { setActiveDocId(docId); setActivePage(1) }}
            onPageChange={setActivePage}
          />
        </aside>

        {/* RIGHT – fields + findings + decision */}
        <section className="cd-right">

          {/* ── 1. Extracted fields ───────────────────────────────────── */}
          <div className="cd-section">
            <h3 className="cd-section-title">Extracted Fields</h3>
            <p className="cd-section-hint">
              Showing fields for{' '}
              <strong>{DOC_TYPE_LABEL[activeDoc?.document_type] ?? activeDoc?.filename}</strong>.
              Switch documents with the tabs on the left.
            </p>

            {activeDoc?.fields.length === 0 ? (
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
                    {activeDoc?.fields.map(field => (
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

          {/* ── 2. Findings ───────────────────────────────────────────── */}
          <div className="cd-section">
            <h3 className="cd-section-title">
              Findings
              {claim.findings.length > 0 && (
                <span className="cd-findings-count">{claim.findings.length}</span>
              )}
            </h3>

            {claim.findings.length === 0 ? (
              <p className="cd-empty cd-empty-green">✓ No findings flagged for this claim.</p>
            ) : (
              <div className="cd-findings-list">
                {claim.findings.map(f => (
                  <FindingCard
                    key={f.id}
                    finding={f}
                    docs={claim.documents}
                    onJumpToDoc={jumpToDoc}
                  />
                ))}
              </div>
            )}
          </div>

          {/* ── 3. Decision ───────────────────────────────────────────── */}
          <div className="cd-section">
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
