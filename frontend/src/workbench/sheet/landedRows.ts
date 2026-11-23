/**
 * An Import's rows landing on a table's rows as typing does (specs/workbench, Running an
 * Import): apart from the sheets' other values, so only the workbench's views load it.
 */
import type { SchemaTable } from "../../api/types"
import { indexKey, presetRows, rowHolds, valuesIndex } from "../../utils/sheetValues"
import { tableIndex } from "../../utils/quoteIdentity"
import { readTypedWhole } from "../../utils/typedNumbers"
import type { SampleRow, SampleValue } from "../../utils/workbenchForm"

/** How an Import's rows land on a table (`ImportWidget["mode"]`), and the rows, as its answer gives them. */
export interface ImportedRows {
  mode: "replace" | "append" | "update"
  rows: readonly SampleRow[]
}

/** `rows`, copied, without the rows at their end holding nothing. */
function withoutEmptyEnd(table: SchemaTable, rows: readonly SampleRow[]): SampleRow[] {
  let end = rows.length
  while (end > 0 && !rowHolds(table, rows[end - 1])) end -= 1
  return rows.slice(0, end).map((row) => ({ ...row }))
}

/**
 * The rows `table` holds once an Import's rows land on `held`, as typing does (specs/workbench,
 * Running an Import), each row of `held` kept in its place, its kept field too: a one-row
 * table's row taking the first row's cells over its own; a many-row table's rows replaced,
 * added after its last row holding anything (`rowHolds`), or updated by its index, each row's
 * index value naming the row whose cells it takes over theirs (a numbered index its number on
 * the sheet) and a row none names added after them, but in a table an index of values gives
 * its rows, which are fixed, where it is left out. A table with no index updates no row: each
 * is added. An empty cell in a row clears the cell it lands on.
 */
export function landedRows(table: SchemaTable, held: readonly SampleRow[], imported: ImportedRows): SampleRow[] {
  const { mode, rows } = imported
  if (table.rows === "one") return rows.length === 0 ? held.slice(0, 1) : [{ ...(held[0] ?? {}), ...rows[0] }]
  if (mode === "replace") return rows.map((row) => ({ ...row }))
  const index = tableIndex(table)
  if (mode === "append" || index === null) return [...withoutEmptyEnd(table, held), ...rows.map((row) => ({ ...row }))]
  const fixed = valuesIndex(table) !== null
  const landed: SampleRow[] = (fixed ? presetRows(table, held) : held).map((row) => ({ ...row }))
  const added: SampleRow[] = []
  const placeOf = (value: SampleValue | undefined): number => {
    if (index.index === "numbered") {
      const number = typeof value === "string" ? readTypedWhole(value) : null
      return number !== null && number >= 1n && number <= BigInt(landed.length) ? Number(number) - 1 : -1
    }
    const key = indexKey(index, value)
    return key === null ? -1 : landed.findIndex((row) => indexKey(index, row[index.name]) === key)
  }
  for (const row of rows) {
    const place = placeOf(row[index.name])
    // A numbered index's values are the rows' places, which the sheets give: never typed.
    const cells: SampleRow = { ...row }
    if (index.index === "numbered") delete cells[index.name]
    if (place >= 0) landed[place] = { ...landed[place], ...cells }
    else if (!fixed) added.push(cells)
  }
  return fixed ? landed : [...withoutEmptyEnd(table, landed), ...added]
}

/** Whether rows landing as `imported` say replace any of `held` holding anything (`rowHolds`): a many-row table's Replace over such a row asks first. */
export const replacesTyped = (table: SchemaTable, held: readonly SampleRow[], imported: ImportedRows): boolean =>
  table.rows === "many" && imported.mode === "replace" && held.some((row) => rowHolds(table, row))
