/**
 * What a Chart plots and how it draws (specs/workbench, Charts): its fields' values as the
 * sheet holds them, typed and priced, a point per field of one-row tables or a series per
 * field over the rows of many-row tables, and the ECharts option drawing them, each value
 * in its column's format.
 */
import { describe, expect, it } from "vitest"
import type { ChartWidget, FieldRef, FormSpec, SchemaColumn, SchemaTable, ShowWhen } from "../../api/types"
import type { ChartThemeTokens } from "../../panels/explore/chartOptions"
import { chartMessage, hasValues, sheetChartData, sheetChartOptions, type ChartData, type ChartValues } from "../sheetChart"
import type { PricedRows, RowsByTable } from "../sheetValues"
import { quotedSchema, sheetVisibility } from "../sheetVisibility"
import { addWidget, BLANK_THEME, COLLECTION_OPTIONS, createWidget, TABLE_OPTIONS, type SampleRow } from "../workbenchForm"

const column = (id: string, name: string, overrides: Partial<SchemaColumn> = {}): SchemaColumn => ({
  id,
  name,
  type: "str",
  label: "",
  key: false,
  required: false,
  min: null,
  max: null,
  options: [],
  options_source: null,
  row_per_value: false,
  index: false,
  help: "",
  placeholder: "",
  group: "",
  default_value: "",
  number_format: "general",
  decimal_places: null,
  use_grouping: true,
  ...overrides,
})

const table = (id: string, overrides: Partial<SchemaTable> = {}): SchemaTable => ({ id, name: id, role: "input", rows: "one", columns: [], ...overrides })

const pounds = { type: "float", number_format: "currency_gbp" } as const

const always: ShowWhen = { match: "all", conditions: [] }
const whileCovered: ShowWhen = { match: "all", conditions: [{ table: "policy", column: "cover", op: "ticked", values: [] }] }

const field = (tableId: string, columnId: string): FieldRef => ({ table: tableId, column: columnId })

/**
 * A motor form: the policy (one row) and its quote (an output, one row); equipment (many
 * rows, keyed by item) priced into premiums and fees (outputs keyed alike); vehicles (many
 * rows keyed by reg, out of line with equipment). Its components are in the one section of
 * one sheet, and the policy's excess and the equipment's notes are typed only into those
 * shown while cover is ticked.
 */
const motorForm = (): FormSpec => ({
  version: 1,
  name: "motor",
  theme: { ...BLANK_THEME },
  schema: {
    tables: [
      table("policy", {
        columns: [
          column("state", "state"),
          column("sum_insured", "sum_insured", pounds),
          column("excess", "excess", { ...pounds, label: "Excess" }),
          column("cover", "cover", { type: "bool" }),
        ],
      }),
      table("quote", {
        role: "output",
        columns: [column("premium", "premium", pounds), column("tax", "tax", { type: "float" })],
      }),
      table("equipment", {
        rows: "many",
        columns: [
          column("row", "row_number", { type: "int", index: true }),
          column("item", "item", { key: true }),
          column("value", "value", pounds),
          column("count", "count", { type: "int" }),
          column("new", "new", { type: "bool" }),
          column("note", "note"),
        ],
      }),
      table("premiums", {
        role: "output",
        rows: "many",
        columns: [column("item", "item", { key: true }), column("premium", "premium", pounds)],
      }),
      table("fees", {
        role: "output",
        rows: "many",
        columns: [column("item", "item", { key: true }), column("fee", "fee", pounds)],
      }),
      table("vehicles", {
        rows: "many",
        columns: [column("reg", "reg", { key: true }), column("value", "value", pounds)],
      }),
    ],
  },
  pages: [
    {
      id: "p1",
      title: "Sheet 1",
      width: 1280,
      show_when: always,
      sections: [
        {
          id: "s1",
          title: "Section 1",
          height: 700,
          show_when: always,
          widgets: [
            { id: "w_policy", type: "collection", x: 0, y: 0, w: 400, h: 120, title: "Policy", columns: 3, ...COLLECTION_OPTIONS, fields: [field("policy", "state"), field("policy", "sum_insured"), field("policy", "cover")], show_when: always },
            { id: "w_excess", type: "collection", x: 0, y: 140, w: 400, h: 120, title: "Excess", columns: 1, ...COLLECTION_OPTIONS, fields: [field("policy", "excess")], show_when: whileCovered },
            {
              id: "w_equipment",
              type: "tableInput",
              x: 0,
              y: 280,
              w: 720,
              h: 200,
              title: "Equipment",
              rows: 3,
              ...TABLE_OPTIONS,
              fields: [field("equipment", "row"), field("equipment", "item"), field("equipment", "value"), field("equipment", "count"), field("equipment", "new")],
              show_when: always,
            },
            { id: "w_notes", type: "tableInput", x: 0, y: 500, w: 720, h: 200, title: "Notes", rows: 3, ...TABLE_OPTIONS, fields: [field("equipment", "note")], show_when: whileCovered },
          ],
        },
      ],
    },
  ],
  sample: {},
})

