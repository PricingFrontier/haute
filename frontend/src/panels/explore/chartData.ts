import type {
  ExplorePivotMemberKey,
  ExplorePivotPath,
  ExplorePivotResult,
} from "../../api/types"
import {
  exploreChartSeriesKey,
  type ChartNumberFormat,
  type ChartStyle,
  type ExploreChartConfig,
} from "./chartConfig"
import {
  isPivotFormulaPlacement,
  pivotOutputs,
} from "./pivotConfig"
import type { ExplorePivotConfig } from "./pivotConfig"

export const CHART_MAX_CATEGORIES = 500
export const CHART_MAX_SERIES = 100
export const CHART_MAX_POINTS = 20_000
export const CHART_MAX_HIERARCHY_DEPTH = 6
export const CHART_MAX_LABEL_LENGTH = 200

const REDUCE_PIVOT_REMEDIATION =
  "Reduce Pivot Rows, Columns, Values or Filters to make this chart smaller."
const DECIMAL_PATTERN =
  /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[Ee][+-]?[0-9]+)?$/

export class ChartDataError extends Error {
  readonly reasonCode: string
  readonly remediation: string
  readonly dimensions: Record<string, string | number>

  constructor(
    reasonCode: string,
    message: string,
    dimensions: Record<string, string | number> = {},
    remediation = "Update the source Pivot and review this chart's configuration.",
  ) {
    super(message)
    this.name = "ChartDataError"
    this.reasonCode = reasonCode
    this.remediation = remediation
    this.dimensions = dimensions
  }
}

export type ChartCategoryData = {
  key: string
  label: string
  rowIndex: number
  path: ExplorePivotPath
}

export type ChartSeriesData = {
  key: string
  id: string
  valueId: string
  name: string
  columnIndex: number
  style: ChartStyle
  values: Array<number | null>
  formattedValues: Array<string | null>
}

export type PivotChartData = {
  categories: ChartCategoryData[]
  series: ChartSeriesData[]
  dormantOverrideIds: string[]
  dormantEncodingIds: string[]
  warnings: string[]
}

function fail(
  reasonCode: string,
  message: string,
  dimensions: Record<string, string | number> = {},
  remediation?: string,
): never {
  throw new ChartDataError(reasonCode, message, dimensions, remediation)
}

function memberLabel(member: ExplorePivotMemberKey): string {
  if (member.kind === "null") return "(blank)"
  if (member.kind === "nan") return "(NaN)"
  return String(member.value)
}

function pathLabel(path: ExplorePivotPath): string {
  if (path.is_grand_total) return "Grand total"
  if (path.members.length === 0) return "All"
  return path.members.map(memberLabel).join(" › ")
}

function pathKey(path: ExplorePivotPath): string {
  return JSON.stringify({
    grand_total: path.is_grand_total,
    members: path.members.map(({ kind, value }) => ({ kind, value })),
  })
}

function checkLimit(dimension: string, actual: number, limit: number): void {
  if (actual <= limit) return
  fail(
    "chart_cardinality_limit",
    `Chart ${dimension} (${actual}) exceeds the supported limit (${limit}).`,
    { dimension, actual, limit },
    REDUCE_PIVOT_REMEDIATION,
  )
}

function checkRenderedPath(path: ExplorePivotPath): void {
  checkLimit(
    "hierarchy_depth",
    path.members.length,
    CHART_MAX_HIERARCHY_DEPTH,
  )
  checkLimit("label_length", pathLabel(path).length, CHART_MAX_LABEL_LENGTH)
}

function includePath(
  path: ExplorePivotPath,
  fullDepth: number,
  chart: ExploreChartConfig,
  axis: "row" | "column",
): boolean {
  // The grand-total opt-in admits row paths only: a column grand total is the
  // sum of the other series, so charting it would double-count stacks.
  if (path.is_grand_total) {
    return axis === "row" && chart.category.include_grand_total
  }
  // The backend emits only full-depth paths plus optional grand totals; a
  // shorter (subtotal-shaped) path is never charted. An over-depth path flows
  // on to checkRenderedPath, which rejects it at the hierarchy-depth limit.
  return path.members.length >= fullDepth
}

