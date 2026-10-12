/**
 * What a Chart plots and how it draws (specs/workbench): its fields' values as the sheet
 * holds them, the sample while building or the quote in Preview, with what pricing them
 * gave, and the ECharts option that draws them. A Chart of one-row tables plots a point
 * per field; a Chart of many-row tables a series per field over the rows, lined up as a
 * Table lines its rows up. Every value is shown in its column's format. Pure: the view
 * draws the option through Explore's chart runtime.
 */
import type { ChartWidget, FormSchema, FormSpec, SchemaColumn, SchemaTable } from "../api/types"
import type { ChartThemeTokens } from "../panels/explore/chartOptions"
import { formatChartNumber } from "./chartHelpers"
import { formatValue } from "./formatValue"
import { formattedNumber, parseTypedNumber, rowFilled, type PricedRows, type RowsByTable } from "./sheetValues"
import {
  chartGrain,
  fieldColumn,
  linedUpOutputs,
  plottable,
  readableName,
  shownFields,
  tableGrain,
  type SampleValue,
} from "./workbenchForm"

/** A schema column with its table. */
type Found = { table: SchemaTable; column: SchemaColumn }

/** A series: one field's values over the rows, or a one-row Chart's points, each as its column shows it. */
export interface ChartSeries {
  /** What identifies it from one option to the next: its field's key, or `points`. */
  key: string
  name: string
  /** Each value, or null for a gap. */
  values: (number | null)[]
  /** Each value as its column shows it, or null for a gap. */
  labels: (string | null)[]
}

/** What a Chart plots. */
export interface ChartData {
  /** One-row tables, a point per field, or many-row tables, a series per field over the rows. */
  rows: "one" | "many"
  /** What names each column, point or slice: the fields' labels, or the rows'. */
  categories: string[]
  series: ChartSeries[]
  /** The column whose format the value axis takes, every plotted column sharing it; null for compact numbers. */
  axis: SchemaColumn | null
  /** It plots a price for other values. */
  stale: boolean
}

/** The sheet's values a Chart reads, as `useSheetValues` gives them. */
export interface ChartValues {
  rows: RowsByTable
  price: { readonly basis: string; readonly sample: Readonly<Record<string, unknown>>; readonly tables: Readonly<Record<string, PricedRows>> } | null
  basis: string
  quoted: FormSchema
}

const ONE_ROW_SERIES = "points"
/** What a row with nothing to name it by, or a priced row with no value there, is named. */
const UNNAMED = "—"

const columnLabel = (column: SchemaColumn): string => column.label || readableName(column.name)

/** A value as its column shows it: in its format, else as the data preview shows one. */
const shownValue = (column: SchemaColumn, value: unknown): string => formattedNumber(column, value) ?? formatValue(value)

/** A typed value as a number, as the server reads it, or null. */
const typedNumber = (value: SampleValue | undefined): number | null => (typeof value === "string" ? parseTypedNumber(value) : null)

/** A priced value as a number, or null for anything else. */
const pricedNumber = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null)

const keyed = (row: Record<string, unknown> | undefined, name: string): boolean => row?.[name] !== undefined && row[name] !== null

/** The column every one of `columns` shares a format with, or null when they differ or there are none. */
function sharedFormat(columns: readonly SchemaColumn[]): SchemaColumn | null {
  const [first] = columns
  if (first === undefined) return null
  const same = columns.every(
    (column) =>
      column.number_format === first.number_format &&
      column.decimal_places === first.decimal_places &&
      column.use_grouping === first.use_grouping,
  )
  return same ? first : null
}

/** The distinct tables of `found`, in order, that `keep` keeps. */
const tablesOf = (found: readonly Found[], keep: (table: SchemaTable) => boolean): SchemaTable[] => [
  ...new Map(found.filter(({ table }) => keep(table)).map(({ table }) => [table.id, table])).values(),
]