/** A Chart of `fields`, with `options`, placed in the motor form's section beside its other components. */
function charted(fields: FieldRef[], options: Partial<ChartWidget> = {}): { spec: FormSpec; widget: ChartWidget } {
  const widget = { ...(createWidget("chart", { x: 760, y: 0, w: 480, h: 280 }) as ChartWidget), title: "Chart", fields, ...options }
  return { spec: addWidget(motorForm(), "s1", widget), widget }
}

type Price = NonNullable<ChartValues["price"]>

/** The sheet's values: `rows` typed, read as the quote reads them (hidden columns left out), and what pricing them gave. */
const valuesOf = (spec: FormSpec, rows: RowsByTable, price: Price | null = null, basis = "now"): ChartValues => ({
  rows,
  price,
  basis,
  quoted: quotedSchema(spec.schema, sheetVisibility(spec, rows).hidden),
})

const priced = (tables: Record<string, PricedRows>, sample: Record<string, unknown> = {}, basis = "now"): Price => ({ basis, sample, tables })

const valuesOfSeries = (data: ChartData) => data.series.map(({ key, values }) => ({ key, values }))

describe("a Chart of one-row tables", () => {
  it("plots a point per field, typed with a currency sign and separators read as a number, and priced", () => {
    const { spec, widget } = charted([field("policy", "sum_insured"), field("quote", "premium")])
    const data = sheetChartData(spec.schema, widget, valuesOf(spec, { policy: [{ sum_insured: "£250,000" }] }, priced({ quote: [{ premium: 1234.5 }] })))
    expect(data.rows).toBe("one")
    expect(data.categories).toEqual(["Sum insured", "Premium"])
    expect(data.series).toEqual([{ key: "points", name: "Chart", values: [250000, 1234.5], labels: ["£250,000.00", "£1,234.50"] }])
  })

  it("leaves a gap for text that is not a number, a column the quote leaves out and a price that is not a number", () => {
    const { spec, widget } = charted([field("policy", "sum_insured"), field("policy", "excess"), field("quote", "premium")])
    // Cover is not ticked, so the excess is on no component that shows: the quote leaves it out however the Chart plots it.
    const data = sheetChartData(spec.schema, widget, valuesOf(spec, { policy: [{ sum_insured: "lots", excess: "500" }] }, priced({ quote: [{ premium: "n/a" }] })))
    expect(data.categories).toEqual(["Sum insured", "Excess", "Premium"])
    expect(data.series[0].values).toEqual([null, null, null])
    expect(data.series[0].labels).toEqual([null, null, null])
    expect(hasValues(data)).toBe(false)

    const covered = sheetChartData(spec.schema, widget, valuesOf(spec, { policy: [{ cover: true, excess: "500" }] }))
    expect(covered.series[0].values).toEqual([null, 500, null])
  })

  it("leaves out a field it cannot plot, a text column or an index, and one whose rows do not line up", () => {
    const { spec, widget } = charted([field("policy", "state"), field("policy", "sum_insured"), field("equipment", "row"), field("equipment", "value")])
    const data = sheetChartData(spec.schema, widget, valuesOf(spec, { policy: [{ state: "CA", sum_insured: "100" }], equipment: [{ item: "Crane", value: "5" }] }))
    expect(data.categories).toEqual(["Sum insured"])
    expect(data.series[0].values).toEqual([100])
  })
})