function numericCellValue(value: unknown): number | null {
  if (value === null) return null
  if (typeof value === "number" && Number.isFinite(value)) return value
  if (typeof value === "string" && DECIMAL_PATTERN.test(value)) {
    const converted = Number(value)
    if (Number.isFinite(converted)) return converted
  }
  return fail(
    "chart_cell_value_invalid",
    "Pivot cells must contain finite numbers, canonical decimal strings, or null gaps.",
  )
}

// Formatter construction is expensive and formatChartValue runs per rendered
// point (up to CHART_MAX_POINTS per chart), so instances are cached per key.
const NUMBER_FORMATTERS = new Map<string, Intl.NumberFormat>()

function numberFormatter(
  key: string,
  options: Intl.NumberFormatOptions,
): Intl.NumberFormat {
  let formatter = NUMBER_FORMATTERS.get(key)
  if (!formatter) {
    formatter = new Intl.NumberFormat("en-GB", options)
    NUMBER_FORMATTERS.set(key, formatter)
  }
  return formatter
}

/**
 * Chart-name → export filename: lower-cased, runs of characters outside
 * a–z/0–9/-/_ collapsed to one "-", trimmed of leading/trailing "-", with
 * "chart" as the all-punctuation fallback, plus ".png".
 */
export function chartExportFileName(name: string): string {
  const sanitised = name
    .toLowerCase()
    .replace(/[^a-z0-9\-_]+/g, "-")
    .replace(/^-+|-+$/g, "")
  return `${sanitised || "chart"}.png`
}

export function formatChartValue(
  value: number,
  format: ChartNumberFormat,
): string {
  if (format === "inherit") {
    // General locale format: grouped digits, at most two fraction digits at
    // magnitude >= 1, at most four significant digits below 1, "0" at zero.
    if (value === 0) return "0"
    return Math.abs(value) >= 1
      ? numberFormatter("inherit_ge1", { maximumFractionDigits: 2 }).format(
          value,
        )
      : numberFormatter("inherit_lt1", {
          maximumSignificantDigits: 4,
        }).format(value)
  }

  const options: Intl.NumberFormatOptions =
    format === "number"
      ? { maximumFractionDigits: 20 }
      : format === "integer"
        ? { maximumFractionDigits: 0 }
        : format === "percent"
          ? { style: "percent", maximumFractionDigits: 2 }
          : {
              style: "currency",
              currency:
                format === "currency_gbp"
                  ? "GBP"
                  : format === "currency_usd"
                    ? "USD"
                    : "EUR",
            }
  return numberFormatter(format, options).format(value)
}

function sameOrderedFields(
  actual: readonly string[],
  expected: readonly string[],
): boolean {
  return (
    actual.length === expected.length &&
    actual.every((field, index) => field === expected[index])
  )
}

function sameOrderedValues(
  pivot: ExplorePivotConfig,
  result: ExplorePivotResult,
): boolean {
  const outputs = pivotOutputs(pivot)
  return (
    outputs.length === result.values.length &&
    outputs.every((value, index) => {
      const resultValue = result.values[index]
      if (isPivotFormulaPlacement(value)) {
        return (
          resultValue?.id === value.id &&
          resultValue.field === value.reference &&
          resultValue.aggregation === "formula"
        )
      }
      return (
        resultValue?.id === value.id &&
        resultValue.field === value.field &&
        resultValue.aggregation === value.aggregation
      )
    })
  )
}

function cellKey(rowIndex: number, columnIndex: number, valueId: string): string {
  return `${rowIndex}\u0000${columnIndex}\u0000${valueId}`
}

function rejectDuplicatePaths(paths: readonly ExplorePivotPath[], axis: string) {
  const keys = new Set<string>()
  for (const path of paths) {
    const key = pathKey(path)
    if (keys.has(key)) {
      fail(
        "chart_path_duplicate",
        `Pivot result contains a duplicate ${axis} path.`,
      )
    }
    keys.add(key)
  }
}

