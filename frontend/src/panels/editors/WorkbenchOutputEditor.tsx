import type { WorkbenchTable } from "../../api/types"
import useWorkbenchStore from "../../stores/useWorkbenchStore"
import { readWorkbenchTables, type WorkbenchOutputMapping } from "../../utils/workbenchTables"
import type { InputSource, OnUpdateConfig } from "./_shared"
import { NO_COLUMNS_NOTE, WorkbenchTablesHeader, tableLabelIssue } from "./WorkbenchInputEditor"

type Picks = Readonly<Record<string, string | null>>

/** What fills a table column: a frame column or none, and whether by name rather than a pick. */
export type MappedSource = { source: string | null; byName: boolean }

/**
 * What fills *column*: its pick in *picks*, else the frame's column of the same name. With
 * the frame's columns unknown (it has not been previewed), the same name is assumed.
 */
// eslint-disable-next-line react-refresh/only-export-components
export function mappedSource(column: string, picks: Picks, frameColumns: readonly string[] | null): MappedSource {
  if (Object.hasOwn(picks, column)) return { source: picks[column], byName: false }
  const byName = frameColumns === null || frameColumns.includes(column)
  return { source: byName ? column : null, byName }
}

/** The mapping with *column* of *table* filled from *source*: a pick, or no entry when it is the default. */
function picked(
  mapping: WorkbenchOutputMapping,
  table: string,
  column: string,
  source: string | null,
  frameColumns: readonly string[] | null,
): WorkbenchOutputMapping {
  const entries: Record<string, string | null> = { ...(mapping[table] ?? {}) }
  const byDefault = mappedSource(column, {}, frameColumns).source
  if (source === byDefault) delete entries[column]
  else entries[column] = source
  const next = { ...mapping }
  if (Object.keys(entries).length === 0) delete next[table]
  else next[table] = entries
  return next
}

function readMapping(value: unknown): WorkbenchOutputMapping {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as WorkbenchOutputMapping)
    : {}
}

/**
 * The Workbench Output's panel (specs/workbench): the response's tables the project's
 * workbench supplies, each with the node connected to its port, and for each column the
 * connected frame's column that fills it: the same-named one unless picked otherwise. The
 * tables are read-only (the editor keeps them current, `useWorkbenchTables`); the
 * mapping is the node's own.
 */
export default function WorkbenchOutputEditor({
  config,
  onUpdate,
  inputSources,
  insideSubmodel = false,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  /** The node's inputs, each with the table port its edge lands on. */
  inputSources: readonly InputSource[]
  /** A submodel is open: the editor updates the tables only at the pipeline's top level. */
  insideSubmodel?: boolean
}) {
  const enabled = useWorkbenchStore((state) => state.enabled)
  const tables = readWorkbenchTables(config)
  const names = tables.map((table) => table.name)
  const mapping = readMapping(config.mapping)

  return (
    <div className="px-4 py-3 space-y-3" data-testid="workbench-output-editor">
      <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>
        Connect a frame to each table. Each column is filled from the frame's column of the same name, or the one you
        pick; a column nothing fills is left empty.
      </p>

      <section data-testid="workbench-output-tables" className="space-y-1.5">
        <WorkbenchTablesHeader
          enabled={enabled}
          insideSubmodel={insideSubmodel}
          empty={tables.length === 0}
        />
        {tables.map((table, index) => (
          <OutputTable
            key={`${index}:${table.name}`}
            table={table}
            issue={tableLabelIssue(names, index, new Set())}
            source={inputSources.find((input) => input.targetHandle === table.name) ?? null}
            picks={mapping[table.name] ?? {}}
            onPick={(column, source, frameColumns) =>
              onUpdate("mapping", picked(mapping, table.name, column, source, frameColumns))}
          />
        ))}
      </section>
    </div>
  )
}

function OutputTable({
  table,
  issue,
  source,
  picks,
  onPick,
}: {
  table: WorkbenchTable
  issue: string | null
  source: InputSource | null
  picks: Picks
  onPick: (column: string, source: string | null, frameColumns: readonly string[] | null) => void
}) {
  // The frame's columns as its last preview recorded them; unknown until it has one.
  const frameColumns = source?.columns ? source.columns.map((column) => column.name) : null
  return (
    <div
      data-testid={`workbench-table-${table.name}`}
      className="rounded-md border px-2 py-1.5 text-xs"
      style={{ borderColor: issue ? "var(--warning)" : "var(--border)", background: "var(--bg-elevated)" }}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-mono font-semibold truncate">{table.name}</span>
        <span className="shrink-0 text-[11px]" style={{ color: "var(--text-muted)" }}>
          {table.rows === "one" ? "one per quote" : "many per quote"}
        </span>
      </div>
      {issue && (
        <p role="alert" className="mt-0.5 text-[11px]" style={{ color: "var(--warning)" }}>
          {issue}
        </p>
      )}
      {table.columns.length === 0 ? (
        <p className="mt-0.5 text-[11px]" style={{ color: "var(--text-muted)" }}>
          {NO_COLUMNS_NOTE}
        </p>
      ) : (
        <>
          <p
            data-testid={`workbench-table-connection-${table.name}`}
            className="mt-0.5 text-[11px]"
            style={{ color: source === null ? "var(--warning)" : "var(--text-muted)" }}
          >
            {source === null ? "Not connected" : `From ${source.sourceLabel}`}
          </p>
          <ul className="mt-1 space-y-0.5">
            {table.columns.map((column) => (
              <MappedColumn
                key={column.name}
                table={table.name}
                name={column.name}
                type={column.type}
                connected={source !== null}
                frameColumns={frameColumns}
                mapped={mappedSource(column.name, picks, frameColumns)}
                onPick={(value) => onPick(column.name, value, frameColumns)}
              />
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

function MappedColumn({
  table,
  name,
  type,
  connected,
  frameColumns,
  mapped,
  onPick,
}: {
  table: string
  name: string
  type: string
  connected: boolean
  frameColumns: readonly string[] | null
  mapped: MappedSource
  onPick: (source: string | null) => void
}) {
  const missing = mapped.source !== null && frameColumns !== null && !frameColumns.includes(mapped.source)
  const unfilled = mapped.source === null
  const options = [...(frameColumns ?? [])]
  if (mapped.source !== null && !options.includes(mapped.source)) options.push(mapped.source)
  const title = unfilled
    ? "Nothing fills this column: it is left empty"
    : missing
      ? `The connected frame has no column called ${mapped.source}`
      : mapped.byName
        ? "Filled by name"
        : undefined
  return (
    <li className="flex items-center gap-2 min-h-6">
      <span className="min-w-0 flex-1 font-mono truncate">{name}</span>
      <span className="shrink-0 font-mono text-[11px]" style={{ color: "var(--text-muted)" }}>
        {type}
      </span>
      <select
        data-testid={`workbench-mapping-${table}-${name}`}
        aria-label={`Fills ${table} ${name}`}
        value={mapped.source ?? ""}
        disabled={!connected}
        title={title}
        onChange={(event) => onPick(event.target.value === "" ? null : event.target.value)}
        className="w-[48%] shrink-0 rounded border px-1 py-0.5 font-mono text-[11px]"
        style={{
          borderColor: unfilled || missing ? "var(--warning)" : "var(--border)",
          color: unfilled || missing ? "var(--warning)" : "var(--text-primary)",
        }}
      >
        <option value="">— none —</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {option === mapped.source && missing
              ? `${option} (missing)`
              : option === mapped.source && mapped.byName
                ? `${option} (by name)`
                : option}
          </option>
        ))}
      </select>
    </li>
  )
}
