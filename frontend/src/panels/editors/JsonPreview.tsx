import { useCallback, useMemo, useRef, useState } from "react"
import {
  ChevronRight,
  ChevronDown,
  Copy,
  Download,
  RefreshCw,
  Loader2,
  AlertTriangle,
  Check,
} from "lucide-react"
import { writeClipboardText, downloadTextFile, clipboardWriteAvailable } from "./shared/tableClipboard"

// ─── JsonPreview ──────────────────────────────────────────────────
//
// A reusable EXPANDABLE JSON viewer for the OUTPUT editor's two previews:
//   1. the assembled-output preview (the whole response document), and
//   2. each frame's input-data preview (that frame's rows).
//
// Both are JSON documents (not the tab-separated mapping tables FrameTableActions
// serves), so this component owns a JSON-specific Copy + Export(download) pair —
// mirroring FrameTableActions' degrade-gracefully behaviour (clipboard disabled
// in a non-secure context; download skipped when the DOM/URL APIs are missing).
//
// The header row carries the chevron toggle, the title, an optional refresh
// button, and the Copy/Export affordances. Copy + Export are present whether the
// body is collapsed or expanded (they act on the same `rows` either way). The
// body renders only when expanded.
//
// PRESENTATIONAL: the host supplies `rows` (already capped to the chunk/row
// limit), the `totalRows` it was capped from (so the truncation note never
// silently hides rows), plus optional loading/error/refresh wiring. It never
// reaches into a particular editor's config shape.

/** Pretty-printed JSON (2-space) of an array of rows. */
function prettyJson(rows: unknown[]): string {
  return JSON.stringify(rows, null, 2)
}

