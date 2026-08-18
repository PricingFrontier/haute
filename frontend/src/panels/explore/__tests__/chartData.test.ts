import { describe, expect, it } from "vitest"

import type {
  ExplorePivotMemberKey,
  ExplorePivotPath,
  ExplorePivotResult,
} from "../../../api/types"
import {
  adaptPivotChartData,
  CHART_MAX_CATEGORIES,
  CHART_MAX_HIERARCHY_DEPTH,
  CHART_MAX_LABEL_LENGTH,
  CHART_MAX_POINTS,
  CHART_MAX_SERIES,
  ChartDataError,
  formatChartValue,
} from "../chartData"
import {
  createExploreChart,
  exploreChartSeriesKey,
  seedValueEncodings,
  type ExploreChartConfig,
} from "../chartConfig"
import { isPivotFormulaPlacement, pivotOutputs } from "../pivotConfig"
import type { ExplorePivotConfig } from "../pivotConfig"

function member(value: string): ExplorePivotMemberKey {
  return { kind: "string", value }
}

function path(
  members: ExplorePivotMemberKey[],
  isGrandTotal = false,
): ExplorePivotPath {
  return { members, is_grand_total: isGrandTotal }
}

function pivot(
  overrides: Partial<ExplorePivotConfig> = {},
): ExplorePivotConfig {
  return {
    version: 1,
    id: "pivot_1",
    name: "Claims pivot",
    enabled: true,
    filters: [],
    rows: [{ id: "row_region", field: "region" }],
    columns: [{ id: "column_year", field: "year" }],
    values: [
      {
        id: "value_paid",
        field: "paid",
        aggregation: "sum",
        reference: "paid_sum",
        display_name: "Paid",
      },
      {
        id: "value_claims",
        field: "claim_id",
        aggregation: "count",
        reference: "claim_id_count",
        display_name: "Claims",
      },
    ],
    formulas: [],
    options: { row_grand_totals: true, column_grand_totals: true },
    ...overrides,
    value_order: overrides.value_order ?? (overrides.values ?? [
      { id: "value_paid" }, { id: "value_claims" },
    ]).map(({ id }) => id),
  }
}

function result(
  sourcePivot: ExplorePivotConfig,
  overrides: Partial<ExplorePivotResult> = {},
): ExplorePivotResult {
  const rowPaths = [path([member("North")]), path([member("South")])]
  const columnPaths = [
    path([{ kind: "integer", value: "2024" }]),
    path([{ kind: "integer", value: "2025" }]),
  ]
  return {
    version: 1,
    node_id: "explore_1",
    pivot_id: sourcePivot.id,
    source: "pricing",
    dataframe_cache_key: "dataframe-current",
    calculation_key: "calculation-current",
    row_fields: sourcePivot.rows.map(({ field }) => field),
    column_fields: sourcePivot.columns.map(({ field }) => field),
    values: pivotOutputs(sourcePivot).map((output) =>
      isPivotFormulaPlacement(output)
        ? { id: output.id, field: output.reference, aggregation: "formula" }
        : { id: output.id, field: output.field, aggregation: output.aggregation },
    ),
    row_paths: rowPaths,
    column_paths: columnPaths,
    cells: rowPaths.flatMap((_, rowIndex) =>
      columnPaths.flatMap((__, columnIndex) =>
        pivotOutputs(sourcePivot).map((value, valueIndex) => ({
          row_index: rowIndex,
          column_index: columnIndex,
          value_id: value.id,
          value:
            rowIndex === 0 && columnIndex === 1 && valueIndex === 0
              ? null
              : String((rowIndex + 1) * 100 + columnIndex * 10 + valueIndex),
        })),
      ),
    ),
    warnings: [],
    generated_at: 1,
    execution_metrics: null,
    ...overrides,
  }
}

function chart(
  sourcePivot: ExplorePivotConfig,
  overrides: Partial<ExploreChartConfig> = {},
): ExploreChartConfig {
  return {
    ...createExploreChart([]),
    pivot_id: sourcePivot.id,
    value_encodings: seedValueEncodings(sourcePivot),
    ...overrides,
  }
}

function expectReason(action: () => unknown, reasonCode: string) {
  try {
    action()
  } catch (error) {
    expect(error).toBeInstanceOf(ChartDataError)
    expect((error as ChartDataError).reasonCode).toBe(reasonCode)
    return
  }
  throw new Error(`Expected ChartDataError ${reasonCode}`)
}

