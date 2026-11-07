import { AlertTriangle, RefreshCw, Undo2 } from "lucide-react"
import type { WorkbenchStoreCompareResponse } from "../api/types"
import { QuietAction } from "../components/form"
import useStoreCompareStore, { currentComparison, environmentState } from "../stores/useStoreCompareStore"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchStore from "../stores/useWorkbenchStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import { tableColumns } from "../utils/quoteIdentity"
import DatabaseDisclosure from "./DatabaseDisclosure"

const QUIET = { color: "var(--text-secondary)" } as const
type WorkbenchStoreChange = WorkbenchStoreCompareResponse["changes"][number]
const ACTIONS: Record<WorkbenchStoreChange["kind"], string> = {
  "create table": "Create table", "add column": "Add", "rename column": "Rename", "rename table": "Rename table",
  "retype column": "Change type of", "reshape table": "Change rows of", "keep column": "Keep", "keep table": "Keep table",
}

function ChangeRow({ change }: { change: WorkbenchStoreChange }) {
  const name = `${change.table}${change.column === null ? "" : `.${change.column}`}`
  return <DatabaseDisclosure title={`${ACTIONS[change.kind]} ${change.was !== null ? `${change.was} → ` : ""}${name}`}>
    <p className="pb-1" style={QUIET}>{change.description}</p>
    {change.quotes !== null && <p className="pb-1 text-[11px]" style={QUIET}>{change.quotes.toLocaleString()} {change.quotes === 1 ? "quote" : "quotes"} with values</p>}
  </DatabaseDisclosure>
}

/** Secondary review and database actions stay collapsed on the main schema pane. */
export function DatabaseChanges() {
  const form = useWorkbenchFormStore((s) => s.form)
  const revision = useWorkbenchFormStore((s) => s.revision)
  const configured = useWorkbenchStore((s) => s.environments)
  const state = useStoreCompareStore()
  const answer = currentComparison(state, form)
  if (answer === null) return null
  const { read } = environmentState(state)
  const attention = answer.blocked.length > 0 || answer.manual.length > 0 || answer.changes.some((change) => change.disposition === "manual")
  return <DatabaseDisclosure title={<span style={attention ? { color: "var(--warning)" } : QUIET}>Database changes{attention ? " · Needs attention" : ""}</span>} className="text-[11px]">
    <StorePlanReview answer={answer} />
    <div className="mt-3 flex flex-col gap-2 pb-2" style={QUIET}>
      <p className="break-all">{answer.provider} · Saved revision {revision ?? "not recorded"}</p>
      {read.status === "read" && <p>Schema snapshot fetched <time dateTime={new Date(read.checkedAt).toISOString()}>{new Date(read.checkedAt).toLocaleString()}</time></p>}
      <QuietAction icon={RefreshCw} className="self-start" onClick={() => state.checkAll(configured)}>Check environments</QuietAction>
    </div>
  </DatabaseDisclosure>
}

/** A brief review on the schema pane. Field decisions live in their own details. */
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
    {answer.blocked.length > 0 && <div role="alert" className="rounded-md border p-3" style={{ borderColor: "var(--border)", background: "var(--bg-elevated)" }}>
      <h3 className="flex items-center gap-1.5 font-semibold" style={{ color: "var(--warning)" }}><AlertTriangle size={13} aria-hidden="true" className="shrink-0" />Not included in SQL</h3>
      <ul className="mt-1 list-disc pl-4" style={QUIET}>{answer.blocked.map((message) => <li key={message}>{message}</li>)}</ul>
    </div>}
    {orphaned.map((rename) => <div key={`${rename.table}.${rename.column}`} className="flex flex-wrap items-center gap-2">
      <span>Removed field: {rename.table}.{rename.was} → {rename.column}</span>
      <QuietAction icon={Undo2} disabled={previewing} onClick={() => unpair(rename.table, rename.column)}>Remove rename choice</QuietAction>
    </div>)}
    {answer.matches && answer.blocked.length === 0 && <p role="status" style={{ color: "var(--success)" }}>Compatible with this schema{retained.length > 0 ? " · unused database fields kept" : ""}.</p>}
    {changes.length > 0 && <section aria-label="SQL changes">
      <h3 className="mb-1 font-semibold">{changes.length} {changes.length === 1 ? "change" : "changes"} to apply</h3>
      {changes.map((change) => <ChangeRow key={`${change.table}.${change.column}.${change.kind}`} change={change} />)}
    </section>}
    {(manual.length > 0 || answer.manual.length > 0) && <section aria-label="Manual work">
      <h3 className="flex items-center gap-1.5 font-semibold" style={{ color: "var(--warning)" }}><AlertTriangle size={13} aria-hidden="true" className="shrink-0" />Manual work required</h3>
      <p className="mt-1" style={QUIET}>The SQL does not complete these changes.</p>
      {manual.map((change) => <ChangeRow key={`${change.table}.${change.column}.${change.kind}`} change={change} />)}
      {answer.manual.map((message) => <p key={message} className="mt-1" style={QUIET}>{message}</p>)}
    </section>}
    {answer.requires_maintenance && <p style={{ color: "var(--warning)" }}>Coordinate these changes with the app release; running-app compatibility has not been checked.</p>}
    {retained.length > 0 && <DatabaseDisclosure className="text-[var(--text-secondary)]" title={`${retained.length} unused database ${retained.length === 1 ? "item" : "items"} kept`}>
      <section aria-label="Retained in database" className="pt-1">{retained.map((change) => <ChangeRow key={`${change.table}.${change.column}`} change={change} />)}</section>
    </DatabaseDisclosure>}
  </div>
}
