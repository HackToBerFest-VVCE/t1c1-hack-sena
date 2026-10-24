import { useEffect, useState } from 'react'
import { getAudit } from '../api'
import type { AuditEntry } from '../types'
import './StubPage.css'

export default function AuditPage() {
  const [entries, setEntries] = useState<AuditEntry[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getAudit().then(data => { setEntries(data); setLoading(false) })
  }, [])

  return (
    <div className="stub-page">
      <h2>Audit Log</h2>
      {loading ? (
        <p className="stub-muted">Loading…</p>
      ) : entries.length === 0 ? (
        <p className="stub-muted">No audit entries yet.</p>
      ) : (
        <table className="audit-table">
          <thead>
            <tr>
              <th>Timestamp</th>
              <th>User</th>
              <th>Action</th>
              <th>Claim</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(e => (
              <tr key={e.id}>
                <td className="mono">{new Date(e.timestamp).toLocaleString('en-IN')}</td>
                <td>{e.user}</td>
                <td><span className="audit-action">{e.action}</span></td>
                <td className="mono">{e.claim_id}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