export function JsonPreview({
  testIdPrefix,
  title,
  /** Rows to render — ALREADY capped to the chunk/row limit by the host. */
  rows,
  /** The pre-cap total, so a truncated preview shows "showing N of M". When
   * omitted or <= rows.length, no truncation note renders. */
  totalRows,
  /** Base filename (no extension) for the Export download. */
  filename,
  isOpen,
  onToggle,
  /** Optional refresh — when supplied, a refresh button renders in the header
   * (e.g. re-run the dry-run). Omit for a static preview (frame input data). */
  onRefresh,
  loading = false,
  error = null,
  /** Optional NON-blocking advisory shown above the body (e.g. a data caveat).
   * Unlike `error`, it renders ALONGSIDE the rows rather than replacing them. */
  note = null,
  /** Optional empty-state line shown (when expanded, not loading, no error) if
   * `rows` is empty. Defaults to a generic message. */
  emptyMessage = "No rows to preview.",
}: {
  testIdPrefix: string
  title: string
  rows: unknown[]
  totalRows?: number
  filename: string
  isOpen: boolean
  onToggle: () => void
  onRefresh?: () => void
  loading?: boolean
  error?: string | null
  note?: string | null
  emptyMessage?: string
}) {
  // Transient "copied / saved" acknowledgement + transient copy/export error.
  const [ack, setAck] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const ackTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const flashAck = useCallback((msg: string) => {
    setActionError(null)
    setAck(msg)
    if (ackTimer.current) clearTimeout(ackTimer.current)
    ackTimer.current = setTimeout(() => setAck(null), 1500)
  }, [])

  const json = useMemo(() => prettyJson(rows), [rows])
  const clipboardOk = clipboardWriteAvailable()
  const truncated = typeof totalRows === "number" && totalRows > rows.length

  const onCopy = useCallback(() => {
    writeClipboardText(json).then(
      () => flashAck("Copied"),
      (e: unknown) => setActionError(e instanceof Error ? e.message : "Copy failed"),
    )
  }, [json, flashAck])

  const onExport = useCallback(() => {
    const ok = downloadTextFile(json, `${filename}.json`, "application/json")
    if (ok) flashAck("Exported")
    else setActionError("Download is unavailable in this context.")
  }, [json, filename, flashAck])

  const iconBtn =
    "p-1 rounded flex items-center justify-center disabled:opacity-40 disabled:cursor-not-allowed"
  const iconStyle = { color: "var(--text-muted)" }

  return (
    <div
      data-testid={testIdPrefix}
      className="rounded-md"
      style={{ border: "1px solid var(--border)", background: "var(--bg-soft)" }}
    >
      <div className="flex items-center gap-2 px-2 py-2">
        <button
          data-testid={`${testIdPrefix}-toggle`}
          onClick={onToggle}
          className="flex items-center gap-1 flex-1 min-w-0 text-left"
          title={isOpen ? "Collapse preview" : "Expand preview"}
        >
          {isOpen ? (
            <ChevronDown size={14} style={{ color: "var(--text-muted)" }} className="shrink-0" />
          ) : (
            <ChevronRight size={14} style={{ color: "var(--text-muted)" }} className="shrink-0" />
          )}
          <span
            className="text-[11px] font-semibold truncate"
            style={{ color: "var(--text-primary)" }}
          >
            {title}
          </span>
          {truncated && (
            <span
              data-testid={`${testIdPrefix}-truncation`}
              className="text-[10px] shrink-0"
              style={{ color: "var(--text-muted)" }}
              title={`Showing the first ${rows.length} of ${totalRows} rows`}
            >
              showing {rows.length} of {totalRows}
            </span>
          )}
        </button>

        {/* Copy + Export are visible whether collapsed or expanded — they act on
            the current rows either way. A refresh button renders only when the
            host wires one (the assembled preview; the frame preview is static). */}
        {loading && (
          <Loader2
            data-testid={`${testIdPrefix}-loading`}
            size={12}
            className="shrink-0 animate-spin"
            style={{ color: "var(--text-muted)" }}
          />
        )}
        {onRefresh && (
          <button
            data-testid={`${testIdPrefix}-refresh`}
            onClick={onRefresh}
            disabled={loading}
            title="Refresh the preview"
            className={iconBtn}
            style={iconStyle}
          >
            <RefreshCw size={12} />
          </button>
        )}
        <button
          data-testid={`${testIdPrefix}-copy`}
          onClick={onCopy}
          disabled={!clipboardOk}
          title={
            clipboardOk
              ? "Copy the JSON to the clipboard"
              : "Clipboard unavailable (requires a secure context)"
          }
          className={iconBtn}
          style={iconStyle}
        >
          <Copy size={12} />
        </button>
        <button
          data-testid={`${testIdPrefix}-export`}
          onClick={onExport}
          title="Download the JSON"
          className={iconBtn}
          style={iconStyle}
        >
          <Download size={12} />
        </button>
        {ack && (
          <span
            data-testid={`${testIdPrefix}-ack`}
            className="text-[10px] flex items-center gap-0.5 shrink-0"
            style={{ color: "var(--success, var(--text-muted))" }}
          >
            <Check size={10} />
            {ack}
          </span>
        )}
      </div>

      {actionError && (
        <div
          data-testid={`${testIdPrefix}-action-error`}
          className="mx-2 mb-2 px-1.5 py-0.5 rounded text-[10px] leading-snug"
          style={{ background: "var(--danger-soft)", color: "var(--danger-text)" }}
        >
          {actionError}
        </div>
      )}

      {isOpen && (
        <div className="px-2 pb-2" style={{ borderTop: "1px solid var(--border)" }}>
          {note && (
            <div
              data-testid={`${testIdPrefix}-note`}
              className="mt-1.5 px-1.5 py-1 rounded text-[10px] leading-snug"
              style={{ background: "var(--warning-soft)", color: "var(--warning-strong)" }}
            >
              {note}
            </div>
          )}
          {loading ? (
            <div
              data-testid={`${testIdPrefix}-loading-body`}
              className="flex items-center gap-1.5 py-2 text-[11px]"
              style={{ color: "var(--text-muted)" }}
            >
              <Loader2 size={12} className="animate-spin" />
              Loading preview…
            </div>
          ) : error ? (
            <div
              data-testid={`${testIdPrefix}-error`}
              className="flex items-start gap-1.5 mt-1.5 px-1.5 py-1 rounded text-[11px] leading-snug"
              style={{ background: "var(--danger-soft)", color: "var(--danger-text)" }}
            >
              <AlertTriangle size={12} className="shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          ) : rows.length === 0 ? (
            <div
              data-testid={`${testIdPrefix}-empty`}
              className="text-[11px] italic py-2"
              style={{ color: "var(--text-muted)" }}
            >
              {emptyMessage}
            </div>
          ) : (
            <pre
              data-testid={`${testIdPrefix}-json`}
              className="mt-1.5 overflow-auto text-[10px] leading-snug font-mono rounded p-2"
              style={{
                background: "var(--bg-input)",
                border: "1px solid var(--border)",
                color: "var(--text-primary)",
                maxHeight: "20rem",
              }}
            >
              {json}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}

export default JsonPreview