/** What a Chart's fields hold on the sheet (specs/workbench, Charts). */
export function sheetChartData(spec: FormSpec, widget: ChartWidget, values: ChartValues): ChartData {
  const { rows: held, price, basis, quoted } = values
  const grain = chartGrain(spec, widget)
  const lined = (found: Found | null): found is Found => found !== null && tableGrain(found.table) === grain
  // A field it cannot plot, or whose rows do not line up, plots nothing; the view names it.
  const plotted = shownFields(spec, widget).flatMap(({ key, found }) =>
    lined(found) && plottable(found.column) ? [{ key, ...found }] : [],
  )
  const category = widget.category === null ? null : fieldColumn(spec, widget.category)
  const label = lined(category) ? category : null
  // A column the quote leaves out: the sheets hide it, so it was never typed.
  const quotedColumns = new Set(quoted.tables.flatMap((table) => table.columns.map((column) => `${table.id}:${column.id}`)))
  const inQuote = ({ table, column }: Found): boolean => quotedColumns.has(`${table.id}:${column.id}`)
  // Priced values plotted, or rows named by priced values, are for other values once the basis moves on.
  const priced = plotted.some(({ table }) => table.role === "output") || label?.table.role === "output"
  const stale = price !== null && price.basis !== basis && priced
  const axis = sharedFormat(plotted.map(({ column }) => column))

  if (grain === "one") {
    const points = plotted.map((found) =>
      found.table.role === "input"
        ? inQuote(found)
          ? typedNumber(held[found.table.id]?.[0]?.[found.column.id])
          : null
        : pricedNumber(price?.tables[found.table.name]?.[0]?.[found.column.name]),
    )
    return {
      rows: "one",
      categories: plotted.map(({ column }) => columnLabel(column)),
      series: [
        {
          key: ONE_ROW_SERIES,
          name: widget.title,
          values: points,
          labels: points.map((value, at) => (value === null ? null : shownValue(plotted[at].column, value))),
        },
      ],
      axis,
      stale,
    }
  }

  const shown = label === null ? plotted : [...plotted, label]
  const inputs = tablesOf(shown, (table) => table.role === "input" && table.rows === "many")
  const outputs = tablesOf(shown, (table) => table.role === "output" && table.rows === "many")
  /** Each row's value of a column: a number for a field, the row's name for its label. */
  let valueAt: (found: Found, row: number) => unknown
  let kept: number[]
  if (inputs.length > 0) {
    // The rows typed into the input tables it shows, by position, as a Table's grid has them.
    const count = Math.max(0, ...inputs.map((table) => held[table.id]?.length ?? 0))
    const read = new Map(quoted.tables.map((table) => [table.id, table]))
    kept = Array.from({ length: count }, (_, row) => row).filter((row) =>
      inputs.some((table) => {
        const quotedTable = read.get(table.id)
        return quotedTable !== undefined && rowFilled(quotedTable, held[table.id]?.[row])
      }),
    )
    const matched = linedUpOutputs(inputs, outputs, held, count, price, quoted)
    valueAt = (found, row) =>
      found.table.role === "input"
        ? inQuote(found)
          ? held[found.table.id]?.[row]?.[found.column.id]
          : undefined
        : matched.get(found.table.id)?.[row]?.[found.column.name]
  } else {
    // Output tables alone: the first one's answer, the others' rows matched by key.
    const [first] = outputs
    const answer = first === undefined ? [] : (price?.tables[first.name] ?? [])
    const keys = first === undefined ? [] : first.columns.filter((column) => column.key).map((column) => column.name)
    const rowOf = (table: SchemaTable, row: number): Record<string, unknown> | undefined => {
      const own = answer[row]
      if (table.id === first?.id) return own
      if (keys.length === 0) return undefined
      return (price?.tables[table.name] ?? []).find((candidate) =>
        keys.every((name) => keyed(own, name) && keyed(candidate, name) && String(candidate[name]) === String(own[name])),
      )
    }
    kept = answer.map((_, row) => row)
    valueAt = (found, row) => rowOf(found.table, row)?.[found.column.name]
  }

  const nameOf = (row: number): string => {
    if (label === null) return String(row + 1)
    if (label.table.role === "input") {
      if (label.column.index) return String(row + 1)
      const typed = valueAt(label, row)
      if (label.column.type === "bool") return typed === true ? "Yes" : "No"
      const text = typeof typed === "string" ? typed.trim() : ""
      return text === "" ? UNNAMED : (formattedNumber(label.column, text) ?? text)
    }
    const priced = valueAt(label, row)
    return priced === undefined || priced === null ? UNNAMED : shownValue(label.column, priced)
  }
  const series = plotted.map((found) => {
    const points = kept.map((row) => {
      const value = valueAt(found, row)
      return found.table.role === "input" ? typedNumber(value as SampleValue | undefined) : pricedNumber(value)
    })
    return {
      key: found.key,
      name: columnLabel(found.column),
      values: points,
      labels: points.map((value) => (value === null ? null : shownValue(found.column, value))),
    }
  })
  return {
    rows: "many",
    categories: kept.map(nameOf),
    // A pie of many rows is a slice per row of its first field.
    series: widget.chart_type === "pie" ? series.slice(0, 1) : series,
    axis,
    stale,
  }
}

