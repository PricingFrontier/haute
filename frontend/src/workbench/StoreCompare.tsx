/** Compact database selection; column decisions stay beside the schema fields. */
import { Code2, RefreshCw } from "lucide-react"
import { useState } from "react"
import type { SchemaTable, WorkbenchStoreDifference } from "../api/types"
import { FIELD_CLASS, INPUT_STYLE, QuietAction } from "../components/form"
import useStoreCompareStore, { comparisonStatus, currentComparison, environmentState } from "../stores/useStoreCompareStore"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchStore from "../stores/useWorkbenchStore"
import { schemaColumns } from "./schema/schemaText"
import StoreSqlWindow from "./StoreSqlWindow"

export function CompareControls() {
  const environments = useWorkbenchStore((s) => s.environments)
  const state = useStoreCompareStore()
  const form = useWorkbenchFormStore((s) => s.form)
  const [sqlOpen, setSqlOpen] = useState(false)
  const { read, compared } = environmentState(state)
  const status = comparisonStatus(state, form)
  const failure = status !== "Check failed" ? null : read.status === "failed" ? read.message : compared.status === "failed" ? compared.message : null
  const colour = (name: string | null) => name === null ? "var(--text-primary)"
    : environmentState(state, name).read.status !== "read" ? "var(--danger-text)"
      : comparisonStatus(state, form, name) === "Compatible" ? "var(--success)" : "var(--caution)"
  return (
    <>
      <div className="flex min-w-0 basis-72 items-center gap-2 text-[11px]">
        {environments.length > 0 && <>
          <label htmlFor="schema-database" className="sr-only">Database</label>
          <select id="schema-database" aria-label="Database" value={state.environment ?? ""}
            className={`${FIELD_CLASS} flex-1`} style={{ ...INPUT_STYLE, color: colour(state.environment) }}
            title={state.environment === null ? "Local schema" : `${state.environment} · ${status}`}
            aria-describedby={state.environment === null ? undefined : "schema-database-status"}
            onChange={(event) => state.choose(event.target.value || null)}>
            <option value="" style={{ color: colour(null) }}>Local</option>
            {environments.map((name) => <option key={name} value={name} style={{ color: colour(name) }}
              title={comparisonStatus(state, form, name)} aria-description={comparisonStatus(state, form, name)}>{name}</option>)}
          </select>
          {state.environment !== null && <span id="schema-database-status" role="status" className="sr-only"
            title={read.status === "read" ? `Schema snapshot fetched ${new Date(read.checkedAt).toLocaleString()}` : undefined}>{status}</span>}
          <QuietAction icon={RefreshCw} onClick={state.readAgain} disabled={state.environment === null || read.status === "reading"}
            className="shrink-0" title="Refresh database status">Refresh</QuietAction>
        </>}
        <QuietAction icon={Code2} onClick={() => setSqlOpen(true)} aria-haspopup="dialog"
          className="shrink-0" title="View and copy generated SQL">Export SQL</QuietAction>
      </div>
      {failure !== null && <p role="alert" className="w-full break-words text-[11px]" style={{ color: "var(--danger-text)" }}>{failure}</p>}
      {sqlOpen && <StoreSqlWindow onClose={() => setSqlOpen(false)} />}
    </>
  )
}

const MARKS: Record<WorkbenchStoreDifference["kind"], string> = {
  missing: "missing", renamed: "renamed", type: "type", rows: "rows", null: "nulls",
  key: "key", refers: "reference", collation: "collation", store: "store columns",
}

/** Table marks accompany their local database-details disclosure. */
export function DatabaseMark({ table }: { table: SchemaTable }) {
  const form = useWorkbenchFormStore((s) => s.form)
  const state = useStoreCompareStore()
  const visible = new Set(schemaColumns(table).map((column) => column.name))
  const differences = currentComparison(state, form)?.differences.filter((each) => each.table === table.name && (each.column === null || !visible.has(each.column))) ?? []
  if (differences.length === 0) return null
  return <span data-testid={`database-mark-${table.name}`} className="shrink-0 rounded-md px-1.5 py-0.5 text-[11px] font-medium"
    style={{ color: "var(--warning)", background: "var(--bg-base)" }} title={differences.map((each) => each.message).join("\n")}>
    {differences.length === 1 ? MARKS[differences[0].kind] : `${differences.length} differences`}
  </span>
}
