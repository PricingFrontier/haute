import { useState, useMemo } from "react"
import { Search } from "lucide-react"
import { configField } from "../../utils/configField"
import type { OnUpdateConfig } from "./_shared"
import type { ColumnInfo } from "../../types/node"
import ColumnTable from "../../components/ColumnTable"
import { EditorLabel } from "../../components/form"

interface ColumnsTabProps {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  /** Full column set before selected_columns filtering */
  availableColumns: ColumnInfo[]
  /** Current output columns (post-filter) */
  columns: ColumnInfo[]
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

  // When selected_columns is empty, all columns are kept
  const isAllSelected = selectedColumns.length === 0

  const isSelected = (col: string) => isAllSelected || selectedColumns.includes(col)

  const toggleColumn = (col: string) => {
    if (isAllSelected) {
      // Switching from "all" to explicit: keep everything except the toggled column
      const next = allColumns.map((c) => c.name).filter((c) => c !== col)
      onUpdate("selected_columns", next)
    } else if (selectedColumns.includes(col)) {
      const next = selectedColumns.filter((c) => c !== col)
      // If nothing selected, revert to "all"
      onUpdate("selected_columns", next.length > 0 ? next : [])
    } else {
      const next = [...selectedColumns, col]
      // If all columns are now selected, revert to "all" (empty = all)
      if (next.length >= allColumns.length) {
        onUpdate("selected_columns", [])
      } else {
        onUpdate("selected_columns", next)
      }
    }
  }

  const selectAll = () => onUpdate("selected_columns", [])

  const selectNone = () => {
    // Keep at least one column to avoid empty DataFrames
    if (allColumns.length > 0) {
      onUpdate("selected_columns", [allColumns[0].name])
    }
  }

  const selectedCount = isAllSelected ? allColumns.length : selectedColumns.length

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
        >
          None
        </button>
      </div>

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

      {!isAllSelected && (
        <p className="text-[10px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
          Deselected columns will be dropped via <code className="font-mono">.select()</code> on this node's output.
        </p>
      )}
    </div>
  )
}
