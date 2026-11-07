import { useState } from "react"
import { createPortal } from "react-dom"
import { Copy, Download } from "lucide-react"
import { apiErrorMessage } from "../api/errors"
import ModalShell from "../components/ModalShell"
import { ModalFormHeader } from "../components/ModalForm"
import { QuietAction } from "../components/form"
import { downloadTextFile, writeClipboardText } from "../panels/editors/shared/tableClipboard"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useStoreSqlExport from "./useStoreSqlExport"

const QUIET_STYLE = { color: "var(--text-secondary)" } as const

/** Query and export actions only; all database decisions belong to the schema pane. */
export default function StoreSqlWindow({ onClose }: { onClose: () => void }) {
  const dirty = useWorkbenchFormStore((s) => s.dirty)
  const { environment, comparison, reason } = useStoreSqlExport()
  const sql = comparison?.sql
  const disabled = reason !== null || sql === undefined
  const manual = comparison !== null && (comparison.blocked.length > 0 || comparison.manual.length > 0 || comparison.changes.some((change) => change.disposition === "manual"))
  const [notice, setNotice] = useState<{ environment: string; sql: string; text: string; error: boolean } | null>(null)
  const target = dirty ? "Working schema · Unsaved changes" : "Working schema · Saved"

  const exportSql = async (download: boolean) => {
    if (disabled || sql === undefined || environment === null) return
    try {
      if (download) {
        if (!downloadTextFile(sql, `${environment}-database-schema.sql`, "application/sql")) {
          throw new Error("SQL download is unavailable in this browser. Use Copy SQL.")
        }
      } else await writeClipboardText(sql)
      setNotice({ environment, sql, error: false, text: download ? "SQL downloaded." : "SQL copied." })
    } catch (error: unknown) {
      setNotice({ environment, sql, error: true, text: apiErrorMessage(error) })
    }
  }

  return createPortal(
    <ModalShell ariaLabel="Export database changes" onClose={onClose} width="w-[560px] max-w-[calc(100vw-24px)]" testId="store-sql-window">
      <div className="flex max-h-[calc(100vh-48px)] flex-col">
        <div className="shrink-0"><ModalFormHeader title="Export SQL"><span>{target}</span>{environment !== null && <span className="ml-2">· {environment}</span>}</ModalFormHeader></div>
        <div className="flex min-h-0 flex-col gap-3 overflow-auto p-4 text-xs leading-relaxed">
          {reason !== null && <p role="alert" style={{ color: "var(--warning)" }}>{reason}</p>}
          {manual && <p style={{ color: "var(--warning)" }}>This SQL adds tables and columns only. Changes needing manual work are explained in the SQL comments below.</p>}
          {sql !== undefined && <pre data-testid="store-sql" className="overflow-auto rounded-md p-3 font-mono text-[11px] leading-relaxed" style={{ background: "var(--bg-base)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>{sql}</pre>}
        </div>
        <div className="shrink-0 border-t p-4 text-xs" style={{ borderColor: "var(--border)" }}>
          {notice !== null && notice.environment === environment && notice.sql === sql && <p role={notice.error ? "alert" : "status"} className="mb-2 leading-relaxed" style={{ color: notice.error ? "var(--danger-text)" : "var(--text-secondary)" }}>{notice.text}</p>}
          <p className="mb-3 text-[11px] leading-relaxed" style={QUIET_STYLE}>Run through your database owner, then use Refresh on the schema pane to verify.</p>
          <div className="flex items-center justify-end gap-3">
            <button type="button" className="focus-ring hover-bg rounded-md px-3 py-1.5 font-medium" style={QUIET_STYLE} onClick={onClose}>Close</button>
            <QuietAction icon={Copy} disabled={disabled} onClick={() => void exportSql(false)}>Copy SQL</QuietAction>
            <button type="button" className="focus-ring inline-flex items-center gap-1.5 rounded-md bg-[var(--structure-action)] px-4 py-1.5 font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50 hover:bg-[var(--structure-action-hover)] disabled:hover:bg-[var(--structure-action)]"
              style={{ color: "var(--text-on-accent)" }} disabled={disabled} onClick={() => void exportSql(true)}><Download size={13} aria-hidden="true" />Download SQL</button>
          </div>
        </div>
      </div>
    </ModalShell>,
    document.body,
  )
}
