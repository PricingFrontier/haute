import { useState, useMemo } from "react"
import { Search } from "lucide-react"
import { configField } from "../../utils/configField"
import type { OnUpdateConfig } from "./_shared"
import type { ColumnInfo } from "../../types/node"
import ColumnTable from "../../components/ColumnTable"
import { EditorLabel } from "../../components/form"
import { useCommittedDraft } from "../../components/form/useCommittedDraft"

interface ColumnsTabProps {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  /** Full column set before selected_columns filtering */
  availableColumns: ColumnInfo[]
  /** Current output columns (post-filter) */
  columns: ColumnInfo[]
}

/** No column ticked: a choice not yet made, which the store cannot hold (none stored is every column). */
const NO_COLUMN = "none"
type Selection = string[] | typeof NO_COLUMN

function sameSelection(left: string[], right: string[]): boolean {
  if (left.length !== right.length) return false
  const sortedLeft = [...left].sort()
  const sortedRight = [...right].sort()
  return sortedLeft.every((value, index) => value === sortedRight[index])
}

export default function ColumnsTab({ config, onUpdate, availableColumns, columns }: ColumnsTabProps) {
  const [search, setSearch] = useState("")

  const selectedColumns = configField<string[]>(config, "selected_columns", [])


  // Use available columns if we have them, else fall back to current columns
  const allColumns = availableColumns.length > 0 ? availableColumns : columns

  // Detect stale columns: in selected_columns but not in allColumns
  const allColumnNames = useMemo(() => new Set(allColumns.map((c) => c.name)), [allColumns])
  const staleColumns = useMemo<ColumnInfo[]>(() => {
    if (selectedColumns.length === 0) return []
    return selectedColumns
      .filter((name) => !allColumnNames.has(name))
      .map((name) => ({ name, dtype: "unknown" }))
  }, [selectedColumns, allColumnNames])

  const filtered = useMemo(() => {
    const q = search.toLowerCase()
    const base = search ? allColumns.filter((c) => c.name.toLowerCase().includes(q)) : allColumns
    // Append stale ghost rows (filtered by search too)
    const staleFiltered = search ? staleColumns.filter((c) => c.name.toLowerCase().includes(q)) : staleColumns
    return [...base, ...staleFiltered]
  }, [allColumns, staleColumns, search])

  // The selection as ticked, on the shared draft: every toggle commits at once, and none
  // ticked stays a draft, refused by the store until a column is chosen. A save leaves it on
  // screen; a change from outside (an Undo, a sync frame) lets it go.
  const selection = useCommittedDraft<Selection>(
    selectedColumns,
    (next) => {
      if (next === NO_COLUMN) return { ok: false }
      // Exactly the available columns is every column, stored as none; a stale
      // name beside them is kept, so it stays visible to be removed.
      const selectsEveryAvailableColumn =
        next.length === allColumns.length &&
        allColumns.every((column) => next.includes(column.name))
      const normalized = selectsEveryAvailableColumn ? [] : next
      if (sameSelection(normalized, selectedColumns)) return
      onUpdate("selected_columns", normalized)
    },
    { equals: (a, b) => a === b || (a !== NO_COLUMN && b !== NO_COLUMN && sameSelection(a, b)) },
  )
  const noColumn = selection.shown === NO_COLUMN

  // When selected_columns is empty, all columns are kept unless none is ticked.
  const isAllSelected = !noColumn && selectedColumns.length === 0

  const isSelected = (col: string) =>
    !noColumn && (isAllSelected || selectedColumns.includes(col))

  const toggleColumn = (col: string) => {
    const base = noColumn ? [] : isAllSelected ? allColumns.map((column) => column.name) : selectedColumns
    const next = base.includes(col)
      ? base.filter((candidate) => candidate !== col)
      : [...base, col]
    // Unticking the last real column waits for a choice; removing the last,
    // stale name leaves no selection, which is every column.
    if (next.length === 0 && allColumnNames.has(col)) {
      selection.edit(NO_COLUMN)
      return
    }
    selection.commit(next)
  }

  const selectAll = () => selection.commit([])
  const selectNone = () => selection.edit(NO_COLUMN)

  const selectedCount = noColumn ? 0 : isAllSelected ? allColumns.length : selectedColumns.length

  if (allColumns.length === 0) {
    return (
      <div className="px-4 py-6 text-center">
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          Preview or run this node to see its output columns
        </p>
      </div>
    )
  }

  return (
    <div className="px-4 py-3 flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <EditorLabel as="span">Output Columns</EditorLabel>
        <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>
          {selectedCount} / {allColumns.length}
        </span>
      </div>

      {/* Search + select all / none */}
      <div className="flex items-center gap-2">
        <div className="flex-1 relative">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2" style={{ color: "var(--text-muted)" }} />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter columns..."
            className="w-full pl-7 pr-2 py-1 text-xs rounded-md border bg-transparent focus:outline-none focus:ring-1"
            style={{ color: "var(--text-primary)", borderColor: "var(--border)", background: "var(--bg-input)" }}
          />
        </div>
        <button
          onClick={selectAll}
          className="text-[10px] font-medium px-1.5 py-0.5 rounded"
          style={{ color: isAllSelected ? "var(--text-muted)" : "var(--accent)" }}
          disabled={isAllSelected}
        >
          All
        </button>
        <button
          onClick={selectNone}
          className="text-[10px] font-medium px-1.5 py-0.5 rounded"
          style={{ color: "var(--accent)" }}
          disabled={noColumn}
        >
          None
        </button>
      </div>

      {noColumn && (
        <p
          role="status"
          className="text-[10px]"
          style={{ color: "var(--warning-strong)" }}
        >
          Select at least one column to apply.
        </p>
      )}

      {/* Column table */}
      <ColumnTable
        columns={filtered}
        className="max-h-[400px] overflow-y-auto"
        checkbox={{
          isChecked: (name) => isSelected(name),
          onToggle: toggleColumn,
        }}
        interactiveRows
        nameColor={(name) => !allColumnNames.has(name) ? "var(--warning-strong)" : isSelected(name) ? "var(--text-primary)" : "var(--text-muted)"}
        nameSuffix={(name) => !allColumnNames.has(name) ? "(not found)" : null}
      />

    </div>
  )
}
