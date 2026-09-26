import { buildCsv } from "../editors/shared/tableClipboard"
import type { OptimiserFactorTableRow } from "../../api/types"

/** One level of a solved factor table, typed by the generated contract. */
export type FactorTableRow = OptimiserFactorTableRow
export type FactorTables = Record<string, FactorTableRow[]>
export type FactorLevelOrder = Record<string, readonly string[]>

/** The columns of a factor-table row, in the order the CSV writes them. */
const FACTOR_TABLE_COLUMNS = ["__factor_group__", "optimal_scenario_value", "quote_count"] as const

/** Whether any factor table has a level. */
export function hasFactorTables(factorTables: FactorTables): boolean {
  return Object.values(factorTables).some((rows) => rows.length > 0)
}

/** Stable sort comparator: items with a defined `orderIndex` come first in
 *  that order; remaining items keep their original relative position. */
function byOrderIndex<T extends { orderIndex: number | undefined; originalIndex: number }>(
  a: T,
  b: T,
): number {
  if (a.orderIndex != null && b.orderIndex != null) {
    return a.orderIndex - b.orderIndex || a.originalIndex - b.originalIndex
  }
  if (a.orderIndex != null) return -1
  if (b.orderIndex != null) return 1
  return a.originalIndex - b.originalIndex
}

export function orderFactorTableRows(
  rows: FactorTableRow[],
  levelOrder: readonly string[] | undefined,
): FactorTableRow[] {
  if (!levelOrder || levelOrder.length === 0) return rows

  const levelIndex = new Map(levelOrder.map((level, index) => [level, index]))
  return rows
    .map((row, originalIndex) => ({
      row,
      originalIndex,
      orderIndex: levelIndex.get(row.__factor_group__),
    }))
    .sort(byOrderIndex)
    .map(({ row }) => row)
}

export function orderedFactorTableEntries(
  factorTables: FactorTables,
  factorLevelOrder: FactorLevelOrder = {},
): [string, FactorTableRow[]][] {
  const factorIndex = new Map(Object.keys(factorLevelOrder).map((factor, index) => [factor, index]))

  return Object.entries(factorTables)
    .filter(([, rows]) => rows.length > 0)
    .map(([factorName, rows], originalIndex) => ({
      factorName,
      originalIndex,
      rows: orderFactorTableRows(rows, factorLevelOrder[factorName]),
      orderIndex: factorIndex.get(factorName),
    }))
    .sort(byOrderIndex)
    .map(({ factorName, rows }) => [factorName, rows] as [string, FactorTableRow[]])
}

/** The scenario range a ratebook solve scored; the deployed combined factor is clipped to it. */
export type CombinedFactorCollar = { min: number; max: number }

export const COLLAR_MIN_COLUMN = "combined_factor_min"
export const COLLAR_MAX_COLUMN = "combined_factor_max"

/**
 * One CSV of every factor table: the table name, then each row's level, rate
 * and quote count, then the combined-factor collar on every row. The collar applies to the
 * product of a quote's rates, not to any one table, and repeats per row so the
 * file stays one rectangular table a rating engine can load as it is.
 */
export function factorTablesCsv(factorTables: FactorTables, collar: CombinedFactorCollar): string {
  const lines: string[][] = [["factor", ...FACTOR_TABLE_COLUMNS, COLLAR_MIN_COLUMN, COLLAR_MAX_COLUMN]]
  for (const [factor, rows] of Object.entries(factorTables)) {
    for (const row of rows) {
      lines.push([
        factor,
        ...FACTOR_TABLE_COLUMNS.map((column) => String(row[column])),
        String(collar.min),
        String(collar.max),
      ])
    }
  }
  return buildCsv(lines)
}
