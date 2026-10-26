import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { uploadClaim } from '../api'
import type { DocumentType } from '../types'
import './UploadPage.css'

// ── constants ─────────────────────────────────────────────────────────────────

const ACCEPT      = '.pdf,.jpg,.jpeg,.png,.bmp,.tiff,.tif,.webp'
const BACKEND_URL = 'http://localhost:8000'

const DOC_TYPES: { value: DocumentType; label: string }[] = [
  { value: 'hospital_bill',     label: 'Hospital Bill'      },
  { value: 'claim_form',        label: 'Claim Form'         },
  { value: 'discharge_summary', label: 'Discharge Summary'  },
  { value: 'prescription',      label: 'Prescription'       },
]

type UploadStage = 'idle' | 'uploading' | 'processing' | 'done' | 'error'

// ── helpers ───────────────────────────────────────────────────────────────────

function fileIcon(name: string) {
  return /\.(jpe?g|png|bmp|tiff?|webp)$/i.test(name) ? '🖼️' : '📄'
}

function formatBytes(bytes: number) {
  if (bytes < 1024)      return `${bytes} B`
  if (bytes < 1048576)   return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1048576).toFixed(1)} MB`
}

/** Auto-guess a document type from the filename */
function guessDocType(name: string): DocumentType {
  const n = name.toLowerCase()
  if (n.includes('claim') || n.includes('form'))        return 'claim_form'
  if (n.includes('discharge') || n.includes('summary')) return 'discharge_summary'
  if (n.includes('prescription') || n.includes('rx'))   return 'prescription'
  return 'hospital_bill'
}

// ── FileEntry: a file + its user-assigned document type ──────────────────────

interface FileEntry {
  id:      string   // local UUID so React keys are stable
  file:    File
  docType: DocumentType
}

let _id = 0
function nextId() { return String(++_id) }

// ── component ─────────────────────────────────────────────────────────────────

export default function UploadPage() {
  const navigate = useNavigate()

  const [entries,        setEntries]        = useState<FileEntry[]>([])
  const [dragging,       setDragging]       = useState(false)
  const [stage,          setStage]          = useState<UploadStage>('idle')
  const [error,          setError]          = useState<string | null>(null)
  const [extractionMode, setExtractionMode] = useState<'gemma' | 'regex' | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const busy = stage === 'uploading' || stage === 'processing' || stage === 'done'

  // Fetch extraction mode from /health on mount so the processing message is accurate
  useEffect(() => {
    fetch(`${BACKEND_URL}/health`)
      .then(r => r.json())
      .then((d: { extraction_mode?: string }) => {
        setExtractionMode(d.extraction_mode === 'gemma' ? 'gemma' : 'regex')
      })
      .catch(() => { /* non-critical – ignore */ })
  }, [])

  // ── file management ──────────────────────────────────────────────────────

  function addFiles(incoming: FileList | null) {
    if (!incoming) return
    const list = Array.from(incoming)
    setEntries(prev => {
      const existingNames = new Set(prev.map(e => e.file.name))
      const fresh: FileEntry[] = list
        .filter(f => !existingNames.has(f.name))
        .map(f => ({ id: nextId(), file: f, docType: guessDocType(f.name) }))
      return [...prev, ...fresh]
    })
    setError(null)
    if (stage === 'error') setStage('idle')
  }

  function removeEntry(id: string) {
    setEntries(prev => prev.filter(e => e.id !== id))
  }

  function setDocType(id: string, docType: DocumentType) {
    setEntries(prev => prev.map(e => e.id === id ? { ...e, docType } : e))
  }

  function clearAll() {
    setEntries([])
    setError(null)
    setStage('idle')
    if (inputRef.current) inputRef.current.value = ''
  }

  // ── drag and drop ────────────────────────────────────────────────────────

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault(); setDragging(true)
  }, [])

  const onDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault(); setDragging(false)
  }, [])

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault(); setDragging(false)
    addFiles(e.dataTransfer.files)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── upload ───────────────────────────────────────────────────────────────

  async function handleUpload() {
    if (entries.length === 0 || busy) return
    setError(null)

    try {
      setStage('uploading')
      await new Promise(r => setTimeout(r, 250))   // let UI update

      setStage('processing')
      const claim = await uploadClaim(entries.map(e => e.file))

      setStage('done')
      await new Promise(r => setTimeout(r, 500))
      navigate(`/claims/${claim.id}`)
    } catch (err: unknown) {
      setStage('error')
      setError(
        err instanceof Error ? err.message : 'Upload failed. Please try again.',
      )
    }
  }

  // ── derived ──────────────────────────────────────────────────────────────

  const totalSize  = entries.reduce((s, e) => s + e.file.size, 0)
  const inProgress = stage === 'uploading' || stage === 'processing'

  // ── render ───────────────────────────────────────────────────────────────

  return (
    <div className="up-page">

      {/* ── heading ───────────────────────────────────────────────────── */}
      <div className="up-heading">
        <h2 className="up-title">Upload Claim Bundle</h2>
        <p className="up-subtitle">
          Add all documents for one claim — bill, claim form, discharge summary,
          prescriptions. The system will extract fields and open the claim for review.
        </p>
      </div>

      {/* ── upload card ───────────────────────────────────────────────── */}
      <div className="up-card">

        {/* ── drop zone ─────────────────────────────────────────────── */}
        <div
          className={`up-drop${dragging ? ' dragging' : ''}${busy ? ' up-drop-busy' : ''}`}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          onClick={() => !busy && inputRef.current?.click()}
          role="button"
          tabIndex={busy ? -1 : 0}
          aria-label="Drop files here or click to browse"
          onKeyDown={e => e.key === 'Enter' && !busy && inputRef.current?.click()}
        >
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={ACCEPT}
            style={{ display: 'none' }}
            onChange={e => addFiles(e.target.files)}
            disabled={busy}
          />

          {inProgress ? (
            <div className="up-progress-inner">
              <span className="up-spinner" />
              <span className="up-progress-msg">
                {stage === 'uploading'
                  ? 'Uploading files…'
                  : extractionMode === 'gemma'
                    ? 'Extracting fields with Gemma 4… this can take up to a minute'
                    : 'Extracting claim data…'}
              </span>
            </div>
          ) : stage === 'done' ? (
            <div className="up-progress-inner up-done">
              <span className="up-done-icon">✓</span>
              <span className="up-progress-msg">Done — opening claim…</span>
            </div>
          ) : (
            <>
              <div className="up-drop-icon">📂</div>
              <p className="up-drop-label">
                <strong>Drag &amp; drop files here</strong>
                <span> or click to browse</span>
              </p>
              <p className="up-drop-hint">PDF · JPG · PNG · BMP · TIFF · WebP</p>
            </>
          )}
        </div>

        {/* ── file list ─────────────────────────────────────────────── */}
        {entries.length > 0 && (
          <div className="up-file-list">
            <div className="up-file-list-header">
              <span className="up-file-count">
                {entries.length} file{entries.length !== 1 ? 's' : ''}
                {totalSize > 0 && (
                  <span className="up-total-size"> · {formatBytes(totalSize)}</span>
                )}
              </span>
              {!busy && (
                <button className="up-clear-all" onClick={clearAll}>
                  Remove all
                </button>
              )}
            </div>

            <ul className="up-files" aria-label="Selected files">
              {entries.map(entry => (
                <li
                  key={entry.id}
                  className={`up-file-item${busy ? ' up-file-item-busy' : ''}`}
                >
                  {/* icon */}
                  <span className="up-file-icon" aria-hidden>
                    {fileIcon(entry.file.name)}
                  </span>

                  {/* name + size */}
                  <div className="up-file-info">
                    <span className="up-file-name" title={entry.file.name}>
                      {entry.file.name}
                    </span>
                    <span className="up-file-size">{formatBytes(entry.file.size)}</span>
                  </div>

                  {/* document type selector */}
                  {!busy ? (
                    <select
                      className="up-doc-type-select"
                      value={entry.docType}
                      onChange={e => setDocType(entry.id, e.target.value as DocumentType)}
                      aria-label={`Document type for ${entry.file.name}`}
                      title="Document type"
                    >
                      {DOC_TYPES.map(dt => (
                        <option key={dt.value} value={dt.value}>{dt.label}</option>
                      ))}
                    </select>
                  ) : (
                    <span className="up-doc-type-label">
                      {DOC_TYPES.find(dt => dt.value === entry.docType)?.label}
                    </span>
                  )}

                  {/* remove / spinner */}
                  {!busy ? (
                    <button
                      className="up-remove-btn"
                      onClick={() => removeEntry(entry.id)}
                      aria-label={`Remove ${entry.file.name}`}
                      title="Remove"
                    >✕</button>
                  ) : (
                    <span className="up-file-status-spinner">
                      <span className="up-mini-spinner" />
                    </span>
                  )}
                </li>
              ))}
            </ul>

            {/* doc-type legend */}
            {!busy && (
              <p className="up-doctype-hint">
                ✎ Adjust document types if the auto-detection is wrong.
              </p>
            )}
          </div>
        )}

        {/* ── error ─────────────────────────────────────────────────── */}
        {error && (
          <div className="up-error" role="alert">
            <span className="up-error-icon">⚠️</span>
            <span>{error}</span>
            <button
              className="up-error-dismiss"
              onClick={() => setError(null)}
              aria-label="Dismiss error"
            >✕</button>
          </div>
        )}

        {/* ── actions ───────────────────────────────────────────────── */}
        <div className="up-actions">
          <button
            className="up-submit-btn"
            onClick={handleUpload}
            disabled={entries.length === 0 || busy}
          >
            {inProgress
              ? <><span className="up-btn-spinner" />{stage === 'uploading' ? 'Uploading…' : extractionMode === 'gemma' ? 'Extracting with Gemma 4…' : 'Processing…'}</>
              : stage === 'done'
                ? '✓ Done'
                : `↑ Upload & Create Claim`}
          </button>

          {(entries.length > 0 || error) && !busy && (
            <button className="up-secondary-btn" onClick={clearAll}>
              Clear all
            </button>
          )}
        </div>

      </div>

      {/* ── checklist ─────────────────────────────────────────────────── */}
      <div className="up-tips">
        <p className="up-tips-title">Typical claim bundle</p>
        <ul className="up-tips-list">
          <li>
            <span className="up-tip-icon">📋</span>
            <span><strong>Claim Form</strong> — signed by insured</span>
          </li>
          <li>
            <span className="up-tip-icon">🏥</span>
            <span><strong>Hospital Bill</strong> — itemised tax invoice</span>
          </li>
          <li>
            <span className="up-tip-icon">📑</span>
            <span><strong>Discharge Summary</strong> — diagnosis &amp; procedure</span>
          </li>
          <li>
            <span className="up-tip-icon">💊</span>
            <span><strong>Prescriptions / Lab reports</strong> — supporting docs</span>
          </li>
        </ul>
      </div>

    </div>
  )
}
