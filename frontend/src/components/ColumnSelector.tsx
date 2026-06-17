import { useState } from "react"
import { GripVertical } from "lucide-react"
import { getDtypeColor } from "../utils/dtypeColors"
import type { ColumnInfo } from "../types/node"
import {
  deriveColumnRows,
  serializeSelection,
  reorderRows,
  type ColumnSelection,
  type ColumnSelectorRow,
} from "../utils/columnSelection"

/**
 * The unified column selector (DESIGN_PRINCIPLES.md §1). One row per column:
 * grip · select · incoming-order · rename · incoming-name · type. The row list
 * is drag-reorderable and that order IS the output order (Polars `.select` honours
 * it; renames map to `.alias`).
 *
 * Deliberately DERIVED-FROM-PROPS: every interaction serialises the rows back to
 * `{ selected_columns, column_renames }` via `onChange` and the parent re-derives —
 * there is no local data state to drift from config (only transient drag state).
 * The rename cell is uncontrolled and keyed by the column's incoming name, so it
 * keeps its draft across reorders/toggles and never fights the cursor.
 */

interface ColumnSelectorProps {
  /** Upstream columns in incoming order (the per-frame schema; see §1.5). */
  availableColumns: ColumnInfo[]
  /** Persisted ordered keep-list; `[]` = keep all in natural order. */
  selectedColumns: string[]
  /** Persisted `{ incomingName: outputName }` renames. */
  columnRenames: Record<string, string>
  onChange: (next: ColumnSelection) => void
  /** Prefix for per-row test ids (`${prefix}-row`, `-select`, `-rename`). */
  testIdPrefix?: string
  /** Empty-state hint when there are no upstream columns. */
  emptyHint?: string
}

/** Uncontrolled, commit-on-blur/Enter rename input. Keyed by column identity upstream. */
function RenameCell({
  initial,
  placeholder,
  onCommit,
  testId,
}: {
  initial: string
  placeholder: string
  onCommit: (value: string) => void
  testId?: string
}) {
  return (
    <input
      type="text"
      data-testid={testId}
      defaultValue={initial}
      placeholder={placeholder}
      spellCheck={false}
      className="w-full px-1.5 py-0.5 text-[11px] font-mono rounded border bg-transparent focus:outline-none focus:ring-1"
      style={{ color: "var(--text-primary)", borderColor: "var(--border)", background: "var(--bg-input)" }}
      onClick={(e) => e.stopPropagation()}
      onBlur={(e) => onCommit(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur()
        else if (e.key === "Escape") {
          e.currentTarget.value = initial
          e.currentTarget.blur()
        }
      }}
    />
  )
}