/** Converts a guarded pivot result to a renderer-independent dense chart dataset. */
export function adaptPivotChartData(
  chart: ExploreChartConfig,
  pivot: ExplorePivotConfig,
  result: ExplorePivotResult,
): PivotChartData {
  if (
    chart.pivot_id === null ||
    chart.pivot_id !== pivot.id ||
    result.pivot_id !== pivot.id
  ) {
    fail(
      "chart_pivot_mismatch",
      "The chart, Pivot configuration, and Pivot result must refer to the same Pivot.",
    )
  }
  if (pivotOutputs(pivot).length === 0) {
    fail(
      "chart_values_required",
      "Add at least one Value or calculated field to the source Pivot before rendering this chart.",
    )
  }

  const expectedRows = pivot.rows.map(({ field }) => field)
  const expectedColumns = pivot.columns.map(({ field }) => field)
  if (
    !sameOrderedFields(result.row_fields, expectedRows) ||
    !sameOrderedFields(result.column_fields, expectedColumns)
  ) {
    fail(
      "chart_pivot_shape_mismatch",
      "The Pivot result Rows or Columns no longer match the current Pivot configuration.",
    )
  }
  if (!sameOrderedValues(pivot, result)) {
    fail(
      "chart_value_identity_mismatch",
      "The Pivot result Values no longer match the current Pivot configuration.",
    )
  }
  if (result.row_paths.length === 0 || result.column_paths.length === 0) {
    fail(
      "chart_result_axis_empty",
      "The Pivot result does not contain the row and column coordinates required by a chart.",
    )
  }

  rejectDuplicatePaths(result.row_paths, "row")
  rejectDuplicatePaths(result.column_paths, "column")

  const rowEntries = result.row_paths
    .map((path, rowIndex) => ({ path, rowIndex }))
    .filter(({ path }) =>
      includePath(path, result.row_fields.length, chart, "row"),
    )
  const columnEntries = result.column_paths
    .map((path, columnIndex) => ({ path, columnIndex }))
    .filter(({ path }) =>
      includePath(path, result.column_fields.length, chart, "column"),
    )

  for (const { path } of [...rowEntries, ...columnEntries]) {
    checkRenderedPath(path)
  }
  checkLimit("categories", rowEntries.length, CHART_MAX_CATEGORIES)
  checkLimit(
    "series",
    columnEntries.length * result.values.length,
    CHART_MAX_SERIES,
  )
  checkLimit(
    "points",
    rowEntries.length * columnEntries.length * result.values.length,
    CHART_MAX_POINTS,
  )

  const pivotValueById = new Map(pivotOutputs(pivot).map((value) => [value.id, value]))
  const encodingByValueId = new Map(
    chart.value_encodings.map((encoding) => [encoding.value_id, encoding]),
  )
  for (const value of result.values) {
    if (!encodingByValueId.has(value.id)) {
      fail(
        "chart_encoding_missing",
        `Chart requires an explicit encoding for Pivot Value "${value.id}".`,
      )
    }
  }

  const resultValueIds = new Set(result.values.map(({ id }) => id))
  const indexedCells = new Map<
    string,
    ExplorePivotResult["cells"][number]
  >()
  for (const cell of result.cells) {
    if (
      !Number.isInteger(cell.row_index) ||
      cell.row_index < 0 ||
      cell.row_index >= result.row_paths.length ||
      !Number.isInteger(cell.column_index) ||
      cell.column_index < 0 ||
      cell.column_index >= result.column_paths.length ||
      !resultValueIds.has(cell.value_id)
    ) {
      fail(
        "chart_cell_coordinate_invalid",
        "Pivot result contains an invalid cell coordinate.",
      )
    }
    const key = cellKey(cell.row_index, cell.column_index, cell.value_id)
    if (indexedCells.has(key)) {
      fail(
        "chart_cell_duplicate",
        "Pivot result contains duplicate cells for one chart coordinate.",
      )
    }
    indexedCells.set(key, cell)
  }

  const categories: ChartCategoryData[] = rowEntries.map(
    ({ path, rowIndex }) => ({
      key: pathKey(path),
      label: pathLabel(path),
      rowIndex,
      path,
    }),
  )
  const usedOverrideIds = new Set<string>()
  const usedEncodingIds = new Set<string>()
  const series: ChartSeriesData[] = []

  for (const { path: columnPath, columnIndex } of columnEntries) {
    for (const value of result.values) {
      const key = exploreChartSeriesKey(value.id, columnPath)
      const override = chart.series_overrides.find(
        (candidate) => candidate.series_key === key,
      )
      const encoding = encodingByValueId.get(value.id)!
      const style = override ?? encoding
      usedEncodingIds.add(encoding.id)
      if (override) usedOverrideIds.add(override.id)

      const displayName = pivotValueById.get(value.id)!.display_name
      const name =
        columnPath.members.length === 0 && !columnPath.is_grand_total
          ? displayName
          : `${pathLabel(columnPath)} · ${displayName}`
      checkLimit("label_length", name.length, CHART_MAX_LABEL_LENGTH)

      const values = categories.map(({ rowIndex }) => {
        const cell = indexedCells.get(cellKey(rowIndex, columnIndex, value.id))
        if (!cell) {
          fail(
            "chart_cell_missing",
            "Pivot result is missing a cell required by this chart.",
          )
        }
        return numericCellValue(cell.value)
      })
      series.push({
        key,
        id: key,
        valueId: value.id,
        name,
        columnIndex,
        style,
        values,
        formattedValues: [],
      })
    }
  }

  // 100% stacking: per category and normalised group, cell -> cell / sum of
  // |cells| over the group's non-null cells. Runs before formatting so
  // formatted text reflects the normalised values.
  const warnings = [...result.warnings]
  const normalizedGroups = new Map<string, ChartSeriesData[]>()
  for (const entry of series) {
    if (entry.style.stack_group !== null && entry.style.stack_normalize) {
      const members = normalizedGroups.get(entry.style.stack_group) ?? []
      members.push(entry)
      normalizedGroups.set(entry.style.stack_group, members)
    }
  }
  for (const [group, members] of normalizedGroups) {
    categories.forEach((category, categoryIndex) => {
      let denominator = 0
      let maximumMagnitude = 0
      let overflowScale: number | null = null
      for (const member of members) {
        const cell = member.values[categoryIndex]
        if (cell !== null) {
          const magnitude = Math.abs(cell)
          denominator += magnitude
          maximumMagnitude = Math.max(maximumMagnitude, magnitude)
        }
      }
      if (!Number.isFinite(denominator)) {
        overflowScale = maximumMagnitude
        denominator = members.reduce((sum, member) => {
          const cell = member.values[categoryIndex]
          return cell === null ? sum : sum + Math.abs(cell) / maximumMagnitude
        }, 0)
      }
      if (denominator === 0) {
        for (const member of members) member.values[categoryIndex] = null
        warnings.push(
          `100% stacking has no non-zero values for category ` +
            `"${category.label}" in stack group "${group}".`,
        )
        return
      }
      for (const member of members) {
        const cell = member.values[categoryIndex]
        if (cell !== null) {
          const numerator = overflowScale === null ? cell : cell / overflowScale
          member.values[categoryIndex] = numerator / denominator
        }
      }
    })
  }
  for (const entry of series) {
    const format = chart.axes[entry.style.axis].number_format
    entry.formattedValues = entry.values.map((cell) =>
      cell === null ? null : formatChartValue(cell, format),
    )
  }

  return {
    categories,
    series,
    dormantOverrideIds: chart.series_overrides
      .filter(({ id }) => !usedOverrideIds.has(id))
      .map(({ id }) => id),
    dormantEncodingIds: chart.value_encodings
      .filter(({ id }) => !usedEncodingIds.has(id))
      .map(({ id }) => id),
    warnings,
  }
}
