import { describe, expect, it } from "vitest"

import {
  createExplorePivot,
  isNumericPivotDtype,
  parseExplorePivots,
  pivotCalculationIdentity,
  pivotAggregationsForDtype,
  type ExplorePivotConfig,
} from "../pivotConfig"

function pivot(overrides: Partial<ExplorePivotConfig> = {}): ExplorePivotConfig {
  return {
    version: 1,
    id: "pivot_1",
    name: "Claims by region",
    enabled: true,
    filters: [],
    columns: [],
    rows: [{ id: "row_1", field: "region" }],
    values: [
      {
        id: "value_1",
        field: "claims",
        aggregation: "sum",
        display_name: "Claims",
      },
    ],
    options: { row_grand_totals: true, column_grand_totals: true, sort_by: null },
    ...overrides,
  }
}

describe("pivotConfig", () => {
  it("selects aggregations from the Polars dtype capabilities", () => {
    expect(isNumericPivotDtype("i64")).toBe(true)
    expect(isNumericPivotDtype("f64")).toBe(true)
    expect(isNumericPivotDtype("Decimal(12, 2)")).toBe(true)
    expect(isNumericPivotDtype("Int64ish")).toBe(false)
    expect(isNumericPivotDtype("Decimalish")).toBe(false)
    expect(pivotAggregationsForDtype("i64")).toEqual([
      "sum", "count", "average", "min", "max", "median", "distinct_count",
    ])
    expect(pivotAggregationsForDtype("Binary")).toEqual([
      "count", "distinct_count", "min", "max",
    ])
    expect(pivotAggregationsForDtype("Duration(ms)")).toEqual([
      "count", "distinct_count", "min", "max",
    ])
    expect(pivotAggregationsForDtype("List(Int64)")).toEqual(["count"])
    expect(pivotAggregationsForDtype("Array(Float64, 3)")).toEqual(["count"])
    expect(pivotAggregationsForDtype("Struct({x: Int64})")).toEqual(["count"])
    expect(pivotAggregationsForDtype("Object")).toEqual(["count"])
  })

  it("rejects versionless cards instead of migrating them", () => {
    // There is no v0 migration: every persisted card is complete version 1.
    expect(
      parseExplorePivots({ pivots: [{ id: "pivot_1" }] }),
    ).toMatchObject({
      ok: false,
      error: expect.stringMatching(/version must be 1/i),
    })
  })

  it("validates v1 names, placement identities, and same-zone duplicates", () => {
    const duplicateName = parseExplorePivots({
      pivots: [pivot(), pivot({ id: "pivot_2", name: " claims BY REGION " })],
    })
    expect(duplicateName).toMatchObject({ ok: false, error: expect.stringMatching(/duplicate pivot name/i) })

    const duplicatePlacement = parseExplorePivots({
      pivots: [pivot({ columns: [{ id: "row_1", field: "year" }] })],
    })
    expect(duplicatePlacement).toMatchObject({ ok: false, error: expect.stringMatching(/duplicate placement id/i) })

    const duplicateRow = parseExplorePivots({
      pivots: [pivot({ rows: [{ id: "row_1", field: "region" }, { id: "row_2", field: "region" }] })],
    })
    expect(duplicateRow).toMatchObject({ ok: false, error: expect.stringMatching(/duplicate field/i) })
  })

  it("allows repeated Values and excludes presentation edits from calculation identity", () => {
    const base = pivot({
      columns: [{ id: "column_1", field: "year" }],
      values: [
        { id: "value_1", field: "claims", aggregation: "sum", display_name: "Claims" },
        { id: "value_2", field: "claims", aggregation: "average", display_name: "Average" },
      ],
    })
    const presentationEdit = {
      ...base,
      name: "Renamed",
      enabled: false,
      columns: base.columns.map((column) => ({
        ...column,
        number_format: "currency_gbp" as const,
        decimal_places: 0,
        use_grouping: false,
      })),
      rows: base.rows.map((row) => ({
        ...row,
        number_format: "percent" as const,
        decimal_places: 4,
        use_grouping: true,
      })),
      values: base.values.map((value) => ({
        ...value,
        display_name: `Renamed ${value.id}`,
        number_format: "currency_eur" as const,
        decimal_places: 2,
        use_grouping: false,
      })),
    }

    expect(parseExplorePivots({ pivots: [base] })).toMatchObject({ ok: true })
    expect(pivotCalculationIdentity(presentationEdit)).toBe(pivotCalculationIdentity(base))
    expect(
      pivotCalculationIdentity({ ...base, rows: [{ id: "row_1", field: "segment" }] }),
    ).not.toBe(pivotCalculationIdentity(base))
  })

  it("deduplicates and canonically orders filter members in the calculation identity", () => {
    const members = [
      { kind: "string" as const, value: "North" },
      { kind: "null" as const, value: null },
      { kind: "string" as const, value: "East" },
    ]
    const base = pivot({
      filters: [{ id: "filter_1", field: "region", members }],
    })
    const reorderedWithDuplicates = pivot({
      filters: [
        {
          id: "filter_1",
          field: "region",
          members: [members[2], members[0], members[1], { ...members[0] }],
        },
      ],
    })

    expect(pivotCalculationIdentity(reorderedWithDuplicates)).toBe(
      pivotCalculationIdentity(base),
    )
    expect(
      pivotCalculationIdentity(
        pivot({ filters: [{ id: "filter_1", field: "region", members: members.slice(0, 2) }] }),
      ),
    ).not.toBe(pivotCalculationIdentity(base))
  })

  it("normalises missing v1 formatting, sort, and colour fields, and rejects invalid enums", () => {
    const parsed = parseExplorePivots({
      pivots: [
        pivot({
          columns: [{ id: "column_1", field: "year" }],
          options: { row_grand_totals: true, column_grand_totals: true },
        }),
      ],
    })
    expect(parsed).toMatchObject({
      ok: true,
      pivots: [
        {
          columns: [{ number_format: "general", decimal_places: null, use_grouping: true }],
          rows: [{
            sort: "ascending",
            number_format: "general",
            decimal_places: null,
            use_grouping: true,
          }],
          values: [{
            sort_rows: "none",
            color_scale: "none",
            color_scale_split_by: null,
            number_format: "general",
            decimal_places: null,
            use_grouping: true,
          }],
          options: { sort_by: null },
        },
      ],
    })
    expect(
      parseExplorePivots({
        pivots: [
          pivot({
            rows: [
              {
                id: "row_1",
                field: "region",
                sort: "sideways" as never,
              },
            ],
          }),
        ],
      }),
    ).toMatchObject({ ok: false })
    expect(
      parseExplorePivots({
        pivots: [
          pivot({
            values: [
              {
                id: "value_1",
                field: "claims",
                aggregation: "sum",
                display_name: "Claims",
                sort_rows: "sideways" as never,
              },
            ],
          }),
        ],
      }),
    ).toMatchObject({ ok: false })
    expect(
      parseExplorePivots({
        pivots: [
          pivot({
            values: [
              {
                id: "value_1",
                field: "claims",
                aggregation: "sum",
                display_name: "Claims",
                color_scale: "rainbow" as never,
              },
            ],
          }),
        ],
      }),
    ).toMatchObject({ ok: false })
    expect(
      parseExplorePivots({
        pivots: [
          pivot({
            values: [
              {
                id: "value_1",
                field: "claims",
                aggregation: "sum",
                display_name: "Claims",
                sort_rows: "ascending",
              },
              {
                id: "value_2",
                field: "claims",
                aggregation: "average",
                display_name: "Average claims",
                sort_rows: "descending",
              },
            ],
          }),
        ],
      }),
    ).toMatchObject({
      ok: false,
      error: expect.stringContaining("only one active Value row sort"),
    })
    expect(
      parseExplorePivots({
        pivots: [
          pivot({
            options: {
              row_grand_totals: true,
              column_grand_totals: true,
              sort_by: "missing",
            },
          }),
        ],
      }),
    ).toMatchObject({
      ok: false,
      error: expect.stringContaining("Row or Value placement"),
    })
    expect(
      parseExplorePivots({
        pivots: [
          pivot({
            values: [
              {
                id: "value_1",
                field: "claims",
                aggregation: "sum",
                display_name: "Claims",
                sort_rows: "descending",
              },
            ],
            options: {
              row_grand_totals: true,
              column_grand_totals: true,
              sort_by: null,
            },
          }),
        ],
      }),
    ).toMatchObject({
      ok: false,
      error: expect.stringContaining("requires options.sort_by"),
    })
  })

  it("accepts active colour-scale splits by Row or Column and rejects invalid references", () => {
    for (const splitId of ["row_1", "column_1"]) {
      expect(parseExplorePivots({
        pivots: [pivot({
          columns: [{ id: "column_1", field: "year" }],
          values: [{
            ...pivot().values[0],
            color_scale: "low_red_high_green",
            color_scale_split_by: splitId,
          }],
        })],
      })).toMatchObject({
        ok: true,
        pivots: [{ values: [{ color_scale_split_by: splitId }] }],
      })
    }

    const invalidCases: Array<[unknown, "none" | "low_red_high_green"]> = [
      [42, "low_red_high_green"],
      ["missing", "low_red_high_green"],
      ["filter_1", "low_red_high_green"],
      ["value_1", "low_red_high_green"],
      ["row_1", "none"],
    ]
    for (const [split, colorScale] of invalidCases) {
      expect(parseExplorePivots({
        pivots: [pivot({
          filters: [{ id: "filter_1", field: "status", members: [] }],
          columns: [{ id: "column_1", field: "year" }],
          values: [{
            ...pivot().values[0],
            color_scale: colorScale,
            color_scale_split_by: split as never,
          }],
        })],
      })).toMatchObject({ ok: false, error: expect.stringMatching(/split/i) })
    }
  })

  it("validates number formats, grouping, and decimal-place boundaries", () => {
    expect(
      parseExplorePivots({
        pivots: [
          pivot({
            columns: [{
              id: "column_1",
              field: "year",
              number_format: "currency_gbp",
              decimal_places: 0,
              use_grouping: false,
            }],
            rows: [{
              id: "row_1",
              field: "region",
              number_format: "percent",
              decimal_places: 10,
              use_grouping: true,
            }],
            values: [{
              id: "value_1",
              field: "claims",
              aggregation: "sum",
              display_name: "Claims",
              number_format: "currency_eur",
              decimal_places: 2,
              use_grouping: false,
            }],
          }),
        ],
      }),
    ).toMatchObject({
      ok: true,
      pivots: [{
        columns: [{ number_format: "currency_gbp", decimal_places: 0, use_grouping: false }],
        rows: [{ number_format: "percent", decimal_places: 10, use_grouping: true }],
        values: [{ number_format: "currency_eur", decimal_places: 2, use_grouping: false }],
      }],
    })

    for (const decimal_places of [-1, 11, 1.5, true, "2"]) {
      expect(
        parseExplorePivots({
          pivots: [
            pivot({
              columns: [{
                id: "column_1",
                field: "year",
                decimal_places: decimal_places as never,
              }],
            }),
          ],
        }),
      ).toMatchObject({
        ok: false,
        error: expect.stringMatching(/decimal places/i),
      })
    }

    for (const [field, value, message] of [
      ["number_format", "accounting", /number format/i],
      ["number_format", 2, /number format/i],
      ["use_grouping", "yes", /grouping/i],
      ["use_grouping", 1, /grouping/i],
    ] as const) {
      expect(
        parseExplorePivots({
          pivots: [
            pivot({
              columns: [{ id: "column_1", field: "year", [field]: value }],
            }),
          ],
        }),
      ).toMatchObject({ ok: false, error: expect.stringMatching(message) })
    }
  })

  it("migrates an existing fixed-decimal v1 placement to Number formatting", () => {
    expect(
      parseExplorePivots({
        pivots: [
          pivot({
            columns: [{ id: "column_1", field: "year", decimal_places: 2 }],
          }),
        ],
      }),
    ).toMatchObject({
      ok: true,
      pivots: [{
        columns: [{
          number_format: "number",
          decimal_places: 2,
          use_grouping: true,
        }],
      }],
    })
  })

  it("derives the selected sort target for a legacy active Value sort", () => {
    const parsed = parseExplorePivots({
      pivots: [
        pivot({
          values: [
            {
              id: "value_1",
              field: "claims",
              aggregation: "sum",
              display_name: "Claims",
              sort_rows: "descending",
            },
          ],
          options: { row_grand_totals: true, column_grand_totals: true },
        }),
      ],
    })

    expect(parsed).toMatchObject({
      ok: true,
      pivots: [{ options: { sort_by: "value_1" } }],
    })
  })

  it("includes row and value sorts but excludes colour scale, scale splits, and number formatting from calculation identity", () => {
    const base = pivot()
    const dormantRowDirection = {
      ...base,
      rows: [{ ...base.rows[0], sort: "descending" as const }],
    }
    const sortedRows = {
      ...dormantRowDirection,
      options: { ...base.options, sort_by: "row_1" },
    }
    const explicitlySelectedDefaultRow = {
      ...base,
      options: { ...base.options, sort_by: "row_1" },
    }
    const sortedValues = {
      ...base,
      values: [{ ...base.values[0], sort_rows: "ascending" as const }],
      options: { ...base.options, sort_by: "value_1" },
    }
    const colourOnly = { ...base, values: [{ ...base.values[0], color_scale: "low_red_high_green" as const }] }
    const splitOnly = {
      ...base,
      values: [{
        ...base.values[0],
        color_scale: "low_red_high_green" as const,
        color_scale_split_by: "row_1",
      }],
    }
    const decimalsOnly = {
      ...base,
      rows: [{
        ...base.rows[0],
        number_format: "percent" as const,
        decimal_places: 3,
        use_grouping: false,
      }],
      values: [{
        ...base.values[0],
        number_format: "currency_usd" as const,
        decimal_places: 2,
        use_grouping: true,
      }],
    }
    expect(pivotCalculationIdentity(dormantRowDirection)).toBe(pivotCalculationIdentity(base))
    expect(pivotCalculationIdentity(explicitlySelectedDefaultRow)).toBe(pivotCalculationIdentity(base))
    expect(pivotCalculationIdentity(sortedRows)).not.toBe(pivotCalculationIdentity(base))
    expect(pivotCalculationIdentity(sortedValues)).not.toBe(pivotCalculationIdentity(base))
    expect(pivotCalculationIdentity(colourOnly)).toBe(pivotCalculationIdentity(base))
    expect(pivotCalculationIdentity(splitOnly)).toBe(pivotCalculationIdentity(base))
    expect(pivotCalculationIdentity(decimalsOnly)).toBe(pivotCalculationIdentity(base))
  })

  it.each([
    ["integer", "01"],
    ["decimal", "NaN"],
    ["date", "2024-02-31"],
    ["datetime", "2024-02-31T12:00:00"],
    ["time", "25:00:00"],
  ] as const)("rejects a malformed %s member value", (kind, value) => {
    const parsed = parseExplorePivots({
      pivots: [
        pivot({
          filters: [{ id: "filter_1", field: "selected", members: [{ kind, value }] }],
        }),
      ],
    })

    expect(parsed).toMatchObject({
      ok: false,
      error: expect.stringMatching(/member value does not match/i),
    })
  })

  it("creates complete cards with the first unused id and name", () => {
    expect(
      createExplorePivot([
        pivot(),
        pivot({ id: "pivot_3", name: "Pivot 3" }),
      ]),
    ).toEqual({
      version: 1,
      id: "pivot_2",
      name: "Pivot 2",
      enabled: true,
      filters: [],
      columns: [],
      rows: [],
      values: [],
      options: { row_grand_totals: true, column_grand_totals: true, sort_by: null },
    })
  })
})
