import type { WorkbenchStoreCompareResponse } from "../api/types"
import useStoreCompareStore from "../stores/useStoreCompareStore"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import { tableColumns } from "../utils/quoteIdentity"

const QUIET = { color: "var(--text-secondary)" } as const
type WorkbenchStoreChange = WorkbenchStoreCompareResponse["changes"][number]
const ACTIONS: Record<WorkbenchStoreChange["kind"], string> = {
  "create table": "Create table", "add column": "Add", "rename column": "Rename", "rename table": "Rename table",
  "retype column": "Change type of", "reshape table": "Change rows of", "keep column": "Keep", "keep table": "Keep table",
}

function ChangeRow({ change }: { change: WorkbenchStoreChange }) {
  const name = `${change.table}${change.column === null ? "" : `.${change.column}`}`
  return <details className="rounded px-2 py-1 hover:bg-[var(--bg-hover)]">
    <summary className="cursor-pointer break-words py-0.5">{ACTIONS[change.kind]} {change.was !== null ? `${change.was} → ` : ""}{name}</summary>
    <p className="pb-1 pl-4 pt-1" style={QUIET}>{change.description}</p>
    {change.quotes !== null && <p className="pb-1 pl-4" style={QUIET}>{change.quotes.toLocaleString()} {change.quotes === 1 ? "quote" : "quotes"} with values</p>}
  </details>
}

/** A brief export summary. Decisions about fields live in their own details. */
export default function StorePlanReview({ answer }: { answer: WorkbenchStoreCompareResponse }) {
  const form = useWorkbenchFormStore((s) => s.form)
  const renames = useStoreCompareStore((s) => s.renames)
  const unpair = useStoreCompareStore((s) => s.unpair)
  const previewing = useWorkbenchViewStore((s) => s.mode === "preview")
  const orphaned = renames.filter((rename) => !form?.schema.tables.some((table) => table.name === rename.table && tableColumns(table).some((column) => column.name === rename.column)))
  const changes = answer.changes.filter((each) => each.disposition === "sql")
  const manual = answer.changes.filter((each) => each.disposition === "manual")
  const retained = answer.changes.filter((each) => each.disposition === "retained")
  return <div className="flex flex-col gap-3" data-testid="store-plan-review">
    {answer.blocked.length > 0 && <div role="alert" className="rounded border p-3" style={{ borderColor: "var(--warning)", color: "var(--warning)" }}>
      <h3 className="font-medium">Resolve before exporting</h3>
      <ul className="mt-1 list-disc pl-4">{answer.blocked.map((message) => <li key={message}>{message}</li>)}</ul>
    </div>}
    {orphaned.map((rename) => <div key={`${rename.table}.${rename.column}`} className="flex flex-wrap items-center gap-2">
      <span>Removed field: {rename.table}.{rename.was} → {rename.column}</span>
      <button type="button" className="focus-ring hover-bg disabled:opacity-50 rounded px-2 py-1" disabled={previewing} onClick={() => unpair(rename.table, rename.column)}>Remove rename choice</button>
    </div>)}
    {answer.matches && answer.blocked.length === 0 && <p role="status" style={{ color: "var(--success)" }}>Compatible with this schema{retained.length > 0 ? " · unused database fields kept" : ""}.</p>}
    {changes.length > 0 && <section aria-label="SQL changes">
      <h3 className="mb-1 font-medium">{changes.length} {changes.length === 1 ? "change" : "changes"} to apply</h3>
      {changes.map((change) => <ChangeRow key={`${change.table}.${change.column}.${change.kind}`} change={change} />)}
    </section>}
    {(manual.length > 0 || answer.manual.length > 0) && <section aria-label="Manual work">
      <h3 className="font-medium" style={{ color: "var(--warning)" }}>Manual work required</h3>
      <p className="mt-1" style={QUIET}>The SQL does not complete these changes.</p>
      {manual.map((change) => <ChangeRow key={`${change.table}.${change.column}.${change.kind}`} change={change} />)}
      {answer.manual.map((message) => <p key={message} className="mt-1" style={QUIET}>{message}</p>)}
    </section>}
    {answer.requires_maintenance && <p style={{ color: "var(--warning)" }}>Coordinate these changes with the app release; running-app compatibility has not been checked.</p>}
    {retained.length > 0 && <details>
      <summary className="cursor-pointer py-1" style={QUIET}>{retained.length} unused database {retained.length === 1 ? "item" : "items"} kept</summary>
      <section aria-label="Retained in database" className="pt-1">{retained.map((change) => <ChangeRow key={`${change.table}.${change.column}`} change={change} />)}</section>
    </details>}
  </div>
}
