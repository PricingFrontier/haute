import type { ColumnInfo } from "../types/node"

/**
 * Pure model for the unified column selector (see `frontend/DESIGN_PRINCIPLES.md`
 * §1). Converts between a frame's persisted selection config — `selected_columns`
 * (an ordered keep-list; `[]` means "keep all, natural order") and `column_renames`
 * (a `{ incomingName: outputName }` map) — and the per-column display rows the UI
 * edits. Reordering the kept rows reorders `selected_columns`, which the backend
 * honours via Polars `.select([...])` (list order); renames map to `.alias(...)`.
 *
 * Kept side-effect-free and framework-free so the row/serialise/reorder rules are
 * exhaustively unit-testable without rendering.
 */

export interface ColumnSelectorRow {
  /** Upstream column name — the identity a rename maps FROM. */
  incomingName: string
  /**
   * 1-based position the column arrived in (its index in `availableColumns` + 1).
   * `0` for a stale row: present in the saved selection but absent upstream.
   */
  incomingOrder: number
  /** Output name: the rename, defaulting to `incomingName`. */
  outputName: string
  /** Column dtype from `availableColumns`; `"unknown"` for stale rows. */
  dtype: string
  /** Whether the column is kept (ticked). */
  selected: boolean
  /** True when the column is in the saved selection but no longer produced upstream. */
  stale: boolean
}

export interface ColumnSelection {
  selectedColumns: string[]
  columnRenames: Record<string, string>
}

/**
 * Build the display rows from the persisted config + the current upstream columns.
 *
 * Order: an empty `selectedColumns` means "keep all in natural (incoming) order";
 * otherwise kept columns lead in their saved order (the output order), followed by
 * the remaining upstream columns unticked in incoming order. Saved names absent
 * upstream become stale ghost rows so they are visible/repairable rather than
 * silently dropped.
 */
export function deriveColumnRows(
  availableColumns: readonly ColumnInfo[],
  selectedColumns: readonly string[],
  columnRenames: Readonly<Record<string, string>>,
): ColumnSelectorRow[] {
  const orderIndex = new Map(availableColumns.map((c, i) => [c.name, i]))
  const dtypeOf = new Map(availableColumns.map((c) => [c.name, c.dtype]))
  const rename = (name: string) => columnRenames[name] ?? name

  if (selectedColumns.length === 0) {
    return availableColumns.map((c, i) => ({
      incomingName: c.name,
      incomingOrder: i + 1,
      outputName: rename(c.name),
      dtype: c.dtype,
      selected: true,
      stale: false,
    }))
  }

  const seen = new Set<string>()
  const rows: ColumnSelectorRow[] = []
  // Kept columns first, in saved order — this IS the output order.
  for (const name of selectedColumns) {
    if (seen.has(name)) continue
    seen.add(name)
    const present = orderIndex.has(name)
    rows.push({
      incomingName: name,
      incomingOrder: present ? (orderIndex.get(name) as number) + 1 : 0,
      outputName: rename(name),
      dtype: present ? dtypeOf.get(name) ?? "unknown" : "unknown",
      selected: true,
      stale: !present,
    })
  }
  // Remaining upstream columns, unticked, in incoming order.
  availableColumns.forEach((c, i) => {
    if (seen.has(c.name)) return
    rows.push({
      incomingName: c.name,
      incomingOrder: i + 1,
      outputName: rename(c.name),
      dtype: c.dtype,
      selected: false,
      stale: false,
    })
  })
  return rows
}

/**
 * Serialise display rows back to the persisted config.
 *
 * `selectedColumns` is the ticked rows in display order — EXCEPT the historic
 * "keep all in natural order" case, which serialises to `[]` (so an untouched
 * frame stays a no-op). Reordering or deselecting any column, or keeping all in a
 * non-incoming order, produces an explicit ordered list. `columnRenames` carries
 * only ticked rows whose `outputName` differs from their `incomingName`.
 */
export function serializeSelection(
  rows: readonly ColumnSelectorRow[],
  availableColumns: readonly ColumnInfo[],
): ColumnSelection {
  const selected = rows.filter((r) => r.selected)
  const selectedNames = selected.map((r) => r.incomingName)

  const columnRenames: Record<string, string> = {}
  for (const r of selected) {
    const out = r.outputName.trim()
    if (out && out !== r.incomingName) columnRenames[r.incomingName] = out
  }

  const availNames = availableColumns.map((c) => c.name)
  const keepsAllNatural =
    selectedNames.length === availNames.length &&
    selectedNames.every((name, i) => name === availNames[i])

  return {
    selectedColumns: keepsAllNatural ? [] : selectedNames,
    columnRenames,
  }
}

/** Move the row at `from` to `to` (immutable array move). Out-of-range is a no-op copy. */
export function reorderRows(
  rows: readonly ColumnSelectorRow[],
  from: number,
  to: number,
): ColumnSelectorRow[] {
  const next = rows.slice()
  if (from === to || from < 0 || to < 0 || from >= rows.length || to >= rows.length) {
    return next
  }
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved)
  return next
}