describe("a Chart of many-row tables", () => {
  // Row 1 holds only a note, which the quote leaves out while cover is not ticked; row 3 nothing at all.
  const equipment: SampleRow[] = [{ item: "Crane", value: "5,000", new: true }, { note: "spare" }, { item: "Drill", value: "lots" }, {}, { item: "Lathe", value: "200" }]
  const typed = [
    { item: "Crane", value: 5000 },
    { item: "Drill", value: "lots" },
    { item: "Lathe", value: 200 },
  ]

  it("plots a series per field over the rows typed into its input tables, a row with nothing the quote takes left out, numbered as the grid numbers them", () => {
    const { spec, widget } = charted([field("equipment", "value")])
    const data = sheetChartData(spec.schema, widget, valuesOf(spec, { equipment }))
    expect(data.rows).toBe("many")
    expect(data.categories).toEqual(["1", "3", "5"])
    expect(data.series).toEqual([{ key: "equipment:value", name: "Value", values: [5000, null, 200], labels: ["£5,000.00", null, "£200.00"] }])
  })

  it("counts a row typed into any of its input tables keyed alike, the others' values gaps on it", () => {
    const form = motorForm()
    form.schema.tables.push(table("cover", { rows: "many", columns: [column("item", "item", { key: true }), column("excess", "excess", pounds)] }))
    const widget = { ...(createWidget("chart", { x: 760, y: 0, w: 480, h: 280 }) as ChartWidget), title: "Chart", fields: [field("equipment", "value"), field("cover", "excess")] }
    const spec = addWidget(form, "s1", widget)
    // Row 1 is typed into equipment alone, row 2 into cover alone, side by side as a Table's grid has them.
    const data = sheetChartData(spec.schema, widget, valuesOf(spec, { equipment: [{ item: "Crane", value: "5" }, {}], cover: [{}, { item: "Crane", excess: "250" }] }))
    expect(data.categories).toEqual(["1", "2"])
    expect(valuesOfSeries(data)).toEqual([
      { key: "equipment:value", values: [5, null] },
      { key: "cover:excess", values: [null, 250] },
    ])
  })

  it("lines its output tables' rows up with the typed rows by key", () => {
    const { spec, widget } = charted([field("equipment", "value"), field("premiums", "premium")])
    const price = priced({ premiums: [{ item: "Lathe", premium: 20 }, { item: "Crane", premium: 500 }] }, { equipment: typed })
    const data = sheetChartData(spec.schema, widget, valuesOf(spec, { equipment }, price))
    expect(valuesOfSeries(data)).toEqual([
      { key: "equipment:value", values: [5000, null, 200] },
      { key: "premiums:premium", values: [500, null, 20] },
    ])
  })

  it("plots output tables alone over the first one's answer, another output table's rows matched by key", () => {
    const { spec, widget } = charted([field("premiums", "premium"), field("fees", "fee")])
    const price = priced({
      premiums: [{ item: "Crane", premium: 500 }, { item: "Drill", premium: "n/a" }],
      fees: [{ item: "Drill", fee: 4 }, { item: "Crane", fee: 3 }],
    })
    const data = sheetChartData(spec.schema, widget, valuesOf(spec, {}, price))
    expect(data.categories).toEqual(["1", "2"])
    expect(valuesOfSeries(data)).toEqual([
      { key: "premiums:premium", values: [500, null] },
      { key: "fees:fee", values: [3, 4] },
    ])
    expect(sheetChartData(spec.schema, widget, valuesOf(spec, {})).categories).toEqual([])
  })

  it("names its rows by an input column as typed: a number in its column's format, a tick Yes or No, a blank as a dash", () => {
    const rows: RowsByTable = { equipment: [{ item: "Crane", value: "5000", new: true }, { item: " ", value: "lots" }, { item: "Lathe", value: "", count: "2" }] }
    const names = (category: FieldRef) => {
      const { spec, widget } = charted([field("equipment", "count")], { category })
      return sheetChartData(spec.schema, widget, valuesOf(spec, rows)).categories
    }
    expect(names(field("equipment", "item"))).toEqual(["Crane", "—", "Lathe"])
    expect(names(field("equipment", "value"))).toEqual(["£5,000.00", "lots", "—"])
    expect(names(field("equipment", "new"))).toEqual(["Yes", "No", "No"])
    // An index numbers the rows as the grid does.
    expect(names(field("equipment", "row"))).toEqual(["1", "2", "3"])
  })

  it("names its rows by an output column as an output cell shows it, a dash where the price has none", () => {
    const { spec, widget } = charted([field("equipment", "value")], { category: field("premiums", "premium") })
    const price = priced({ premiums: [{ item: "Crane", premium: 1500 }] }, { equipment: typed })
    expect(sheetChartData(spec.schema, widget, valuesOf(spec, { equipment }, price)).categories).toEqual(["£1,500.00", "—", "—"])
  })

  it("numbers its rows while the column naming them does not line up", () => {
    const { spec, widget } = charted([field("equipment", "value")], { category: field("vehicles", "reg") })
    const data = sheetChartData(spec.schema, widget, valuesOf(spec, { equipment, vehicles: [{ reg: "AB12" }] }))
    expect(data.categories).toEqual(["1", "3", "5"])
  })

  it("leaves out a field it cannot plot and a field whose rows do not line up", () => {
    const { spec, widget } = charted([field("equipment", "item"), field("equipment", "row"), field("equipment", "value"), field("vehicles", "value"), field("policy", "sum_insured")])
    const data = sheetChartData(spec.schema, widget, valuesOf(spec, { equipment, vehicles: [{ reg: "AB12", value: "9" }], policy: [{ sum_insured: "1" }] }))
    expect(data.series.map(({ key }) => key)).toEqual(["equipment:value"])
  })

  it("keeps a pie's first series, a slice per row", () => {
    const { spec, widget } = charted([field("equipment", "count"), field("equipment", "value")], { chart_type: "pie" })
    const data = sheetChartData(spec.schema, widget, valuesOf(spec, { equipment }))
    expect(data.series.map(({ key }) => key)).toEqual(["equipment:count"])
  })
})

