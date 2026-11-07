import { useEffect, useState } from "react"
import { Copy, Download, RefreshCw } from "lucide-react"
import { apiErrorMessage } from "../api/errors"
import type { FormSpec, WorkbenchStoreSqlResponse } from "../api/types"
import { fetchStoreSql } from "../api/workbench"
import ModalShell from "../components/ModalShell"
import { ModalFormHeader } from "../components/ModalForm"
import { FIELD_CLASS, INPUT_STYLE, QuietAction } from "../components/form"
import { downloadTextFile, writeClipboardText } from "../panels/editors/shared/tableClipboard"
import useStoreCompareStore, { currentComparison, environmentState } from "../stores/useStoreCompareStore"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchStore from "../stores/useWorkbenchStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import StorePlanReview from "./StorePlanReview"
import DatabaseDisclosure from "./DatabaseDisclosure"
import useStoreComparison from "./useStoreComparison"

type Source = "database" | "branch" | "none"
type OfflineAnswer = { form: FormSpec; source: Source; contextVersion: number } & (
  | { status: "ready"; answer: WorkbenchStoreSqlResponse }
  | { status: "failed"; message: string }
)
const QUIET_STYLE = { color: "var(--text-secondary)" } as const

/** One environment throughout review, export and verification. Offline SQL is explicit. */
export default function StoreSqlWindow({ onClose }: { onClose: () => void }) {
  const form = useWorkbenchFormStore((s) => s.form)
  const revision = useWorkbenchFormStore((s) => s.revision)
  const dirty = useWorkbenchFormStore((s) => s.dirty)
  const stale = useWorkbenchFormStore((s) => s.stale)
  const previewing = useWorkbenchViewStore((s) => s.mode === "preview")
  const configured = useWorkbenchStore((s) => s.environments)
  const state = useStoreCompareStore()
  const { contextVersion, environment, choose, readAgain } = state
  const { read, compared } = environmentState(state)
  const comparison = currentComparison(state, form)
  const [source, setSource] = useState<Source>(environment === null ? "branch" : "database")
  const [offline, setOffline] = useState<OfflineAnswer | null>(null)
  const [notice, setNotice] = useState<{ sql: string; text: string; error: boolean } | null>(null)
  useStoreComparison()

  useEffect(() => {
    if (form === null || source === "database") return
    let live = true
    void fetchStoreSql(form, source).then((answer) => {
      if (live) setOffline({ form, source, contextVersion, status: "ready", answer })
    }, (error: unknown) => {
      if (live) setOffline({ form, source, contextVersion, status: "failed", message: apiErrorMessage(error) })
    })
    return () => { live = false }
  }, [form, source, contextVersion])

  const answered = offline?.form === form && offline.source === source && offline.contextVersion === contextVersion ? offline : null
  const answer = answered?.status === "ready" ? answered.answer : null
  const names = source === "database" ? configured : answer?.environments.map((each) => each.name) ?? configured
  // Only an initially unselected environment chooses the first declared one. A missing
  // selected environment is an error, never a reason to export another one's SQL.
  const first = names[0]
  useEffect(() => {
    if (environment === null && first !== undefined) choose(first, source === "database")
    else if (source === "database" && environment !== null && read.status === "idle") choose(environment)
  }, [environment, first, source, read.status, choose])

  const entry = answer?.environments.find((each) => each.name === environment)
  const sql = source === "database" ? comparison?.sql : entry?.sql
  const provider = source === "database" ? comparison?.provider : entry?.provider
  const blocked = source === "database" && (comparison?.blocked.length ?? 0) > 0
  const disabled = sql === undefined || blocked || previewing || stale
  const readError = read.status === "failed" ? read.message : compared.status === "failed" ? compared.message : null
  const failure = source === "database" ? readError : answered?.status === "failed" ? answered.message : null
  const empty = source === "database" ? configured.length === 0 : answer?.environments.length === 0
  const target = dirty ? "Working schema · Unsaved changes" : "Working schema · Saved"

  const exportSql = async (download: boolean) => {
    if (disabled || sql === undefined || environment === null) return
    try {
      if (download) {
        if (!downloadTextFile(sql, `${environment}-${source}-schema.sql`, "application/sql")) {
          throw new Error("SQL download is unavailable in this browser. Use Copy SQL.")
        }
      } else await writeClipboardText(sql)
      setNotice({ sql, error: false, text: download ? "SQL downloaded. Run it through your database owner, then check again." : "SQL copied. Run it through your database owner, then check again." })
    } catch (error: unknown) {
      setNotice({ sql, error: true, text: apiErrorMessage(error) })
    }
  }

  return (
    <ModalShell ariaLabel="Export database changes" onClose={onClose} width="w-[560px] max-w-[calc(100vw-24px)]" testId="store-sql-window">
      <div className="flex max-h-[calc(100vh-48px)] flex-col">
        <div className="shrink-0"><ModalFormHeader title="Export SQL">{target}</ModalFormHeader></div>
        <div className="flex min-h-0 flex-col gap-3 overflow-auto p-4 text-xs leading-relaxed">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <label className="flex min-w-0 flex-wrap items-center gap-2">
              <span style={QUIET_STYLE}>Environment</span>
              <select aria-label="Environment" value={environment ?? ""} onChange={(event) => choose(event.target.value, source === "database")} className={`${FIELD_CLASS} max-w-full`} style={INPUT_STYLE}>
                {environment === null && <option value="">Choose environment</option>}
                {names.map((name) => <option key={name} value={name}>{name}</option>)}
              </select>
            </label>
            {source === "database" && environment !== null && (
              <QuietAction icon={RefreshCw} disabled={read.status === "reading"} onClick={readAgain}>Check {environment} again</QuietAction>
            )}
          </div>
          {source !== "database" && <p style={{ color: "var(--warning)" }}>Offline SQL · Not checked against this database. {answer && (answer.branch === null ? "Starts from a new database." : `Starts from ${answer.branch}.`)}</p>}
          {stale && <p role="alert" style={{ color: "var(--danger-text)" }}>The schema changed on disk. Reload it before exporting SQL.</p>}
          {empty ? <p role="status">No hosted environments are declared.</p>
            : failure !== null ? <p role="alert" style={{ color: "var(--danger-text)" }}>{failure}</p>
              : environment !== null && !names.includes(environment) ? <p role="alert">The selected environment is no longer declared. Choose an environment.</p>
                : sql === undefined ? <p role="status" style={QUIET_STYLE}>{source === "database" ? "Checking and comparing the database…" : "Writing the SQL…"}</p>
                  : <>
                    {source === "database" && comparison !== null && <StorePlanReview answer={comparison} />}
                    <DatabaseDisclosure title="View SQL">
                      <pre data-testid="store-sql" className="max-h-[35vh] overflow-auto rounded-md p-3 font-mono text-[11px] leading-relaxed" style={{ background: "var(--bg-base)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>{sql}</pre>
                      <QuietAction icon={Copy} className="mt-2" disabled={disabled} onClick={() => void exportSql(false)}>Copy SQL</QuietAction>
                    </DatabaseDisclosure>
                  </>}
          <DatabaseDisclosure title="SQL options" className="text-[var(--text-secondary)]">
            <div className="flex flex-col gap-2 text-[11px]">
              <label className="flex min-w-0 flex-wrap items-center gap-2">
                <span>Compare from</span>
                <select aria-label="Compare from" value={source} onChange={(event) => setSource(event.target.value as Source)} className={`${FIELD_CLASS} max-w-full`} style={INPUT_STYLE}>
                  <option value="database">Database snapshot</option>
                  <option value="branch">Default branch (offline)</option>
                  <option value="none">New database (offline)</option>
                </select>
              </label>
              <p className="break-all">Saved revision {revision ?? "not recorded"}{provider ? ` · ${provider}` : ""}</p>
              {source === "database" && read.status === "read" && <p>Database checked <time dateTime={new Date(read.checkedAt).toISOString()}>{new Date(read.checkedAt).toLocaleString()}</time></p>}
              {configured.length > 0 && <QuietAction icon={RefreshCw} className="self-start" onClick={() => state.checkAll(configured)}>Check environments</QuietAction>}
            </div>
          </DatabaseDisclosure>
        </div>
        <div className="shrink-0 border-t p-4 text-xs" style={{ borderColor: "var(--border)" }}>
          {notice !== null && notice.sql === sql && <p role={notice.error ? "alert" : "status"} className="mb-2 leading-relaxed" style={{ color: notice.error ? "var(--danger-text)" : "var(--text-secondary)" }}>{notice.text}</p>}
          <p className="mb-3 text-[11px] leading-relaxed" style={QUIET_STYLE}>Exporting leaves the database unchanged. Your database owner runs the SQL.</p>
          <div className="flex items-center justify-end gap-2">
            <button type="button" className="focus-ring hover-bg rounded-md px-3 py-1.5 font-medium" style={QUIET_STYLE} onClick={onClose}>Close</button>
            <button type="button" className="focus-ring inline-flex items-center gap-1.5 rounded-md bg-[var(--structure-action)] px-4 py-1.5 font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50 hover:bg-[var(--structure-action-hover)] disabled:hover:bg-[var(--structure-action)]"
              style={{ color: "var(--text-on-accent)" }} disabled={disabled} onClick={() => void exportSql(true)}><Download size={13} aria-hidden="true" />Download SQL</button>
          </div>
        </div>
      </div>
    </ModalShell>
  )
}
