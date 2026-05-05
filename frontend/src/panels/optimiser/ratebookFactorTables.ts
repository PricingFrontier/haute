export type FactorTableRow = Record<string, unknown>
export type FactorTables = Record<string, FactorTableRow[]>

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
