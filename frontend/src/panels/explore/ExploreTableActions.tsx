import { useCallback, useEffect, useRef, useState } from "react"
import { Check, Copy, Download } from "lucide-react"
import { sanitiseLabelForFilesystem } from "../../utils/apiInputPorts"
import type { TableGrid } from "../editors/FrameTableActions"

function clipboardWriteAvailable(): boolean {
  if (typeof navigator === "undefined" || !navigator.clipboard) return false
  if (typeof isSecureContext === "boolean" && !isSecureContext) return false
  return typeof navigator.clipboard.writeText === "function"
}

function exploreExportFilename(source: string, table: string): string {
  const trimmedSource = source.trim()
  const safeSource = trimmedSource ? sanitiseLabelForFilesystem(trimmedSource) : "source"
  return `explore-${safeSource}-${table}`
}

export default function ExploreTableActions({
  grid,
  source,
  tableSlug,
  testIdPrefix,
  tableLabel,
}: {
  grid: TableGrid
  source: string
  tableSlug: string
  testIdPrefix: string
  tableLabel: string
}) {
  const [ack, setAck] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const ackTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const clipboardOk = clipboardWriteAvailable()
  const empty = grid.rows.length === 0
  const filename = exploreExportFilename(source, tableSlug)

  useEffect(() => () => {
    if (ackTimer.current) clearTimeout(ackTimer.current)
  }, [])

  const flashAck = useCallback((message: string) => {
    setError(null)
    setAck(message)
    if (ackTimer.current) clearTimeout(ackTimer.current)
    ackTimer.current = setTimeout(() => setAck(null), 1500)
  }, [])

  const copy = useCallback(async () => {
    try {
      const { buildTsv, writeClipboardText } = await import(
        "../editors/shared/tableClipboard"
      )
      await writeClipboardText(buildTsv([grid.headers, ...grid.rows]))
      flashAck("Copied")
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Copy failed")
    }
  }, [flashAck, grid])

  const download = useCallback(async () => {
    try {
      const { buildCsv, downloadTextFile } = await import(
        "../editors/shared/tableClipboard"
      )
      const downloaded = downloadTextFile(
        buildCsv([grid.headers, ...grid.rows]),
        `${filename}.csv`,
        "text/csv",
      )
      if (downloaded) flashAck("Downloaded")
      else setError("Download is unavailable in this context.")
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Download failed")
    }
  }, [filename, flashAck, grid])

  const buttonClass =
    "inline-flex items-center gap-1 rounded px-1.5 py-1 text-[10px] font-semibold disabled:cursor-not-allowed disabled:opacity-40"
  const buttonStyle = { color: "var(--text-muted)", border: "1px solid var(--border)" }

  return (
    <div data-testid={`${testIdPrefix}-actions`} className="flex flex-wrap items-center gap-1">
      <button
        type="button"
        data-testid={`${testIdPrefix}-copy-tsv`}
        aria-label={`Copy ${tableLabel} table as TSV`}
        title={
          clipboardOk
            ? `Copy ${tableLabel} table as TSV`
            : "Clipboard unavailable (requires a secure context)"
        }
        disabled={empty || !clipboardOk}
        onClick={copy}
        className={buttonClass}
        style={buttonStyle}
      >
        <Copy size={12} /> Copy TSV
      </button>
      <button
        type="button"
        data-testid={`${testIdPrefix}-download-csv`}
        aria-label={`Download ${tableLabel} table as CSV`}
        title={`Download ${tableLabel} table as CSV`}
        disabled={empty}
        onClick={download}
        className={buttonClass}
        style={buttonStyle}
      >
        <Download size={12} /> CSV
      </button>
      {ack ? (
        <span
          data-testid={`${testIdPrefix}-export-ack`}
          className="inline-flex items-center gap-0.5 text-[10px]"
          style={{ color: "var(--success, var(--text-muted))" }}
        >
          <Check size={10} /> {ack}
        </span>
      ) : null}
      {error ? (
        <span
          data-testid={`${testIdPrefix}-export-error`}
          className="text-[10px]"
          style={{ color: "var(--danger-text)" }}
        >
          {error}
        </span>
      ) : null}
    </div>
  )
}
