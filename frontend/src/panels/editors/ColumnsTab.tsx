import { configField } from "../../utils/configField"
import type { OnUpdateConfig } from "./_shared"
import type { ColumnInfo } from "../../types/node"
import type { ColumnSelection } from "../../utils/columnSelection"
import ColumnSelector from "../../components/ColumnSelector"
import { EditorLabel } from "../../components/form"

interface ColumnsTabProps {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  /** Full column set before selected_columns filtering. */
  availableColumns: ColumnInfo[]
  /** Current output columns (post-filter); fallback when availableColumns is empty. */
  columns: ColumnInfo[]
}

/**
 * Output-column selection for a node's "Columns" tab. A thin adapter over the
 * shared ColumnSelector (DESIGN_PRINCIPLES.md §1): reads/writes selected_columns
 * (ordered keep-list) + column_renames, so the node's output is projected via
 * `.select([...])` in the chosen order and renamed via `.alias`.
 */
export default function ColumnsTab({ config, onUpdate, availableColumns, columns }: ColumnsTabProps) {
  const selectedColumns = configField<string[]>(config, "selected_columns", [])
  const columnRenames = configField<Record<string, string>>(config, "column_renames", {})
  const allColumns = availableColumns.length > 0 ? availableColumns : columns

  const handleChange = (next: ColumnSelection) => {
    onUpdate({ selected_columns: next.selectedColumns, column_renames: next.columnRenames })
  }

  return (
    <div className="px-4 py-3 flex flex-col gap-2">
      <EditorLabel as="span">Output Columns</EditorLabel>
      <ColumnSelector
        availableColumns={allColumns}
        selectedColumns={selectedColumns}
        columnRenames={columnRenames}
        onChange={handleChange}
        searchable
        testIdPrefix="columns"
        emptyHint="Preview or run this node to see its output columns"
      />
    </div>
  )
}
