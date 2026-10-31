/**
 * The schema pane's Compare (specs/workbench): an environment chosen beside the pane's SQL,
 * its database's tables read through its connection as it is chosen and as Read again is
 * pressed, and the form the view holds compared with them as it changes; what is found shown
 * above the tables, each table and column of the form marked where it differs (`DatabaseMark`),
 * and each column the database holds beyond the form paired with one it lacks as a rename.
 */
import { RefreshCw, X } from "lucide-react"
import type { WorkbenchStoreDifference, WorkbenchStoreExtra } from "../api/types"
import { INPUT_STYLE } from "../components/form"
import useStoreCompareStore from "../stores/useStoreCompareStore"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchStore from "../stores/useWorkbenchStore"
import { IconButton } from "./schema/IconButton"
import { CHIP_CLASS } from "./schema/schemaText"

const QUIET_STYLE = { color: "var(--text-muted)" } as const

/** How a mark names each kind of difference. */
const MARKS: Record<WorkbenchStoreDifference["kind"], string> = {
  missing: "missing",
  renamed: "renamed",
  type: "type",
  rows: "rows",
  null: "nulls",
  key: "key",
  refers: "reference",
  collation: "collation",
  store: "store columns",
}

/** The differences of the newest comparison, while it is of the form the view holds. */
function useDifferences(): readonly WorkbenchStoreDifference[] {
  const form = useWorkbenchFormStore((s) => s.form)
  return useStoreCompareStore((s) =>
    s.compared.status === "compared" && s.compared.form === form ? s.compared.answer.differences : NONE,
  )
}
const NONE: readonly WorkbenchStoreDifference[] = []