/** Whether a Chart has a number to plot. */
export const hasValues = (data: ChartData): boolean => data.series.some((entry) => entry.values.some((value) => value !== null))

/**
 * What a Chart says in place of its plot, or null while it plots: "Nothing to chart yet"
 * with no number to plot, and for a pie, whose slices share out a whole, the first value
 * below 0, named, or that every value is 0, rather than slices that would misstate them.
 */
export function chartMessage(widget: ChartWidget, data: ChartData): string | null {
  if (!hasValues(data)) return "Nothing to chart yet"
  const [slices] = data.series
  if (widget.chart_type !== "pie" || slices === undefined) return null
  const negative = slices.values.findIndex((value) => value !== null && value < 0)
  if (negative >= 0) return `A pie can't show a value below 0: ${data.categories[negative]} is ${slices.labels[negative]}`
  return slices.values.some((value) => value !== null && value > 0) ? null : "Every value is 0, so there is no pie to draw"
}

/** Where the legend sits around the plot. */
const LEGEND_PLACES = {
  bottom: { bottom: 0, left: "center", orient: "horizontal" },
  top: { top: 0, left: "center", orient: "horizontal" },
  left: { left: 0, top: "middle", orient: "vertical" },
  right: { right: 0, top: "middle", orient: "vertical" },
} as const

/** A pie's centre, clear of its legend. */
const PIE_CENTRES = {
  bottom: ["50%", "45%"],
  top: ["50%", "55%"],
  left: ["62%", "50%"],
  right: ["38%", "50%"],
  none: ["50%", "50%"],
} as const

type LabelParams = { dataIndex?: number; percent?: number }

