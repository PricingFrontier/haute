/** Database information and explicit rename choices, separate from field configuration. */
import { Undo2 } from "lucide-react"
import type { SchemaColumn, SchemaTable } from "../api/types"
import { EditorLabel, QuietAction } from "../components/form"
import { Select } from "../haute-ui"
import { comparisonFailure, comparisonStatus, useDatabaseComparison, environmentState } from "../stores/useStoreCompareStore"
import { IDENTITY_NAMES } from "../utils/quoteIdentity"
import DatabaseDisclosure, { DatabaseExtras } from "./DatabaseDisclosure"
import { fieldDatabaseIssues } from "./fieldDatabaseIssues"
import { schemaColumns } from "./schema/schemaText"

const QUIET = { color: "var(--text-muted)" } as const

function useColumnDatabase(table: string, column: string) {
  const { state, form, answer } = useDatabaseComparison()
  const { read } = environmentState(state)
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
  const label = answer === null ? comparisonStatus(state, form)
    : invalidRename || manualRepair || changes.some((each) => each.disposition === "manual") ? "Needs attention"
      : differences.some((each) => each.kind === "renamed") ? "Rename"
        : missing ? "New column"
          : differences.some((each) => each.kind === "type") ? "Type change"
            : differences.length > 0 ? "Needs attention" : "Matches"
  const failure = comparisonFailure(state, form)
  return { state, answer, differences, changes, missing, label, failure, pairing, invalidRename, stored }
}

export function ColumnDatabaseStatus({ table, column, open, onOpen }: { table: string; column: string; open: boolean; onOpen: () => void }) {
  const { state, label, differences } = useColumnDatabase(table, column)
  if (state.environment === null) return null
  const color = label === "Check failed" ? "var(--danger-text)" : label === "Matches" ? "var(--success)" : label === "Rename" ? "var(--text-accent)"
    : label === "New column" || label === "Type change" || label === "Needs attention" ? "var(--warning)" : "var(--text-muted)"
  return <button type="button" aria-label={`Database status of ${column}`} aria-expanded={open} onClick={onOpen}
    data-testid={`database-mark-${table}-${column}`} className="focus-ring hover-bg inline-flex min-w-0 max-w-[45%] shrink-0 items-center gap-1.5 self-center rounded-md px-1.5 py-0.5 text-left text-[11px]"
    style={{ color }} title={differences.map((each) => each.message).join("\n") || `${state.environment}: ${label}`}>
    <span aria-hidden="true" className="size-1.5 shrink-0 rounded-full bg-current" /><span className="truncate">{label}</span>
  </button>
}

export function ColumnDatabaseDetails({ table, column, readOnly }: { table: SchemaTable; column: SchemaColumn; readOnly: boolean }) {
  const { state, answer, differences, changes, missing, label, failure, pairing, invalidRename, stored } = useColumnDatabase(table.name, column.name)
  if (state.environment === null) return null
  const candidates = answer?.extras.filter((each) => each.form_table === table.name && each.column !== null) ?? []
  const ordinary = column.index === null && !IDENTITY_NAMES.has(column.name)
  const canPair = ordinary && (pairing !== undefined || (missing && candidates.length > 0))
  return <section aria-label={`Database details for ${table.name}.${column.name}`}
    className="mr-12 ml-7 flex max-w-[32rem] min-w-0 flex-col gap-2 rounded-md px-2 py-1.5 text-[11px] leading-relaxed"
    style={{ border: "1px solid var(--border)", background: "var(--bg-base)" }}>
    <div className="flex items-baseline justify-between gap-2">
      <EditorLabel as="span">Database</EditorLabel>
      <span className="truncate" style={QUIET}>{state.environment}</span>
    </div>
    {failure !== null ? <p role="alert" style={{ color: "var(--danger-text)" }}>{failure}</p>
      : answer === null ? <p style={QUIET}>Waiting for the current database comparison…</p>
        : <>
          {fieldDatabaseIssues(table, column, differences, answer, stored).map((issue) => <div key={issue.title} className="flex flex-col gap-1">
            <p className="font-medium" style={{ color: "var(--text-primary)" }}>{issue.title}</p>
            <p style={QUIET}>{issue.description}</p>
            <p style={QUIET}><span className="font-medium">To resolve: </span>{issue.action}</p>
          </div>)}
          {label === "Matches" && <p style={QUIET}>This field matches the database.</p>}
          {invalidRename && <p role="alert" style={{ color: "var(--warning)" }}>This rename cannot be used in {state.environment}. Choose an available column or remove the rename choice.</p>}
          {(changes.length > 0 || differences.length > 0) && <DatabaseDisclosure title="SQL details" className="text-[var(--text-muted)]">
            <div className="flex flex-col gap-2 break-words">
              {changes.map((change) => <p key={change.kind}>{change.description}</p>)}
              {differences.map((each) => <p key={each.message}>{each.message}</p>)}
            </div>
          </DatabaseDisclosure>}
        </>}
    {canPair && <label className="flex min-w-0 flex-col gap-1">
      <span style={QUIET}>Renamed from</span>
      <Select aria-label={`Database column for ${table.name}.${column.name}`} value={pairing?.was ?? ""}
        disabled={readOnly || answer === null} fieldSize="compact" className="w-full"
        onChange={(event) => event.target.value
          ? state.pair({ table: table.name, column: column.name, was: event.target.value })
          : state.unpair(table.name, column.name)}>
        <option value="">{pairing === undefined ? "Create a new column" : "Remove rename choice"}</option>
        {pairing !== undefined && !candidates.some((each) => each.column === pairing.was) && <option value={pairing.was}>{pairing.was} → {column.name}</option>}
        {candidates.map((each) => <option key={each.column} value={each.column!}>{each.column} → {column.name}{each.quotes === null ? " · count unavailable" : ` · ${each.quotes} quotes`}</option>)}
      </Select>
    </label>}
    {!ordinary && pairing !== undefined && <QuietAction icon={Undo2} disabled={readOnly || answer === null}
      className="self-start" onClick={() => state.unpair(table.name, column.name)}>Remove rename choice</QuietAction>}
  </section>
}

export function TableDatabaseDetails({ table }: { table: SchemaTable }) {
  const { answer } = useDatabaseComparison()
  if (answer === null) return null
  const visible = new Set(schemaColumns(table).map((column) => column.name))
  const differences = answer.differences.filter((each) => each.table === table.name && (each.column === null || !visible.has(each.column)))
  const extras = answer.extras.filter((each) => each.form_table === table.name && each.column !== null)
  const count = extras.length
  if (differences.length === 0 && count === 0) return null
  return <DatabaseDisclosure className="mt-1 px-1 text-[11px] text-[var(--text-muted)]"
    title={`Database details${count > 0 ? ` · ${count} database-only ${count === 1 ? "field" : "fields"}` : ""}`}>
    <div role="region" aria-label={`Database details for ${table.name}`} className="flex flex-col gap-2 pb-1 leading-relaxed">
      {differences.map((each) => <p key={each.message}>{each.message}</p>)}
      <DatabaseExtras extras={extras} />
    </div>
  </DatabaseDisclosure>
}
