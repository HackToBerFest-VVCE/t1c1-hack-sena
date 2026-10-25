import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getClaims } from '../api'
import type { Claim, ClaimStatus, RiskLevel } from '../types'
import './DashboardPage.css'

// ── static option lists ───────────────────────────────────────────────────────

const RISK_LEVELS: RiskLevel[]   = ['High', 'Medium', 'Low']
const STATUSES:    ClaimStatus[] = ['pending', 'queried', 'escalated', 'approved']

// ── sort config ───────────────────────────────────────────────────────────────

type SortKey = 'risk_score' | 'patient_name' | 'hospital_name' | 'status' | 'created_at'
type SortDir = 'asc' | 'desc'

const RISK_ORDER: Record<RiskLevel, number> = { High: 3, Medium: 2, Low: 1 }

function sortClaims(claims: Claim[], key: SortKey, dir: SortDir): Claim[] {
  const sorted = [...claims].sort((a, b) => {
    let cmp = 0
    if (key === 'risk_score') {
      // sort by risk_score, but also secondary-sort by risk_level ordinal
      cmp = (b.risk_score - a.risk_score) || (RISK_ORDER[b.risk_level] - RISK_ORDER[a.risk_level])
    } else if (key === 'patient_name' || key === 'hospital_name' || key === 'status') {
      cmp = a[key].localeCompare(b[key])
    } else if (key === 'created_at') {
      cmp = a.created_at.localeCompare(b.created_at)
    }
    return dir === 'asc' ? cmp : -cmp
  })
  return sorted
}

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

interface ThProps {
  label:     string
  sortKey:   SortKey
  active:    SortKey
  dir:       SortDir
  onSort:    (key: SortKey) => void
  className?: string
}

function Th({ label, sortKey, active, dir, onSort, className }: ThProps) {
  const isActive = active === sortKey
  return (
    <th
      className={`sortable-th${isActive ? ' th-active' : ''}${className ? ` ${className}` : ''}`}
      onClick={() => onSort(sortKey)}
      aria-sort={isActive ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}
    >
      {label}
      <span className="sort-icon" aria-hidden>
        {isActive ? (dir === 'asc' ? ' ▲' : ' ▼') : ' ⇅'}
      </span>
    </th>
  )
}

// ── main component ────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const navigate = useNavigate()

  const [claims,  setClaims]  = useState<Claim[]>([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  // filter state
  const [search,       setSearch]       = useState('')
  const [riskFilter,   setRiskFilter]   = useState<RiskLevel | ''>('')
  const [statusFilter, setStatusFilter] = useState<ClaimStatus | ''>('')

  // sort state — default: risk_score descending
  const [sortKey, setSortKey] = useState<SortKey>('risk_score')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  useEffect(() => {
    getClaims()
      .then(setClaims)
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [])

  function handleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      // risk_score always starts desc; others start asc
      setSortDir(key === 'risk_score' ? 'desc' : 'asc')
    }
  }

  // sort, then filter
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return sortClaims(claims, sortKey, sortDir)
      .filter(c => !riskFilter   || c.risk_level === riskFilter)
      .filter(c => !statusFilter || c.status     === statusFilter)
      .filter(c => {
        if (!q) return true
        return (
          c.id.toLowerCase().includes(q)            ||
          c.patient_name.toLowerCase().includes(q)  ||
          c.hospital_name.toLowerCase().includes(q)
        )
      })
  }, [claims, sortKey, sortDir, search, riskFilter, statusFilter])

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

  const thProps = { active: sortKey, dir: sortDir, onSort: handleSort }

  // ── render ─────────────────────────────────────────────────────────────────
  return (
    <div className="queue-page">

      {/* ── page title + summary cards ─── */}
      <div className="queue-top">
        <h2 className="queue-title">Claims Queue</h2>
        <div className="summary-cards">
          <SummaryCard label="Total"     value={counts.total}                  />
          <SummaryCard label="High Risk" value={counts.high}      accent="red"   />
          <SummaryCard label="Pending"   value={counts.pending}   accent="amber" />
          <SummaryCard label="Escalated" value={counts.escalated} accent="blue"  />
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
                  <th className="col-id">Claim ID</th>
                  <Th label="Patient"  sortKey="patient_name"  {...thProps} className="col-patient"  />
                  <Th label="Hospital" sortKey="hospital_name" {...thProps} className="col-hospital" />
                  <Th label="Risk"     sortKey="risk_score"    {...thProps} className="col-risk"     />
                  <th className="col-reason">Top Reason</th>
                  <Th label="Status"   sortKey="status"        {...thProps} className="col-status"   />
                  <Th label="Date"     sortKey="created_at"    {...thProps} className="col-date"     />
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
