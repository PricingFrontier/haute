import { describe, expect, it } from "vitest"

import {
  createExplorePivot,
  isNumericPivotDtype,
  nextPivotValueReference,
  parseExplorePivots,
  pivotCalculationIdentity,
  pivotAggregationsForDtype,
  pivotFormulas,
  pivotOutputs,
  serializeExplorePivot,
  type ExplorePivotConfig,
} from "../pivotConfig"

function pivot(overrides: Partial<ExplorePivotConfig> = {}): ExplorePivotConfig {
  const base: ExplorePivotConfig = {
    version: 1 as const,
    id: "pivot_1",
    name: "Claims by region",
    enabled: true,
    filters: [],
    columns: [],
    rows: [{ id: "row_1", field: "region", number_format: "general", decimal_places: null, use_grouping: true }],
    values: [
      {
        id: "value_1",
        field: "claims",
        aggregation: "sum",
        reference: "claims_sum",
        display_name: "Claims",
        color_scale_split_by: null,
        number_format: "general",
        decimal_places: null,
        use_grouping: true,
      },
    ],
    formulas: [],
    value_order: [],
    options: { row_grand_totals: true, column_grand_totals: true, sort_by: null },
  }
  const next = { ...base, ...overrides }
  return {
    ...next,
    value_order: overrides.value_order ?? [
      ...next.values.map(({ id }) => id),
      ...next.formulas.map(({ id }) => id),
    ],
  }
}

