import { describe, expect, it } from "vitest"

import {
  applyChartPreset,
  createExploreChart,
  dependentChartsForPivot,
  exploreChartSeriesKey,
  nextExploreChartId,
  nextExploreChartName,
  parseExploreCharts,
  resolveExploreChartSource,
  seedValueEncodings,
  type ChartPreset,
  type ExploreChartConfig,
} from "../chartConfig"
import type { ExplorePivotConfig } from "../pivotConfig"

function pivot(
  overrides: Partial<ExplorePivotConfig> = {},
): ExplorePivotConfig {
  return {
    version: 1,
    id: "pivot_1",
    name: "Pivot 1",
    enabled: true,
    filters: [],
    columns: [],
    rows: [],
    values: [
      {
        id: "value_1",
        field: "amount",
        aggregation: "sum",
        display_name: "Amount",
      },
      {
        id: "value_2",
        field: "count",
        aggregation: "count",
        display_name: "Count",
      },
      {
        id: "value_3",
        field: "rate",
        aggregation: "average",
        display_name: "Rate",
      },
    ],
    options: { row_grand_totals: true, column_grand_totals: true },
    ...overrides,
  }
}

function configured(
  sourcePivot = pivot(),
  overrides: Partial<ExploreChartConfig> = {},
): ExploreChartConfig {
  return {
    ...createExploreChart([]),
    pivot_id: sourcePivot.id,
    value_encodings: seedValueEncodings(sourcePivot),
    ...overrides,
  }
}

function mutableRecord(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Expected a mutable record fixture")
  }
  return value as Record<string, unknown>
}

function firstMutableRecord(value: unknown): Record<string, unknown> {
  if (!Array.isArray(value) || value.length === 0) {
    throw new Error("Expected a non-empty mutable list fixture")
  }
  return mutableRecord(value[0])
}