describe("a Chart's axis and freshness", () => {
  it("gives the axis the format every plotted column shares, and none when they differ", () => {
    const shared = charted([field("policy", "sum_insured"), field("policy", "excess"), field("quote", "premium")])
    expect(sheetChartData(shared.spec.schema, shared.widget, valuesOf(shared.spec, {})).axis).toMatchObject({ number_format: "currency_gbp", decimal_places: null })
    const mixed = charted([field("policy", "sum_insured"), field("quote", "tax")])
    expect(sheetChartData(mixed.spec.schema, mixed.widget, valuesOf(mixed.spec, {})).axis).toBeNull()
  })

  it("is stale only while it plots an output, or names its rows by one, priced for other values", () => {
    const outputs = charted([field("policy", "sum_insured"), field("quote", "premium")])
    const inputs = charted([field("policy", "sum_insured")])
    const namedByPrice = charted([field("equipment", "value")], { category: field("premiums", "premium") })
    const stale = (chart: { spec: FormSpec; widget: ChartWidget }, price: Price | null) =>
      sheetChartData(chart.spec.schema, chart.widget, valuesOf(chart.spec, {}, price, "now")).stale
    expect(stale(outputs, priced({}, {}, "before"))).toBe(true)
    expect(stale(outputs, priced({}, {}, "now"))).toBe(false)
    expect(stale(outputs, null)).toBe(false)
    expect(stale(inputs, priced({}, {}, "before"))).toBe(false)
    // Typed values named by what an earlier pricing gave: the names are for other values.
    expect(stale(namedByPrice, priced({}, {}, "before"))).toBe(true)
    expect(stale(namedByPrice, priced({}, {}, "now"))).toBe(false)
  })

  it("has values while any series has a number", () => {
    const series = (values: (number | null)[]) => ({ key: "k", name: "K", values, labels: values.map(() => null) })
    const data = (values: (number | null)[][]): ChartData => ({ rows: "many", categories: [], series: values.map(series), axis: null, stale: false })
    expect(hasValues(data([]))).toBe(false)
    expect(hasValues(data([[null, null]]))).toBe(false)
    expect(hasValues(data([[null], [null, 0]]))).toBe(true)
  })

  it("says why it plots nothing, and why a pie cannot share out a value below 0 or values that are all 0", () => {
    const message = (chart_type: ChartWidget["chart_type"], values: (number | null)[]) => {
      const { widget } = charted([], { chart_type })
      const labels = values.map((value) => (value === null ? null : `£${value}`))
      return chartMessage(widget, { rows: "one", categories: ["Premium", "Discount"], series: [{ key: "points", name: "", values, labels }], axis: null, stale: false })
    }
    expect(message("column", [null, null])).toBe("Nothing to chart yet")
    expect(message("pie", [null, null])).toBe("Nothing to chart yet")
    expect(message("pie", [100, 20])).toBeNull()
    // A slice of 0 beside others, or a gap, is drawn as no slice.
    expect(message("pie", [100, 0])).toBeNull()
    expect(message("pie", [null, 20])).toBeNull()
    expect(message("pie", [100, -20])).toBe("A pie can't show a value below 0: Discount is £-20")
    expect(message("pie", [0, 0])).toBe("Every value is 0, so there is no pie to draw")
    expect(message("pie", [0, null])).toBe("Every value is 0, so there is no pie to draw")
    // Columns, lines and areas draw below 0 and at 0 along their axis.
    expect(message("column", [100, -20])).toBeNull()
    expect(message("line", [0, 0])).toBeNull()
  })
})

