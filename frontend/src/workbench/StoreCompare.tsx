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
import useStoreCompareStore, { comparisonStatus, currentComparison, environmentState } from "../stores/useStoreCompareStore"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchStore from "../stores/useWorkbenchStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import { tableColumns } from "../utils/quoteIdentity"
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

/** The differences of the newest comparison, while it is of the form and the pairings the view holds. */
function useDifferences(): readonly WorkbenchStoreDifference[] {
  const form = useWorkbenchFormStore((s) => s.form)
  return useStoreCompareStore((s) => currentComparison(s, form)?.differences ?? NONE)
}
const NONE: readonly WorkbenchStoreDifference[] = []

/** The environment Compare chooses, among those haute.toml declares, and Read again. */
export function CompareControls() {
  const environments = useWorkbenchStore((s) => s.environments)
  const state = useStoreCompareStore()
  const form = useWorkbenchFormStore((s) => s.form)
  if (environments.length === 0) return null
  const reading = environments.some((name) => environmentState(state, name).read.status === "reading")
  return (
    <div className="flex flex-col gap-2 px-4 pt-3 pb-2" style={{ borderBottom: "1px solid var(--border)" }}>
      <div className="flex flex-wrap items-center justify-between gap-2 text-[11px]" style={QUIET_STYLE}>
        <span>Shared schema · Database environments</span>
        <button type="button" className="btn-ghost rounded px-1.5 py-1" disabled={reading} onClick={() => state.checkAll(environments)}>Check environments</button>
      </div>
      <div role="group" aria-label="Database environments" className="flex flex-wrap gap-1.5">
        {environments.map((name) => (
          <button key={name} type="button" aria-label={`Compare ${name}`} aria-describedby={`environment-status-${name}`} aria-pressed={state.environment === name}
            className="btn-ghost flex min-w-20 flex-1 flex-col items-start rounded px-2 py-1.5 text-xs"
            style={state.environment === name ? { background: "var(--bg-hover)", boxShadow: "inset 0 0 0 1px var(--border)" } : undefined}
            onClick={() => state.choose(name)}>
            <span className="font-medium">{name}</span>
            <span id={`environment-status-${name}`} className="text-[11px]" style={QUIET_STYLE}>{comparisonStatus(state, form, name)}</span>
          </button>
        ))}
      </div>
    </div>
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
function PairSelect({ extra }: { extra: WorkbenchStoreExtra & { form_table: string; column: string } }) {
  const form = useWorkbenchFormStore((s) => s.form)
  const differences = useDifferences()
  const pair = useStoreCompareStore((s) => s.pair)
  const previewing = useWorkbenchViewStore((s) => s.mode === "preview")
  const table = form?.schema.tables.find((each) => each.name === extra.form_table)
  const missing = new Set(
    differences
      .filter((each) => each.table === extra.form_table && each.kind === "missing" && each.column !== null)
      .map((each) => each.column),
  )
  const candidates = table === undefined ? [] : tableColumns(table).filter((column) => missing.has(column.name))
  if (candidates.length === 0) return null
  return (
    <select
      aria-label={`Rename of ${extra.table}.${extra.column}`}
      value=""
      disabled={previewing}
      onChange={(event) => {
        if (event.target.value) pair({ table: extra.form_table, column: event.target.value, was: extra.column })
      }}
      className={CHIP_CLASS}
      style={{ ...INPUT_STYLE, color: "var(--text-secondary)" }}
    >
      <option value="">Rename to…</option>
      {candidates.map((column) => (
        <option key={column.name} value={column.name}>
          {column.name}
        </option>
      ))}
    </select>
  )
}

const quotes = (count: number | null) =>
  count === null
    ? "quotes not counted (table lacks quote identity)"
    : count === 0
      ? ""
      : count === 1
        ? "1 quote"
        : `${count} quotes`

/** What Compare found, above the schema's tables: reading, a failure, a match, Haute's own differences, extras and pairings. */
export function ComparePanel({ onReview }: { onReview: () => void }) {
  const environment = useStoreCompareStore((s) => s.environment)
  const { read, compared } = useStoreCompareStore((s) => environmentState(s))
  const renames = useStoreCompareStore((s) => s.renames)
  const unpair = useStoreCompareStore((s) => s.unpair)
  const form = useWorkbenchFormStore((s) => s.form)
  const previewing = useWorkbenchViewStore((s) => s.mode === "preview")
  const answer = useStoreCompareStore((s) => currentComparison(s, form))
  const readAgain = useStoreCompareStore((s) => s.readAgain)
  if (environment === null) return null
  const own = answer?.differences.filter((each) => each.table === null) ?? []
  const columnName = (table: string, column: string) => {
    const held = form?.schema.tables.find((each) => each.name === table)
    return (held === undefined ? undefined : tableColumns(held).find((each) => each.name === column)?.name) ?? column
  }
  return (
    <div data-testid="store-compare" className="flex max-h-[35vh] shrink-0 flex-col gap-2 overflow-auto px-4 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">Comparing with {environment}</span>
        <IconButton icon={RefreshCw} onClick={readAgain} disabled={read.status === "reading"} aria-label={`Check ${environment} again`} title="Check again" />
        <button type="button" className="btn-ghost ml-auto rounded px-2 py-1" disabled={previewing || answer === null} onClick={onReview}>Review changes</button>
      </div>
      {read.status === "read" && <p className="text-[11px]" style={QUIET_STYLE}>Database checked <time dateTime={new Date(read.checkedAt).toISOString()}>{new Date(read.checkedAt).toLocaleString()}</time></p>}
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
      ) : answer === null ? <p role="status" style={QUIET_STYLE}>Comparing the working schema…</p> : (
        <>
          {answer.matches && answer.blocked.length === 0 && (
            <div role="status" style={{ color: "var(--success)" }}>
              The {environment} database is compatible with this schema.
            </div>
          )}
          {own.map((each) => (
            <div key={each.message} style={{ color: "var(--warning)" }}>
              {each.message}
            </div>
          ))}
          {answer.extras.length > 0 && (
            <section aria-label="Retained in database" className="flex flex-col gap-1">
              <div className="font-semibold" style={QUIET_STYLE}>
                Retained in database
              </div>
              {answer.extras.map((extra) => (
                <div key={`${extra.table}.${extra.column ?? ""}`} className="flex flex-wrap items-center gap-1.5">
                  <span className="font-mono" style={{ color: "var(--text-primary)" }}>
                    {extra.column === null ? extra.table : `${extra.table}.${extra.column}`}
                  </span>
                  <span style={QUIET_STYLE}>{quotes(extra.quotes)}</span>
                  {extra.form_table !== null && extra.column !== null && (
                    <PairSelect extra={{ ...extra, form_table: extra.form_table, column: extra.column }} />
                  )}
                </div>
              ))}
            </section>
          )}
          {renames.length > 0 && (
            <section aria-label="Rename choices" className="flex flex-col gap-1">
              <div className="font-semibold" style={QUIET_STYLE}>
                Rename choices · shared across environments
              </div>
              {renames.map((rename) => (
                <div key={`${rename.table}.${rename.column}`} className="flex flex-wrap items-center gap-1.5">
                  <span className="font-mono" style={{ color: "var(--text-primary)" }}>
                    {rename.table}.{rename.was} → {columnName(rename.table, rename.column)}
                  </span>
                  <IconButton
                    icon={X}
                    onClick={() => unpair(rename.table, rename.column)}
                    aria-label={`Unpair ${rename.was}`}
                    title="Unpair"
                    disabled={previewing}
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
