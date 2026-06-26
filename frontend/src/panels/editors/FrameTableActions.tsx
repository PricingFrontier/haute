import { useState, useRef, useCallback } from "react"
import { Copy, Share2, Save, ClipboardPaste, Check } from "lucide-react"
import {
  buildTsv,
  buildCsv,
  parsePastedGrid,
  writeClipboardText,
  downloadTextFile,
  clipboardWriteAvailable,
} from "./shared/tableClipboard"

// ─── FrameTableActions ────────────────────────────────────────────
//
// SHARED tableness for a mapping table. One reusable strip of affordances —
// COPY (table as tab-separated text, paste-able back in), SHARE (the
// schema-mapping JSON), SAVE (download the schema JSON, or the grid as
// CSV/TSV), and PASTE-IN (tab-separated text → rows) — wired into BOTH the
// OUTPUT editor (its per-frame column-mapping table AND its top-level
// frames-paths table) and the apiInput editor's tables.
//
// The component is deliberately PRESENTATIONAL + format-agnostic: a host
// supplies the data via plain getters (`getGrid`, `getSchema`) and a sink
// (`onPaste`). It never reaches into a particular editor's config shape, so
// the same strip serves a 4-field outputMapping table, a frames-paths table,
// and an apiInput tables/columns grid without modification.
//
// Clipboard + download both degrade gracefully: in a non-secure context (no
// `navigator.clipboard`) the copy/share buttons render disabled with a reason,
// and if the download APIs are missing `onSave`'s blob path is skipped — no
// throw ever escapes a button click.

/** A grid for copy/save: a header row followed by body rows. All cells are
 * already stringified by the host. */
export interface TableGrid {
  headers: string[]
  rows: string[][]
}

export type SaveFormat = "json" | "csv" | "tsv"