const tokens: ChartThemeTokens = {
  background: "#FFFFFF",
  text: "#111827",
  muted: "#6B7280",
  grid: "#E5E7EB",
  series: ["#2563EB", "#DC2626", "#059669", "#7C3AED"],
}

type LabelFormatter = (params: { dataIndex?: number; percent?: number }) => string
type DrawnSeries = {
  id: string
  name: string
  type: string
  data: unknown[]
  stack?: string
  areaStyle?: unknown
  colorBy?: string
  center?: string[]
  label: { show: boolean; position?: string; formatter: LabelFormatter }
  tooltip: { valueFormatter: (value: unknown, dataIndex: number) => string }
}
type DrawnAxis = { id: string; type: string; data?: string[]; inverse?: boolean; boundaryGap?: boolean; axisLabel: { formatter?: (value: number) => string } }
type Drawn = {
  animation: boolean
  tooltip: { trigger: string; backgroundColor: string; borderColor: string; textStyle: { color: string } }
  legend: { id: string; show: boolean; orient: string; left?: number | string; right?: number; top?: number | string; bottom?: number }[]
  grid: unknown[]
  xAxis: DrawnAxis[]
  yAxis: DrawnAxis[]
  series: DrawnSeries[]
}

const pounds2 = column("p", "p", pounds)

/** Equipment's values and premiums over two rows, in pounds. */
const twoSeries: ChartData = {
  rows: "many",
  categories: ["Crane", "Drill"],
  series: [
    { key: "equipment:value", name: "Value", values: [5000, null], labels: ["£5,000.00", null] },
    { key: "premiums:premium", name: "Premium", values: [500, 40], labels: ["£500.00", "£40.00"] },
  ],
  axis: pounds2,
  stale: false,
}

