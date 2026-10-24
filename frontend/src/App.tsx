import { useCallback, useRef, useState } from 'react'
import axios from 'axios'
import './App.css'

// ── types ─────────────────────────────────────────────────────────────────────

interface LineItem {
  description: string
  amount: number
}

interface ExtractedFields {
  patient_name:    string | null
  hospital_name:   string | null
  policy_number:   string | null
  bill_date:       string | null
  admission_date:  string | null
  discharge_date:  string | null
  line_items:      LineItem[]
  total_amount:    number | null
  _note?:          string
}

interface PageText {
  page:  number
  text:  string
}

interface ClaimResult {
  filename:          string
  page_count?:       number
  extracted_fields?: ExtractedFields
  pages?:            PageText[]
  error?:            string
}

// ── helpers ───────────────────────────────────────────────────────────────────

const ACCEPTED = 'application/pdf,image/jpeg,image/png,image/bmp,image/tiff,image/webp'

function fmt(amount: number): string {
  return `Rs.\u00a0${amount.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function fileIcon(name: string): string {
  return /\.(jpe?g|png|bmp|tiff?|webp)$/i.test(name) ? '🖼️' : '📄'
}

function sumItems(items: LineItem[]): number {
  return items.reduce((s, i) => s + i.amount, 0)
}

const FIELD_LABELS: Record<string, string> = {
  patient_name:   'Patient Name',
  hospital_name:  'Hospital',
  policy_number:  'Policy Number',
  bill_date:      'Bill Date',
  admission_date: 'Admission Date',
  discharge_date: 'Discharge Date',
  total_amount:   'Total Amount',
}

// ── sub-components ────────────────────────────────────────────────────────────

function RawText({ pages }: { pages: PageText[] }) {
  const [open, setOpen] = useState(false)
  const combined = pages.map(p => `— Page ${p.page} —\n${p.text}`).join('\n\n')

  return (
    <div>
      <button className="raw-toggle" onClick={() => setOpen(o => !o)}>
        {open ? '▲' : '▼'} Raw text
      </button>
      {open && <pre className="raw-block">{combined}</pre>}
    </div>
  )
}

function FieldsTable({ fields }: { fields: ExtractedFields }) {
  const scalar = Object.entries(FIELD_LABELS).filter(([k]) => k !== 'total_amount')

  return (
    <div>
      <p className="section-label">Extracted Fields</p>
      {fields._note && (
        <div className="note-pill" style={{ marginBottom: '0.75rem' }}>
          ⚠️ {fields._note}
        </div>
      )}
      <div className="fields-grid">
        {scalar.map(([key, label]) => {
          const val = (fields as Record<string, unknown>)[key]
          return [
            <span className="field-key" key={`k-${key}`}>{label}</span>,
            <span className="field-val" key={`v-${key}`}>
              {val != null ? String(val) : <span className="null-val">—</span>}
            </span>,
          ]
        })}
      </div>
    </div>
  )
}

function LineItemsTable({ fields }: { fields: ExtractedFields }) {
  const items = fields.line_items ?? []
  const computedSum = sumItems(items)
  const reported    = fields.total_amount
  const mismatch    = reported != null && Math.abs(reported - computedSum) > 0.005

  return (
    <div>
      <p className="section-label">Line Items</p>
      {items.length === 0 ? (
        <p style={{ color: 'var(--muted)', fontSize: '0.875rem' }}>No line items found.</p>
      ) : (
        <table className="line-items-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Description</th>
              <th>Amount</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item, i) => (
              <tr key={i}>
                <td>{i + 1}</td>
                <td>{item.description}</td>
                <td>{fmt(item.amount)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td colSpan={2}>Sum of line items</td>
              <td>{fmt(computedSum)}</td>
            </tr>
            {reported != null && (
              <tr>
                <td colSpan={2}>
                  Total as printed
                  {mismatch && (
                    <span className="total-mismatch">
                      {' '}⚠ Discrepancy: {fmt(Math.abs(reported - computedSum))}
                    </span>
                  )}
                </td>
                <td className={mismatch ? 'total-mismatch' : undefined}>
                  {fmt(reported)}
                </td>
              </tr>
            )}
          </tfoot>
        </table>
      )}
    </div>
  )
}

function ResultCard({ result }: { result: ClaimResult }) {
  if (result.error) {
    return (
      <div className="card card-error">
        <div className="card-header">
          <span className="card-icon">⚠️</span>
          <span className="card-filename">{result.filename}</span>
        </div>
        <div className="card-error-msg">{result.error}</div>
      </div>
    )
  }

  const fields = result.extracted_fields!
  const pages  = result.pages ?? []

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-icon">{fileIcon(result.filename)}</span>
        <span className="card-filename">{result.filename}</span>
        {result.page_count != null && (
          <span className="card-pages">{result.page_count} page{result.page_count !== 1 ? 's' : ''}</span>
        )}
      </div>
      <div className="card-body">
        <FieldsTable fields={fields} />
        <LineItemsTable fields={fields} />
        {pages.length > 0 && <RawText pages={pages} />}
      </div>
    </div>
  )
}

// ── main app ──────────────────────────────────────────────────────────────────

export default function App() {
  const [files,    setFiles]    = useState<File[]>([])
  const [dragging, setDragging] = useState(false)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState<string | null>(null)
  const [results,  setResults]  = useState<ClaimResult[] | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // ── file management ──────────────────────────────────────────────────────

  function addFiles(incoming: FileList | null) {
    if (!incoming) return
    const next = Array.from(incoming)
    setFiles(prev => {
      const names = new Set(prev.map(f => f.name))
      return [...prev, ...next.filter(f => !names.has(f.name))]
    })
    setError(null)
  }

  function removeFile(name: string) {
    setFiles(prev => prev.filter(f => f.name !== name))
  }

  function clearAll() {
    setFiles([])
    setResults(null)
    setError(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  // ── drag-and-drop ────────────────────────────────────────────────────────

  const onDragOver  = useCallback((e: React.DragEvent) => { e.preventDefault(); setDragging(true)  }, [])
  const onDragLeave = useCallback((e: React.DragEvent) => { e.preventDefault(); setDragging(false) }, [])
  const onDrop      = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    addFiles(e.dataTransfer.files)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── upload ───────────────────────────────────────────────────────────────

  async function handleUpload() {
    if (files.length === 0) return
    setLoading(true)
    setError(null)
    setResults(null)

    const form = new FormData()
    files.forEach(f => form.append('files', f))

    try {
      const { data } = await axios.post<ClaimResult[]>(
        'http://localhost:8000/upload',
        form,
        { headers: { 'Content-Type': 'multipart/form-data' } },
      )
      setResults(data)
    } catch (err: unknown) {
      if (axios.isAxiosError(err)) {
        setError(
          err.response?.data?.detail
            ?? err.response?.data
            ?? err.message
            ?? 'Upload failed.',
        )
      } else {
        setError('Unexpected error. Is the backend running on port 8000?')
      }
    } finally {
      setLoading(false)
    }
  }

  // ── render ───────────────────────────────────────────────────────────────

  return (
    <div className="app">
      {/* ── main ── */}
      <main className="main">

        {/* upload panel */}
        <div className="upload-section">
          {/* drop zone */}
          <div
            className={`drop-zone${dragging ? ' dragging' : ''}`}
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
            onClick={() => inputRef.current?.click()}
          >
            <input
              ref={inputRef}
              type="file"
              multiple
              accept={ACCEPTED}
              onChange={e => addFiles(e.target.files)}
              tabIndex={-1}
            />
            <div className="drop-icon">📂</div>
            <p className="drop-label">
              <strong>Drag &amp; drop files here</strong> or click to browse
            </p>
            <p className="drop-hint">Accepts PDF, JPG, PNG, BMP, TIFF, WebP</p>
          </div>

          {/* selected file chips */}
          {files.length > 0 && (
            <div className="file-list">
              {files.map(f => (
                <span className="file-chip" key={f.name}>
                  {fileIcon(f.name)} {f.name}
                  <button
                    type="button"
                    aria-label={`Remove ${f.name}`}
                    onClick={e => { e.stopPropagation(); removeFile(f.name) }}
                  >
                    ✕
                  </button>
                </span>
              ))}
            </div>
          )}

          {/* actions */}
          <div className="actions">
            <button
              className="btn-upload"
              onClick={handleUpload}
              disabled={files.length === 0 || loading}
            >
              {loading ? <><span className="spinner" /> Processing…</> : '↑ Upload & Extract'}
            </button>
            {(files.length > 0 || results) && (
              <button className="btn-clear" onClick={clearAll} disabled={loading}>
                Clear
              </button>
            )}
          </div>

          {/* error */}
          {error && (
            <div className="error-banner" role="alert">
              ⚠️ <span>{error}</span>
            </div>
          )}
        </div>

        {/* results */}
        {results && results.length > 0 && (
          <section className="results">
            <p className="results-heading">
              {results.length} document{results.length !== 1 ? 's' : ''} processed
            </p>
            {results.map(r => (
              <ResultCard key={r.filename} result={r} />
            ))}
          </section>
        )}
      </main>
    </div>
  )
}
