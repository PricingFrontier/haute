import { describe, expect, it } from "vitest"

import type { PivotChartData } from "../chartData"
import { createExploreChart, type ExploreChartConfig } from "../chartConfig"
import { buildComboChartOptions, type ChartThemeTokens } from "../chartOptions"

const light: ChartThemeTokens = {
  background: "#FFFFFF",
  text: "#111827",
  muted: "#6B7280",
  grid: "#E5E7EB",
  series: ["#2563EB", "#DC2626", "#059669", "#7C3AED"],
}

type AxisExtent = { min: number; max: number }
type GeneratedAxis = {
  name: string
  show?: boolean
  min?: number | ((extent: AxisExtent) => number)
  max?: number | ((extent: AxisExtent) => number)
}
type GeneratedSeries = {
  color?: string
  label: { formatter: (params: { dataIndex?: number }) => string }
  tooltip: { valueFormatter: (value: unknown) => string }
}
type GeneratedOptions = {
  backgroundColor: string
  textStyle: { color: string }
  animation: boolean
  aria: unknown
  tooltip: { renderMode: string }
  xAxis: { data: string[]; axisLabel: { rotate: number } }
  yAxis: GeneratedAxis[]
  legend: Record<string, unknown>
  grid: { top: number; right: number; bottom: number; left: number }
  series: GeneratedSeries[]
}

function evaluateBound(
  bound: GeneratedAxis["min"],
  extent: AxisExtent,
): number {
  if (typeof bound !== "function") throw new Error("Expected an automatic axis bound")
  return bound(extent)
}

function configuredChart(overrides: Partial<ExploreChartConfig> = {}): ExploreChartConfig {
  return {
    ...createExploreChart([]),
    name: "Claims <script>alert(1)</script>",
    pivot_id: "pivot_1",
    category: {
      source: "rows",
      include_grand_total: false,
      label_rotation: 45,
    },
    legend: { visible: true, position: "right" },
    axes: {
      primary: {
        title: "Paid",
        minimum: null,
        maximum: null,
        number_format: "currency_gbp",
      },
      secondary: {
        title: "Claims",
        minimum: 1,
        maximum: 10,
        number_format: "integer",
      },
    },
    ...overrides,
  }
}

function dataset(): PivotChartData {
  return {
    categories: [
      {
        key: "north",
        label: "North <img src=x onerror=alert(1)>",
        rowIndex: 0,
        path: { members: [], is_grand_total: false },
      },
      {
        key: "south",
        label: "South",
        rowIndex: 1,
        path: { members: [], is_grand_total: false },
      },
    ],
    series: [
      {
        key: "paid",
        id: "encoding_paid",
        valueId: "paid",
        name: "Paid",
        columnIndex: 0,
        style: {
          mark: "column",
          axis: "primary",
          stack_group: "paid-stack",
          color: null,
          data_labels: true,
          markers: false,
        },
        values: [5, -2],
        formattedValues: ["£5.00", "-£2.00"],
      },
      {
        key: "claims",
        id: "encoding_claims",
        valueId: "claims",
        name: "Claims",
        columnIndex: 0,
        style: {
          mark: "line",
          axis: "secondary",
          stack_group: null,
          color: "#AABBCC",
          data_labels: false,
          markers: true,
        },
        values: [null, 8],
        formattedValues: [null, "8"],
      },
      {
        key: "rate",
        id: "encoding_rate",
        valueId: "rate",
        name: "Rate",
        columnIndex: 0,
        style: {
          mark: "area",
          axis: "primary",
          stack_group: null,
          color: null,
          data_labels: false,
          markers: false,
        },
        values: [0.2, 0.3],
        formattedValues: ["20%", "30%"],
      },
    ],
    dormantOverrideIds: [],
    dormantEncodingIds: [],
    warnings: [],
  }
}

