import { describe, expect, it } from "vitest"

import {
  createExplorePivot,
  parseExplorePivots,
  pivotCalculationIdentity,
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
    options: { row_grand_totals: true, column_grand_totals: true },
    ...overrides,
  }
}

describe("pivotConfig", () => {
  it("migrates versionless cards and preserves future literals", () => {
    expect(
      parseExplorePivots({ pivots: [{ id: "pivot_1", future: { format: "currency" } }] }),
    ).toEqual({
      ok: true,
      pivots: [
        {
          id: "pivot_1",
          future: { format: "currency" },
          version: 1,
          name: "Pivot 1",
          enabled: true,
          filters: [],
          columns: [],
          rows: [],
          values: [],
          options: { row_grand_totals: true, column_grand_totals: true },
        },
      ],
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
      values: [
        { id: "value_1", field: "claims", aggregation: "sum", display_name: "Claims" },
        { id: "value_2", field: "claims", aggregation: "average", display_name: "Average" },
      ],
    })
    const presentationEdit = {
      ...base,
      name: "Renamed",
      enabled: false,
      values: base.values.map((value) => ({ ...value, display_name: `Renamed ${value.id}` })),
    }

    expect(parseExplorePivots({ pivots: [base] })).toMatchObject({ ok: true })
    expect(pivotCalculationIdentity(presentationEdit)).toBe(pivotCalculationIdentity(base))
    expect(
      pivotCalculationIdentity({ ...base, rows: [{ id: "row_1", field: "segment" }] }),
    ).not.toBe(pivotCalculationIdentity(base))
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
      options: { row_grand_totals: true, column_grand_totals: true },
    })
  })
})
