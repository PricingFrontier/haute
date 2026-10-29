import type { WorkbenchColumn, WorkbenchTable } from "../../api/types"
import useWorkbenchStore from "../../stores/useWorkbenchStore"
import { NODE_TYPES } from "../../utils/nodeTypes"
import { columnFill, workbenchNodeTables, type ColumnFill } from "../../utils/workbenchTables"
import type { InputSource } from "./_shared"
import { NO_COLUMNS_NOTE, WorkbenchTablesHeader, tableLabelIssue } from "./WorkbenchInputEditor"

/** A column's border: red while it is missing, orange while its frame's column is of another type. */
const FILL_BORDERS: Readonly<Record<ColumnFill["kind"], string>> = {
  missing: "var(--danger)",
  mistyped: "var(--caution)",
  filled: "var(--border)",
  unknown: "var(--border)",
  haute: "var(--border)",
}

function fillTitle(fill: ColumnFill): string | undefined {
  if (fill.kind === "missing") return "Missing from the frame"
  if (fill.kind === "mistyped") return `${fill.dtype} in the frame`
  return undefined
}

/**
 * The Workbench Output's panel (specs/workbench): the response's tables the newest fetch
 * of the project's workbench gave, each with the node connected to its port, and each of
 * its columns, which the connected frame's column of exactly its name fills, bordered red
 * while it is missing and orange while that column is of a type that does not fit; the
 * quote's reference and option, which the response gives, are neither. The tables are
 * read-only, and nothing of them is in the node.
 */
export default function WorkbenchOutputEditor({
  inputSources,
  reservedFrameLabels,
}: {
  /** The node's inputs, each with the table port its edge lands on. */
  inputSources: readonly InputSource[]
  /** The document's reserved labels, which a table's name is held against as the schema editor holds it. */
  reservedFrameLabels: ReadonlySet<string>
}) {
  const workbench = useWorkbenchStore((state) => state.tables)
  const tables = workbenchNodeTables(NODE_TYPES.WORKBENCH_OUTPUT, workbench)
  const names = tables.map((table) => table.name)

  return (
    <div className="px-4 py-3 space-y-3" data-testid="workbench-output-editor">
      <section data-testid="workbench-output-tables" className="space-y-1.5">
        <WorkbenchTablesHeader empty={tables.length === 0} />
        {tables.map((table, index) => (
          <OutputTable
            key={`${index}:${table.name}`}
            table={table}
            issue={tableLabelIssue(names, index, reservedFrameLabels)}
            source={inputSources.find((input) => input.targetHandle === table.name) ?? null}
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
}: {
  table: WorkbenchTable
  issue: string | null
  source: InputSource | null
}) {
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
            style={{ color: source === null ? "var(--danger-text)" : "var(--text-muted)" }}
          >
            {source === null ? "Not connected" : `From ${source.sourceLabel}`}
          </p>
          <ul className="mt-1 space-y-1">
            {table.columns.map((column, at) => (
              <OutputColumn
                key={column.name}
                table={table.name}
                column={column}
                fill={columnFill(table, at, source !== null, source?.columns)}
              />
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

function OutputColumn({ table, column, fill }: { table: string; column: WorkbenchColumn; fill: ColumnFill }) {
  return (
    <li
      data-testid={`workbench-column-${table}-${column.name}`}
      title={fillTitle(fill)}
      className="flex items-center gap-2 min-h-6 rounded border px-1.5"
      style={{ borderColor: FILL_BORDERS[fill.kind] }}
    >
      <span className="min-w-0 flex-1 font-mono truncate">{column.name}</span>
      <span className="shrink-0 font-mono text-[11px]" style={{ color: "var(--text-muted)" }}>
        {column.type}
      </span>
    </li>
  )
}