describe("pivot chart data adapter", () => {
  it("orders Columns outside Values, retains null gaps, and formats per axis", () => {
    const sourcePivot = pivot()
    const configured = chart(sourcePivot, {
      axes: {
        primary: {
          title: "",
          minimum: null,
          maximum: null,
          number_format: "currency_gbp",
        },
        secondary: {
          title: "",
          minimum: null,
          maximum: null,
          number_format: "integer",
          enabled: true,
        },
      },
      value_encodings: seedValueEncodings(sourcePivot).map((encoding) =>
        encoding.value_id === "value_claims"
          ? { ...encoding, axis: "secondary" as const }
          : encoding,
      ),
    })

    const data = adaptPivotChartData(configured, sourcePivot, result(sourcePivot))

    expect(data.categories.map(({ label }) => label)).toEqual(["North", "South"])
    expect(data.series.map(({ name }) => name)).toEqual([
      "2024 · Paid",
      "2024 · Claims",
      "2025 · Paid",
      "2025 · Claims",
    ])
    expect(data.series[2].values).toEqual([null, 210])
    expect(data.series[2].formattedValues[0]).toBeNull()
    expect(data.series[0].formattedValues[0]).toMatch(/^£100/)
    expect(data.series[1].formattedValues[0]).toBe("101")
  })

  it("adapts formula outputs as ordinary chart series", () => {
    const sourcePivot = pivot({
      formulas: [{
        id: "formula_average",
        reference: "average_cost",
        display_name: "Average cost",
        expression: 'pl.col("paid").sum() / pl.col("claim_id").count()',
        number_format: "number",
        decimal_places: 2,
        use_grouping: true,
      }],
    })

    const data = adaptPivotChartData(
      chart(sourcePivot),
      sourcePivot,
      result(sourcePivot),
    )

    expect(data.series.map(({ name }) => name)).toContain("2024 · Average cost")
    expect(data.series.find(({ valueId }) => valueId === "formula_average")?.values).toEqual([
      102,
      202,
    ])
  })

  it("uses typed canonical keys for exact overrides and reports dormant mappings", () => {
    const sourcePivot = pivot()
    const sourceResult = result(sourcePivot)
    const key = exploreChartSeriesKey("value_paid", sourceResult.column_paths[0])
    const configured = chart(sourcePivot, {
      value_encodings: [
        ...seedValueEncodings(sourcePivot),
        {
          id: "encoding_dormant",
          value_id: "value_removed",
          mark: "column",
          axis: "primary",
          stack_group: null,
          stack_normalize: false,
          color: null,
          data_labels: false,
          markers: false,
        },
      ],
      series_overrides: [
        {
          id: "override_2024_paid",
          series_key: key,
          mark: "line",
          axis: "secondary",
          stack_group: null,
          stack_normalize: false,
          color: "#AABBCC",
          data_labels: true,
          markers: true,
        },
        {
          id: "override_dormant",
          series_key: exploreChartSeriesKey(
            "value_paid",
            path([member("missing")]),
          ),
          mark: "column",
          axis: "primary",
          stack_group: null,
          stack_normalize: false,
          color: null,
          data_labels: false,
          markers: false,
        },
      ],
    })

    const data = adaptPivotChartData(configured, sourcePivot, sourceResult)

    expect(data.series[0].style).toMatchObject({
      mark: "line",
      axis: "secondary",
      color: "#AABBCC",
    })
    expect(data.dormantOverrideIds).toEqual(["override_dormant"])
    expect(data.dormantEncodingIds).toEqual(["encoding_dormant"])
    expect(exploreChartSeriesKey("value_paid", path([member("1")]))).not.toBe(
      exploreChartSeriesKey(
        "value_paid",
        path([{ kind: "integer", value: "1" }]),
      ),
    )
  })

  it("supports no Rows or Columns as a single All category/value series", () => {
    const sourcePivot = pivot({
      rows: [],
      columns: [],
      values: [pivot().values[0]],
    })
    const sourceResult = result(sourcePivot, {
      row_fields: [],
      column_fields: [],
      row_paths: [path([])],
      column_paths: [path([])],
      cells: [
        {
          row_index: 0,
          column_index: 0,
          value_id: "value_paid",
          value: -3,
        },
      ],
    })

    expect(
      adaptPivotChartData(chart(sourcePivot), sourcePivot, sourceResult),
    ).toMatchObject({
      categories: [{ label: "All" }],
      series: [{ name: "Paid", values: [-3] }],
    })
  })

  it("normalises 100% stack groups per category and warns once on zero denominators", () => {
    const sourcePivot = pivot({
      columns: [],
      values: [
        { id: "value_a", field: "a", aggregation: "sum", reference: "a_sum", display_name: "A" },
        { id: "value_b", field: "b", aggregation: "sum", reference: "b_sum", display_name: "B" },
        { id: "value_c", field: "c", aggregation: "sum", reference: "c_sum", display_name: "C" },
      ],
    })
    const rows = ["mixed", "simple", "gapped", "empty"]
    const cellsByRow: Array<Array<number | null>> = [
      [30, -10, 60],
      [25, 75, null],
      [null, 40, 40],
      [null, 0, null],
    ]
    const sourceResult = result(sourcePivot, {
      column_fields: [],
      row_paths: rows.map((label) => path([member(label)])),
      column_paths: [path([])],
      cells: cellsByRow.flatMap((cells, rowIndex) =>
        cells.map((value, valueIndex) => ({
          row_index: rowIndex,
          column_index: 0,
          value_id: sourcePivot.values[valueIndex].id,
          value,
        })),
      ),
    })
    const configured = chart(sourcePivot, {
      value_encodings: seedValueEncodings(sourcePivot).map((encoding) => ({
        ...encoding,
        stack_group: "s",
        stack_normalize: true,
      })),
      axes: {
        primary: {
          title: "",
          minimum: null,
          maximum: null,
          number_format: "percent",
        },
        secondary: {
          title: "",
          minimum: null,
          maximum: null,
          number_format: "inherit",
          enabled: true,
        },
      },
    })

    const data = adaptPivotChartData(configured, sourcePivot, sourceResult)

    expect(data.series.map(({ values }) => values)).toEqual([
      [0.3, 0.25, null, null],
      [-0.1, 0.75, 0.5, null],
      [0.6, null, 0.5, null],
    ])
    // Formatting runs after normalisation: labels/tooltips/table show the
    // normalised shares, and zero-denominator categories stay gaps.
    expect(data.series.map(({ formattedValues }) => formattedValues)).toEqual([
      ["30%", "25%", null, null],
      ["-10%", "75%", "50%", null],
      ["60%", null, "50%", null],
    ])
    const zeroDenominatorWarnings = data.warnings.filter(
      (warning) => warning.includes("empty") && warning.includes('"s"'),
    )
    expect(zeroDenominatorWarnings).toHaveLength(1)
  })

  it("normalises finite huge stack members without overflow", () => {
    const sourcePivot = pivot({
      columns: [],
      values: [
        { id: "value_a", field: "a", aggregation: "sum", reference: "a_sum", display_name: "A" },
        { id: "value_b", field: "b", aggregation: "sum", reference: "b_sum", display_name: "B" },
      ],
    })
    const sourceResult = result(sourcePivot, {
      column_fields: [],
      row_paths: [path([member("Huge")])],
      column_paths: [path([])],
      cells: sourcePivot.values.map((value) => ({
        row_index: 0,
        column_index: 0,
        value_id: value.id,
        value: 1e308,
      })),
    })
    const configured = chart(sourcePivot, {
      value_encodings: seedValueEncodings(sourcePivot).map((encoding) => ({
        ...encoding,
        stack_group: "s",
        stack_normalize: true,
      })),
    })

    const data = adaptPivotChartData(configured, sourcePivot, sourceResult)

    expect(data.series.map(({ values }) => values)).toEqual([[0.5], [0.5]])
  })

  it("leaves non-normalised sibling groups untouched", () => {
    const sourcePivot = pivot({
      columns: [],
      values: [
        { id: "value_a", field: "a", aggregation: "sum", reference: "a_sum", display_name: "A" },
        { id: "value_b", field: "b", aggregation: "sum", reference: "b_sum", display_name: "B" },
        { id: "value_c", field: "c", aggregation: "sum", reference: "c_sum", display_name: "C" },
      ],
    })
    const sourceResult = result(sourcePivot, {
      column_fields: [],
      row_paths: [path([member("North")])],
      column_paths: [path([])],
      cells: sourcePivot.values.map((value, valueIndex) => ({
        row_index: 0,
        column_index: 0,
        value_id: value.id,
        value: (valueIndex + 1) * 10,
      })),
    })
    const configured = chart(sourcePivot, {
      value_encodings: seedValueEncodings(sourcePivot).map(
        (encoding, index) =>
          index < 2
            ? { ...encoding, stack_group: "s", stack_normalize: true }
            : { ...encoding, stack_group: "raw" },
      ),
    })

    const data = adaptPivotChartData(configured, sourcePivot, sourceResult)

    expect(data.series.map(({ values }) => values)).toEqual([
      [10 / 30],
      [20 / 30],
      [30],
    ])
    expect(data.warnings).toEqual([])
  })

  it("never charts column grand-total paths, even when grand totals are requested", () => {
    const sourcePivot = pivot({ values: [pivot().values[0]] })
    const rowPaths = [path([member("North")]), path([], true)]
    const columnPaths = [
      path([{ kind: "integer", value: "2024" }]),
      path([], true),
    ]
    const sourceResult = result(sourcePivot, {
      row_paths: rowPaths,
      column_paths: columnPaths,
      cells: rowPaths.flatMap((_, rowIndex) =>
        columnPaths.map((__, columnIndex) => ({
          row_index: rowIndex,
          column_index: columnIndex,
          value_id: "value_paid",
          value: rowIndex * 10 + columnIndex + 1,
        })),
      ),
    })

    const data = adaptPivotChartData(
      chart(sourcePivot, {
        category: {
          source: "rows",
          include_grand_total: true,
          label_rotation: 0,
        },
      }),
      sourcePivot,
      sourceResult,
    )
    expect(data.categories.map(({ label }) => label)).toEqual([
      "North",
      "Grand total",
    ])
    expect(data.series.map(({ name }) => name)).toEqual(["2024 · Paid"])
  })

  it("excludes subtotal-shaped paths and includes grand totals only on request", () => {
    const sourcePivot = pivot({
      rows: [
        { id: "row_region", field: "region" },
        { id: "row_team", field: "team" },
      ],
      columns: [],
      values: [pivot().values[0]],
    })
    const rowPaths = [
      path([member("North"), member("A")]),
      path([member("North")]),
      path([], true),
    ]
    const sourceResult = result(sourcePivot, {
      row_fields: ["region", "team"],
      column_fields: [],
      row_paths: rowPaths,
      column_paths: [path([])],
      cells: rowPaths.map((_, rowIndex) => ({
        row_index: rowIndex,
        column_index: 0,
        value_id: "value_paid",
        value: rowIndex + 1,
      })),
    })

    const ordinary = adaptPivotChartData(chart(sourcePivot), sourcePivot, sourceResult)
    expect(ordinary.categories.map(({ label }) => label)).toEqual(["North › A"])

    const withGrandTotal = adaptPivotChartData(
      chart(sourcePivot, {
        category: {
          source: "rows",
          include_grand_total: true,
          label_rotation: 0,
        },
      }),
      sourcePivot,
      sourceResult,
    )
    // A partial-depth path ("North") is never charted: the backend emits only
    // full-depth paths plus optional grand totals.
    expect(withGrandTotal.categories.map(({ label }) => label)).toEqual([
      "North › A",
      "Grand total",
    ])
  })

  it("rejects stale field/value identities, missing cells, and invalid numeric cells", () => {
    const sourcePivot = pivot()
    const configured = chart(sourcePivot)
    const base = result(sourcePivot)

    expectReason(
      () =>
        adaptPivotChartData(configured, sourcePivot, {
          ...base,
          row_fields: ["territory"],
        }),
      "chart_pivot_shape_mismatch",
    )
    expectReason(
      () =>
        adaptPivotChartData(configured, sourcePivot, {
          ...base,
          cells: base.cells.slice(1),
        }),
      "chart_cell_missing",
    )
    expectReason(
      () =>
        adaptPivotChartData(configured, sourcePivot, {
          ...base,
          cells: [
            { ...base.cells[0], value: Number.POSITIVE_INFINITY },
            ...base.cells.slice(1),
          ],
        }),
      "chart_cell_value_invalid",
    )
    expectReason(
      () =>
        adaptPivotChartData(configured, sourcePivot, {
          ...base,
          cells: [{ ...base.cells[0], value: true }, ...base.cells.slice(1)],
        }),
      "chart_cell_value_invalid",
    )
  })

  it("requires at least one Value and one explicit encoding per current Value", () => {
    const noValues = pivot({ values: [] })
    expectReason(
      () =>
        adaptPivotChartData(
          chart(noValues),
          noValues,
          result(noValues, { values: [], cells: [] }),
        ),
      "chart_values_required",
    )

    const sourcePivot = pivot()
    expectReason(
      () =>
        adaptPivotChartData(
          { ...chart(sourcePivot), value_encodings: [] },
          sourcePivot,
          result(sourcePivot),
        ),
      "chart_encoding_missing",
    )
  })

  it.each([
    [
      "categories",
      CHART_MAX_CATEGORIES + 1,
      (sourcePivot: ExplorePivotConfig, count: number) => {
        const rowPaths = Array.from({ length: count }, (_, index) =>
          path([member(`R${index}`)]),
        )
        return result(sourcePivot, { row_paths: rowPaths, cells: [] })
      },
    ],
    [
      "series",
      CHART_MAX_SERIES + 1,
      (sourcePivot: ExplorePivotConfig, count: number) => {
        const columnPaths = Array.from({ length: count }, (_, index) =>
          path([member(`C${index}`)]),
        )
        return result(sourcePivot, { column_paths: columnPaths, cells: [] })
      },
    ],
    [
      "points",
      CHART_MAX_POINTS + 1,
      (sourcePivot: ExplorePivotConfig) => {
        const rowPaths = Array.from({ length: 201 }, (_, index) =>
          path([member(`R${index}`)]),
        )
        const columnPaths = Array.from({ length: 100 }, (_, index) =>
          path([member(`C${index}`)]),
        )
        return result(sourcePivot, {
          row_paths: rowPaths,
          column_paths: columnPaths,
          cells: [],
        })
      },
    ],
  ] as const)("rejects %s above its hard limit", (dimension, count, makeResult) => {
    const sourcePivot = pivot({ values: [pivot().values[0]] })
    try {
      adaptPivotChartData(
        chart(sourcePivot),
        sourcePivot,
        makeResult(sourcePivot, count),
      )
    } catch (error) {
      expect(error).toBeInstanceOf(ChartDataError)
      expect((error as ChartDataError).reasonCode).toBe("chart_cardinality_limit")
      expect((error as ChartDataError).dimensions.dimension).toBe(dimension)
      return
    }
    throw new Error(`Expected ${dimension} limit rejection`)
  })

  it("rejects hierarchy depth and rendered label length above their hard limits", () => {
    const sourcePivot = pivot({ values: [pivot().values[0]] })
    const deepPath = path(
      Array.from({ length: CHART_MAX_HIERARCHY_DEPTH + 1 }, (_, index) =>
        member(String(index)),
      ),
    )
    expectReason(
      () =>
        adaptPivotChartData(
          chart(sourcePivot),
          sourcePivot,
          result(sourcePivot, { row_paths: [deepPath], cells: [] }),
        ),
      "chart_cardinality_limit",
    )

    const longPath = path([
      member("x".repeat(CHART_MAX_LABEL_LENGTH + 1)),
    ])
    expectReason(
      () =>
        adaptPivotChartData(
          chart(sourcePivot),
          sourcePivot,
          result(sourcePivot, { row_paths: [longPath], cells: [] }),
        ),
      "chart_cardinality_limit",
    )
  })
})

describe("formatChartValue", () => {
  it("renders inherit as the General locale format", () => {
    expect(formatChartValue(1234567.891, "inherit")).toBe("1,234,567.89")
    expect(formatChartValue(1234.5, "inherit")).toBe("1,234.5")
    expect(formatChartValue(12, "inherit")).toBe("12")
    expect(formatChartValue(0.12345, "inherit")).toBe("0.1235")
    expect(formatChartValue(-0.000123, "inherit")).toBe("-0.000123")
    expect(formatChartValue(0, "inherit")).toBe("0")
  })

  it("keeps explicit formats unchanged", () => {
    expect(formatChartValue(1234.5, "integer")).toBe("1,235")
    expect(formatChartValue(0.5, "percent")).toBe("50%")
    expect(formatChartValue(100, "currency_gbp")).toBe("£100.00")
  })
})