/** The environment Compare chooses, among those haute.toml declares, and Read again. */
export function CompareControls() {
  const environments = useWorkbenchStore((s) => s.environments)
  const environment = useStoreCompareStore((s) => s.environment)
  const reading = useStoreCompareStore((s) => s.read.status === "reading")
  const choose = useStoreCompareStore((s) => s.choose)
  const readAgain = useStoreCompareStore((s) => s.readAgain)
  return (
    <span className="flex items-center gap-1">
      <select
        aria-label="Compare with"
        value={environment ?? ""}
        onChange={(event) => choose(event.target.value || null)}
        className={CHIP_CLASS}
        style={{ ...INPUT_STYLE, color: "var(--text-secondary)" }}
      >
        <option value="">Compare</option>
        {environments.map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
      </select>
      {environment !== null && (
        <IconButton
          icon={RefreshCw}
          onClick={readAgain}
          disabled={reading}
          aria-label={`Read the ${environment} database again`}
          title="Read again"
          style={{ color: "var(--text-muted)" }}
        />
      )}
    </span>
  )
}

/** A table's or a column's differences from the database, by the form's ids; nothing where none. */
export function DatabaseMark({ table, column = null }: { table: string; column?: string | null }) {
  const differences = useDifferences().filter((each) => each.table === table && each.column === column)
  if (differences.length === 0) return null
  const renamedOnly = differences.every((each) => each.kind === "renamed")
  return (
    <span
      data-testid={column === null ? `database-mark-${table}` : `database-mark-${table}-${column}`}
      className="shrink-0 rounded px-1 text-[10px]"
      style={{
        color: renamedOnly ? "var(--accent)" : "var(--warning)",
        border: `1px solid ${renamedOnly ? "var(--accent)" : "var(--warning)"}`,
      }}
      title={differences.map((each) => each.message).join("\n")}
    >
      {differences.length === 1 ? MARKS[differences[0].kind] : `${differences.length} differences`}
    </span>
  )
}

/** An extra column's pairing with a column of its table that the form has and the database lacks. */
function PairSelect({ extra }: { extra: WorkbenchStoreExtra & { table_id: string; column: string } }) {
  const form = useWorkbenchFormStore((s) => s.form)
  const differences = useDifferences()
  const pair = useStoreCompareStore((s) => s.pair)
  const table = form?.schema.tables.find((each) => each.id === extra.table_id)
  const missing = new Set(
    differences
      .filter((each) => each.table === extra.table_id && each.kind === "missing" && each.column !== null)
      .map((each) => each.column),
  )
  const candidates = table?.columns.filter((column) => missing.has(column.id)) ?? []
  if (candidates.length === 0) return null
  return (
    <select
      aria-label={`Rename of ${extra.table}.${extra.column}`}
      value=""
      onChange={(event) => {
        if (event.target.value) pair({ table: extra.table_id, column: event.target.value, was: extra.column })
      }}
      className={CHIP_CLASS}
      style={{ ...INPUT_STYLE, color: "var(--text-secondary)" }}
    >
      <option value="">Rename to…</option>
      {candidates.map((column) => (
        <option key={column.id} value={column.id}>
          {column.name}
        </option>
      ))}
    </select>
  )
}

const quotes = (count: number) => (count === 0 ? "" : count === 1 ? "1 quote" : `${count} quotes`)

/** What Compare found, above the schema's tables: reading, a failure, a match, Haute's own differences, extras and pairings. */
export function ComparePanel() {
  const environment = useStoreCompareStore((s) => s.environment)
  const read = useStoreCompareStore((s) => s.read)
  const compared = useStoreCompareStore((s) => s.compared)
  const renames = useStoreCompareStore((s) => s.renames)
  const unpair = useStoreCompareStore((s) => s.unpair)
  const form = useWorkbenchFormStore((s) => s.form)
  if (environment === null) return null
  const answer = compared.status === "compared" ? compared.answer : null
  const own = answer?.differences.filter((each) => each.table === null) ?? []
  const columnName = (table: string, column: string) =>
    form?.schema.tables.find((each) => each.id === table)?.columns.find((each) => each.id === column)?.name ?? column
  return (
    <div data-testid="store-compare" className="flex flex-col gap-1.5 px-2 pt-2 text-[11px]">
      {read.status === "reading" ? (
        <div role="status" style={QUIET_STYLE}>
          Reading the {environment} database…
        </div>
      ) : read.status === "failed" ? (
        <div role="alert" style={{ color: "var(--danger-text)" }}>
          {read.message}
        </div>
      ) : compared.status === "failed" ? (
        <div role="alert" style={{ color: "var(--danger-text)" }}>
          {compared.message}
        </div>
      ) : answer === null ? null : (
        <>
          {answer.matches && (
            <div role="status" style={{ color: "var(--success)" }}>
              The {environment} database matches the form.
            </div>
          )}
          {own.map((each) => (
            <div key={each.message} style={{ color: "var(--warning)" }}>
              {each.message}
            </div>
          ))}
          {answer.extras.length > 0 && (
            <section aria-label="Not in the form" className="flex flex-col gap-1">
              <div className="font-semibold" style={QUIET_STYLE}>
                Not in the form
              </div>
              {answer.extras.map((extra) => (
                <div key={`${extra.table}.${extra.column ?? ""}`} className="flex items-center gap-1.5">
                  <span className="font-mono" style={{ color: "var(--text-primary)" }}>
                    {extra.column === null ? extra.table : `${extra.table}.${extra.column}`}
                  </span>
                  <span style={QUIET_STYLE}>{quotes(extra.quotes)}</span>
                  {extra.table_id !== null && extra.column !== null && (
                    <PairSelect extra={{ ...extra, table_id: extra.table_id, column: extra.column }} />
                  )}
                </div>
              ))}
            </section>
          )}
          {renames.length > 0 && (
            <section aria-label="Renamed" className="flex flex-col gap-1">
              <div className="font-semibold" style={QUIET_STYLE}>
                Renamed
              </div>
              {renames.map((rename) => (
                <div key={`${rename.table}.${rename.column}`} className="flex items-center gap-1.5">
                  <span className="font-mono" style={{ color: "var(--text-primary)" }}>
                    {rename.was} → {columnName(rename.table, rename.column)}
                  </span>
                  <IconButton
                    icon={X}
                    onClick={() => unpair(rename.table, rename.column)}
                    aria-label={`Unpair ${rename.was}`}
                    title="Unpair"
                    style={{ color: "var(--text-muted)" }}
                  />
                </div>
              ))}
            </section>
          )}
        </>
      )}
    </div>
  )
}