export default function ColumnSelector({
  availableColumns,
  selectedColumns,
  columnRenames,
  onChange,
  testIdPrefix = "column",
  emptyHint = "Preview or run this node to see its columns",
}: ColumnSelectorProps) {
  const [dragIndex, setDragIndex] = useState<number | null>(null)
  const [overIndex, setOverIndex] = useState<number | null>(null)

  const rows = deriveColumnRows(availableColumns, selectedColumns, columnRenames)
  const emit = (nextRows: ColumnSelectorRow[]) =>
    onChange(serializeSelection(nextRows, availableColumns))

  const setRow = (index: number, patch: Partial<ColumnSelectorRow>) =>
    rows.map((row, i) => (i === index ? { ...row, ...patch } : row))

  const toggle = (index: number) => emit(setRow(index, { selected: !rows[index].selected }))
  const rename = (index: number, outputName: string) => {
    const next = outputName.trim() || rows[index].incomingName
    if (next === rows[index].outputName) return
    emit(setRow(index, { outputName: next }))
  }
  const drop = (index: number) => {
    if (dragIndex !== null && dragIndex !== index) emit(reorderRows(rows, dragIndex, index))
    setDragIndex(null)
    setOverIndex(null)
  }

  const keptCount = rows.filter((r) => r.selected).length
  const allKept = keptCount === rows.length && rows.length > 0

  if (availableColumns.length === 0 && selectedColumns.length === 0) {
    return (
      <p className="px-4 py-6 text-center text-xs" style={{ color: "var(--text-muted)" }}>
        {emptyHint}
      </p>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>
          {keptCount} / {rows.length} kept
        </span>
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            data-testid={`${testIdPrefix}-select-all`}
            onClick={() => emit(rows.map((r) => ({ ...r, selected: true })))}
            disabled={allKept}
            className="text-[10px] font-medium px-1.5 py-0.5 rounded"
            style={{ color: allKept ? "var(--text-muted)" : "var(--accent)" }}
          >
            All
          </button>
          <button
            type="button"
            data-testid={`${testIdPrefix}-select-none`}
            onClick={() =>
              // Keep at least the first row so a frame never empties.
              emit(rows.map((r, i) => ({ ...r, selected: i === 0 })))
            }
            className="text-[10px] font-medium px-1.5 py-0.5 rounded"
            style={{ color: "var(--accent)" }}
          >
            None
          </button>
        </div>
      </div>

      <div
        className="rounded-lg overflow-hidden"
        style={{ border: "1px solid var(--border)", background: "var(--bg-input)" }}
      >
        <table className="w-full text-xs">
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-elevated)" }}>
              <th style={{ width: 22 }} />
              <th style={{ width: 28 }} />
              <th className="text-left px-1 py-1.5 font-semibold" style={{ color: "var(--text-muted)", width: 28 }} title="Incoming order">
                #
              </th>
              <th className="text-left px-2 py-1.5 font-semibold" style={{ color: "var(--text-muted)" }}>
                Output name
              </th>
              <th className="text-left px-2 py-1.5 font-semibold" style={{ color: "var(--text-muted)" }}>
                From
              </th>
              <th className="text-left px-2 py-1.5 font-semibold" style={{ color: "var(--text-muted)" }}>
                Type
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr
                key={row.incomingName}
                data-testid={`${testIdPrefix}-row`}
                data-incoming-name={row.incomingName}
                data-selected={row.selected ? "true" : "false"}
                onDragOver={(e) => {
                  e.preventDefault()
                  setOverIndex(i)
                }}
                onDrop={() => drop(i)}
                style={{
                  borderBottom: "1px solid var(--border)",
                  background: overIndex === i && dragIndex !== null ? "var(--bg-hover)" : undefined,
                  opacity: row.selected ? 1 : 0.55,
                }}
              >
                <td className="px-1 py-1 text-center">
                  <span
                    draggable
                    data-testid={`${testIdPrefix}-grip`}
                    onDragStart={() => setDragIndex(i)}
                    onDragEnd={() => {
                      setDragIndex(null)
                      setOverIndex(null)
                    }}
                    className="inline-flex cursor-grab active:cursor-grabbing"
                    style={{ color: "var(--text-muted)" }}
                    aria-label={`Reorder ${row.incomingName}`}
                  >
                    <GripVertical size={12} />
                  </span>
                </td>
                <td className="px-1 py-1 text-center">
                  <input
                    type="checkbox"
                    data-testid={`${testIdPrefix}-select`}
                    checked={row.selected}
                    onChange={() => toggle(i)}
                    className="accent-blue-500 rounded"
                    aria-label={`Keep ${row.incomingName}`}
                  />
                </td>
                <td className="px-1 py-1 font-mono text-[10px]" style={{ color: "var(--text-muted)" }}>
                  {row.incomingOrder > 0 ? row.incomingOrder : "—"}
                </td>
                <td className="px-2 py-1">
                  <RenameCell
                    initial={row.outputName}
                    placeholder={row.incomingName}
                    onCommit={(value) => rename(i, value)}
                    testId={`${testIdPrefix}-rename`}
                  />
                </td>
                <td
                  className="px-2 py-1 font-mono"
                  style={{ color: row.stale ? "var(--warning-strong)" : "var(--text-muted)" }}
                >
                  {row.incomingName}
                  {row.stale && (
                    <span className="ml-1 text-[10px] font-sans italic" style={{ opacity: 0.8 }}>
                      (not found)
                    </span>
                  )}
                </td>
                <td className="px-2 py-1">
                  <span className={`text-[11px] font-medium ${getDtypeColor(row.dtype)}`}>{row.dtype}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {!allKept && (
        <p className="text-[10px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
          Unticked columns are dropped via <code className="font-mono">.select()</code>; drag to reorder, edit a name to <code className="font-mono">.alias()</code> it.
        </p>
      )}
    </div>
  )
}
