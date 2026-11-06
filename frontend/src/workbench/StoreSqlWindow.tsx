import { useEffect, useState } from "react"
import { apiErrorMessage } from "../api/errors"
import type { FormSpec, WorkbenchStoreSqlResponse } from "../api/types"
import { fetchStoreSql } from "../api/workbench"
import ModalShell from "../components/ModalShell"
import { ModalFormHeader } from "../components/ModalForm"
import { downloadTextFile, writeClipboardText } from "../panels/editors/shared/tableClipboard"
import useStoreCompareStore, { currentComparison, environmentState } from "../stores/useStoreCompareStore"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchStore from "../stores/useWorkbenchStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import StorePlanReview from "./StorePlanReview"
import useStoreComparison from "./useStoreComparison"

type Source = "database" | "branch" | "none"
type OfflineAnswer = { form: FormSpec; source: Source; contextVersion: number } & (
  | { status: "ready"; answer: WorkbenchStoreSqlResponse }
  | { status: "failed"; message: string }
)
const QUIET_STYLE = { color: "var(--text-secondary)" } as const
const SELECT_STYLE = { background: "var(--bg-input)", borderColor: "var(--border)" } as const

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
  const target = dirty ? "Working schema · Unsaved changes" : `Working schema · Saved revision ${revision ?? "not recorded"}`

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
    <ModalShell ariaLabel="Review database changes" onClose={onClose} width="w-[760px] max-w-[calc(100vw-24px)]" testId="store-sql-window">
      <ModalFormHeader title="Review database changes"><span className="break-all">{target}</span></ModalFormHeader>
      <div className="flex max-h-[75vh] flex-col gap-4 overflow-auto px-4 py-3 text-xs">
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1">
            <span>Environment</span>
            <select aria-label="Environment" value={environment ?? ""} onChange={(event) => choose(event.target.value, source === "database")} className="rounded border px-2 py-1" style={SELECT_STYLE}>
              {environment === null && <option value="">Choose environment</option>}
              {names.map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span>Compare from</span>
            <select aria-label="Compare from" value={source} onChange={(event) => setSource(event.target.value as Source)} className="rounded border px-2 py-1" style={SELECT_STYLE}>
              <option value="database">Database snapshot</option>
              <option value="branch">Default branch (offline)</option>
              <option value="none">New database (offline)</option>
            </select>
          </label>
          {source === "database" && environment !== null && (
            <button type="button" className="btn-ghost rounded px-2 py-1" disabled={read.status === "reading"} onClick={readAgain}>Check {environment} again</button>
          )}
          {provider && <span style={QUIET_STYLE}>{provider}</span>}
        </div>
        {source === "database" && read.status === "read" && <p style={QUIET_STYLE}>Database checked <time dateTime={new Date(read.checkedAt).toISOString()}>{new Date(read.checkedAt).toLocaleString()}</time></p>}
        {source !== "database" && <p style={{ color: "var(--warning)" }}>Offline SQL · Not checked against this database. {answer && (answer.branch === null ? "Starts from a new database." : `Starts from ${answer.branch}.`)}</p>}
        {stale && <p role="alert" style={{ color: "var(--danger-text)" }}>The schema changed on disk. Reload it before exporting SQL.</p>}
        {empty ? <p role="status">No hosted environments are declared.</p>
          : failure !== null ? <p role="alert" style={{ color: "var(--danger-text)" }}>{failure}</p>
            : environment !== null && !names.includes(environment) ? <p role="alert">The selected environment is no longer declared. Choose an environment.</p>
              : sql === undefined ? <p role="status" style={QUIET_STYLE}>{source === "database" ? "Checking and comparing the database…" : "Writing the SQL…"}</p>
                : <>
                  {source === "database" && comparison !== null && <StorePlanReview answer={comparison} />}
                  <details open={source !== "database"}>
                    <summary className="cursor-pointer py-1">View SQL</summary>
                    <pre data-testid="store-sql" className="mt-2 max-h-[35vh] overflow-auto rounded p-3 font-mono text-[11px] leading-relaxed" style={{ background: "var(--bg-base)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>{sql}</pre>
                  </details>
                </>}
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn-primary rounded px-3 py-1.5" disabled={disabled} onClick={() => void exportSql(true)}>Download SQL</button>
          <button type="button" className="btn-ghost rounded px-3 py-1.5" disabled={disabled} onClick={() => void exportSql(false)}>Copy SQL</button>
          <button type="button" className="btn-ghost ml-auto rounded px-3 py-1.5" onClick={onClose}>Close</button>
        </div>
        {notice !== null && notice.sql === sql && <p role={notice.error ? "alert" : "status"} style={{ color: notice.error ? "var(--danger-text)" : "var(--text-secondary)" }}>{notice.text}</p>}
        <p style={QUIET_STYLE}>Saving the schema and exporting SQL leave the database unchanged. After the database owner runs it, check again to verify its tables.</p>
      </div>
    </ModalShell>
  )
}
