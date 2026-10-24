import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getClaims } from '../api'
import type { Claim, ClaimStatus, RiskLevel } from '../types'
import './DashboardPage.css'

// ── static option lists ───────────────────────────────────────────────────────

const RISK_LEVELS: RiskLevel[]    = ['High', 'Medium', 'Low']
const STATUSES:    ClaimStatus[]  = ['pending', 'queried', 'escalated', 'approved']

// ── helpers ───────────────────────────────────────────────────────────────────

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString('en-IN', {
    day: '2-digit', month: 'short', year: 'numeric',
  })
}

function highlight(text: string, q: string) {
  if (!q) return <>{text}</>
  const idx = text.toLowerCase().indexOf(q.toLowerCase())
  if (idx === -1) return <>{text}</>
  return (
    <>
      {text.slice(0, idx)}
      <mark className="hl">{text.slice(idx, idx + q.length)}</mark>
      {text.slice(idx + q.length)}
    </>
  )
}

// ── sub-components ────────────────────────────────────────────────────────────

function RiskBadge({ level, score }: { level: RiskLevel; score: number }) {
  return (
    <span className={`risk-badge risk-${level.toLowerCase()}`}>
      <span className="risk-dot" />
      {level} · {score}
    </span>
  )
}

function StatusChip({ status }: { status: ClaimStatus }) {
  return (
    <span className={`status-chip status-${status}`}>
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </span>
  )
}

// ── main component ────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const navigate = useNavigate()

  const [claims,  setClaims]  = useState<Claim[]>([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  // filter state
  const [search,    setSearch]    = useState('')
  const [riskFilter, setRiskFilter] = useState<RiskLevel | ''>('')
  const [statusFilter, setStatusFilter] = useState<ClaimStatus | ''>('')

  useEffect(() => {
    getClaims()
      .then(setClaims)
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [])

  // sort by risk_score desc, then apply filters
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return [...claims]
      .sort((a, b) => b.risk_score - a.risk_score)
      .filter(c => !riskFilter   || c.risk_level === riskFilter)
      .filter(c => !statusFilter || c.status     === statusFilter)
      .filter(c => {
        if (!q) return true
        return (
          c.id.toLowerCase().includes(q)           ||
          c.patient_name.toLowerCase().includes(q) ||
          c.hospital_name.toLowerCase().includes(q)
        )
      })
  }, [claims, search, riskFilter, statusFilter])

  // ── summary counts ─────────────────────────────────────────────────────────
  const counts = useMemo(() => ({
    total:     claims.length,
    high:      claims.filter(c => c.risk_level === 'High').length,
    pending:   claims.filter(c => c.status === 'pending').length,
    escalated: claims.filter(c => c.status === 'escalated').length,
  }), [claims])

  function clearFilters() {
    setSearch('')
    setRiskFilter('')
    setStatusFilter('')
  }

  const hasFilters = search || riskFilter || statusFilter

  // ── render ─────────────────────────────────────────────────────────────────
  return (
    <div className="queue-page">

      {/* ── page title + summary cards ─── */}
      <div className="queue-top">
        <h2 className="queue-title">Claims Queue</h2>
        <div className="summary-cards">
          <SummaryCard label="Total"     value={counts.total}     />
          <SummaryCard label="High Risk" value={counts.high}     accent="red"   />
          <SummaryCard label="Pending"   value={counts.pending}  accent="amber" />
          <SummaryCard label="Escalated" value={counts.escalated} accent="blue" />
        </div>
      </div>

      {/* ── filters ───────────────────────────────────────────────────────── */}
      <div className="queue-filters">
        <div className="search-wrap">
          <span className="search-icon">🔍</span>
          <input
            className="search-input"
            type="search"
            placeholder="Search by ID, patient or hospital…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>

        <select
          className="filter-select"
          value={riskFilter}
          onChange={e => setRiskFilter(e.target.value as RiskLevel | '')}
        >
          <option value="">All risk levels</option>
          {RISK_LEVELS.map(r => <option key={r} value={r}>{r}</option>)}
        </select>

        <select
          className="filter-select"
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value as ClaimStatus | '')}
        >
          <option value="">All statuses</option>
          {STATUSES.map(s => (
            <option key={s} value={s}>
              {s.charAt(0).toUpperCase() + s.slice(1)}
            </option>
          ))}
        </select>

        {hasFilters && (
          <button className="clear-filters" onClick={clearFilters}>
            ✕ Clear
          </button>
        )}

        <span className="result-count">
          {filtered.length} claim{filtered.length !== 1 ? 's' : ''}
        </span>
      </div>

      {/* ── table ─────────────────────────────────────────────────────────── */}
      {loading && <div className="queue-loading"><span className="q-spinner" />Loading claims…</div>}
      {error   && <div className="queue-error">⚠️ {error}</div>}

      {!loading && !error && (
        filtered.length === 0 ? (
          <div className="queue-empty">
            No claims match the current filters.
            {hasFilters && (
              <button className="clear-filters inline" onClick={clearFilters}>
                Clear filters
              </button>
            )}
          </div>
        ) : (
          <div className="table-wrap">
            <table className="claims-table">
              <thead>
                <tr>
                  <th>Claim ID</th>
                  <th>Patient</th>
                  <th>Hospital</th>
                  <th>Risk</th>
                  <th>Top Reason</th>
                  <th>Status</th>
                  <th>Date</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map(claim => (
                  <tr
                    key={claim.id}
                    className="claim-row"
                    tabIndex={0}
                    role="button"
                    aria-label={`Open claim ${claim.id}`}
                    onClick={() => navigate(`/claims/${claim.id}`)}
                    onKeyDown={e => e.key === 'Enter' && navigate(`/claims/${claim.id}`)}
                  >
                    <td className="col-id mono">
                      {highlight(claim.id, search)}
                    </td>
                    <td className="col-patient">
                      {highlight(claim.patient_name, search)}
                    </td>
                    <td className="col-hospital muted-cell">
                      {highlight(claim.hospital_name, search)}
                    </td>
                    <td className="col-risk">
                      <RiskBadge level={claim.risk_level} score={claim.risk_score} />
                    </td>
                    <td className="col-reason muted-cell">
                      {claim.risk_reasons[0] ?? '—'}
                    </td>
                    <td className="col-status">
                      <StatusChip status={claim.status} />
                    </td>
                    <td className="col-date mono">
                      {fmtDate(claim.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}
    </div>
  )
}

// ── summary card ──────────────────────────────────────────────────────────────

function SummaryCard({
  label, value, accent,
}: {
  label:   string
  value:   number
  accent?: 'red' | 'amber' | 'blue'
}) {
  return (
    <div className={`summary-card${accent ? ` summary-${accent}` : ''}`}>
      <span className="summary-value">{value}</span>
      <span className="summary-label">{label}</span>
    </div>
  )
}
