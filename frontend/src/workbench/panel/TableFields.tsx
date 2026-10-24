/** A schema table's columns to tick in a component's Fields (specs/workbench). */
import { ChevronDown, ChevronRight } from "lucide-react"
import type { SchemaTable } from "../../api/types"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { sheetColumns } from "../../utils/quoteIdentity"
import { plottable, readableName, toggleField, type FieldsWidget } from "../../utils/workbenchForm"
import { COLUMN_TYPES } from "../columnTypes"

export function TableFields({
  widget,
  table,
  usable,
  plots,
  matched,
  collapsed,
  onToggle,
}: {
  widget: FieldsWidget
  table: SchemaTable
  /** Whether its rows line up with the fields already chosen. */
  usable: boolean
  /** A Chart's: only the columns it can plot can be ticked. */
  plots: boolean
  /** While the Fields are searched, the ids of its columns the search matches, the only ones shown; null while they are not. */
  matched: ReadonlySet<string> | null
  collapsed: boolean
  onToggle: () => void
}) {
  const change = useWorkbenchFormStore((s) => s.change)
  const isShown = (columnId: string) => widget.fields.some((field) => field.table === table.id && field.column === columnId)
  // The quote's reference and option are never on the sheets: they are not offered.
  const offered = sheetColumns(table)
  const shown = offered.filter((column) => isShown(column.id)).length
  const Chevron = collapsed ? ChevronRight : ChevronDown
  const columns = matched === null ? offered : offered.filter((column) => matched.has(column.id))

  return (
    <div
      className="rounded-md"
      style={{ border: "1px solid var(--border)", background: "var(--bg-elevated)", opacity: usable ? 1 : 0.5 }}
      title={usable ? undefined : "Its rows don't line up with the fields already chosen"}
      data-testid={`fields-table-${table.id}`}
    >
      <button
        type="button"
        onClick={onToggle}
        disabled={matched !== null}
        aria-expanded={!collapsed}
        aria-label={`${collapsed ? "Expand" : "Collapse"} ${table.name}`}
        className={`focus-ring flex w-full items-center gap-1.5 rounded-md px-1.5 py-1 text-left ${matched === null ? "hover-bg" : ""}`}
      >
        <Chevron size={14} className="shrink-0" style={{ color: "var(--text-muted)" }} aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate font-mono font-semibold">{table.name}</span>
        <span className="text-[11px] font-semibold" style={{ color: table.role === "input" ? "var(--accent)" : "var(--success)" }}>
          {table.role === "input" ? "Input" : "Output"}
        </span>
        <span className="w-8 text-right text-[11px]" style={{ color: "var(--text-muted)" }}>
          {shown}/{offered.length}
        </span>
      </button>
      {!collapsed && (
        <div className="flex flex-col px-1 py-1" style={{ borderTop: "1px solid var(--border)" }}>
          {offered.length === 0 && (
            <p className="px-1.5 py-0.5" style={{ color: "var(--text-muted)" }}>
              No columns yet
            </p>
          )}
          {columns.map((column) => {
            const { label, icon: Icon, color } = COLUMN_TYPES[column.type]
            const checked = isShown(column.id)
            const tickable = checked || (usable && (!plots || plottable(column)))
            return (
              <label
                key={column.id}
                title={plots && !plottable(column) ? "A Chart plots number columns other than an index" : undefined}
                className={`flex items-center gap-1.5 rounded px-1.5 py-0.5 ${tickable ? "cursor-pointer hover-bg" : "cursor-not-allowed"}`}
              >
                <input
                  type="checkbox"
                  aria-label={`Show ${table.name} ${column.name}`}
                  checked={checked}
                  disabled={!tickable}
                  onChange={() => change((spec) => toggleField(spec, widget.id, { table: table.id, column: column.id }))}
                  style={{ accentColor: "var(--accent)" }}
                />
                <span title={label} className={`flex size-4 shrink-0 items-center justify-center ${color}`}>
                  <Icon size={12} aria-hidden="true" />
                </span>
                <span className="min-w-0 flex-1 truncate font-mono">{column.name}</span>
                <span className="shrink-0 truncate" style={{ color: "var(--text-muted)" }}>
                  {column.label || readableName(column.name)}
                </span>
              </label>
            )
          })}
        </div>
      )}
    </div>
  )
}
