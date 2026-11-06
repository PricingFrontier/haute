/** Database information and explicit rename choices in each field's existing details. */
import type { SchemaColumn, SchemaTable, WorkbenchStoreCompareResponse, WorkbenchStoreDifference } from "../api/types"
import { COMPACT_FIELD_CLASS, INPUT_STYLE } from "../components/form"
import useStoreCompareStore, { comparisonStatus, currentComparison, environmentState } from "../stores/useStoreCompareStore"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import { IDENTITY_NAMES } from "../utils/quoteIdentity"

const QUIET = { color: "var(--text-muted)" } as const
const DESCRIBED_DIFFERENCES: Partial<Record<WorkbenchStoreCompareResponse["changes"][number]["kind"], WorkbenchStoreDifference["kind"]>> = {
  "add column": "missing", "rename column": "renamed", "retype column": "type",
}

function useColumnDatabase(table: string, column: string) {
  const state = useStoreCompareStore()
  const form = useWorkbenchFormStore((s) => s.form)
  const answer = currentComparison(state, form)
  const { read, compared } = environmentState(state)
  const differences = answer?.differences.filter((each) => each.table === table &&
    (each.column === column || (each.column === null && each.kind === "missing"))) ?? []
  const changes = answer?.changes.filter((each) => each.table === table && each.column === column) ?? []
  const pairing = state.renames.find((each) => each.table === table && each.column === column)
  const accepted = answer?.renames.some((each) => each.table === table && each.column === column && each.was === pairing?.was)
  const stored = read.status === "read" ? read.tables.find((each) => each.name === table) : undefined
  const alreadyPresent = pairing !== undefined && stored?.columns.some((each) => each.name === column) === true && !stored.columns.some((each) => each.name === pairing.was)
  const invalidRename = answer !== null && pairing !== undefined && !accepted && !alreadyPresent
  const missing = differences.some((each) => each.kind === "missing")
  const manualRepair = differences.some((each) => each.kind !== "missing" && each.kind !== "renamed" && each.kind !== "type")
  const additional = differences.filter((each) => !changes.some((change) => DESCRIBED_DIFFERENCES[change.kind] === each.kind))
  const label = answer === null ? comparisonStatus(state, form)
    : invalidRename || manualRepair || changes.some((each) => each.disposition === "manual") ? "Needs attention"
      : differences.some((each) => each.kind === "renamed") ? "Rename"
        : missing ? "New column"
          : differences.some((each) => each.kind === "type") ? "Type change"
            : differences.length > 0 ? "Needs attention" : "Matches"
  const failure = label !== "Check failed" ? null : read.status === "failed" ? read.message : compared.status === "failed" ? compared.message : null
  return { state, answer, differences, additional, changes, missing, label, failure, pairing, invalidRename }
}

export function ColumnDatabaseStatus({ table, column, open, onOpen }: { table: string; column: string; open: boolean; onOpen: () => void }) {
  const { state, label, differences } = useColumnDatabase(table, column)
  if (state.environment === null) return null
  const color = label === "Matches" ? "var(--success)" : label === "Rename" ? "var(--accent)"
    : label === "New column" || label === "Type change" || label === "Needs attention" ? "var(--warning)" : "var(--text-muted)"
  return <button type="button" aria-label={`Database status of ${column}`} aria-expanded={open} onClick={onOpen}
    data-testid={`database-mark-${table}-${column}`} className="focus-ring max-w-[45%] shrink-0 self-center truncate rounded px-1.5 py-0.5 text-left text-[11px] hover:bg-[var(--bg-hover)]"
    style={{ color }} title={differences.map((each) => each.message).join("\n") || `${state.environment}: ${label}`}>
    <span aria-hidden="true" className="mr-1">●</span>{label}
  </button>
}

