/** Compact database selection; column decisions stay beside the schema fields. */
import { RefreshCw } from "lucide-react"
import type { WorkbenchStoreDifference } from "../api/types"
import { FIELD_CLASS, INPUT_STYLE } from "../components/form"
import useStoreCompareStore, { comparisonStatus, currentComparison, environmentState } from "../stores/useStoreCompareStore"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchStore from "../stores/useWorkbenchStore"
import { IconButton } from "./schema/IconButton"

export function CompareControls() {
  const environments = useWorkbenchStore((s) => s.environments)
  const state = useStoreCompareStore()
  const form = useWorkbenchFormStore((s) => s.form)
  if (environments.length === 0) return null
  const { read, compared } = environmentState(state)
  const status = comparisonStatus(state, form)
  const failure = status !== "Check failed" ? null : read.status === "failed" ? read.message : compared.status === "failed" ? compared.message : null
  return (
    <>
      <div className="flex w-52 max-w-[48%] min-w-0 items-center gap-1.5 text-[11px]">
        <label htmlFor="schema-database" className="sr-only">Database</label>
        <select id="schema-database" aria-label="Database" value={state.environment ?? ""}
          className={`${FIELD_CLASS} flex-1`} style={INPUT_STYLE}
          title={state.environment === null ? "Database" : `${state.environment} · ${status}`}
          onChange={(event) => state.choose(event.target.value || null)}>
          <option value="">No database</option>
          {environments.map((name) => <option key={name} value={name}>{name} · {comparisonStatus(state, form, name)}</option>)}
        </select>
        {state.environment !== null && <>
          <span role="status" className="sr-only"
            title={read.status === "read" ? `Database checked ${new Date(read.checkedAt).toLocaleString()}` : undefined}>{status}</span>
          <IconButton icon={RefreshCw} onClick={state.readAgain} disabled={read.status === "reading"}
            className="disabled:opacity-50" style={{ color: "var(--text-muted)" }}
            aria-label={`Check ${state.environment} again`} title="Refresh database status" />
        </>}
      </div>
      {failure !== null && <p role="alert" className="w-full break-words text-[11px]" style={{ color: "var(--danger-text)" }}>{failure}</p>}
    </>
  )
}

const MARKS: Record<WorkbenchStoreDifference["kind"], string> = {
  missing: "missing", renamed: "renamed", type: "type", rows: "rows", null: "nulls",
  key: "key", refers: "reference", collation: "collation", store: "store columns",
}

/** Table marks accompany their local database-details disclosure. */
export function DatabaseMark({ table }: { table: string }) {
  const form = useWorkbenchFormStore((s) => s.form)
  const state = useStoreCompareStore()
  const differences = currentComparison(state, form)?.differences.filter((each) => each.table === table && each.column === null) ?? []
  if (differences.length === 0) return null
  return <span data-testid={`database-mark-${table}`} className="shrink-0 rounded-md px-1.5 py-0.5 text-[11px] font-medium"
    style={{ color: "var(--warning)", background: "var(--bg-base)" }} title={differences.map((each) => each.message).join("\n")}>
    {differences.length === 1 ? MARKS[differences[0].kind] : `${differences.length} differences`}
  </span>
}