describe("ComboChart option builder", () => {
  it("maps closed column/line/area styles, stacks, axes, labels, and null gaps", () => {
    const options = buildComboChartOptions({
      chart: configuredChart(),
      data: dataset(),
      tokens: light,
      reducedMotion: false,
    }) as unknown as GeneratedOptions

    expect(options.xAxis.data).toEqual(["North <img src=x onerror=alert(1)>", "South"])
    expect(options.xAxis.axisLabel.rotate).toBe(45)
    expect(options.series).toMatchObject([
      {
        type: "bar",
        stack: "paid-stack",
        yAxisIndex: 0,
        data: [5, -2],
        label: { show: true },
        color: light.series[0],
      },
      {
        type: "line",
        yAxisIndex: 1,
        data: [null, 8],
        showSymbol: true,
        color: "#AABBCC",
      },
      {
        type: "line",
        yAxisIndex: 0,
        data: [0.2, 0.3],
        color: light.series[2],
        areaStyle: expect.any(Object),
      },
    ])
    expect(options.yAxis[0]).toMatchObject({
      name: "Paid",
      min: expect.any(Function),
      max: expect.any(Function),
    })
    expect(evaluateBound(options.yAxis[0].min, { min: -2, max: 5 })).toBe(-2)
    expect(evaluateBound(options.yAxis[0].max, { min: -2, max: 5 })).toBe(5)
    expect(options.yAxis[1]).toMatchObject({ name: "Claims", min: 1, max: 10 })
    expect(options.legend).toMatchObject({
      show: true,
      type: "scroll",
      orient: "vertical",
      right: 0,
      width: 96,
    })
    expect(options.grid).toMatchObject({ right: 120, left: 56 })
    expect(options.series[0].label.formatter({ dataIndex: 0 })).toBe("£5.00")
    expect(options.series[0].tooltip.valueFormatter(5)).toMatch(/^£5/)
    expect(options.series[1].tooltip.valueFormatter(null)).toBe("—")
  })

  it("uses rich text rather than HTML, deterministic theme colours, and reduced motion", () => {
    const dark: ChartThemeTokens = {
      ...light,
      background: "#111827",
      text: "#F9FAFB",
      grid: "#374151",
      series: ["#60A5FA", "#F87171"],
    }
    const options = buildComboChartOptions({
      chart: configuredChart(),
      data: dataset(),
      tokens: dark,
      reducedMotion: true,
    }) as unknown as GeneratedOptions

    expect(options.backgroundColor).toBe(dark.background)
    expect(options.textStyle.color).toBe(dark.text)
    expect(options.tooltip.renderMode).toBe("richText")
    expect(options.animation).toBe(false)
    expect(options.aria).toMatchObject({ enabled: true, decal: { show: true } })
    expect(options.series[0].color).toBe(dark.series[0])
    expect(options.series[2].color).toBe(dark.series[0])

    const repeatedValueData = dataset()
    repeatedValueData.series[1] = {
      ...repeatedValueData.series[1],
      valueId: repeatedValueData.series[0].valueId,
      style: { ...repeatedValueData.series[1].style, color: null },
    }
    const repeatedValueOptions = buildComboChartOptions({
      chart: configuredChart(),
      data: repeatedValueData,
      tokens: light,
      reducedMotion: false,
    }) as unknown as GeneratedOptions
    expect(repeatedValueOptions.series[0].color).not.toBe(
      repeatedValueOptions.series[1].color,
    )
  })

  it("includes zero for automatic column axes but does not force line-only axes", () => {
    const data = dataset()
    data.series[0].values = [4, 9]
    data.series[1].style.axis = "primary"
    data.series[1].values = [100, 8]
    data.series[2].style.axis = "secondary"
    const options = buildComboChartOptions({
      chart: configuredChart({
        axes: {
          primary: {
            title: "",
            minimum: null,
            maximum: null,
            number_format: "number",
          },
          secondary: {
            title: "",
            minimum: null,
            maximum: null,
            number_format: "number",
          },
        },
      }),
      data,
      tokens: light,
      reducedMotion: false,
    }) as unknown as GeneratedOptions

    expect(evaluateBound(options.yAxis[0].min, { min: 4, max: 100 })).toBe(0)
    expect(evaluateBound(options.yAxis[0].max, { min: 4, max: 100 })).toBe(100)
    expect(options.yAxis[1].min).toBeUndefined()
    expect(options.yAxis[1].max).toBeUndefined()
  })

  it("reserves plot space for positioned legends and hides an unused secondary axis", () => {
    const data = dataset()
    for (const series of data.series) series.style.axis = "primary"

    const topOptions = buildComboChartOptions({
      chart: configuredChart({ legend: { visible: true, position: "top" } }),
      data,
      tokens: light,
      reducedMotion: false,
    }) as unknown as GeneratedOptions
    expect(topOptions.legend).toMatchObject({ top: 30, left: "center", width: "80%" })
    expect(topOptions.grid.top).toBe(68)
    expect(topOptions.yAxis[1].show).toBe(false)

    const leftOptions = buildComboChartOptions({
      chart: configuredChart({ legend: { visible: true, position: "left" } }),
      data,
      tokens: light,
      reducedMotion: false,
    }) as unknown as GeneratedOptions
    expect(leftOptions.grid.left).toBe(120)
    expect(leftOptions.grid.right).toBe(32)
  })
})