export function ColumnDatabaseDetails({ table, column, readOnly }: { table: SchemaTable; column: SchemaColumn; readOnly: boolean }) {
  const { state, answer, additional, changes, missing, label, failure, pairing, invalidRename } = useColumnDatabase(table.name, column.name)
  if (state.environment === null) return null
  const candidates = answer?.extras.filter((each) => each.form_table === table.name && each.column !== null) ?? []
  const ordinary = column.index === null && !IDENTITY_NAMES.has(column.name)
  const canPair = ordinary && (pairing !== undefined || (missing && candidates.length > 0))
  return <section aria-label={`Database details for ${table.name}.${column.name}`} className="col-span-2 mb-2 flex min-w-0 flex-col gap-2 border-b pb-2" style={{ borderColor: "var(--border)" }}>
    <div className="flex items-baseline justify-between gap-2">
      <span className="font-medium">Database <span style={QUIET}>· {state.environment}</span></span>
      <span style={QUIET}>{label}</span>
    </div>
    {failure !== null ? <p role="alert" style={{ color: "var(--danger-text)" }}>{failure}</p>
      : answer === null ? <p style={QUIET}>Waiting for the current database comparison…</p>
        : <>
          {changes.map((change) => <p key={change.kind} style={QUIET}>{change.description}{change.quotes !== null && change.quotes > 0 && ` ${change.quotes.toLocaleString()} ${change.quotes === 1 ? "quote has" : "quotes have"} values.`}</p>)}
          {additional.map((each) => <p key={each.message} style={QUIET}>{each.message}</p>)}
          {label === "Matches" && <p style={QUIET}>This field matches the database.</p>}
          {invalidRename && <p role="alert" style={{ color: "var(--warning)" }}>This rename cannot be used in {state.environment}. Choose an available column or remove the rename choice.</p>}
        </>}
    {canPair && <label className="flex min-w-0 flex-col gap-1">
      <span style={QUIET}>Use database values from</span>
      <select aria-label={`Database column for ${table.name}.${column.name}`} value={pairing?.was ?? ""}
        disabled={readOnly || answer === null} className={`${COMPACT_FIELD_CLASS} w-full`} style={INPUT_STYLE}
        onChange={(event) => event.target.value
          ? state.pair({ table: table.name, column: column.name, was: event.target.value })
          : state.unpair(table.name, column.name)}>
        <option value="">{pairing === undefined ? "Create a new column" : "Remove rename choice"}</option>
        {pairing !== undefined && !candidates.some((each) => each.column === pairing.was) && <option value={pairing.was}>{pairing.was} → {column.name}</option>}
        {candidates.map((each) => <option key={each.column} value={each.column!}>{each.column} → {column.name}{each.quotes === null ? " · count unavailable" : ` · ${each.quotes} quotes`}</option>)}
      </select>
      <span style={QUIET}>A rename keeps existing values and applies across environments.</span>
    </label>}
    {!ordinary && pairing !== undefined && <button type="button" disabled={readOnly || answer === null}
      className="focus-ring hover-bg self-start rounded px-2 py-1 disabled:opacity-50"
      onClick={() => state.unpair(table.name, column.name)}>Remove rename choice</button>}
  </section>
}

export function TableDatabaseDetails({ table }: { table: string }) {
  const state = useStoreCompareStore()
  const form = useWorkbenchFormStore((s) => s.form)
  const answer = currentComparison(state, form)
  if (answer === null) return null
  const differences = answer.differences.filter((each) => each.table === table && each.column === null)
  const retained = answer.changes.filter((each) => each.table === table && each.disposition === "retained")
  const extras = answer.extras.filter((each) => each.form_table === table && each.column !== null && !retained.some((change) => change.column === each.column))
  const reshape = answer.changes.some((each) => each.table === table && each.kind === "reshape table")
  const count = retained.length + extras.length
  if (differences.length === 0 && count === 0) return null
  return <details className="mt-1 px-1 text-[11px]" style={QUIET}>
    <summary className="cursor-pointer py-1">Database details{count > 0 ? ` · ${count} ${extras.length === 0 ? "retained" : "database-only"} ${count === 1 ? "field" : "fields"}` : ""}</summary>
    <div role="region" aria-label={`Database details for ${table}`} className="flex flex-col gap-2 py-1">
      {differences.map((each) => <p key={each.message}>{each.message}</p>)}
      {retained.map((each) => <div key={each.column}><code>{each.column}</code><p>{each.description}</p>{each.quotes !== null && each.quotes > 0 && <p>{each.quotes} quotes with values</p>}</div>)}
      {extras.map((each) => <div key={each.column}><code>{each.column}</code>
        <p>{reshape ? "Preserving these values is part of the table's manual migration." : "This database column is not used by the schema."}</p>
        <p>{each.quotes === null ? "Quote count unavailable" : `${each.quotes} quotes with values`}</p>
      </div>)}
    </div>
  </details>
}
