import { buildCsv } from "../editors/shared/tableClipboard"

export type FactorTableRow = Record<string, unknown>
export type FactorTables = Record<string, FactorTableRow[]>
export type FactorLevelOrder = Record<string, readonly string[]>

export const RATE_COLUMN = "optimal_scenario_value"
export const GROUP_COLUMN = "__factor_group__"
export const QUOTE_COUNT_COLUMN = "quote_count"

export function formatFactorLevel(row: FactorTableRow, index: number): string {
  const explicitGroup = row[GROUP_COLUMN]
  if (explicitGroup != null) return String(explicitGroup)

  const fallbackKey = Object.keys(row).find((key) => key !== RATE_COLUMN)
  const fallbackValue = fallbackKey ? row[fallbackKey] : null
  return fallbackValue == null ? `Level ${index + 1}` : String(fallbackValue)
}

export function numericRate(row: FactorTableRow): number | null {
  const value = row[RATE_COLUMN]
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

export function numericQuoteCount(row: FactorTableRow): number | null {
  const value = row[QUOTE_COUNT_COLUMN]
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null
}

export function hasFactorTables(
  factorTables: FactorTables | null | undefined,
): factorTables is FactorTables {
  return (
    factorTables != null
    && Object.values(factorTables).some((rows) => Array.isArray(rows) && rows.length > 0)
  )
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
      orderIndex: levelIndex.get(formatFactorLevel(row, originalIndex)),
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
    .filter(([, rows]) => Array.isArray(rows) && rows.length > 0)
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
 * One CSV of every factor table: the table name, then each row's own columns,
 * then the combined-factor collar on every row. The collar applies to the
 * product of a quote's rates, not to any one table, and repeats per row so the
 * file stays one rectangular table a rating engine can load as it is.
 */
export function factorTablesCsv(factorTables: FactorTables, collar: CombinedFactorCollar): string {
  const columns: string[] = []
  for (const rows of Object.values(factorTables)) {
    for (const row of rows) {
      for (const key of Object.keys(row)) if (!columns.includes(key)) columns.push(key)
    }
  }
  const lines: string[][] = [["factor", ...columns, COLLAR_MIN_COLUMN, COLLAR_MAX_COLUMN]]
  for (const [factor, rows] of Object.entries(factorTables)) {
    for (const row of rows) {
      lines.push([
        factor,
        ...columns.map((column) => (row[column] == null ? "" : String(row[column]))),
        String(collar.min),
        String(collar.max),
      ])
    }
  }
  return buildCsv(lines)
}
