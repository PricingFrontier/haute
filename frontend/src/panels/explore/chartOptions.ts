import { PIVOT_CHART_COLORS } from "../../theme/colors"
import type { ExploreChartConfig } from "./chartConfig"
import { formatChartValue, type PivotChartData } from "./chartData"

export type ChartThemeTokens = {
  background: string
  text: string
  muted: string
  grid: string
  series: readonly string[]
}

type BuildOptionsInput = {
  chart: ExploreChartConfig
  data: PivotChartData
  tokens: ChartThemeTokens
  reducedMotion: boolean
}

const DEFAULT_SERIES_COLOURS = PIVOT_CHART_COLORS.defaultSeries

function seriesColour(
  seriesIndex: number,
  explicit: string | null,
  colours: readonly string[],
): string {
  if (explicit !== null) return explicit
  const palette = colours.length > 0 ? colours : DEFAULT_SERIES_COLOURS
  return palette[seriesIndex % palette.length] ?? DEFAULT_SERIES_COLOURS[0]
}

function automaticBounds(
  chart: ExploreChartConfig,
  data: PivotChartData,
  axis: "primary" | "secondary",
): {
  min?: number | ((extent: { min: number; max: number }) => number)
  max?: number | ((extent: { min: number; max: number }) => number)
} {
  const config = chart.axes[axis]
  const axisSeries = data.series.filter((series) => series.style.axis === axis)
  const hasColumns = axisSeries.some((series) => series.style.mark === "column")

  if (!hasColumns) {
    return {
      ...(config.minimum === null ? {} : { min: config.minimum }),
      ...(config.maximum === null ? {} : { max: config.maximum }),
    }
  }

  return {
    min: config.minimum ?? ((extent) => Math.min(0, extent.min)),
    max: config.maximum ?? ((extent) => Math.max(0, extent.max)),
  }
}

function legendPosition(
  position: ExploreChartConfig["legend"]["position"],
): Record<string, unknown> {
  if (position === "top") {
    return { top: 30, left: "center", orient: "horizontal", width: "80%" }
  }
  if (position === "bottom") {
    return { bottom: 0, left: "center", orient: "horizontal", width: "80%" }
  }
  if (position === "left") {
    return { left: 0, top: 52, orient: "vertical", width: 96, height: "70%" }
  }
  return { right: 0, top: 52, orient: "vertical", width: 96, height: "70%" }
}

/** Builds a renderer-independent, SVG-safe ECharts option object. */
export function buildComboChartOptions({
  chart,
  data,
  tokens,
  reducedMotion,
}: BuildOptionsInput): Record<string, unknown> {
  const series = data.series.map((entry, seriesIndex) => {
    const colour = seriesColour(seriesIndex, entry.style.color, tokens.series)
    const isColumn = entry.style.mark === "column"
    const isArea = entry.style.mark === "area"
    const numberFormat = chart.axes[entry.style.axis].number_format
    return {
      name: entry.name,
      type: isColumn ? "bar" : "line",
      yAxisIndex: entry.style.axis === "primary" ? 0 : 1,
      data: entry.values,
      ...(isColumn && entry.style.stack_group !== null ? { stack: entry.style.stack_group } : {}),
      ...(isColumn ? { itemStyle: { color: colour } } : { lineStyle: { color: colour } }),
      ...(isArea ? { areaStyle: { color: colour, opacity: 0.2 } } : {}),
      ...(isColumn ? {} : { showSymbol: entry.style.markers }),
      label: {
        show: entry.style.data_labels,
        color: tokens.text,
        formatter: ({ dataIndex }: { dataIndex?: number }) =>
          dataIndex === undefined ? "" : (entry.formattedValues[dataIndex] ?? ""),
      },
      tooltip: {
        valueFormatter: (value: unknown) =>
          typeof value === "number" && Number.isFinite(value)
            ? formatChartValue(value, numberFormat)
            : "—",
      },
      connectNulls: false,
    }
  })
  const primaryBounds = automaticBounds(chart, data, "primary")
  const secondaryBounds = automaticBounds(chart, data, "secondary")
  const hasManyCategories = data.categories.length > 12
  const hasSecondarySeries = data.series.some((entry) => entry.style.axis === "secondary")
  const visibleLegendPosition = chart.legend.visible ? chart.legend.position : null

  return {
    backgroundColor: tokens.background,
    textStyle: { color: tokens.text },
    animation: !reducedMotion,
    aria: { enabled: true, decal: { show: true } },
    title: { text: chart.name, textStyle: { color: tokens.text } },
    tooltip: {
      trigger: "axis",
      renderMode: "richText",
      confine: true,
      textStyle: { color: tokens.text },
    },
    legend: {
      show: chart.legend.visible,
      type: "scroll",
      textStyle: { color: tokens.text },
      ...legendPosition(chart.legend.position),
    },
    grid: {
      top: visibleLegendPosition === "top" ? 68 : 52,
      right: visibleLegendPosition === "right" ? 120 : 32,
      bottom:
        visibleLegendPosition === "bottom" ? (hasManyCategories ? 88 : 64) : hasManyCategories ? 76 : 48,
      left: visibleLegendPosition === "left" ? 120 : 56,
      containLabel: true,
    },
    xAxis: {
      type: "category",
      data: data.categories.map((category) => category.label),
      axisLabel: { rotate: chart.category.label_rotation, color: tokens.muted },
      axisLine: { lineStyle: { color: tokens.grid } },
    },
    yAxis: [
      {
        type: "value",
        name: chart.axes.primary.title,
        nameTextStyle: { color: tokens.muted },
        axisLabel: {
          color: tokens.muted,
          formatter: (value: unknown) =>
            typeof value === "number"
              ? formatChartValue(value, chart.axes.primary.number_format)
              : String(value),
        },
        splitLine: { lineStyle: { color: tokens.grid } },
        ...primaryBounds,
      },
      {
        type: "value",
        show: hasSecondarySeries,
        name: chart.axes.secondary.title,
        nameTextStyle: { color: tokens.muted },
        axisLabel: {
          color: tokens.muted,
          formatter: (value: unknown) =>
            typeof value === "number"
              ? formatChartValue(value, chart.axes.secondary.number_format)
              : String(value),
        },
        splitLine: { lineStyle: { color: tokens.grid } },
        ...secondaryBounds,
      },
    ],
    ...(hasManyCategories
      ? {
          dataZoom: [
            { type: "inside", start: 0, end: 100 },
            { type: "slider", start: 0, end: 100 },
          ],
        }
      : {}),
    series,
  }
}
