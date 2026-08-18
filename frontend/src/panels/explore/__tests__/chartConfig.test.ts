import { describe, expect, it } from "vitest"

import {
  applyChartPreset,
  createExploreChart,
  dependentChartsForPivot,
  detectChartPreset,
  exploreChartSeriesKey,
  nextExploreChartId,
  nextExploreChartName,
  exploreChartSeriesLabel,
  parseExploreCharts,
  reconcileValueEncodings,
  renameChartStackGroup,
  resolveExploreChartSource,
  seedValueEncodings,
  setChartStacking,
  setChartStyleAxis,
  setSecondaryAxisEnabled,
  type ChartPreset,
  type ExploreChartConfig,
} from "../chartConfig"
import type { ExplorePivotConfig } from "../pivotConfig"

function pivot(
  overrides: Partial<ExplorePivotConfig> = {},
): ExplorePivotConfig {
  const values = overrides.values ?? [
    {
      id: "value_1",
      field: "amount",
      aggregation: "sum",
      reference: "amount_sum",
      display_name: "Amount",
    },
    {
      id: "value_2",
      field: "count",
      aggregation: "count",
      reference: "count_count",
      display_name: "Count",
    },
    {
      id: "value_3",
      field: "rate",
      aggregation: "average",
      reference: "rate_mean",
      display_name: "Rate",
    },
  ]
  const formulas = overrides.formulas ?? []
  return {
    version: 1,
    id: "pivot_1",
    name: "Pivot 1",
    enabled: true,
    filters: [],
    columns: [],
    rows: [],
    values,
    formulas,
    options: { row_grand_totals: true, column_grand_totals: true },
    ...overrides,
    value_order: overrides.value_order ?? [...values, ...formulas].map(({ id }) => id),
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
  it("rejects versionless cards instead of migrating them", () => {
    // There is no v0 migration: every persisted card is complete version 1.
    expect(
      parseExploreCharts({
        charts: [{ id: "chart_1", enabled: false }],
      }),
    ).toMatchObject({
      ok: false,
      error: expect.stringMatching(/version must be 1/i),
    })
  })

  it("validates and detaches a complete v1 card with nested future literals", () => {
    const raw = configured(pivot(), {
      future: { nested: [1] },
      category: {
        source: "rows",
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
          enabled: true,
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
      firstMutableRecord(chart.value_encodings).stack_normalize = "yes"
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
    ["malformed series identity", (chart: Record<string, unknown>) => {
      const override = structuredClone(firstMutableRecord(chart.value_encodings))
      delete override.value_id
      override.id = "override_1"
      override.series_key = "not-json"
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
    ["hundred_percent_stacked_columns", ["column", "column", "column"], ["stack_1", "stack_1", "stack_1"], ["primary", "primary", "primary"]],
    ["combo", ["column", "column", "line"], [null, null, null], ["primary", "primary", "primary"]],
  ] as const)(
    "applies the %s preset atomically",
    (preset, marks, stacks, axes) => {
      const source = pivot()
      const before = configured(source, {
        series_overrides: [
          {
            id: "override_1",
            series_key: exploreChartSeriesKey("value_1", []),
            mark: "line",
            axis: "secondary",
            stack_group: null,
            stack_normalize: false,
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

  it("materialises structurally valid override identities into canonical keys", () => {
    const nonCanonicalKey = '{ "column_path": [], "value_id": "value_1", "version": 1 }'
    const override = {
      id: "override_1",
      series_key: nonCanonicalKey,
      mark: "column" as const,
      axis: "primary" as const,
      stack_group: null,
      stack_normalize: false,
      color: null,
      data_labels: false,
      markers: false,
    }
    const raw = configured(pivot(), { series_overrides: [override] })
    const parsed = parseExploreCharts({ charts: [raw] })

    if (!parsed.ok) throw new Error(parsed.error)
    expect(parsed.charts[0].series_overrides[0].series_key).toBe(
      exploreChartSeriesKey("value_1", []),
    )
    expect(
      parseExploreCharts({
        charts: [
          {
            ...raw,
            series_overrides: [
              override,
              {
                ...override,
                id: "override_2",
                series_key: exploreChartSeriesKey("value_1", []),
              },
            ],
          },
        ],
      }),
    ).toMatchObject({ ok: false, error: expect.stringMatching(/duplicate/i) })
  })
})

describe("orientation and stack-normalisation schema", () => {
  function rawChart(): Record<string, unknown> {
    const base = configured()
    return JSON.parse(JSON.stringify(base)) as Record<string, unknown>
  }

  it.each([
    (raw: Record<string, unknown>) => {
      delete raw.orientation
    },
    (raw: Record<string, unknown>) => {
      raw.orientation = null
    },
    (raw: Record<string, unknown>) => {
      const encodings = raw.value_encodings as Record<string, unknown>[]
      delete encodings[0].stack_normalize
    },
    (raw: Record<string, unknown>) => {
      const encodings = raw.value_encodings as Record<string, unknown>[]
      encodings[0].stack_normalize = null
    },
  ])(
    "requires orientation and stack_normalize: absent and null both reject",
    (mutate) => {
      const raw = rawChart()
      mutate(raw)
      expect(parseExploreCharts({ charts: [raw] })).toMatchObject({ ok: false })
    },
  )

  it("accepts horizontal orientation and stacked line/area marks", () => {
    const raw = rawChart()
    raw.orientation = "horizontal"
    const encodings = raw.value_encodings as Record<string, unknown>[]
    encodings[0].mark = "line"
    encodings[0].stack_group = "s"
    encodings[1].mark = "area"
    encodings[1].stack_group = "s"
    const parsed = parseExploreCharts({ charts: [raw] })
    if (!parsed.ok) throw new Error(parsed.error)
    expect(parsed.charts[0].orientation).toBe("horizontal")
    expect(parsed.charts[0].value_encodings[0].stack_group).toBe("s")
  })

  it.each([
    [
      "unsupported orientation",
      (raw: Record<string, unknown>) => {
        raw.orientation = "diagonal"
      },
      /orientation/i,
    ],
    [
      "normalize without a group",
      (raw: Record<string, unknown>) => {
        ;(raw.value_encodings as Record<string, unknown>[])[0].stack_normalize =
          true
      },
      /stack_normalize requires a stack group/i,
    ],
    [
      "mixed normalize within one group",
      (raw: Record<string, unknown>) => {
        const encodings = raw.value_encodings as Record<string, unknown>[]
        encodings[0].stack_group = "s"
        encodings[0].stack_normalize = true
        encodings[1].stack_group = "s"
        encodings[1].stack_normalize = false
      },
      /must agree/i,
    ],
    [
      "mixed axis within one group",
      (raw: Record<string, unknown>) => {
        const encodings = raw.value_encodings as Record<string, unknown>[]
        encodings[0].stack_group = "s"
        encodings[1].stack_group = "s"
        encodings[1].axis = "secondary"
      },
      /must agree/i,
    ],
  ])("rejects %s", (_label, mutate, expected) => {
    const raw = rawChart()
    mutate(raw)
    const parsed = parseExploreCharts({ charts: [raw] })
    expect(parsed.ok).toBe(false)
    if (!parsed.ok) expect(parsed.error).toMatch(expected)
  })
})

describe("secondary axis enablement", () => {
  it("requires the enabled flag and validates it as a boolean", () => {
    const absent = JSON.parse(JSON.stringify(configured())) as Record<
      string,
      unknown
    >
    delete (absent.axes as Record<string, Record<string, unknown>>).secondary
      .enabled
    expect(parseExploreCharts({ charts: [absent] })).toMatchObject({
      ok: false,
      error: expect.stringMatching(/enabled must be a boolean/i),
    })

    for (const invalidValue of [null, 1]) {
      const invalid = JSON.parse(JSON.stringify(configured())) as Record<
        string,
        unknown
      >
      ;(
        (invalid.axes as Record<string, Record<string, unknown>>).secondary
      ).enabled = invalidValue
      expect(parseExploreCharts({ charts: [invalid] }).ok).toBe(false)
    }
  })

  it.each([
    [
      "encoding",
      (chart: ExploreChartConfig): ExploreChartConfig => ({
        ...chart,
        value_encodings: chart.value_encodings.map((encoding, index) =>
          index === 0 ? { ...encoding, axis: "secondary" as const } : encoding,
        ),
      }),
    ],
    [
      "override",
      (chart: ExploreChartConfig): ExploreChartConfig => ({
        ...chart,
        series_overrides: [
          {
            id: "override_1",
            series_key: exploreChartSeriesKey("value_1", []),
            mark: "column" as const,
            axis: "secondary" as const,
            stack_group: null,
            stack_normalize: false,
            color: null,
            data_labels: false,
            markers: false,
          },
        ],
      }),
    ],
  ])("rejects a disabled secondary axis used by an %s", (_collection, use) => {
    const disabledButUsed = JSON.parse(
      JSON.stringify(use(configured())),
    ) as Record<string, unknown>
    ;(
      (disabledButUsed.axes as Record<string, Record<string, unknown>>)
        .secondary
    ).enabled = false
    const rejected = parseExploreCharts({ charts: [disabledButUsed] })
    expect(rejected.ok).toBe(false)
    if (!rejected.ok) {
      expect(rejected.error).toMatch(/secondary axis is disabled/i)
    }
  })

  it("moves secondary styles in both collections to primary when disabling and only flips the flag when enabling", () => {
    const source = pivot()
    const withSecondary = configured(source, {
      value_encodings: seedValueEncodings(source).map((encoding, index) =>
        index === 2
          ? {
              ...encoding,
              axis: "secondary" as const,
              stack_group: "s",
              stack_normalize: false,
            }
          : encoding,
      ),
      series_overrides: [
        {
          id: "override_1",
          series_key: exploreChartSeriesKey("value_1", []),
          mark: "line" as const,
          axis: "secondary" as const,
          stack_group: "s",
          stack_normalize: false,
          color: "#112233",
          data_labels: true,
          markers: true,
        },
      ],
    })

    const disabled = setSecondaryAxisEnabled(withSecondary, false)
    expect(disabled.axes.secondary.enabled).toBe(false)
    expect(disabled.value_encodings[2]).toMatchObject({
      axis: "primary",
      stack_group: null,
      stack_normalize: false,
    })
    expect(disabled.series_overrides[0]).toMatchObject({
      axis: "primary",
      stack_group: null,
      stack_normalize: false,
      color: "#112233",
      data_labels: true,
      markers: true,
    })
    expect(parseExploreCharts({ charts: [disabled] }).ok).toBe(true)

    const reEnabled = setSecondaryAxisEnabled(disabled, true)
    expect(reEnabled.axes.secondary.enabled).toBe(true)
    expect(reEnabled.value_encodings[2].axis).toBe("primary")
    expect(reEnabled.series_overrides[0].axis).toBe("primary")

    expect(setSecondaryAxisEnabled(reEnabled, true)).toBe(reEnabled)
  })

  it("leaves a disabled secondary axis untouched across preset application", () => {
    const source = pivot()
    const disabled = setSecondaryAxisEnabled(configured(source), false)
    const applied = applyChartPreset(disabled, "combo", source)
    expect(applied.axes.secondary.enabled).toBe(false)
    expect(
      applied.value_encodings.every(({ axis }) => axis === "primary"),
    ).toBe(true)
  })
})

describe("presets and detection", () => {
  const multiValue = pivot()

  it("applies the 100% preset with a normalised stack and percent primary axis", () => {
    const chart = configured(multiValue)
    const applied = applyChartPreset(
      chart,
      "hundred_percent_stacked_columns",
      multiValue,
    )
    expect(
      applied.value_encodings.map(
        ({ mark, stack_group, stack_normalize }) => ({
          mark,
          stack_group,
          stack_normalize,
        }),
      ),
    ).toEqual([
      { mark: "column", stack_group: "stack_1", stack_normalize: true },
      { mark: "column", stack_group: "stack_1", stack_normalize: true },
      { mark: "column", stack_group: "stack_1", stack_normalize: true },
    ])
    expect(applied.axes.primary.number_format).toBe("percent")
  })

  it("resets a percent primary format to inherit on non-100% presets and preserves others", () => {
    const chart = configured(multiValue)
    const hundredPercent = applyChartPreset(
      chart,
      "hundred_percent_stacked_columns",
      multiValue,
    )
    const backToClustered = applyChartPreset(
      hundredPercent,
      "clustered_columns",
      multiValue,
    )
    expect(backToClustered.axes.primary.number_format).toBe("inherit")

    const currency: ExploreChartConfig = {
      ...chart,
      axes: {
        ...chart.axes,
        primary: { ...chart.axes.primary, number_format: "currency_gbp" },
      },
    }
    expect(
      applyChartPreset(currency, "stacked_columns", multiValue).axes.primary
        .number_format,
    ).toBe("currency_gbp")
  })

  it("preserves orientation across preset application", () => {
    const chart: ExploreChartConfig = {
      ...configured(multiValue),
      orientation: "horizontal",
    }
    expect(
      applyChartPreset(chart, "stacked_columns", multiValue).orientation,
    ).toBe("horizontal")
  })

  it("seeds the Combo default: columns with the last Value as a line", () => {
    const seeded = seedValueEncodings(pivot())
    expect(seeded.map(({ mark, markers }) => ({ mark, markers }))).toEqual([
      { mark: "column", markers: false },
      { mark: "column", markers: false },
      { mark: "line", markers: true },
    ])
    expect(detectChartPreset(configured(pivot()))).toBe("combo")

    const single = pivot({ values: [pivot().values[0]] })
    expect(seedValueEncodings(single)).toHaveLength(1)
    expect(seedValueEncodings(single)[0]).toMatchObject({
      mark: "column",
      markers: false,
    })
  })

  it("round-trips every preset through detection on a multi-Value pivot", () => {
    const chart = configured(multiValue)
    const presets: ChartPreset[] = [
      "clustered_columns",
      "stacked_columns",
      "hundred_percent_stacked_columns",
      "combo",
    ]
    for (const preset of presets) {
      expect(
        detectChartPreset(applyChartPreset(chart, preset, multiValue)),
      ).toBe(preset)
    }
    // On a single-Value chart Combo's seed is one plain column — starting
    // from a fully styled state (line, secondary axis, normalised stack,
    // markers) so every asserted reset transition is exercised.
    const singleValue = pivot({ values: [pivot().values[0]] })
    const single = configured(singleValue, {
      value_encodings: [
        {
          ...seedValueEncodings(singleValue)[0],
          mark: "line" as const,
          axis: "secondary" as const,
          stack_group: "s",
          stack_normalize: true,
          markers: true,
        },
      ],
    })
    const applied = applyChartPreset(single, "combo", singleValue)
    expect(applied.value_encodings[0]).toMatchObject({
      mark: "column",
      axis: "primary",
      stack_group: null,
      stack_normalize: false,
      markers: false,
    })
    expect(detectChartPreset(applied)).toBe("clustered_columns")
  })

  it("ignores presentation-only fields and stack-group names in detection", () => {
    const stacked = applyChartPreset(
      configured(multiValue),
      "stacked_columns",
      multiValue,
    )
    const renamedAndRecoloured: ExploreChartConfig = {
      ...stacked,
      value_encodings: stacked.value_encodings.map((encoding) => ({
        ...encoding,
        stack_group: "actuarial",
        color: "#112233",
        markers: true,
        data_labels: true,
      })),
    }
    expect(detectChartPreset(renamedAndRecoloured)).toBe("stacked_columns")
  })

  it("reports everything outside the column layouts as the general combo category", () => {
    // The Combo default already seeds a trailing line, so each case starts
    // from an all-column baseline and changes exactly one dimension to prove
    // every predicate independently.
    const allColumns = seedValueEncodings(multiValue).map((encoding) => ({
      ...encoding,
      mark: "column" as const,
      markers: false,
    }))
    expect(
      detectChartPreset(
        configured(multiValue, { value_encodings: allColumns }),
      ),
    ).toBe("clustered_columns")

    const mixedMarks = configured(multiValue, {
      value_encodings: allColumns.map((encoding, index) =>
        index === 2 ? { ...encoding, mark: "line" as const } : encoding,
      ),
    })
    expect(detectChartPreset(mixedMarks)).toBe("combo")

    const secondaryAssignment = configured(multiValue, {
      value_encodings: allColumns.map((encoding, index) =>
        index === 2 ? { ...encoding, axis: "secondary" as const } : encoding,
      ),
    })
    expect(detectChartPreset(secondaryAssignment)).toBe("combo")

    const allLines = configured(multiValue, {
      value_encodings: allColumns.map((encoding) => ({
        ...encoding,
        mark: "line" as const,
      })),
    })
    expect(detectChartPreset(allLines)).toBe("combo")

    const withArea = configured(multiValue, {
      value_encodings: allColumns.map((encoding, index) =>
        index === 0 ? { ...encoding, mark: "area" as const } : encoding,
      ),
    })
    expect(detectChartPreset(withArea)).toBe("combo")
    expect(
      detectChartPreset(configured(multiValue, { value_encodings: [] })),
    ).toBe("combo")
  })
})

describe("stacking transitions", () => {
  function stackedChart(): ExploreChartConfig {
    const base = configured()
    return {
      ...base,
      value_encodings: base.value_encodings.map((encoding, index) => ({
        ...encoding,
        stack_group: index < 2 ? "stack_1" : null,
        stack_normalize: false,
      })),
    }
  }

  it("joins the sole same-axis group and rewrites its mode group-wide", () => {
    const chart = stackedChart()

    const joined = setChartStacking(chart, "encoding_3", "stacked")
    expect(
      joined.value_encodings.map(({ stack_group, stack_normalize }) => ({
        stack_group,
        stack_normalize,
      })),
    ).toEqual([
      { stack_group: "stack_1", stack_normalize: false },
      { stack_group: "stack_1", stack_normalize: false },
      { stack_group: "stack_1", stack_normalize: false },
    ])

    const normalized = setChartStacking(joined, "encoding_1", "normalized")
    expect(
      normalized.value_encodings.every(
        ({ stack_group, stack_normalize }) =>
          stack_group === "stack_1" && stack_normalize === true,
      ),
    ).toBe(true)
  })

  it("clears only the chosen series on none and allocates fresh groups per axis", () => {
    const chart = stackedChart()

    const cleared = setChartStacking(chart, "encoding_2", "none")
    expect(
      cleared.value_encodings.map(({ stack_group }) => stack_group),
    ).toEqual(["stack_1", null, null])

    const secondaryAxis: ExploreChartConfig = {
      ...chart,
      value_encodings: chart.value_encodings.map((encoding) =>
        encoding.id === "encoding_3"
          ? { ...encoding, axis: "secondary" as const }
          : encoding,
      ),
    }
    const allocated = setChartStacking(secondaryAxis, "encoding_3", "normalized")
    expect(allocated.value_encodings[2]).toMatchObject({
      stack_group: "stack_2",
      stack_normalize: true,
      axis: "secondary",
    })
    expect(allocated.value_encodings[0]).toMatchObject({
      stack_group: "stack_1",
      stack_normalize: false,
    })
  })

  it("clears group membership when a grouped series changes axis", () => {
    const chart = stackedChart()
    const moved = setChartStyleAxis(chart, "encoding_1", "secondary")
    expect(moved.value_encodings[0]).toMatchObject({
      axis: "secondary",
      stack_group: null,
      stack_normalize: false,
    })
    expect(moved.value_encodings[1]).toMatchObject({
      axis: "primary",
      stack_group: "stack_1",
    })
  })

  it("renames whole groups, merges only compatibly, and rejects incompatible renames", () => {
    const chart: ExploreChartConfig = {
      ...stackedChart(),
      value_encodings: stackedChart().value_encodings.map((encoding) =>
        encoding.id === "encoding_3"
          ? {
              ...encoding,
              axis: "secondary" as const,
              stack_group: "other",
              stack_normalize: false,
            }
          : encoding,
      ),
    }

    const renamed = renameChartStackGroup(chart, "encoding_1", "actuarial")
    if (typeof renamed === "string") throw new Error(renamed)
    expect(
      renamed.value_encodings.map(({ stack_group }) => stack_group),
    ).toEqual(["actuarial", "actuarial", "other"])

    const incompatible = renameChartStackGroup(chart, "encoding_1", "other")
    expect(typeof incompatible).toBe("string")

    const compatible: ExploreChartConfig = {
      ...chart,
      value_encodings: chart.value_encodings.map((encoding) =>
        encoding.id === "encoding_3"
          ? { ...encoding, axis: "primary" as const }
          : encoding,
      ),
    }
    const merged = renameChartStackGroup(compatible, "encoding_1", "other")
    if (typeof merged === "string") throw new Error(merged)
    expect(
      merged.value_encodings.map(({ stack_group }) => stack_group),
    ).toEqual(["other", "other", "other"])

    expect(typeof renameChartStackGroup(chart, "encoding_1", "  ")).toBe(
      "string",
    )
  })
})

describe("exploreChartSeriesLabel", () => {
  const sourcePivot = pivot()

  it("decodes canonical keys against the pivot", () => {
    const key = exploreChartSeriesKey("value_1", [
      { kind: "integer", value: "2099" },
    ])
    expect(exploreChartSeriesLabel(key, sourcePivot)).toBe("2099 · Amount")

    const bare = exploreChartSeriesKey("value_2", [])
    expect(exploreChartSeriesLabel(bare, sourcePivot)).toBe("Count")

    const removed = exploreChartSeriesKey("value_gone", [
      { kind: "string", value: "North" },
    ])
    expect(exploreChartSeriesLabel(removed, sourcePivot)).toBe(
      "North · a removed Value",
    )

  })

  it.each([
    ["non-JSON", "not-json"],
    [
      "wrong version type",
      '{"version":true,"value_id":"value_1","column_path":[]}',
    ],
    [
      "extra identity field",
      '{"version":1,"value_id":"value_1","column_path":[],"extra":true}',
    ],
    [
      "invalid typed member",
      '{"version":1,"value_id":"value_1","column_path":[{"kind":"boolean","value":"true"}]}',
    ],
  ])("rejects %s series identities", (_case, seriesKey) => {
    expect(() => exploreChartSeriesLabel(seriesKey, sourcePivot)).toThrow(
      /canonical|invalid/i,
    )
  })
})

describe("reconcileValueEncodings", () => {
  it("treats post-aggregation formulas as chartable Pivot outputs", () => {
    const sourcePivot = pivot({
      formulas: [{
        id: "formula_1",
        reference: "average_cost",
        display_name: "Average cost",
        expression: 'pl.col("amount").sum() / pl.col("count").count()',
        number_format: "number",
        decimal_places: 2,
        use_grouping: true,
      }],
    })

    expect(seedValueEncodings(sourcePivot).map(({ value_id }) => value_id)).toEqual([
      "value_1",
      "value_2",
      "value_3",
      "formula_1",
    ])
  })

  it("returns the same reference when every pivot Value is encoded", () => {
    const sourcePivot = pivot()
    const complete = configured(sourcePivot)
    expect(reconcileValueEncodings(complete, sourcePivot)).toBe(complete)
  })

  it("seeds defaults for unencoded Values in pivot order with first-unused ids", () => {
    const sourcePivot = pivot({
      values: [
        ...pivot().values,
        {
          id: "value_4",
          field: "exposure",
          aggregation: "sum",
          reference: "exposure_sum",
          display_name: "Exposure",
        },
      ],
    })
    const trailing = configured(sourcePivot, {
      value_encodings: [
        {
          id: "encoding_1",
          value_id: "value_1",
          mark: "line",
          axis: "secondary",
          stack_group: null,
          stack_normalize: false,
          color: "#112233",
          data_labels: true,
          markers: true,
        },
        {
          id: "encoding_3",
          value_id: "value_2",
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
          id: "encoding_2",
          series_key: exploreChartSeriesKey("value_1", []),
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
    const snapshot = structuredClone(trailing)

    const reconciled = reconcileValueEncodings(trailing, sourcePivot)

    expect(trailing).toEqual(snapshot)
    expect(
      reconciled.value_encodings.map(({ id, value_id }) => ({ id, value_id })),
    ).toEqual([
      { id: "encoding_1", value_id: "value_1" },
      { id: "encoding_3", value_id: "value_2" },
      { id: "encoding_4", value_id: "value_3" },
      { id: "encoding_5", value_id: "value_4" },
    ])
    expect(reconciled.value_encodings[2]).toMatchObject({
      mark: "column",
      axis: "primary",
      stack_group: null,
      color: null,
      data_labels: false,
      markers: false,
    })
    expect(reconciled.value_encodings[0]).toEqual(trailing.value_encodings[0])
    expect(reconciled.series_overrides).toEqual(trailing.series_overrides)
  })
})
