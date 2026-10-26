import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getAudit } from '../api'
import type { AuditEntry } from '../types'
import './AuditPage.css'

// ── constants ─────────────────────────────────────────────────────────────────

const ACTION_VARIANTS: Record<string, string> = {
  APPROVED:  'au-action-approved',
  ESCAL:     'au-action-escalated',
  QUER:      'au-action-queried',
  UPDAT:     'au-action-updated',
  FIELD:     'au-action-updated',
  CREATED:   'au-action-created',
  UPLOAD:    'au-action-created',
}

// ── helpers ───────────────────────────────────────────────────────────────────

function fmtTimestamp(iso: string) {
  const d = new Date(iso)
  return {
    date: d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }),
    time: d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }),
  }
}

function actionVariant(action: string): string {
  for (const [key, cls] of Object.entries(ACTION_VARIANTS)) {
    if (action.toUpperCase().includes(key)) return cls
  }
  return 'au-action-default'
}

function SnapshotBlock({ data, label }: { data: Record<string, unknown> | null; label: string }) {
  if (!data || Object.keys(data).length === 0) {
    return (
      <div className="au-snap-block au-snap-empty">
        <span className="au-snap-block-label">{label}</span>
        <span className="au-null">—</span>
      </div>
    )
  }
  return (
    <div className="au-snap-block">
      <span className="au-snap-block-label">{label}</span>
      <ul className="au-snapshot">
        {Object.entries(data).map(([k, v]) => (
          <li key={k}>
            <span className="au-snap-key">{k}</span>
            <span className="au-snap-sep">→</span>
            <span className="au-snap-val">{String(v)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function ActionBadge({ action }: { action: string }) {
  return <span className={`au-action ${actionVariant(action)}`}>{action}</span>
}

/** Convert visible filtered rows to a CSV string and trigger download */
function exportCsv(entries: AuditEntry[]) {
  const headers = ['ID', 'Timestamp', 'User', 'Action', 'Claim ID', 'Before', 'After']
  const rows = entries.map(e => [
    e.id,
    e.timestamp,
    e.user,
    e.action,
    e.claim_id,
    e.before ? JSON.stringify(e.before) : '',
    e.after  ? JSON.stringify(e.after)  : '',
  ].map(v => `"${String(v).replace(/"/g, '""')}"`).join(','))

  const csv  = [headers.join(','), ...rows].join('\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href     = url
  a.download = `audit-log-${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

// ── component ─────────────────────────────────────────────────────────────────

export default function AuditPage() {
  const navigate = useNavigate()

  const [entries,    setEntries]    = useState<AuditEntry[]>([])
  const [loading,    setLoading]    = useState(true)
  const [error,      setError]      = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<number | null>(null)

  // filters
  const [claimFilter,  setClaimFilter]  = useState('')
  const [userFilter,   setUserFilter]   = useState('')
  const [actionFilter, setActionFilter] = useState('')
  const [dateFrom,     setDateFrom]     = useState('')
  const [dateTo,       setDateTo]       = useState('')

  useEffect(() => {
    getAudit()
      .then(data => {
        const sorted = [...data].sort(
          (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime(),
        )
        setEntries(sorted)
      })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [])

  // unique option lists
  const uniqueUsers = useMemo(
    () => [...new Set(entries.map(e => e.user))].sort(),
    [entries],
  )
  const uniqueActions = useMemo(
    () => [...new Set(entries.map(e => e.action))].sort(),
    [entries],
  )
  const uniqueClaimIds = useMemo(
    () => [...new Set(entries.map(e => e.claim_id))].sort(),
    [entries],
  )

  const hasFilters = claimFilter || userFilter || actionFilter || dateFrom || dateTo

  const filtered = useMemo(() => {
    const cq = claimFilter.trim().toUpperCase()
    const uq = userFilter.trim().toLowerCase()
    const aq = actionFilter.trim().toUpperCase()
    const from = dateFrom ? new Date(dateFrom).getTime() : null
    const to   = dateTo   ? new Date(dateTo + 'T23:59:59').getTime() : null

    return entries.filter(e => {
      if (cq && !e.claim_id.toUpperCase().includes(cq)) return false
      if (uq && !e.user.toLowerCase().includes(uq))     return false
      if (aq && !e.action.toUpperCase().includes(aq))   return false
      const t = new Date(e.timestamp).getTime()
      if (from && t < from) return false
      if (to   && t > to)   return false
      return true
    })
  }, [entries, claimFilter, userFilter, actionFilter, dateFrom, dateTo])

  function clearFilters() {
    setClaimFilter(''); setUserFilter(''); setActionFilter('')
    setDateFrom(''); setDateTo('')
  }

  function toggleExpand(id: number) {
    setExpandedId(prev => (prev === id ? null : id))
  }

  // ── render ────────────────────────────────────────────────────────────────

  return (
    <div className="au-page">

      {/* ── heading ───────────────────────────────────────────────────── */}
      <div className="au-heading">
        <div className="au-heading-left">
          <h2 className="au-title">Audit Log</h2>
          <p className="au-subtitle">
            Immutable record of every state change. Supervisor access only.
          </p>
        </div>

        <div className="au-heading-right">
          {!loading && !error && (
            <>
              <span className="au-total-badge">
                {filtered.length} / {entries.length} entries
              </span>
              <button
                className="au-export-btn"
                onClick={() => exportCsv(filtered)}
                disabled={filtered.length === 0}
                title="Download filtered rows as CSV"
              >
                ↓ Export CSV
              </button>
            </>
          )}
        </div>
      </div>

      {/* ── filter bar ────────────────────────────────────────────────── */}
      <div className="au-filters">

        {/* claim ID */}
        <div className="au-search-wrap">
          <span className="au-search-icon">🔍</span>
          <input
            className="au-search-input"
            type="search"
            list="au-claim-ids"
            placeholder="Claim ID…"
            value={claimFilter}
            onChange={e => setClaimFilter(e.target.value)}
            aria-label="Filter by claim ID"
          />
          <datalist id="au-claim-ids">
            {uniqueClaimIds.map(id => <option key={id} value={id} />)}
          </datalist>
        </div>

        {/* user */}
        <select
          className="au-filter-select"
          value={userFilter}
          onChange={e => setUserFilter(e.target.value)}
          aria-label="Filter by user"
        >
          <option value="">All users</option>
          {uniqueUsers.map(u => <option key={u} value={u}>{u}</option>)}
        </select>

        {/* action */}
        <select
          className="au-filter-select"
          value={actionFilter}
          onChange={e => setActionFilter(e.target.value)}
          aria-label="Filter by action"
        >
          <option value="">All actions</option>
          {uniqueActions.map(a => <option key={a} value={a}>{a}</option>)}
        </select>

        {/* date from */}
        <div className="au-date-range">
          <input
            className="au-date-input"
            type="date"
            value={dateFrom}
            onChange={e => setDateFrom(e.target.value)}
            aria-label="From date"
            title="From date"
          />
          <span className="au-date-sep">–</span>
          <input
            className="au-date-input"
            type="date"
            value={dateTo}
            onChange={e => setDateTo(e.target.value)}
            aria-label="To date"
            title="To date"
          />
        </div>

        {hasFilters && (
          <button className="au-clear-btn" onClick={clearFilters}>
            ✕ Clear
          </button>
        )}
      </div>

      {/* ── states ────────────────────────────────────────────────────── */}
      {loading && (
        <div className="au-loading">
          <span className="au-spinner" /> Loading audit log…
        </div>
      )}

      {error && (
        <div className="au-error" role="alert">⚠️ {error}</div>
      )}

      {!loading && !error && filtered.length === 0 && (
        <div className="au-empty">
          No entries match the current filters.
          {hasFilters && (
            <button className="au-clear-btn inline" onClick={clearFilters}>
              Clear filters
            </button>
          )}
        </div>
      )}

      {/* ── table ─────────────────────────────────────────────────────── */}
      {!loading && !error && filtered.length > 0 && (
        <div className="au-table-wrap">
          <table className="au-table">
            <thead>
              <tr>
                <th className="col-ts">Timestamp</th>
                <th className="col-user">User</th>
                <th className="col-action">Action</th>
                <th className="col-claim">Claim</th>
                <th className="col-delta">Change</th>
                <th className="col-expand" aria-label="Expand" />
              </tr>
            </thead>
            <tbody>
              {filtered.map(entry => {
                const { date, time } = fmtTimestamp(entry.timestamp)
                const expanded = expandedId === entry.id

                // Build a compact one-liner for the collapsed "Change" cell
                const changeKeys = [
                  ...Object.keys(entry.before ?? {}),
                  ...Object.keys(entry.after  ?? {}),
                ]
                const uniqueKeys = [...new Set(changeKeys)]

                return (
                  <AuditRow
                    key={entry.id}
                    entry={entry}
                    date={date}
                    time={time}
                    expanded={expanded}
                    uniqueKeys={uniqueKeys}
                    onToggle={() => toggleExpand(entry.id)}
                    onClaimClick={() => navigate(`/claims/${entry.claim_id}`)}
                  />
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── row sub-component (avoids <> key warning inside tbody) ────────────────────

interface AuditRowProps {
  entry:       AuditEntry
  date:        string
  time:        string
  expanded:    boolean
  uniqueKeys:  string[]
  onToggle:    () => void
  onClaimClick: (e: React.MouseEvent) => void
}

function AuditRow({ entry, date, time, expanded, uniqueKeys, onToggle, onClaimClick }: AuditRowProps) {
  return (
    <>
      <tr
        className={`au-row${expanded ? ' au-row-expanded' : ''}`}
        onClick={onToggle}
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onKeyDown={e => e.key === 'Enter' && onToggle()}
      >
        {/* timestamp */}
        <td className="col-ts">
          <span className="au-date">{date}</span>
          <span className="au-time">{time}</span>
        </td>

        {/* user */}
        <td className="col-user">
          <span className="au-user-avatar" aria-hidden>{entry.user[0].toUpperCase()}</span>
          <span className="au-user-email">{entry.user}</span>
        </td>

        {/* action */}
        <td className="col-action">
          <ActionBadge action={entry.action} />
        </td>

        {/* claim link */}
        <td className="col-claim">
          <button
            className="au-claim-link"
            onClick={e => { e.stopPropagation(); onClaimClick(e) }}
            title={`Open claim ${entry.claim_id}`}
          >
            {entry.claim_id}
          </button>
        </td>

        {/* compact change summary */}
        <td className="col-delta">
          {uniqueKeys.length === 0 ? (
            <span className="au-null">—</span>
          ) : (
            <ul className="au-delta-summary">
              {uniqueKeys.map(k => {
                const before = entry.before?.[k]
                const after  = entry.after?.[k]
                return (
                  <li key={k}>
                    <span className="au-snap-key">{k}</span>
                    {before !== undefined && (
                      <span className="au-delta-before">{String(before)}</span>
                    )}
                    {before !== undefined && after !== undefined && (
                      <span className="au-delta-arrow">→</span>
                    )}
                    {after !== undefined && (
                      <span className="au-delta-after">{String(after)}</span>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
        </td>

        {/* chevron */}
        <td className="col-expand">
          <span className="au-chevron" aria-hidden>{expanded ? '▲' : '▼'}</span>
        </td>
      </tr>

      {/* expanded detail */}
      {expanded && (
        <tr className="au-detail-row">
          <td colSpan={6}>
            <div className="au-detail">
              <SnapshotBlock data={entry.before} label="Before" />
              <div className="au-detail-arrow" aria-hidden>→</div>
              <SnapshotBlock data={entry.after}  label="After"  />
              <div className="au-detail-meta">
                <p className="au-detail-label">Entry ID</p>
                <span className="au-mono">{entry.id}</span>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