export function FrameTableActions({
  testIdPrefix,
  /** The grid to copy as TSV and to save as CSV/TSV. */
  getGrid,
  /** The schema-mapping object to share/save as JSON. */
  getSchema,
  /** Base filename (no extension) for the Save download. */
  filename,
  /** Called with the parsed grid (tab-separated → string[][]) on Paste-in.
   * The FIRST row may be a header the host recognises and drops — that policy
   * lives in the host, the component passes the raw matrix through verbatim. */
  onPaste,
  /** When false, the Paste-in affordance is hidden (e.g. a read-only table). */
  pasteable = true,
}: {
  testIdPrefix: string
  getGrid: () => TableGrid
  getSchema: () => unknown
  filename: string
  onPaste?: (grid: string[][]) => void
  pasteable?: boolean
}) {
  // Transient "copied / pasted / saved" acknowledgement + transient error.
  const [ack, setAck] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pasteOpen, setPasteOpen] = useState(false)
  const [pasteDraft, setPasteDraft] = useState("")
  const ackTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const flashAck = useCallback((msg: string) => {
    setError(null)
    setAck(msg)
    if (ackTimer.current) clearTimeout(ackTimer.current)
    ackTimer.current = setTimeout(() => setAck(null), 1500)
  }, [])

  const clipboardOk = clipboardWriteAvailable()

  const onCopy = useCallback(() => {
    const { headers, rows } = getGrid()
    const tsv = buildTsv([headers, ...rows])
    writeClipboardText(tsv).then(
      () => flashAck("Copied"),
      (e: unknown) => setError(e instanceof Error ? e.message : "Copy failed"),
    )
  }, [getGrid, flashAck])

  const onShare = useCallback(() => {
    const json = JSON.stringify(getSchema(), null, 2)
    writeClipboardText(json).then(
      () => flashAck("Copied JSON"),
      (e: unknown) => setError(e instanceof Error ? e.message : "Copy failed"),
    )
  }, [getSchema, flashAck])

  const onSave = useCallback(
    (format: SaveFormat) => {
      let text: string
      let ext: string
      let mime: string
      if (format === "json") {
        text = JSON.stringify(getSchema(), null, 2)
        ext = "json"
        mime = "application/json"
      } else {
        const { headers, rows } = getGrid()
        const grid = [headers, ...rows]
        text = format === "csv" ? buildCsv(grid) : buildTsv(grid)
        ext = format
        mime = format === "csv" ? "text/csv" : "text/tab-separated-values"
      }
      const ok = downloadTextFile(text, `${filename}.${ext}`, mime)
      if (ok) flashAck("Saved")
      else setError("Download is unavailable in this context.")
    },
    [getGrid, getSchema, filename, flashAck],
  )

  const commitPaste = useCallback(() => {
    const grid = parsePastedGrid(pasteDraft)
    onPaste?.(grid)
    setPasteOpen(false)
    setPasteDraft("")
    flashAck("Pasted")
  }, [pasteDraft, onPaste, flashAck])

  const iconBtn =
    "p-1 rounded flex items-center justify-center disabled:opacity-40 disabled:cursor-not-allowed"
  const iconStyle = { color: "var(--text-muted)" }

  return (
    <div data-testid={`${testIdPrefix}-actions`} className="flex flex-col gap-1">
      <div className="flex items-center gap-0.5">
        <button
          data-testid={`${testIdPrefix}-copy`}
          onClick={onCopy}
          disabled={!clipboardOk}
          title={
            clipboardOk
              ? "Copy table as tab-separated text"
              : "Clipboard unavailable (requires a secure context)"
          }
          className={iconBtn}
          style={iconStyle}
        >
          <Copy size={12} />
        </button>
        <button
          data-testid={`${testIdPrefix}-share`}
          onClick={onShare}
          disabled={!clipboardOk}
          title={
            clipboardOk
              ? "Copy the schema mapping as JSON"
              : "Clipboard unavailable (requires a secure context)"
          }
          className={iconBtn}
          style={iconStyle}
        >
          <Share2 size={12} />
        </button>
        <button
          data-testid={`${testIdPrefix}-save-json`}
          onClick={() => onSave("json")}
          title="Download the schema mapping as JSON"
          className={iconBtn}
          style={iconStyle}
        >
          <Save size={12} />
        </button>
        <button
          data-testid={`${testIdPrefix}-save-csv`}
          onClick={() => onSave("csv")}
          title="Download the table as CSV"
          className={`${iconBtn} text-[9px] font-bold`}
          style={iconStyle}
        >
          csv
        </button>
        <button
          data-testid={`${testIdPrefix}-save-tsv`}
          onClick={() => onSave("tsv")}
          title="Download the table as TSV"
          className={`${iconBtn} text-[9px] font-bold`}
          style={iconStyle}
        >
          tsv
        </button>
        {pasteable && onPaste && (
          <button
            data-testid={`${testIdPrefix}-paste-toggle`}
            onClick={() => {
              setPasteOpen((v) => !v)
              setError(null)
            }}
            title="Paste tab-separated rows in"
            className={iconBtn}
            style={iconStyle}
          >
            <ClipboardPaste size={12} />
          </button>
        )}
        {ack && (
          <span
            data-testid={`${testIdPrefix}-ack`}
            className="text-[10px] flex items-center gap-0.5"
            style={{ color: "var(--success, var(--text-muted))" }}
          >
            <Check size={10} />
            {ack}
          </span>
        )}
      </div>

      {error && (
        <div
          data-testid={`${testIdPrefix}-error`}
          className="px-1.5 py-0.5 rounded text-[10px] leading-snug"
          style={{ background: "var(--danger-soft)", color: "var(--danger-text)" }}
        >
          {error}
        </div>
      )}

      {pasteOpen && pasteable && onPaste && (
        <div className="flex flex-col gap-1">
          <textarea
            data-testid={`${testIdPrefix}-paste-input`}
            value={pasteDraft}
            onChange={(e) => setPasteDraft(e.target.value)}
            placeholder="Paste tab-separated rows here, then Apply."
            rows={3}
            className="w-full text-[11px] px-1.5 py-1 rounded font-mono"
            style={{
              background: "var(--bg-input)",
              border: "1px solid var(--border)",
              color: "var(--text-primary)",
            }}
          />
          <div className="flex items-center gap-2">
            <button
              data-testid={`${testIdPrefix}-paste-apply`}
              onClick={commitPaste}
              disabled={pasteDraft.trim() === ""}
              className="text-[11px] font-semibold px-2 py-0.5 rounded disabled:opacity-40"
              style={{ background: "var(--accent, var(--text-muted))", color: "var(--text-on-accent, var(--bg))" }}
            >
              Apply paste
            </button>
            <button
              data-testid={`${testIdPrefix}-paste-cancel`}
              onClick={() => {
                setPasteOpen(false)
                setPasteDraft("")
              }}
              className="text-[11px] font-semibold px-2 py-0.5 rounded"
              style={{ color: "var(--text-muted)" }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export default FrameTableActions
