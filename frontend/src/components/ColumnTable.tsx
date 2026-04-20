import { getDtypeColor } from "../utils/dtypeColors"

export interface ColumnTableColumn {
  name: string
  dtype: string
}

interface ColumnTableProps {
  /** Columns to render in the table. */
  columns: ColumnTableColumn[]
  /** Optional className for the outer wrapper (e.g. max-height). */
  className?: string
  /**
   * When provided, renders a checkbox column. The callback receives
   * `(columnName)` on each toggle; `isChecked` controls the checkbox state.
   */
  checkbox?: {
    isChecked: (columnName: string) => boolean
    onToggle: (columnName: string) => void
    /** Accent class for the checkbox (e.g. "accent-rose-500", "accent-blue-500"). */
    accentClass?: string
  }
  /** When true, rows highlight on hover and are clickable (clicking toggles the checkbox). */
  interactiveRows?: boolean
  /** Override text color for the column name cell. Receives the column name and returns a CSS color. */
  nameColor?: (columnName: string) => string
  /** Optional suffix to append to column names. Receives the column name and returns a string or null. */
  nameSuffix?: (columnName: string) => string | null
}

/**
 * Shared table for displaying column name + dtype pairs.
 *
 * Used by OutputEditor, ColumnsTab, and SchemaPreview.
 */
export default function ColumnTable({
  columns,
  className = "",
  checkbox,
  interactiveRows = false,
  nameColor,
  nameSuffix,
}: ColumnTableProps) {
  return (
    <div
      className={`rounded-lg overflow-hidden ${className}`.trim()}
      style={{ border: "1px solid var(--border)", background: "var(--bg-input)" }}
    >
      <table className="w-full text-xs">
        <thead>
          <tr style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-elevated)" }}>
            {checkbox && (
              <th
                className="text-left px-2.5 py-1.5 font-semibold"
                style={{ color: "var(--text-muted)", width: 28 }}
              />
            )}
            <th className="text-left px-2.5 py-1.5 font-semibold" style={{ color: "var(--text-muted)" }}>
              Column
            </th>
            <th className="text-left px-2.5 py-1.5 font-semibold" style={{ color: "var(--text-muted)" }}>
              Type
            </th>
          </tr>
        </thead>
        <tbody>
          {columns.map((col) => {
            const checked = checkbox?.isChecked(col.name) ?? false
            const color = nameColor?.(col.name) ?? "var(--text-primary)"

            const row = (
              <tr
                key={col.name}
                className={interactiveRows ? "cursor-pointer hover-bg" : undefined}
                style={{ borderBottom: "1px solid var(--border)" }}
                onClick={interactiveRows && checkbox ? () => checkbox.onToggle(col.name) : undefined}
              >
                {checkbox && (
                  <td className="px-2.5 py-1.5 text-center">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => checkbox.onToggle(col.name)}
                      onClick={interactiveRows ? (e) => e.stopPropagation() : undefined}
                      className={`${checkbox.accentClass ?? "accent-blue-500"} rounded`}
                    />
                  </td>
                )}
                <td className="px-2.5 py-1.5 font-mono" style={{ color }}>
                  {col.name}
                  {nameSuffix?.(col.name) && (
                    <span className="ml-1 text-[10px] font-sans italic" style={{ opacity: 0.7 }}>
                      {nameSuffix(col.name)}
                    </span>
                  )}
                </td>
                <td className="px-2.5 py-1.5">
                  <span className={`text-[11px] font-medium ${getDtypeColor(col.dtype)}`}>
                    {col.dtype}
                  </span>
                </td>
              </tr>
            )

            return row
          })}
        </tbody>
      </table>
    </div>
  )
}
