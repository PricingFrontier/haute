import { KeyRound } from "lucide-react"
import type { WorkbenchColumn, WorkbenchTable } from "../../api/types"
import useWorkbenchStore from "../../stores/useWorkbenchStore"
import { NODE_TYPES } from "../../utils/nodeTypes"
import { columnFill, workbenchNodeTables, type ColumnFill } from "../../utils/workbenchTables"
import type { InputSource } from "./_shared"
import { WorkbenchTableCard, WorkbenchTablesHeader, tableLabelIssue } from "./WorkbenchInputEditor"

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
 * of the project's workbench gave, each table's outputs by its port's name, with the node
 * connected to that port, and each of its columns, which the connected frame's column of
 * exactly its name fills, bordered red while it is missing and orange while that column is
 * of a type that does not fit; the quote's reference and option, which the response gives,
 * are neither. A table with inputs carries its key column first after the identity, marked
 * as its key. The tables are read-only, and nothing of them is in the node.
 */
export default function WorkbenchOutputEditor({
  inputSources,
  reservedFrameLabels,
}: {
  /** The node's inputs, each with the table port its edge lands on. */
  inputSources: readonly InputSource[]
  /** The document's reserved labels, which a table's port is held against as the schema editor holds it. */
  reservedFrameLabels: ReadonlySet<string>
}) {
  const workbench = useWorkbenchStore((state) => state.tables)
  const tables = workbenchNodeTables(NODE_TYPES.WORKBENCH_OUTPUT, workbench)
  const ports = tables.map((table) => table.port)

  return (
    <div className="px-4 py-3 space-y-3" data-testid="workbench-output-editor">
      <section data-testid="workbench-output-tables" className="space-y-1.5">
        <WorkbenchTablesHeader empty={tables.length === 0} />
        {tables.map((table, index) => (
          <OutputTable
            key={`${index}:${table.port}`}
            table={table}
            issue={tableLabelIssue(ports, index, reservedFrameLabels)}
            source={inputSources.find((input) => input.targetHandle === table.port) ?? null}
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
    <WorkbenchTableCard table={table} issue={issue}>
      <p
        data-testid={`workbench-table-connection-${table.port}`}
        className="mt-0.5 text-[11px]"
        style={{ color: source === null ? "var(--danger-text)" : "var(--text-muted)" }}
      >
        {source === null ? "Not connected" : `From ${source.sourceLabel}`}
      </p>
      <ul className="mt-1 space-y-1">
        {table.columns.map((column, at) => (
          <OutputColumn
            key={column.name}
            table={table.port}
            column={column}
            isKey={column.name === table.key}
            fill={columnFill(table, at, source !== null, source?.columns)}
          />
        ))}
      </ul>
    </WorkbenchTableCard>
  )
}

function OutputColumn({ table, column, isKey, fill }: { table: string; column: WorkbenchColumn; isKey: boolean; fill: ColumnFill }) {
  return (
    <li
      data-testid={`workbench-column-${table}-${column.name}`}
      title={fillTitle(fill)}
      className="flex items-center gap-2 min-h-6 rounded border px-1.5"
      style={{ borderColor: FILL_BORDERS[fill.kind] }}
    >
      <span className="min-w-0 flex-1 font-mono truncate">{column.name}</span>
      {isKey && <KeyRound size={11} role="img" aria-label="Key" className="shrink-0" style={{ color: "var(--accent)" }} />}
      <span className="shrink-0 font-mono text-[11px]" style={{ color: "var(--text-muted)" }}>
        {column.type}
      </span>
    </li>
  )
}