describe("pivotConfig", () => {
  it("normalizes and preserves the canonical mixed Value output order", () => {
    const raw = pivot({
      values: [
        { ...pivot().values[0], id: "value_1", reference: "claims_sum" },
        { ...pivot().values[0], id: "value_2", aggregation: "average", reference: "claims_mean", display_name: "Average" },
      ],
      formulas: ["formula_1", "formula_2"] as never,
    })
    const config = {
      pivot_formulas: [
        { id: "formula_1", reference: "first_formula", display_name: "First formula", expression: "pl.lit(1)", number_format: "general", decimal_places: null, use_grouping: true },
        { id: "formula_2", reference: "second_formula", display_name: "Second formula", expression: "pl.lit(2)", number_format: "general", decimal_places: null, use_grouping: true },
      ],
      pivots: [{
        ...raw,
        formulas: ["formula_1", "formula_2"],
        value_order: ["formula_2", "value_1", "formula_1", "value_2"],
      }],
    }

    const parsed = parseExplorePivots(config)
    expect(parsed).toMatchObject({ ok: true })
    if (!parsed.ok) throw new Error(parsed.error)
    expect(pivotOutputs(parsed.pivots[0]).map(({ id }) => id)).toEqual(config.pivots[0].value_order)
    expect(serializeExplorePivot(parsed.pivots[0]).value_order).toEqual(config.pivots[0].value_order)

    expect(parseExplorePivots({
      ...config,
      pivots: [{ ...config.pivots[0], value_order: undefined }],
    })).toMatchObject({ ok: false, error: expect.stringMatching(/value order/i) })

    for (const value_order of [
      ["value_1", "value_1", "formula_1", "formula_2"],
      ["value_1", "formula_1", "formula_2"],
      ["value_1", "value_2", "formula_1", "unknown"],
      "value_1",
    ]) {
      expect(parseExplorePivots({
        ...config,
        pivots: [{ ...config.pivots[0], value_order }],
      })).toMatchObject({ ok: false, error: expect.stringMatching(/value order/i) })
    }

    expect(pivotCalculationIdentity({ ...parsed.pivots[0], value_order: ["value_1", "formula_2", "formula_1", "value_2"] }))
      .not.toBe(pivotCalculationIdentity(parsed.pivots[0]))
  })

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
      pivots: [pivot({ columns: [{ id: "row_1", field: "year", number_format: "general", decimal_places: null, use_grouping: true }] })],
    })
    expect(duplicatePlacement).toMatchObject({ ok: false, error: expect.stringMatching(/duplicate placement id/i) })

    const duplicateRow = parseExplorePivots({
      pivots: [pivot({ rows: [{ id: "row_1", field: "region", number_format: "general", decimal_places: null, use_grouping: true }, { id: "row_2", field: "region", number_format: "general", decimal_places: null, use_grouping: true }] })],
    })
    expect(duplicateRow).toMatchObject({ ok: false, error: expect.stringMatching(/duplicate field/i) })
  })

  it("allows repeated Values and excludes presentation edits from calculation identity", () => {
    const base = pivot({
      columns: [{ id: "column_1", field: "year", number_format: "general", decimal_places: null, use_grouping: true }],
      values: [
        { ...pivot().values[0], id: "value_1", reference: "claims_sum" },
        { ...pivot().values[0], id: "value_2", aggregation: "average", reference: "claims_mean", display_name: "Average" },
      ],
      formulas: [{
        id: "formula_1",
        reference: "claims_ratio",
        display_name: "Claims ratio",
        expression: 'pl.col("claims").sum() / pl.col("claims").mean()',
        number_format: "number",
        decimal_places: 2,
        use_grouping: true,
      }],
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
      formulas: pivotFormulas(base).map((formula) => ({
        ...formula,
        display_name: "Renamed formula",
        number_format: "currency_usd" as const,
        decimal_places: 4,
        use_grouping: false,
      })),
    }

    expect(parseExplorePivots({
      pivot_formulas: base.formulas,
      pivots: [{ ...base, formulas: ["formula_1"] }],
    })).toMatchObject({ ok: true })
    expect(pivotCalculationIdentity(presentationEdit)).toBe(pivotCalculationIdentity(base))
    expect(
      pivotCalculationIdentity({ ...base, rows: [{ ...base.rows[0], field: "segment" }] }),
    ).not.toBe(pivotCalculationIdentity(base))
    expect(
      pivotCalculationIdentity({
        ...base,
        formulas: pivotFormulas(base).map((formula) => ({
          ...formula,
          expression: 'pl.col("claims").sum() + pl.col("claims").mean()',
        })),
      }),
    ).not.toBe(pivotCalculationIdentity(base))
  })

  it("rejects non-canonical Value aliases and inline formula definitions", () => {
    for (const reference of ["value_1", "average_total_claims"]) {
      expect(parseExplorePivots({
        pivots: [pivot({
          values: [{ ...pivot().values[0], field: "total_claims", aggregation: "average", reference, display_name: "total_claims" }],
        })],
      })).toMatchObject({
        ok: false,
        error: expect.stringMatching(/field-first.*total_claims_mean/i),
      })
    }

    expect(parseExplorePivots({
      pivots: [pivot({
        formulas: [{ id: "formula_1", reference: "double_average", display_name: "Double average", expression: 'pl.col("claims").sum() * 2', number_format: "general", decimal_places: null, use_grouping: true }],
      })],
    })).toMatchObject({
      ok: false,
      error: expect.stringMatching(/shared formula ids/i),
    })
  })

  it("accepts double-digit duplicate Value reference suffixes", () => {
    const values = Array.from({ length: 10 }, (_, index) => {
      const position = index + 1
      return {
        ...pivot().values[0],
        id: `value_${position}`,
        field: "claims",
        aggregation: "sum" as const,
        reference: position === 1 ? "claims_sum" : `claims_sum_${position}`,
        display_name: "Claims",
      }
    })

    expect(parseExplorePivots({ pivots: [pivot({ values })] })).toMatchObject({ ok: true })
  })

  it("derives Value references from the sanitized field before adding the aggregation", () => {
    const config = pivot({
      values: [{
        ...pivot().values[0],
        id: "value_1",
        field: "!!!",
        aggregation: "sum",
        reference: "value_sum",
        display_name: "!!!",
      }],
    })

    expect(nextPivotValueReference(pivot({ values: [] }), "!!!", "sum")).toBe("value_sum")
    expect(parseExplorePivots({ pivots: [config] })).toMatchObject({ ok: true })
  })

  it("requires the complete canonical formula contract", () => {
    const missingValueReference = pivot()
    const [{ reference: _reference, ...valueWithoutReference }, ...restValues] =
      missingValueReference.values
    const missingValueReferenceRaw = {
      ...missingValueReference,
      values: [valueWithoutReference, ...restValues],
    }
    expect(parseExplorePivots({ pivots: [missingValueReferenceRaw] })).toMatchObject({
      ok: false,
      error: expect.stringMatching(/Value reference/i),
    })

    const missingFormulaSelections = { ...pivot() } as Record<string, unknown>
    delete missingFormulaSelections.formulas
    expect(parseExplorePivots({ pivots: [missingFormulaSelections] })).toMatchObject({
      ok: false,
      error: expect.stringMatching(/formulas must be a list/i),
    })

    expect(parseExplorePivots({
      pivot_formulas: [{
        id: "formula_1",
        display_name: "Double claims",
        expression: 'pl.col("claims").sum() * 2',
      }],
      pivots: [pivot()],
    })).toMatchObject({
      ok: false,
      error: expect.stringMatching(/formula reference/i),
    })
  })

  it("resolves shared calculated fields for every pivot and serializes selections as ids", () => {
    const formula = {
      id: "formula_1",
      reference: "claims_ratio",
      display_name: "Claims ratio",
      expression: 'pl.col("claims").sum() / pl.col("claims").mean()',
      number_format: "number",
      decimal_places: 2,
      use_grouping: true,
    }
    const first = { ...pivot({ values: [] }), formulas: [formula.id], value_order: [formula.id] }
    const second = {
      ...pivot({ id: "pivot_2", name: "Second", values: [] }),
      formulas: [formula.id],
      value_order: [formula.id],
    }

    const parsed = parseExplorePivots({
      pivot_formulas: [formula],
      pivots: [first, second],
    })
    expect(parsed).toMatchObject({
      ok: true,
      formulas: [formula],
      pivots: [
        { formulas: [formula] },
        { formulas: [formula] },
      ],
    })
    if (!parsed.ok) throw new Error(parsed.error)
    expect(serializeExplorePivot(parsed.pivots[0])).toMatchObject({
      values: [],
      formulas: ["formula_1"],
    })

    expect(parseExplorePivots({
      pivot_formulas: [formula],
      pivots: [{ ...pivot(), formulas: ["missing"] }],
    })).toMatchObject({
      ok: false,
      error: expect.stringMatching(/unknown shared formula.*missing/i),
    })
  })

  it("validates shared formula identities", () => {
    expect(parseExplorePivots({
      pivot_formulas: [{
          id: "formula_1",
          reference: "claims_sum",
          display_name: "Duplicate",
          expression: "pl.lit(1)",
          number_format: "general",
          decimal_places: null,
          use_grouping: true,
      }],
      pivots: [{ ...pivot(), formulas: ["formula_1"] }],
    })).toMatchObject({ ok: false, error: expect.stringMatching(/duplicate output reference/i) })

    expect(parseExplorePivots({
      pivot_formulas: [{
          id: "formula_1",
          reference: "ratio",
          display_name: "Ratio",
          expression: " ",
          number_format: "general",
          decimal_places: null,
          use_grouping: true,
      }],
      pivots: [{ ...pivot(), formulas: ["formula_1"] }],
    })).toMatchObject({ ok: false, error: expect.stringMatching(/formula expression/i) })
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

  it("rejects missing v1 formatting and colour fields, and rejects invalid enums", () => {
    for (const field of ["number_format", "decimal_places", "use_grouping"] as const) {
      const { [field]: _omitted, ...placement } = pivot().rows[0]
      expect(parseExplorePivots({ pivots: [pivot({ rows: [placement as never] })] })).toMatchObject({
        ok: false, error: expect.stringMatching(/required/i),
      })
    }
    const formula = {
      id: "formula_1",
      reference: "claims_ratio",
      display_name: "Claims ratio",
      expression: 'pl.col("claims").sum() / pl.col("claims").mean()',
      number_format: "general" as const,
      decimal_places: null,
      use_grouping: true,
    }
    for (const field of ["number_format", "decimal_places", "use_grouping"] as const) {
      const { [field]: _omitted, ...incompleteFormula } = formula
      expect(parseExplorePivots({
        pivot_formulas: [incompleteFormula],
        pivots: [pivot()],
      })).toMatchObject({ ok: false, error: expect.stringMatching(/required/i) })
    }
    const { color_scale_split_by: _omittedSplit, ...valueWithoutSplit } = pivot().values[0]
    expect(parseExplorePivots({ pivots: [pivot({ values: [valueWithoutSplit as never] })] })).toMatchObject({
      ok: false, error: expect.stringMatching(/split.*required/i),
    })
    expect(
      parseExplorePivots({
        pivots: [
          pivot({
            rows: [
              { ...pivot().rows[0], sort: "sideways" as never },
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
              { ...pivot().values[0], sort_rows: "sideways" as never },
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
              { ...pivot().values[0], color_scale: "rainbow" as never },
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
              { ...pivot().values[0], sort_rows: "ascending" },
              { ...pivot().values[0], id: "value_2", aggregation: "average", reference: "claims_mean", display_name: "Average claims", sort_rows: "descending" },
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
              { ...pivot().values[0], sort_rows: "descending" },
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
          columns: [{ id: "column_1", field: "year", number_format: "general", decimal_places: null, use_grouping: true }],
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
          columns: [{ id: "column_1", field: "year", number_format: "general", decimal_places: null, use_grouping: true }],
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
              reference: "claims_sum",
              display_name: "Claims",
              color_scale_split_by: null,
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
              columns: [{ ...pivot().rows[0], id: "column_1", field: "year", decimal_places: decimal_places as never }],
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
              columns: [{ ...pivot().rows[0], id: "column_1", field: "year", [field]: value }],
            }),
          ],
        }),
      ).toMatchObject({ ok: false, error: expect.stringMatching(message) })
    }
  })

  it("rejects an existing fixed-decimal v1 placement missing number formatting", () => {
    expect(
      parseExplorePivots({
        pivots: [
          pivot({
            columns: [{ id: "column_1", field: "year", decimal_places: 2 } as never],
          }),
        ],
      }),
    ).toMatchObject({ ok: false, error: expect.stringMatching(/number format.*required/i) })
  })

  it("derives the selected sort target for a legacy active Value sort", () => {
    const parsed = parseExplorePivots({
      pivots: [
        pivot({
          values: [
            {
              ...pivot().values[0],
              id: "value_1",
              field: "claims",
              aggregation: "sum",
              reference: "claims_sum",
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
      formulas: [],
      value_order: [],
      options: { row_grand_totals: true, column_grand_totals: true, sort_by: null },
    })
  })
})