describe("chart config", () => {
  it("migrates only the versionless v0 shape and deeply detaches future literals", () => {
    const future = { nested: ["value", { count: 1 }] }
    const result = parseExploreCharts({
      charts: [{ id: "chart_1", enabled: false, future }],
    })

    expect(result).toMatchObject({
      ok: true,
      charts: [
        {
          version: 1,
          id: "chart_1",
          name: "Chart 1",
          enabled: false,
          pivot_id: null,
          kind: "combo",
          value_encodings: [],
          series_overrides: [],
        },
      ],
    })
    future.nested[0] = "changed"
    if (result.ok) {
      expect(
        (result.charts[0].future as { nested: unknown[] }).nested[0],
      ).toBe("value")
    }
    expect(
      parseExploreCharts({
        charts: [{ id: "chart_1", enabled: true, name: "not-v0" }],
      }),
    ).toMatchObject({ ok: false, error: expect.stringMatching(/versionless/i) })
  })

  it("allocates legacy names around existing version-1 chart names", () => {
    const existing = configured(pivot(), {
      id: "configured",
      name: "Chart 1",
    })

    const parsed = parseExploreCharts({
      charts: [
        { id: "legacy_a", enabled: true },
        existing,
        { id: "legacy_b", enabled: false },
      ],
    })

    expect(parsed).toMatchObject({
      ok: true,
      charts: [
        { name: "Chart 2" },
        { name: "Chart 1" },
        { name: "Chart 3" },
      ],
    })
  })

  it("validates and detaches a complete v1 card with nested future literals", () => {
    const raw = configured(pivot(), {
      future: { nested: [1] },
      category: {
        source: "rows",
        include_subtotals: true,
        include_grand_total: true,
        label_rotation: -45,
        future_category: ["safe"],
      },
      value_encodings: [
        {
          ...seedValueEncodings(pivot())[0],
          future_style: { safe: true },
        },
        ...seedValueEncodings(pivot()).slice(1),
      ],
      axes: {
        primary: {
          title: "Paid",
          minimum: -1,
          maximum: 10,
          number_format: "currency_gbp",
          future_axis: [1],
        },
        secondary: {
          title: "",
          minimum: null,
          maximum: null,
          number_format: "integer",
        },
        future_axes: "safe",
      },
      legend: {
        visible: true,
        position: "right",
        future_legend: 1,
      },
    })
    const parsed = parseExploreCharts({ charts: [raw] })

    expect(parsed).toEqual({ ok: true, charts: [raw] })
    if (!parsed.ok) throw new Error(parsed.error)
    expect(parsed.charts[0]).not.toBe(raw)
    expect(parsed.charts[0].category).not.toBe(raw.category)
    expect(parsed.charts[0].value_encodings[0]).not.toBe(
      raw.value_encodings[0],
    )
  })

  it.each([
    ["version", (chart: Record<string, unknown>) => { chart.version = 2 }],
    ["required", (chart: Record<string, unknown>) => { delete chart.pivot_id }],
    ["category", (chart: Record<string, unknown>) => {
      mutableRecord(chart.category).label_rotation = 91
    }],
    ["mark", (chart: Record<string, unknown>) => {
      firstMutableRecord(chart.value_encodings).mark = "pie"
    }],
    ["stack", (chart: Record<string, unknown>) => {
      const encoding = firstMutableRecord(chart.value_encodings)
      encoding.mark = "line"
      encoding.stack_group = "stack"
    }],
    ["color", (chart: Record<string, unknown>) => {
      firstMutableRecord(chart.value_encodings).color = "red"
    }],
    ["misplaced encoding identity", (chart: Record<string, unknown>) => {
      firstMutableRecord(chart.value_encodings).series_key = "known-wrong-shape"
    }],
    ["misplaced override identity", (chart: Record<string, unknown>) => {
      const override = structuredClone(firstMutableRecord(chart.value_encodings))
      override.id = "override_1"
      override.series_key = "series_1"
      chart.series_overrides = [override]
    }],
    ["axis", (chart: Record<string, unknown>) => {
      mutableRecord(mutableRecord(chart.axes).primary).minimum = true
    }],
    ["minimum", (chart: Record<string, unknown>) => {
      const primary = mutableRecord(mutableRecord(chart.axes).primary)
      primary.minimum = 2
      primary.maximum = 1
    }],
    ["legend", (chart: Record<string, unknown>) => {
      mutableRecord(chart.legend).position = "centre"
    }],
  ])("rejects invalid known %s fields", (_case, mutate) => {
    const raw = structuredClone(configured()) as Record<string, unknown>
    mutate(raw)
    expect(parseExploreCharts({ charts: [raw] }).ok).toBe(false)
  })

  it("rejects duplicate card/nested identities, duplicate names, and complex future values", () => {
    const first = configured()
    expect(parseExploreCharts({ charts: [first, first] })).toMatchObject({
      ok: false,
      error: expect.stringMatching(/duplicate chart id/i),
    })
    expect(
      parseExploreCharts({
        charts: [first, { ...first, id: "other", name: " chart 1 " }],
      }),
    ).toMatchObject({
      ok: false,
      error: expect.stringMatching(/duplicate chart name/i),
    })
    expect(
      parseExploreCharts({ charts: [{ ...first, future: new Date() }] }),
    ).toMatchObject({ ok: false })
    expect(
      parseExploreCharts({
        charts: [
          {
            ...first,
            value_encodings: [
              first.value_encodings[0],
              { ...first.value_encodings[1], id: first.value_encodings[0].id },
              first.value_encodings[2],
            ],
          },
        ],
      }),
    ).toMatchObject({ ok: false, error: expect.stringMatching(/duplicate/i) })
  })

  it("allocates first-unused ids/names and resolves sources without fallback", () => {
    const source = pivot({ enabled: false })
    const one = configured(source)
    const three = {
      ...configured(source),
      id: "chart_3",
      name: "Chart 3",
    }

    expect(nextExploreChartId([one, three])).toBe("chart_2")
    expect(nextExploreChartName([one, three])).toBe("Chart 2")
    expect(createExploreChart([one, three])).toMatchObject({
      id: "chart_2",
      name: "Chart 2",
      pivot_id: null,
    })
    expect(resolveExploreChartSource(one, [source])).toEqual({
      status: "resolved",
      pivot: source,
    })
    expect(
      resolveExploreChartSource({ ...one, pivot_id: null }, [source]),
    ).toEqual({ status: "draft" })
    expect(
      resolveExploreChartSource({ ...one, pivot_id: "missing" }, [source]),
    ).toEqual({ status: "missing", pivotId: "missing" })
    expect(dependentChartsForPivot([one, three], source.id)).toEqual([
      one,
      three,
    ])
  })

  it.each([
    ["clustered_columns", ["column", "column", "column"], [null, null, null], ["primary", "primary", "primary"]],
    ["stacked_columns", ["column", "column", "column"], ["stack_1", "stack_1", "stack_1"], ["primary", "primary", "primary"]],
    ["lines", ["line", "line", "line"], [null, null, null], ["primary", "primary", "primary"]],
    ["column_line", ["column", "column", "line"], [null, null, null], ["primary", "primary", "primary"]],
    ["column_line_secondary", ["column", "column", "line"], [null, null, null], ["primary", "primary", "secondary"]],
    ["stacked_column_line", ["column", "column", "line"], ["stack_1", "stack_1", null], ["primary", "primary", "primary"]],
  ] as const)(
    "applies the %s preset atomically",
    (preset, marks, stacks, axes) => {
      const source = pivot()
      const before = configured(source, {
        series_overrides: [
          {
            id: "override_1",
            series_key: "old",
            mark: "line",
            axis: "secondary",
            stack_group: null,
            color: null,
            data_labels: false,
            markers: true,
          },
        ],
      })
      const next = applyChartPreset(before, preset as ChartPreset, source)

      expect(next.value_encodings.map(({ mark }) => mark)).toEqual(marks)
      expect(next.value_encodings.map(({ stack_group }) => stack_group)).toEqual(
        stacks,
      )
      expect(next.value_encodings.map(({ axis }) => axis)).toEqual(axes)
      expect(next.series_overrides).toEqual([])
      expect(next.value_encodings.map(({ id }) => id)).toEqual(
        before.value_encodings.map(({ id }) => id),
      )
    },
  )

  it("produces canonical typed series keys", () => {
    const stringKey = exploreChartSeriesKey("value_1", {
      members: [{ kind: "string", value: "1" }],
    })
    const integerKey = exploreChartSeriesKey("value_1", {
      members: [{ kind: "integer", value: "1" }],
    })
    expect(JSON.parse(stringKey)).toEqual({
      version: 1,
      value_id: "value_1",
      column_path: [{ kind: "string", value: "1" }],
    })
    expect(stringKey).not.toBe(integerKey)
  })
})