/** The ECharts option that draws a Chart's values (specs/workbench, Charts). */
export function sheetChartOptions(widget: ChartWidget, data: ChartData, tokens: ChartThemeTokens, reducedMotion: boolean): Record<string, unknown> {
  const pie = widget.chart_type === "pie"
  const legendShown = widget.legend !== "none" && (pie || data.series.length > 1)
  // On the panel's own colours, so its text reads in either theme.
  const tooltip = {
    renderMode: "richText",
    confine: true,
    backgroundColor: tokens.background,
    borderColor: tokens.grid,
    textStyle: { color: tokens.text },
  }
  const base = {
    backgroundColor: "transparent",
    color: [...tokens.series],
    textStyle: { color: tokens.text, fontSize: 11 },
    animation: !reducedMotion,
    aria: { enabled: true, decal: { show: true } },
    legend: [
      {
        // Identified by its place: a new option merges into a component of the same id, so a
        // legend moved elsewhere is a new one rather than one holding both places.
        id: `legend:${widget.legend}`,
        show: legendShown,
        type: "scroll",
        icon: "roundRect",
        itemWidth: 12,
        itemHeight: 8,
        textStyle: { color: tokens.text, fontSize: 11 },
        ...(widget.legend === "none" ? LEGEND_PLACES.bottom : LEGEND_PLACES[widget.legend]),
      },
    ],
  }
  if (pie) {
    const [first] = data.series
    const slices = first === undefined
      ? []
      : data.categories.flatMap((name, at) => {
          const value = first.values[at]
          return value === null ? [] : [{ name, value, label: first.labels[at] ?? "" }]
        })
    return {
      ...base,
      tooltip: { ...tooltip, trigger: "item" },
      grid: [],
      xAxis: [],
      yAxis: [],
      series: [
        {
          id: `pie:${first?.key ?? ONE_ROW_SERIES}`,
          name: first?.name ?? "",
          type: "pie",
          // Smaller while labelled, so its labels fit beside it.
          radius: widget.data_labels ? ["34%", "58%"] : ["42%", "70%"],
          center: [...PIE_CENTRES[legendShown ? widget.legend : "none"]],
          avoidLabelOverlap: true,
          data: slices.map(({ name, value }) => ({ name, value })),
          itemStyle: { borderColor: tokens.background, borderWidth: 1 },
          label: {
            show: widget.data_labels,
            color: tokens.text,
            fontSize: 10,
            lineHeight: 13,
            // Wrapped rather than cut short where it meets the chart's edge.
            overflow: "break",
            // Its name over its value, narrow enough to sit beside the pie.
            formatter: ({ dataIndex, percent }: LabelParams) => {
              const slice = dataIndex === undefined ? undefined : slices[dataIndex]
              return slice === undefined ? "" : `${slice.name}\n${slice.label} (${percent ?? 0}%)`
            },
          },
          labelLine: { show: widget.data_labels },
          tooltip: { valueFormatter: (_value: unknown, dataIndex: number) => slices[dataIndex]?.label ?? UNNAMED },
        },
      ],
    }
  }

  const horizontal = widget.orientation === "horizontal"
  const columns = widget.chart_type === "column"
  const axisLabel = (value: number): string => (data.axis === null ? null : formattedNumber(data.axis, value)) ?? formatChartNumber(value)
  const place = legendShown ? widget.legend : "none"
  const categoryAxis = {
    id: "category",
    type: "category",
    data: data.categories,
    // A horizontal chart's category axis runs bottom-up: inverted, its first row is on top.
    inverse: horizontal,
    boundaryGap: widget.chart_type !== "area",
    axisLabel: { color: tokens.muted, fontSize: 11, hideOverlap: true },
    axisTick: { alignWithLabel: true },
    axisLine: { lineStyle: { color: tokens.grid } },
  }
  const valueAxis = {
    id: "value",
    type: "value",
    axisLabel: { color: tokens.muted, fontSize: 11, formatter: axisLabel },
    splitLine: { lineStyle: { color: tokens.grid } },
  }
  const labelPlace = widget.stacked && columns ? "inside" : horizontal ? "right" : "top"
  return {
    ...base,
    tooltip: { ...tooltip, trigger: "axis", axisPointer: { type: columns ? "shadow" : "line" } },
    grid: [
      {
        id: "grid",
        // The axis labels kept inside the grid's edges (ECharts 6's containLabel).
        outerBoundsMode: "same",
        outerBoundsContain: "axisLabel",
        top: (place === "top" ? 28 : 12) + (widget.data_labels && !horizontal ? 8 : 0),
        bottom: place === "bottom" ? 28 : 8,
        left: place === "left" ? 112 : 8,
        right: (place === "right" ? 112 : 12) + (widget.data_labels && horizontal ? 24 : 0),
      },
    ],
    xAxis: [horizontal ? valueAxis : categoryAxis],
    yAxis: [horizontal ? categoryAxis : valueAxis],
    series: data.series.map((entry) => ({
      // Identified by its field and how it is drawn: a new option merges into a series of the
      // same id, moving it to its new values, and what one drawn otherwise leaves out, as an
      // unstacked series does its stack, would stay; so a series drawn otherwise is a new one.
      id: `${widget.chart_type}${widget.stacked ? ":stacked" : ""}:${entry.key}`,
      // A one-row Chart's point is named by the axis, which the tooltip heads with.
      name: data.rows === "one" ? "" : entry.name,
      type: columns ? "bar" : "line",
      data: entry.values,
      ...(widget.stacked ? { stack: "stacked" } : {}),
      ...(widget.chart_type === "area" ? { areaStyle: { opacity: 0.25 } } : {}),
      // A one-row Chart's points are different things, a colour each.
      ...(columns ? { barMaxWidth: 48, ...(data.rows === "one" ? { colorBy: "data" } : {}) } : { symbolSize: 6 }),
      connectNulls: false,
      emphasis: { focus: "series" },
      label: {
        show: widget.data_labels,
        position: labelPlace,
        color: tokens.text,
        fontSize: 10,
        formatter: ({ dataIndex }: LabelParams) => (dataIndex === undefined ? "" : (entry.labels[dataIndex] ?? "")),
      },
      tooltip: { valueFormatter: (_value: unknown, dataIndex: number) => entry.labels[dataIndex] ?? UNNAMED },
    })),
  }
}