/** A one-row Chart's points: the sum insured and a gap for the premium. */
const points: ChartData = {
  rows: "one",
  categories: ["Sum insured", "Premium", "Tax"],
  series: [{ key: "points", name: "Chart", values: [250000, null, 12], labels: ["£250,000.00", null, "12"] }],
  axis: null,
  stale: false,
}

const chartOf = (options: Partial<ChartWidget> = {}): ChartWidget => ({ ...(createWidget("chart", { x: 0, y: 0, w: 480, h: 280 }) as ChartWidget), title: "Chart", ...options })

const draw = (options: Partial<ChartWidget>, data: ChartData, reducedMotion = false): Drawn =>
  sheetChartOptions(chartOf(options), data, tokens, reducedMotion) as unknown as Drawn

describe("a Chart's drawing", () => {
  it("draws columns as bars, each series identified by its field, the rows along the category axis", () => {
    const drawn = draw({}, twoSeries)
    expect(drawn.series.map(({ id, type, name, data }) => ({ id, type, name, data }))).toEqual([
      { id: "column:equipment:value", type: "bar", name: "Value", data: [5000, null] },
      { id: "column:premiums:premium", type: "bar", name: "Premium", data: [500, 40] },
    ])
    expect(drawn.xAxis[0]).toMatchObject({ type: "category", data: ["Crane", "Drill"], inverse: false })
    expect(drawn.yAxis[0].type).toBe("value")
    expect(drawn.series.every((series) => series.stack === undefined)).toBe(true)
    // A one-row Chart's points are different things, a colour each.
    expect(draw({}, points).series[0].colorBy).toBe("data")
  })

  it("draws a line, and an area as a line with its area filled", () => {
    const line = draw({ chart_type: "line" }, twoSeries)
    expect(line.series.map(({ id, type, areaStyle }) => ({ id, type, areaStyle }))).toEqual([
      { id: "line:equipment:value", type: "line", areaStyle: undefined },
      { id: "line:premiums:premium", type: "line", areaStyle: undefined },
    ])
    const area = draw({ chart_type: "area" }, twoSeries)
    expect(area.series.map(({ type }) => type)).toEqual(["line", "line"])
    expect(area.series.every((series) => series.areaStyle !== undefined)).toBe(true)
    expect(area.xAxis[0].boundaryGap).toBe(false)
  })

  it("stacks its series, and lays a horizontal chart's rows down its side, the first on top", () => {
    const stacked = draw({ stacked: true }, twoSeries)
    expect(stacked.series.map(({ stack }) => stack)).toEqual(["stacked", "stacked"])
    // A new option merges into a series of the same id, so a stacked series is another series:
    // unstacking one draws it anew rather than leaving its stack in place.
    expect(stacked.series.map(({ id }) => id)).toEqual(["column:stacked:equipment:value", "column:stacked:premiums:premium"])
    const horizontal = draw({ orientation: "horizontal" }, twoSeries)
    expect(horizontal.yAxis[0]).toMatchObject({ type: "category", data: ["Crane", "Drill"], inverse: true })
    expect(horizontal.xAxis[0].type).toBe("value")
  })

  it("draws a pie of one row as a slice per field, a gap left out, with no axes", () => {
    const drawn = draw({ chart_type: "pie" }, points)
    expect(drawn.xAxis).toEqual([])
    expect(drawn.yAxis).toEqual([])
    expect(drawn.grid).toEqual([])
    expect(drawn.series).toHaveLength(1)
    expect(drawn.series[0]).toMatchObject({
      id: "pie:points",
      type: "pie",
      data: [
        { name: "Sum insured", value: 250000 },
        { name: "Tax", value: 12 },
      ],
    })
  })

  it("draws a pie of many rows as a slice per row of its series, identified by its field", () => {
    const pie: ChartData = { ...twoSeries, series: [twoSeries.series[1]] }
    const drawn = draw({ chart_type: "pie", data_labels: true }, pie)
    expect(drawn.series[0]).toMatchObject({ id: "pie:premiums:premium", name: "Premium", data: [{ name: "Crane", value: 500 }, { name: "Drill", value: 40 }] })
    expect(drawn.series[0].label.show).toBe(true)
    expect(drawn.series[0].label.formatter({ dataIndex: 1, percent: 7.4 })).toBe("Drill\n£40.00 (7.4%)")
    expect(drawn.series[0].tooltip.valueFormatter(40, 1)).toBe("£40.00")
  })

  it("places the legend where it is set, drawn only for two series or more or for a pie", () => {
    expect(draw({ legend: "left" }, twoSeries).legend[0]).toMatchObject({ show: true, left: 0, orient: "vertical" })
    expect(draw({ legend: "top" }, twoSeries).legend[0]).toMatchObject({ show: true, top: 0, orient: "horizontal" })
    // A legend moved elsewhere is a new legend, never one merged into the last holding both places.
    expect(draw({ legend: "top" }, twoSeries).legend[0].id).not.toBe(draw({ legend: "bottom" }, twoSeries).legend[0].id)
    expect(draw({ legend: "right" }, twoSeries).legend[0]).toMatchObject({ show: true, right: 0, orient: "vertical" })
    expect(draw({ legend: "bottom" }, twoSeries).legend[0]).toMatchObject({ show: true, bottom: 0 })
    expect(draw({ legend: "none" }, twoSeries).legend[0].show).toBe(false)
    expect(draw({}, points).legend[0].show).toBe(false)
    expect(draw({ chart_type: "pie" }, points).legend[0].show).toBe(true)
  })

  it("labels each value and gives it in the tooltip in its column's format, a gap as a dash", () => {
    const unlabelled = draw({}, twoSeries)
    expect(unlabelled.series[0].label.show).toBe(false)
    const [value, premium] = draw({ data_labels: true }, twoSeries).series
    expect(value.label.show).toBe(true)
    expect(value.label.formatter({ dataIndex: 0 })).toBe("£5,000.00")
    expect(value.label.formatter({ dataIndex: 1 })).toBe("")
    expect(premium.label.formatter({ dataIndex: 1 })).toBe("£40.00")
    expect(value.tooltip.valueFormatter(5000, 0)).toBe("£5,000.00")
    expect(value.tooltip.valueFormatter(null, 1)).toBe("—")
  })

  it("formats the value axis in the format its columns share, else compactly", () => {
    expect(draw({}, twoSeries).yAxis[0].axisLabel.formatter?.(1500)).toBe("£1,500.00")
    expect(draw({}, points).yAxis[0].axisLabel.formatter?.(250000)).toBe("250K")
    expect(draw({ orientation: "horizontal" }, twoSeries).xAxis[0].axisLabel.formatter?.(1500)).toBe("£1,500.00")
  })

  it("draws its tooltip on the panel's colours, a one-row Chart's points named by the axis alone", () => {
    const colours = { backgroundColor: tokens.background, borderColor: tokens.grid, textStyle: { color: tokens.text } }
    expect(draw({}, twoSeries).tooltip).toMatchObject({ trigger: "axis", ...colours })
    expect(draw({ chart_type: "pie" }, points).tooltip).toMatchObject({ trigger: "item", ...colours })
    // Its one series is the Chart's own: the tooltip heads with the field, so the series goes unnamed.
    expect(draw({}, points).series[0].name).toBe("")
    expect(draw({}, twoSeries).series.map(({ name }) => name)).toEqual(["Value", "Premium"])
  })

  it("animates unless motion is reduced", () => {
    expect(draw({}, twoSeries).animation).toBe(true)
    expect(draw({}, twoSeries, true).animation).toBe(false)
  })
})
