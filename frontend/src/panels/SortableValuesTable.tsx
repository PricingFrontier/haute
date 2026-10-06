/**
 * The shared sortable values table and its search box.
 *
 * Headers of sortable columns are buttons; the sorted column carries
 * `aria-sort` and a ▲/▼ indicator. A column that cannot be sorted right now
 * shows a disabled button, and one that never sorts shows plain text. Sorting
 * itself belongs to the caller (the GLM coefficients sort in the browser, the
 * optimiser's Quotes ask the server), which keeps the sort state and passes it
 * in; `nextSort` (`valuesSort.ts`) is the one cycle both use.
 */
import type { CSSProperties, ReactNode } from "react"

import type { SortState } from "./valuesSort"

export interface SortableValuesColumn<Row, K extends string> {
  key: K
  label: ReactNode
  align: "left" | "right" | "center"
  /** `sortable` is a sort button, `disabled` a disabled one, `none` plain header text. */
  sort: "sortable" | "disabled" | "none"
  cell: (row: Row) => ReactNode
  cellClassName?: string
  cellStyle?: CSSProperties
}

interface SortableValuesTableProps<Row, K extends string> {
  label: string
  columns: readonly SortableValuesColumn<Row, K>[]
  rows: readonly Row[]
  rowKey: (row: Row, index: number) => string
  sort: SortState<K> | null
  onSort: (key: K) => void
  /** Shown as a `role="status"` line instead of the table when there are no rows. */
  emptyMessage: string
  className?: string
  maxHeight?: number
}

const ALIGN_CLASS = { left: "text-left", right: "text-right", center: "text-center" } as const

export function SortableValuesTable<Row, K extends string>({
  label,
  columns,
  rows,
  rowKey,
  sort,
  onSort,
  emptyMessage,
  className = "w-full text-[13px] font-sans [&_td]:tabular-nums",
  maxHeight = 480,
}: SortableValuesTableProps<Row, K>) {
  if (rows.length === 0) {
    return (
      <p role="status" className="text-[13px]" style={{ color: "var(--text-muted)" }}>
        {emptyMessage}
      </p>
    )
  }
  return (
    <div className="overflow-auto" style={{ maxHeight }}>
      <table aria-label={label} className={className} style={{ borderCollapse: "collapse" }}>
        <thead className="sticky top-0" style={{ background: "var(--bg-elevated)", zIndex: 1 }}>
          <tr style={{ borderBottom: "1px solid var(--border)" }}>
            {columns.map((column) => {
              const sorted = sort?.key === column.key ? sort : null
              return (
                <th
                  key={column.key}
                  scope="col"
                  aria-sort={sorted ? (sorted.dir === "asc" ? "ascending" : "descending") : undefined}
                  className={`px-2 py-1.5 font-semibold ${ALIGN_CLASS[column.align]}`}
                  style={{ color: "var(--text-muted)", whiteSpace: "nowrap" }}
                >
                  {column.sort === "none" ? (
                    column.label
                  ) : (
                    <button
                      type="button"
                      disabled={column.sort === "disabled"}
                      onClick={() => onSort(column.key)}
                      className="focus-ring disabled:cursor-not-allowed"
                    >
                      {column.label}
                      {sorted ? (sorted.dir === "asc" ? " ▲" : " ▼") : ""}
                    </button>
                  )}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr
              key={rowKey(row, index)}
              style={{
                borderBottom: "1px solid var(--border)",
                background: index % 2 === 0 ? "transparent" : "rgba(255,255,255,.02)",
              }}
            >
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={`px-2 py-1 ${ALIGN_CLASS[column.align]} ${column.cellClassName ?? ""}`}
                  style={{ color: "var(--text-primary)", ...column.cellStyle }}
                >
                  {column.cell(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** The labelled search box that sits above a values table. */
export function ValuesTableSearch({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <label className="min-w-0 flex-1 text-[13px]" style={{ color: "var(--text-secondary)" }}>
      {label}
      <input
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 block w-full rounded border px-2 py-1 text-[13px]"
        style={{
          background: "var(--bg-input)",
          borderColor: "var(--border)",
          color: "var(--text-primary)",
        }}
      />
    </label>
  )
}
