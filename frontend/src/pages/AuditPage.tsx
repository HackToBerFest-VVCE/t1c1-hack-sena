import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getAudit } from '../api'
import type { AuditEntry } from '../types'
import './AuditPage.css'

// ── helpers ───────────────────────────────────────────────────────────────────

function fmtTimestamp(iso: string) {
  const d = new Date(iso)
  return {
    date: d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }),
    time: d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }),
  }
}

/** Render a before/after snapshot as "key: value" lines */
function SnapshotCell({ data }: { data: Record<string, unknown> | null }) {
  if (!data) return <span className="au-null">—</span>
  const entries = Object.entries(data)
  if (entries.length === 0) return <span className="au-null">—</span>
  return (
    <ul className="au-snapshot">
      {entries.map(([k, v]) => (
        <li key={k}>
          <span className="au-snap-key">{k}</span>
          <span className="au-snap-sep">→</span>
          <span className="au-snap-val">{String(v)}</span>
        </li>
      ))}
    </ul>
  )
}

/** Colour-code the action verb */
function ActionBadge({ action }: { action: string }) {
  let variant = 'au-action-default'
  if (action.includes('APPROVED'))  variant = 'au-action-approved'
  else if (action.includes('ESCAL')) variant = 'au-action-escalated'
  else if (action.includes('QUER'))  variant = 'au-action-queried'
  else if (action.includes('UPDAT') || action.includes('FIELD')) variant = 'au-action-updated'

  return <span className={`au-action ${variant}`}>{action}</span>
}

// ── component ─────────────────────────────────────────────────────────────────

export default function AuditPage() {
  const navigate = useNavigate()

  const [entries,   setEntries]   = useState<AuditEntry[]>([])
  const [loading,   setLoading]   = useState(true)
  const [error,     setError]     = useState<string | null>(null)
  const [claimFilter, setClaimFilter] = useState('')
  const [expandedId,  setExpandedId]  = useState<string | null>(null)

  useEffect(() => {
    getAudit()
      .then(data => {
        // sort newest first
        const sorted = [...data].sort(
          (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime(),
        )
        setEntries(sorted)
      })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [])

  // unique claim IDs for the datalist
  const claimIds = useMemo(
    () => [...new Set(entries.map(e => e.claim_id))].sort(),
    [entries],
  )

  const filtered = useMemo(() => {
    const q = claimFilter.trim().toUpperCase()
    return q ? entries.filter(e => e.claim_id.toUpperCase().includes(q)) : entries
  }, [entries, claimFilter])

  function toggleExpand(id: string) {
    setExpandedId(prev => (prev === id ? null : id))
  }

  // ── render ────────────────────────────────────────────────────────────────

  return (
    <div className="au-page">

      {/* ── page heading ──────────────────────────────────────────────── */}
      <div className="au-heading">
        <div className="au-heading-left">
          <h2 className="au-title">Audit Log</h2>
          <p className="au-subtitle">
            Immutable record of every state change. Supervisor access only.
          </p>
        </div>

        {!loading && !error && (
          <span className="au-total-badge">
            {filtered.length} / {entries.length} entries
          </span>
        )}
      </div>

      {/* ── filter bar ────────────────────────────────────────────────── */}
      <div className="au-filters">
        <div className="au-search-wrap">
          <span className="au-search-icon">🔍</span>
          <input
            id="au-claim-filter"
            className="au-search-input"
            type="search"
            list="au-claim-ids"
            placeholder="Filter by Claim ID…"
            value={claimFilter}
            onChange={e => setClaimFilter(e.target.value)}
          />
          <datalist id="au-claim-ids">
            {claimIds.map(id => <option key={id} value={id} />)}
          </datalist>
        </div>

        {claimFilter && (
          <button className="au-clear-btn" onClick={() => setClaimFilter('')}>
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
          No entries match{claimFilter ? ` claim "${claimFilter}"` : ''}.
          {claimFilter && (
            <button className="au-clear-btn inline" onClick={() => setClaimFilter('')}>
              Clear filter
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
                <th className="col-before">Before</th>
                <th className="col-after">After</th>
                <th className="col-expand" aria-label="Expand row" />
              </tr>
            </thead>
            <tbody>
              {filtered.map(entry => {
                const { date, time } = fmtTimestamp(entry.timestamp)
                const expanded = expandedId === entry.id

                return (
                  <>
                    <tr
                      key={entry.id}
                      className={`au-row${expanded ? ' au-row-expanded' : ''}`}
                      onClick={() => toggleExpand(entry.id)}
                      role="button"
                      tabIndex={0}
                      aria-expanded={expanded}
                      onKeyDown={e => e.key === 'Enter' && toggleExpand(entry.id)}
                    >
                      {/* timestamp */}
                      <td className="col-ts">
                        <span className="au-date">{date}</span>
                        <span className="au-time">{time}</span>
                      </td>

                      {/* user */}
                      <td className="col-user">
                        <span className="au-user-avatar">{entry.user[0].toUpperCase()}</span>
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
                          onClick={e => { e.stopPropagation(); navigate(`/claims/${entry.claim_id}`) }}
                          title={`Open claim ${entry.claim_id}`}
                        >
                          {entry.claim_id}
                        </button>
                      </td>

                      {/* before (collapsed: single line) */}
                      <td className="col-before au-snap-collapsed">
                        <SnapshotCell data={entry.before} />
                      </td>

                      {/* after (collapsed: single line) */}
                      <td className="col-after au-snap-collapsed">
                        <SnapshotCell data={entry.after} />
                      </td>

                      {/* expand toggle */}
                      <td className="col-expand">
                        <span className="au-chevron" aria-hidden>
                          {expanded ? '▲' : '▼'}
                        </span>
                      </td>
                    </tr>

                    {/* expanded detail row */}
                    {expanded && (
                      <tr key={`${entry.id}-detail`} className="au-detail-row">
                        <td colSpan={7}>
                          <div className="au-detail">
                            <div className="au-detail-col">
                              <p className="au-detail-label">Before</p>
                              <SnapshotCell data={entry.before} />
                            </div>
                            <div className="au-detail-arrow">→</div>
                            <div className="au-detail-col">
                              <p className="au-detail-label">After</p>
                              <SnapshotCell data={entry.after} />
                            </div>
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
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
