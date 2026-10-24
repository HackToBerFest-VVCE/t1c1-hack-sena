import { useCallback, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { uploadClaim } from '../api'
import './UploadPage.css'

// ── constants ─────────────────────────────────────────────────────────────────

const ACCEPT = '.pdf,.jpg,.jpeg,.png,.bmp,.tiff,.tif,.webp'
const ACCEPT_MIME = 'application/pdf,image/jpeg,image/png,image/bmp,image/tiff,image/webp'

type UploadStage = 'idle' | 'uploading' | 'processing' | 'done' | 'error'

// ── helpers ───────────────────────────────────────────────────────────────────

function fileIcon(name: string) {
  return /\.(jpe?g|png|bmp|tiff?|webp)$/i.test(name) ? '🖼️' : '📄'
}

function formatBytes(bytes: number) {
  if (bytes < 1024)       return `${bytes} B`
  if (bytes < 1024 ** 2)  return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`
}

// ── stage messages ────────────────────────────────────────────────────────────

const STAGE_MSG: Record<UploadStage, string> = {
  idle:       '',
  uploading:  'Uploading files…',
  processing: 'Extracting claim data…',
  done:       'Done — redirecting…',
  error:      '',
}

// ── component ─────────────────────────────────────────────────────────────────

export default function UploadPage() {
  const navigate = useNavigate()

  const [files,    setFiles]    = useState<File[]>([])
  const [dragging, setDragging] = useState(false)
  const [stage,    setStage]    = useState<UploadStage>('idle')
  const [error,    setError]    = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const busy = stage === 'uploading' || stage === 'processing' || stage === 'done'

  // ── file management ──────────────────────────────────────────────────────

  function addFiles(incoming: FileList | null | File[]) {
    if (!incoming) return
    const list = incoming instanceof FileList ? Array.from(incoming) : incoming
    setFiles(prev => {
      const names = new Set(prev.map(f => f.name))
      return [...prev, ...list.filter(f => !names.has(f.name))]
    })
    setError(null)
    if (stage === 'error') setStage('idle')
  }

  function removeFile(name: string) {
    setFiles(prev => prev.filter(f => f.name !== name))
  }

  function clearAll() {
    setFiles([])
    setError(null)
    setStage('idle')
    if (inputRef.current) inputRef.current.value = ''
  }

  // ── drag and drop ────────────────────────────────────────────────────────

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(true)
  }, [])

  const onDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
  }, [])

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    addFiles(e.dataTransfer.files)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── upload ───────────────────────────────────────────────────────────────

  async function handleUpload() {
    if (files.length === 0 || busy) return
    setError(null)

    try {
      setStage('uploading')
      // small pause so the user sees the uploading state before mock resolves
      await new Promise(r => setTimeout(r, 300))

      setStage('processing')
      const claim = await uploadClaim(files)

      setStage('done')
      // brief "done" flash before navigating
      await new Promise(r => setTimeout(r, 600))
      navigate(`/claims/${claim.id}`)
    } catch (err: unknown) {
      setStage('error')
      setError(
        err instanceof Error
          ? err.message
          : 'Upload failed. Please try again.',
      )
    }
  }

  // ── render ───────────────────────────────────────────────────────────────

  const totalSize  = files.reduce((s, f) => s + f.size, 0)
  const inProgress = stage === 'uploading' || stage === 'processing'

  return (
    <div className="up-page">

      {/* ── page heading ──────────────────────────────────────────────── */}
      <div className="up-heading">
        <h2 className="up-title">Upload Claim Documents</h2>
        <p className="up-subtitle">
          Upload one or more PDFs or images. The system will extract claim data
          and create a new claim record.
        </p>
      </div>

      {/* ── upload card ───────────────────────────────────────────────── */}
      <div className="up-card">

        {/* drop zone */}
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
              <span className="up-progress-msg">{STAGE_MSG[stage]}</span>
            </div>
          ) : stage === 'done' ? (
            <div className="up-progress-inner up-done">
              <span className="up-done-icon">✓</span>
              <span className="up-progress-msg">{STAGE_MSG.done}</span>
            </div>
          ) : (
            <>
              <div className="up-drop-icon">📂</div>
              <p className="up-drop-label">
                <strong>Drag &amp; drop files here</strong>
                <span> or click to browse</span>
              </p>
              <p className="up-drop-hint">
                PDF, JPG, PNG, BMP, TIFF, WebP — multiple files allowed
              </p>
            </>
          )}
        </div>

        {/* ── file list ─────────────────────────────────────────────── */}
        {files.length > 0 && (
          <div className="up-file-list">
            <div className="up-file-list-header">
              <span className="up-file-count">
                {files.length} file{files.length !== 1 ? 's' : ''} selected
                {totalSize > 0 && <span className="up-total-size"> · {formatBytes(totalSize)}</span>}
              </span>
              {!busy && (
                <button className="up-clear-all" onClick={clearAll}>
                  Remove all
                </button>
              )}
            </div>

            <ul className="up-files">
              {files.map(f => (
                <li key={f.name} className={`up-file-item${busy ? ' up-file-item-busy' : ''}`}>
                  <span className="up-file-icon">{fileIcon(f.name)}</span>
                  <span className="up-file-name" title={f.name}>{f.name}</span>
                  <span className="up-file-size">{formatBytes(f.size)}</span>
                  {!busy && (
                    <button
                      className="up-remove-btn"
                      onClick={() => removeFile(f.name)}
                      aria-label={`Remove ${f.name}`}
                      title="Remove"
                    >
                      ✕
                    </button>
                  )}
                  {busy && <span className="up-file-status-spinner"><span className="up-mini-spinner" /></span>}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* ── error ─────────────────────────────────────────────────── */}
        {error && (
          <div className="up-error" role="alert">
            <span className="up-error-icon">⚠️</span>
            <span>{error}</span>
            <button className="up-error-dismiss" onClick={() => setError(null)} aria-label="Dismiss error">✕</button>
          </div>
        )}

        {/* ── actions ───────────────────────────────────────────────── */}
        <div className="up-actions">
          <button
            className="up-submit-btn"
            onClick={handleUpload}
            disabled={files.length === 0 || busy}
          >
            {inProgress
              ? <><span className="up-btn-spinner" /> {STAGE_MSG[stage]}</>
              : stage === 'done'
                ? '✓ Done'
                : `↑ Upload & Create Claim`}
          </button>

          {(files.length > 0 || error) && !busy && (
            <button className="up-secondary-btn" onClick={clearAll}>
              Clear
            </button>
          )}
        </div>

      </div>

      {/* ── tips ──────────────────────────────────────────────────────── */}
      <div className="up-tips">
        <p className="up-tips-title">What to upload</p>
        <ul className="up-tips-list">
          <li>Hospital bill / tax invoice (PDF)</li>
          <li>Claim form (PDF or image)</li>
          <li>Discharge summary (PDF)</li>
          <li>Prescriptions or lab reports (PDF or image)</li>
        </ul>
      </div>

    </div>
  )
}
