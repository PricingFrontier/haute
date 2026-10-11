/**
 * The pure operations on the workbench's form (specs/workbench): the schema's tables and
 * columns, what the sheets show of them, and what would stop the schema being the
 * pipeline's tables.
 */
import { describe, expect, it } from "vitest"
import type { ChartWidget, Condition, FieldRef, FormSchema, FormSpec, SchemaColumn, SchemaTable, ShowWhen } from "../../api/types"
import {
  addPage,
  addSchemaColumn,
  addSchemaTable,
  addWidget,
  changeSchemaColumnType,
  CHART_OPTIONS,
  chartGrain,
  conditionText,
  conditionUses,
  createIndexColumn,
  createPage,
  createSchemaColumn,
  createSchemaTable,
  createWidget,
  duplicateWidget,
  fieldColumn,
  fieldUses,
  findWidget,
  HEADING_OPTIONS,
  labelColumns,
  linedUpOutputs,
  moveField,
  movePage,
  moveSchemaColumn,
  pageProblems,
  plottable,
  pricingBasis,
  readableName,
  removePage,
  removeSchemaColumn,
  removeSchemaTable,
  removeWidget,
  renamePage,
  rowsShown,
  schemaProblems,
  setPageShowWhen,
  setSchemaTableRows,
  shownFields,
  showsFields,
  showWhenText,
  TABLE_OPTIONS,
  tableGrain,
  toggleField,
  uniqueName,
  updateSchemaColumn,
  updateSchemaTable,
  updateWidget,
  widgetName,
  widgetProblems,
  withCell,
  withSampleCell,
  withSampleRows,
} from "../workbenchForm"

const blank = (): FormSpec => ({
  version: 1,
  name: "t",
  schema: { tables: [] },
  pages: [{ id: "p", title: "Sheet 1", show_when: { match: "all", conditions: [] }, widgets: [] }],
  sample: {},
})

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
  index: false,
  help: "",
  placeholder: "",
  number_format: "general",
  decimal_places: null,
  use_grouping: true,
  ...overrides,
})

const table = (id: string, name: string, overrides: Partial<SchemaTable> = {}): SchemaTable => ({
  id,
  name,
  role: "input",
  rows: "one",
  columns: [],
  ...overrides,
})

/** A blank form with one schema table, `t`, holding the given columns. */
const withTable = (...columns: SchemaColumn[]): FormSpec =>
  addSchemaTable(blank(), table("t", "t", { columns }))

const messages = (spec: FormSpec, reserved: ReadonlySet<string> = new Set()) =>
  schemaProblems(spec, reserved).map((problem) => problem.message)

describe("names", () => {
  it("makes a name unique by numbering it, and a column readable from its name", () => {
    expect(uniqueName("table", ["table", "table_2"])).toBe("table_3")
    expect(uniqueName("policy", ["table"])).toBe("policy")
    expect(readableName("sum_insured")).toBe("Sum insured")
  })

  it("names new tables and columns uniquely, and an empty table is no problem", () => {
    let spec = blank()
    const first = createSchemaTable(spec)
    spec = addSchemaTable(spec, first)
    spec = addSchemaTable(spec, createSchemaTable(spec))
    spec = addSchemaColumn(spec, first.id, createSchemaColumn(spec, first.id))
    spec = addSchemaColumn(spec, first.id, createSchemaColumn(spec, first.id))

    expect(spec.schema.tables.map((t) => t.name)).toEqual(["table", "table_2"])
    expect(spec.schema.tables[0].columns.map((c) => [c.name, c.type, c.index])).toEqual([
      ["column", "str", false],
      ["column_2", "str", false],
    ])
    expect(messages(spec)).toEqual([])
  })
})

describe("problems", () => {
  it("holds a table's name to the port rules, with the document's reserved labels", () => {
    let spec = addSchemaTable(blank(), table("a", "sum insured"))
    spec = addSchemaTable(spec, table("b", "class"))
    spec = addSchemaTable(spec, table("c", "Policy"))
    spec = addSchemaTable(spec, table("d", "policy"))

    expect(messages(spec, new Set(["class"]))).toEqual([
      'Table "sum insured": A frame label must be an ASCII identifier (letters, digits, and underscores only).',
      'Table "class": A frame label cannot be a Python hard keyword or a name node code binds itself (such as pl or pipeline).',
      'Table "Policy": Label collides with "policy": both become "Policy" on disk (case-insensitive - macOS/Windows treat case-variant filenames as one file).',
      'Table "policy": Label collides with "Policy": both become "policy" on disk (case-insensitive - macOS/Windows treat case-variant filenames as one file).',
    ])
  })

  it("catches invalid, keyword and repeated column names, as the server refuses them", () => {
    const spec = withTable(column("a", "sum insured"), column("b", "value"), column("c", "value"), column("d", "class"))

    expect(messages(spec)).toEqual([
      'Column "sum insured" in Table "t" needs a name of letters, digits and _',
      'Column "value" in Table "t" repeats the name value',
      'Column "class" in Table "t" cannot be named with a Python keyword',
    ])
  })

  it("flags allowed values on a tick box or a date, which cannot have them", () => {
    const spec = withTable(column("a", "smoker", { type: "bool", options: ["Y"] }), column("b", "start", { type: "date", options: ["2020-01-01"] }))

    expect(messages(spec)).toEqual([
      'Column "smoker" in Table "t" allows values, which a tick box cannot',
      'Column "start" in Table "t" allows values, which a date cannot',
    ])
  })

  it("needs a key in a many-row table, and drops keys and the index when a table goes back to one row", () => {
    let spec = setSchemaTableRows(withTable(column("a", "item"), column("b", "value")), "t", "many")
    expect(messages(spec)).toEqual(['Table "t" has many rows, so it needs a key column'])

    spec = updateSchemaColumn(spec, "t", "a", { key: true })
    expect(messages(spec)).toEqual([])
    const index = createIndexColumn(spec, "t")
    spec = addSchemaColumn(spec, "t", index, 0)
    spec = withSampleCell(spec, "t", 0, "a", "x")
    spec = addWidget(spec, "p", { ...createWidget("tableInput", { x: 0, y: 0, w: 720, h: 200 }), fields: [{ table: "t", column: index.id }] })

    spec = setSchemaTableRows(spec, "t", "one")
    expect(spec.schema.tables[0].columns.map((c) => [c.name, c.key])).toEqual([["item", false], ["value", false]])
    expect(spec.pages[0].widgets[0]).toMatchObject({ fields: [] })
    expect(spec.sample).toEqual({ t: [{ a: "x" }] })
  })

  it("changes a column's type, dropping the rules the new type cannot have", () => {
    let spec = withTable(
      column("a", "value", { type: "float", min: 1, max: 5, options: ["1", "2"], number_format: "percent", decimal_places: 1, use_grouping: false }),
    )

    // Between Decimal and Integer, a number keeps its range, its allowed values and its format.
    spec = changeSchemaColumnType(spec, "t", "a", "int")
    expect(spec.schema.tables[0].columns[0]).toMatchObject({
      type: "int",
      min: 1,
      max: 5,
      options: ["1", "2"],
      number_format: "percent",
      decimal_places: 1,
      use_grouping: false,
    })
    spec = changeSchemaColumnType(spec, "t", "a", "str")
    expect(spec.schema.tables[0].columns[0]).toMatchObject({
      type: "str",
      min: null,
      max: null,
      options: ["1", "2"],
      number_format: "general",
      decimal_places: null,
      use_grouping: true,
    })
    spec = changeSchemaColumnType(spec, "t", "a", "date")
    expect(spec.schema.tables[0].columns[0]).toMatchObject({ type: "date", options: [] })
    spec = changeSchemaColumnType(spec, "t", "a", "int")
    expect(spec.schema.tables[0].columns[0]).toMatchObject({ type: "int", min: null, max: null, options: [] })
  })

  it("checks an input column's rules, which an output table ignores", () => {
    const spec = withTable(
      column("a", "value", { type: "float", min: 10, max: 5 }),
      column("b", "year", { type: "int", options: ["2020", "2020.5"] }),
      column("c", "deductible", { type: "float", options: ["500", "1e3", "lots"] }),
      column("d", "row_number", { type: "int", index: true, min: 10, max: 5 }),
    )

    expect(messages(spec)).toEqual([
      'Column "value" in Table "t" has a minimum above its maximum',
      `Column "year" in Table "t" allows "2020.5", which isn't a whole number`,
      `Column "deductible" in Table "t" allows "lots", which isn't a number`,
    ])
    expect(messages(updateSchemaTable(spec, "t", { role: "output" }))).toEqual([])
  })
})

describe("columns", () => {
  it("inserts and moves columns", () => {
    let spec = withTable(column("a", "a"), column("b", "b"), column("c", "c"))
    const names = () => spec.schema.tables[0].columns.map((c) => c.name)

    spec = addSchemaColumn(spec, "t", column("x", "x"), 1)
    expect(names()).toEqual(["a", "x", "b", "c"])
    spec = moveSchemaColumn(spec, "t", "a", 3)
    expect(names()).toEqual(["x", "b", "c", "a"])

    // The index stays first: it is not moved, and nothing is moved above it.
    spec = setSchemaTableRows(spec, "t", "many")
    spec = addSchemaColumn(spec, "t", createIndexColumn(spec, "t"), 0)
    expect(moveSchemaColumn(spec, "t", spec.schema.tables[0].columns[0].id, 2)).toBe(spec)
    expect(moveSchemaColumn(spec, "t", "b", 0)).toBe(spec)
    spec = moveSchemaColumn(spec, "t", "b", 1)
    expect(names()).toEqual(["row_number", "b", "x", "c", "a"])
  })

  it("gives a table an index column, an Integer named row_number, that can be its key", () => {
    let spec = setSchemaTableRows(withTable(column("a", "item", { key: true })), "t", "many")
    const index = createIndexColumn(spec, "t")

    expect([index.name, index.type, index.index]).toEqual(["row_number", "int", true])
    spec = addSchemaColumn(spec, "t", index, 0)
    spec = updateSchemaColumn(spec, "t", "a", { key: false })
    expect(messages(spec)).toContain('Table "t" has many rows, so it needs a key column')
    spec = updateSchemaColumn(spec, "t", index.id, { key: true })
    expect(messages(spec)).toEqual([])
    expect(createIndexColumn(spec, "t").name).toBe("row_number_2")
  })

  it("refuses to edit a table or column that is not in the schema", () => {
    expect(() => updateSchemaTable(blank(), "missing", { name: "x" })).toThrow("no table missing")
    expect(() => updateSchemaColumn(withTable(), "t", "missing", { name: "x" })).toThrow("no column missing")
  })
})

describe("what the sheets show", () => {
  /** Policy details (one row) shown by a Collection, equipment (many rows) by a Table, with a sample. */
  const sheet = (): FormSpec => {
    let spec = addSchemaTable(blank(), table("policy", "policy", { columns: [column("policy.state", "state")] }))
    spec = addSchemaTable(
      spec,
      table("equipment", "equipment", {
        rows: "many",
        columns: [column("equipment.item", "item", { key: true }), column("equipment.value", "value", { type: "float" })],
      }),
    )
    spec = {
      ...spec,
      pages: [
        {
          id: "p",
          title: "Sheet 1",
          show_when: { match: "all", conditions: [] },
          widgets: [
            { id: "w_boxes", type: "collection", x: 0, y: 0, w: 720, h: 120, title: "Policy", columns: 3, fields: [{ table: "policy", column: "policy.state" }], show_when: { match: "all", conditions: [] } },
            {
              id: "w_grid",
              type: "tableInput",
              x: 0,
              y: 160,
              w: 720,
              h: 200,
              title: "Equipment",
              rows: 3,
              ...TABLE_OPTIONS,
              fields: [{ table: "equipment", column: "equipment.item" }, { table: "equipment", column: "equipment.value" }],
              show_when: { match: "all", conditions: [] },
            },
          ],
        },
      ],
      sample: {
        policy: [{ "policy.state": "NY" }],
        equipment: [{ "equipment.item": "A", "equipment.value": "100" }, { "equipment.item": "B" }],
      },
    }
    return spec
  }
  const fields = (spec: FormSpec, widgetId: string) =>
    spec.pages[0].widgets.filter(showsFields).find((widget) => widget.id === widgetId)?.fields.map((field) => field.column)

  it("finds the widgets showing a table or a column, and the column a field names", () => {
    const spec = sheet()

    expect(fieldUses(spec, "equipment").map((w) => w.id)).toEqual(["w_grid"])
    expect(fieldUses(spec, "equipment", "equipment.value").map((w) => w.id)).toEqual(["w_grid"])
    expect(fieldUses(spec, "policy", "missing")).toEqual([])
    expect(fieldColumn(spec, { table: "equipment", column: "equipment.value" })?.column.name).toBe("value")
    expect(fieldColumn(spec, { table: "gone", column: "x" })).toBeNull()
  })

  it("removing a column takes it out of the widgets that show it, and out of the sample", () => {
    const spec = removeSchemaColumn(sheet(), "equipment", "equipment.value")

    expect(fields(spec, "w_grid")).toEqual(["equipment.item"])
    expect(spec.sample.equipment).toEqual([{ "equipment.item": "A" }, { "equipment.item": "B" }])
  })

  it("removing a table takes its columns out of the widgets, and its rows out of the sample", () => {
    const spec = removeSchemaTable(sheet(), "policy")

    expect(spec.schema.tables.map((t) => t.id)).toEqual(["equipment"])
    expect(fields(spec, "w_boxes")).toEqual([])
    expect(Object.keys(spec.sample)).toEqual(["equipment"])
  })

  it("leaves its input untouched", () => {
    const before = sheet()
    const snapshot = JSON.stringify(before)

    removeSchemaTable(before, "policy")
    updateSchemaColumn(before, "policy", "policy.state", { name: "region" })

    expect(JSON.stringify(before)).toBe(snapshot)
  })

  it("names new sheets uniquely, renames them, and keeps the last one", () => {
    let spec = blank()
    const second = createPage(spec)
    expect(second.title).toBe("Sheet 2")
    spec = addPage(spec, second)
    spec = renamePage(spec, second.id, "Equipment")
    expect(spec.pages.map((page) => page.title)).toEqual(["Sheet 1", "Equipment"])
    expect(createPage(spec).title).toBe("Sheet 3")

    spec = removePage(spec, "p")
    expect(spec.pages.map((page) => page.id)).toEqual([second.id])
    expect(() => removePage(spec, second.id)).toThrow("The last sheet cannot be removed")
  })

  it("moves a sheet to another place in the tabs' order, a move to where it is changing nothing", () => {
    const spec = [createPage, createPage].reduce((current, create) => addPage(current, create(current)), blank())
    const titles = (form: FormSpec) => form.pages.map((page) => page.title)
    expect(titles(spec)).toEqual(["Sheet 1", "Sheet 2", "Sheet 3"])

    expect(titles(movePage(spec, 0, 2))).toEqual(["Sheet 2", "Sheet 3", "Sheet 1"])
    expect(titles(movePage(spec, 2, 0))).toEqual(["Sheet 3", "Sheet 1", "Sheet 2"])
    expect(titles(spec)).toEqual(["Sheet 1", "Sheet 2", "Sheet 3"])
    expect(movePage(spec, 1, 1)).toBe(spec)
    expect(() => movePage(spec, 0, 3)).toThrow("Cannot move the sheet at 0 to 3: the form has 3 sheets")
  })

  it("adds, updates, duplicates and removes components, each kind with its own layout setting", () => {
    let spec = blank()
    const grid = createWidget("tableInput", { x: 0, y: 0, w: 720, h: 200 })
    const boxes = createWidget("collection", { x: 0, y: 240, w: 720, h: 120 })
    // A Table as the server's defaults have it: numbered, compact, lined between rows, striped, not fixed.
    expect(grid).toMatchObject({ type: "tableInput", title: "", fields: [], rows: 3, row_numbers: true, density: "compact", gridlines: "rows", striped: true, fixed_rows: false })
    expect(boxes).toMatchObject({ type: "collection", title: "", fields: [], columns: 3 })
    expect(grid).not.toHaveProperty("columns")
    expect(boxes).not.toHaveProperty("density")
    // In the file's order, so a component the view made compares equal to it saved.
    expect(Object.keys(grid)).toEqual(["id", "x", "y", "w", "h", "type", "title", "fields", "show_when", "rows", "row_numbers", "density", "gridlines", "striped", "fixed_rows"])

    spec = addWidget(addWidget(spec, "p", grid), "p", boxes)
    spec = updateWidget(spec, grid.id, { title: "Equipment", rows: 5, x: 16, gridlines: "all", fixed_rows: true })
    expect(findWidget(spec, grid.id)).toMatchObject({ index: 0, widget: { title: "Equipment", rows: 5, x: 16, gridlines: "all", fixed_rows: true } })
    expect(() => updateWidget(spec, grid.id, { columns: 2 })).toThrow("Only a Collection has columns")
    expect(() => updateWidget(spec, boxes.id, { rows: 2 })).toThrow("Only a Table has rows")
    expect(() => updateWidget(spec, boxes.id, { density: "comfortable" })).toThrow("Only a Table has density")
    expect(() => updateWidget(spec, "missing", { title: "x" })).toThrow("No sheet has a component missing")

    const copied = duplicateWidget(spec, grid.id)
    const copy = findWidget(copied.spec, copied.id)
    expect(copy).toMatchObject({ index: 1, widget: { type: "tableInput", title: "Equipment", rows: 5, x: 32, y: 16 } })
    expect(copied.id).not.toBe(grid.id)

    spec = removeWidget(copied.spec, grid.id)
    expect(spec.pages[0].widgets.map((widget) => widget.id)).toEqual([copied.id, boxes.id])
    expect(findWidget(spec, grid.id)).toBeNull()
  })

  it("creates a Chart with its own options, which only a Chart takes, and duplicates it with them", () => {
    const chart = createWidget("chart", { x: 0, y: 0, w: 480, h: 280 })
    const grid = createWidget("tableInput", { x: 0, y: 320, w: 720, h: 200 })
    const boxes = createWidget("collection", { x: 0, y: 560, w: 720, h: 120 })
    // Its rows numbered, of columns, upright, unstacked, its legend at the bottom and no data labels.
    expect(CHART_OPTIONS).toEqual({ category: null, chart_type: "column", orientation: "vertical", stacked: false, legend: "bottom", data_labels: false })
    expect(chart).toMatchObject({ type: "chart", title: "", fields: [], ...CHART_OPTIONS })
    expect(Object.keys(chart)).toEqual(["id", "x", "y", "w", "h", "type", "title", "fields", "show_when", "category", "chart_type", "orientation", "stacked", "legend", "data_labels"])
    expect(chart).not.toHaveProperty("rows")
    expect(chart).not.toHaveProperty("density")
    expect(chart).not.toHaveProperty("columns")

    let spec = [chart, grid, boxes].reduce((current, widget) => addWidget(current, "p", widget), blank())
    const options = { category: { table: "t", column: "c" }, chart_type: "pie", orientation: "horizontal", stacked: true, legend: "right", data_labels: true } as const
    spec = updateWidget(spec, chart.id, { title: "Premiums", ...options })
    expect(findWidget(spec, chart.id)?.widget).toMatchObject({ title: "Premiums", ...options })
    expect(() => updateWidget(spec, grid.id, { chart_type: "line" })).toThrow("Only a Chart has chart_type")
    expect(() => updateWidget(spec, boxes.id, { chart_type: "line" })).toThrow("Only a Chart has chart_type")
    expect(() => updateWidget(spec, chart.id, { rows: 2 })).toThrow("Only a Table has rows")
    expect(() => updateWidget(spec, chart.id, { columns: 2 })).toThrow("Only a Collection has columns")

    const copied = duplicateWidget(spec, chart.id)
    expect(findWidget(copied.spec, copied.id)).toMatchObject({ index: 1, widget: { type: "chart", title: "Premiums", x: 16, y: 16, ...options } })
  })

  it("creates a Heading of its text, description, size and alignment alone, which only a Heading takes, showing no fields", () => {
    const heading = createWidget("heading", { x: 0, y: 0, w: 480, h: 40 })
    const grid = createWidget("tableInput", { x: 0, y: 80, w: 720, h: 200 })
    // Medium and aligned left, with no text or description yet, and no title or fields, in the file's order.
    expect(HEADING_OPTIONS).toEqual({ size: "medium", align: "left" })
    expect(heading).toMatchObject({ type: "heading", text: "", description: "", ...HEADING_OPTIONS })
    expect(Object.keys(heading)).toEqual(["id", "x", "y", "w", "h", "type", "text", "description", "show_when", "size", "align"])
    expect([showsFields(heading), showsFields(grid)]).toEqual([false, true])

    let spec = [heading, grid].reduce((current, widget) => addWidget(current, "p", widget), blank())
    const look = { text: "Property", description: " Buildings and contents ", size: "large", align: "center" } as const
    spec = updateWidget(spec, heading.id, { ...look, x: 16 })
    expect(findWidget(spec, heading.id)?.widget).toMatchObject({ ...look, x: 16 })
    expect(() => updateWidget(spec, grid.id, { text: "Equipment" })).toThrow("Only a Heading has text")
    expect(() => updateWidget(spec, grid.id, { description: "By item" })).toThrow("Only a Heading has description")
    expect(() => updateWidget(spec, grid.id, { size: "small" })).toThrow("Only a Heading has size")
    expect(() => updateWidget(spec, grid.id, { align: "right" })).toThrow("Only a Heading has align")
    expect(() => updateWidget(spec, heading.id, { title: "Property" })).toThrow("A Heading has no title")
    expect(() => updateWidget(spec, heading.id, { fields: [] })).toThrow("A Heading has no fields")
    expect(() => updateWidget(spec, heading.id, { rows: 2 })).toThrow("Only a Table has rows")
    expect(() => toggleField(spec, heading.id, { table: "t", column: "c" })).toThrow("A Heading shows no fields")
    expect(() => moveField(spec, heading.id, 0, 1)).toThrow("A Heading shows no fields")
    expect(shownFields(spec, heading)).toEqual([])

    const copied = duplicateWidget(spec, heading.id)
    expect(findWidget(copied.spec, copied.id)).toMatchObject({ index: 1, widget: { type: "heading", ...look, x: 32, y: 16 } })
  })

  it("names a Heading by its text, says when it has none, and passes it by when a column goes", () => {
    let spec = addWidget(sheet(), "p", { ...createWidget("heading", { x: 0, y: 400, w: 480, h: 40 }), id: "w_head" })
    const head = (form: FormSpec) => form.pages[0].widgets[2]
    expect(widgetName(head(spec))).toBe("A Heading")
    expect(widgetProblems(spec)).toEqual([{ widgetId: "w_head", message: "A Heading has no text" }])
    expect(widgetProblems(updateWidget(spec, "w_head", { text: "   " })).map((p) => p.message)).toEqual(["A Heading has no text"])

    spec = updateWidget(spec, "w_head", { text: " Equipment schedule " })
    expect(widgetName(head(spec))).toBe('Heading "Equipment schedule"')
    expect(widgetProblems(spec)).toEqual([])
    // Kept as typed: only its name reads it without the spaces around it.
    expect(head(spec)).toMatchObject({ text: " Equipment schedule " })

    // A Heading shows no column, so it is no column's use, and removing one passes it by.
    expect(fieldUses(spec, "equipment").map((widget) => widget.id)).toEqual(["w_grid"])
    expect(head(removeSchemaTable(removeSchemaColumn(spec, "equipment", "equipment.value"), "policy"))).toEqual(head(spec))
  })

  it("shows a column in a component, stops showing it, and moves fields in order", () => {
    let spec = sheet()
    spec = toggleField(spec, "w_boxes", { table: "policy", column: "policy.state" })
    expect(fields(spec, "w_boxes")).toEqual([])
    spec = toggleField(spec, "w_boxes", { table: "policy", column: "policy.state" })
    expect(fields(spec, "w_boxes")).toEqual(["policy.state"])

    spec = moveField(spec, "w_grid", 1, 0)
    expect(fields(spec, "w_grid")).toEqual(["equipment.value", "equipment.item"])
  })

  it("sets a sample cell, adding empty rows up to it, and replaces several tables' rows as one edit", () => {
    let spec = withSampleCell(sheet(), "equipment", 3, "equipment.item", "C")
    expect(spec.sample.equipment).toEqual([{ "equipment.item": "A", "equipment.value": "100" }, { "equipment.item": "B" }, {}, { "equipment.item": "C" }])
    spec = withSampleCell(spec, "policy", 0, "policy.year", true)
    expect(spec.sample.policy).toEqual([{ "policy.state": "NY", "policy.year": true }])
    expect(withCell([], 0, "c", "x")).toEqual([{ c: "x" }])

    spec = withSampleRows(spec, { equipment: [{ "equipment.item": "B" }], premiums: [{ "premiums.item": "B" }] })
    expect(spec.sample.equipment).toEqual([{ "equipment.item": "B" }])
    expect(spec.sample.premiums).toEqual([{ "premiums.item": "B" }])
    expect(spec.sample.policy).toEqual([{ "policy.state": "NY", "policy.year": true }])
  })

  it("prices on the schema and the sample alone: laying the sheet out differently changes nothing", () => {
    const spec = sheet()
    const moved = updateWidget(spec, "w_grid", { x: 100, rows: 7 })
    const typed = withSampleCell(spec, "policy", 0, "policy.state", "CA")
    const retyped = updateSchemaColumn(spec, "policy", "policy.state", { type: "int" })

    expect(pricingBasis(moved)).toBe(pricingBasis(spec))
    expect(pricingBasis(typed)).not.toBe(pricingBasis(spec))
    expect(pricingBasis(retyped)).not.toBe(pricingBasis(spec))
    expect(pricingBasis(spec)).toBe(pricingBasis(spec))
  })

  it("names a component by its kind and title, and says what stops it showing what it should", () => {
    let spec = sheet()
    expect(widgetProblems(spec)).toEqual([])
    expect(widgetName(spec.pages[0].widgets[0])).toBe('Collection "Policy"')
    expect(widgetName(updateWidget(spec, "w_grid", { title: "" }).pages[0].widgets[1])).toBe("A Table")
    expect([rowsShown({ type: "collection" }), rowsShown({ type: "tableInput" })]).toEqual(["one", "many"])
    expect(spec.schema.tables.map(tableGrain)).toEqual(["one", "many:item"])

    const empty = updateWidget(spec, "w_boxes", { fields: [] })
    expect(widgetProblems(empty).map((p) => p.message)).toEqual(['Collection "Policy" shows no fields'])

    const dangling = updateWidget(spec, "w_grid", { fields: [{ table: "gone", column: "gone.x" }] })
    expect(widgetProblems(dangling).map((p) => p.message)).toEqual([
      'Table "Equipment" shows a column that is no longer in the schema',
    ])

    // A table switched between one row and many after its columns were chosen.
    expect(widgetProblems(setSchemaTableRows(spec, "policy", "many")).map((p) => p.message)).toEqual([
      'Collection "Policy" shows a many-row table; a Table shows those',
    ])
    expect(widgetProblems(setSchemaTableRows(spec, "equipment", "one")).map((p) => p.message)).toEqual([
      'Table "Equipment" shows a one-row table; a Collection shows those',
    ])

    // Two many-row tables line up only by their keys' names.
    spec = addSchemaTable(spec, table("premiums", "premiums", { role: "output", rows: "many", columns: [column("premiums.item", "item", { key: true }), column("premiums.premium", "premium", { type: "float" })] }))
    spec = toggleField(spec, "w_grid", { table: "premiums", column: "premiums.premium" })
    expect(widgetProblems(spec)).toEqual([])
    expect(widgetProblems(updateSchemaColumn(spec, "premiums", "premiums.item", { key: false })).map((p) => p.message)).toEqual([
      'Table "Equipment" shows tables whose rows do not line up',
    ])
  })
})

describe("when sheets and components show", () => {
  const always = (): ShowWhen => ({ match: "all", conditions: [] })
  const ticked: Condition = { table: "policy", column: "policy.bi", op: "ticked", values: [] }
  const collection = (id: string, title: string, columns: [string, string][], showWhen: ShowWhen = always()) => ({
    id,
    type: "collection" as const,
    x: 0,
    y: 0,
    w: 720,
    h: 120,
    title,
    fields: columns.map(([tableId, columnId]) => ({ table: tableId, column: columnId })),
    show_when: showWhen,
    columns: 3,
  })
  /** A tick box and a class on the main sheet, and a sheet of business interruption shown while the box is ticked. */
  const covered = (): FormSpec => ({
    ...blank(),
    schema: {
      tables: [
        table("policy", "policy", {
          columns: [
            column("policy.bi", "bi", { type: "bool", label: "BI cover" }),
            column("policy.class", "class", { options: ["Motor", "Fleet"] }),
            column("policy.start", "start", { type: "date" }),
            column("policy.extra", "extra"),
          ],
        }),
        table("bi", "bi", { columns: [column("bi.limit", "limit", { type: "float" })] }),
      ],
    },
    pages: [
      { id: "p", title: "Main", show_when: always(), widgets: [collection("w_policy", "Policy", [["policy", "policy.bi"], ["policy", "policy.class"]])] },
      { id: "p_bi", title: "BI", show_when: { match: "all", conditions: [ticked] }, widgets: [collection("w_bi", "Limits", [["bi", "bi.limit"]])] },
    ],
    sample: { bi: [{ "bi.limit": "500,000" }] },
  })

  it("creates sheets and components always shown, each with a show_when of its own", () => {
    const spec = covered()
    expect(createPage(spec).show_when).toEqual(always())
    const first = createWidget("collection", { x: 0, y: 0, w: 200, h: 100 })
    const second = createWidget("tableInput", { x: 0, y: 0, w: 200, h: 100 })
    const third = createWidget("heading", { x: 0, y: 0, w: 200, h: 40 })
    expect([first.show_when, second.show_when, third.show_when]).toEqual([always(), always(), always()])
    expect(first.show_when).not.toBe(second.show_when)
  })

  it("sets when a sheet or a component shows, leaving its input untouched", () => {
    const spec = covered()
    const either: ShowWhen = { match: "any", conditions: [ticked, { table: "policy", column: "policy.class", op: "equals", values: ["Fleet"] }] }

    const next = setPageShowWhen(spec, "p_bi", either)
    expect(next.pages[1].show_when).toEqual(either)
    expect(next.pages[1].show_when).not.toBe(either)
    expect(spec.pages[1].show_when.conditions).toEqual([ticked])
    expect(() => setPageShowWhen(spec, "missing", either)).toThrow("The form has no sheet missing")
    expect(findWidget(updateWidget(spec, "w_bi", { show_when: either }), "w_bi")?.widget.show_when).toEqual(either)
  })

  it("takes the conditions testing a column or a table out with it, wherever they are", () => {
    const onClass: Condition = { table: "policy", column: "policy.class", op: "equals", values: ["Fleet"] }
    const spec = updateWidget(covered(), "w_bi", { show_when: { match: "all", conditions: [onClass] } })
    expect(conditionUses(spec, "policy", "policy.bi").map((holder) => holder.id)).toEqual(["p_bi"])
    expect(conditionUses(spec, "policy").map((holder) => holder.id)).toEqual(["p_bi", "w_bi"])
    expect(conditionUses(spec, "bi")).toEqual([])

    const withoutBox = removeSchemaColumn(spec, "policy", "policy.bi")
    expect(withoutBox.pages[1].show_when.conditions).toEqual([])
    expect(findWidget(withoutBox, "w_bi")?.widget.show_when.conditions).toEqual([onClass])

    const withoutPolicy = removeSchemaTable(spec, "policy")
    expect(withoutPolicy.pages[1].show_when.conditions).toEqual([])
    expect(findWidget(withoutPolicy, "w_bi")?.widget.show_when.conditions).toEqual([])
  })

  it("counts a Heading's conditions as any component's, and takes them out with their column or table", () => {
    const onClass: Condition = { table: "policy", column: "policy.class", op: "equals", values: ["Fleet"] }
    const heading = { ...createWidget("heading", { x: 0, y: 0, w: 480, h: 40 }), id: "w_head", text: "Fleet", show_when: { match: "all" as const, conditions: [onClass] } }
    const spec = addWidget(covered(), "p", heading)
    expect(conditionUses(spec, "policy", "policy.class").map((holder) => holder.id)).toEqual(["w_head"])

    expect(findWidget(removeSchemaColumn(spec, "policy", "policy.class"), "w_head")?.widget.show_when.conditions).toEqual([])
    expect(findWidget(removeSchemaTable(spec, "policy"), "w_head")?.widget.show_when.conditions).toEqual([])
    expect(findWidget(removeSchemaColumn(spec, "policy", "policy.bi"), "w_head")?.widget.show_when.conditions).toEqual([onClass])
  })

  it("says what stops a sheet's or a component's conditions doing what they say", () => {
    const spec = covered()
    expect([...pageProblems(spec), ...widgetProblems(spec)]).toEqual([])

    // Retyped under its condition: the test no longer fits.
    expect(pageProblems(changeSchemaColumnType(spec, "policy", "policy.bi", "str")).map((p) => p.message)).toEqual([
      'Sheet "BI" tests bi with "is ticked", which a Text column cannot take',
    ])
    // A value no allowed value can match.
    const property = updateWidget(spec, "w_bi", { show_when: { match: "all", conditions: [{ table: "policy", column: "policy.class", op: "one_of", values: ["Fleet", "Property"] }] } })
    expect(widgetProblems(property).map((p) => p.message)).toEqual([
      'Collection "Limits" tests class against "Property", which isn\'t one of its allowed values',
    ])
    // Tested only on the sheet it shows, or on no component: nothing could show the sheet again.
    expect(pageProblems(setPageShowWhen(spec, "p_bi", { match: "all", conditions: [{ table: "bi", column: "bi.limit", op: "filled", values: [] }] })).map((p) => p.message)).toEqual([
      'Sheet "BI" waits on limit, which is only on components that hide with it, so it could not show again once hidden',
    ])
    expect(pageProblems(setPageShowWhen(spec, "p_bi", { match: "all", conditions: [{ table: "policy", column: "policy.extra", op: "filled", values: [] }] })).map((p) => p.message)).toEqual([
      'Sheet "BI" waits on extra, which is on no Table or Collection',
    ])
    // A Chart only plots, so a column only a Chart plots is on no Table or Collection either.
    const plotted = addWidget(changeSchemaColumnType(spec, "policy", "policy.extra", "float"), "p", {
      ...createWidget("chart", { x: 0, y: 240, w: 480, h: 280 }),
      fields: [{ table: "policy", column: "policy.extra" }],
    })
    expect(pageProblems(setPageShowWhen(plotted, "p_bi", { match: "all", conditions: [{ table: "policy", column: "policy.extra", op: "filled", values: [] }] })).map((p) => p.message)).toEqual([
      'Sheet "BI" waits on extra, which is on no Table or Collection',
    ])
    // A Heading's conditions are checked as any component's, and it is named by its text.
    const headed = addWidget(spec, "p_bi", {
      ...createWidget("heading", { x: 0, y: 160, w: 480, h: 40 }),
      text: "Business interruption",
      show_when: { match: "all", conditions: [{ table: "policy", column: "policy.gone", op: "filled", values: [] }] },
    })
    expect(widgetProblems(headed).map((p) => p.message)).toEqual(['Heading "Business interruption" tests a column that is no longer in the schema'])
  })

  it("reads conditions as sentences by their columns' labels", () => {
    const spec = covered()
    const onClass: Condition = { table: "policy", column: "policy.class", op: "one_of", values: ["Motor", "Fleet"] }
    expect(conditionText(spec, ticked)).toBe("BI cover is ticked")
    expect(conditionText(spec, onClass)).toBe("Class is one of Motor, Fleet")
    expect(conditionText(spec, { table: "policy", column: "policy.start", op: "above", values: ["2026-01-01"] })).toBe("Start is after 2026-01-01")
    expect(conditionText(spec, { table: "policy", column: "policy.gone", op: "filled", values: [] })).toBe("A missing column is filled")

    expect(showWhenText(spec, always())).toBe("Always shows")
    expect(showWhenText(spec, { match: "all", conditions: [ticked] })).toBe("Shows when BI cover is ticked")
    expect(showWhenText(spec, { match: "any", conditions: [ticked, onClass] })).toBe(
      "Shows when any of these are true: BI cover is ticked; Class is one of Motor, Fleet",
    )
  })

  it("prices what the sheets show: a condition that hides or shows a column changes the basis, a move does not", () => {
    const spec = covered()
    expect(pricingBasis(setPageShowWhen(spec, "p_bi", always()))).not.toBe(pricingBasis(spec))
    expect(pricingBasis(updateWidget(spec, "w_bi", { x: 100 }))).toBe(pricingBasis(spec))
  })
})

describe("charts", () => {
  /**
   * A one-row policy, equipment typed in many rows numbered by an index and keyed by item,
   * premiums priced for each item, and claims keyed otherwise.
   */
  const schema = (): FormSchema => ({
    tables: [
      table("policy", "policy", {
        columns: [
          column("policy.si", "sum_insured", { type: "float" }),
          column("policy.excess", "excess", { type: "int" }),
          column("policy.state", "state"),
        ],
      }),
      table("equipment", "equipment", {
        rows: "many",
        columns: [
          column("equipment.row", "row_number", { type: "int", index: true }),
          column("equipment.item", "item", { key: true, label: "Item" }),
          column("equipment.value", "value", { type: "float" }),
        ],
      }),
      table("premiums", "premiums", {
        role: "output",
        rows: "many",
        columns: [column("premiums.item", "item", { key: true }), column("premiums.premium", "premium", { type: "float" })],
      }),
      table("claims", "claims", {
        rows: "many",
        columns: [column("claims.claim", "claim", { key: true }), column("claims.paid", "paid", { type: "float" })],
      }),
    ],
  })
  const ref = (tableId: string, columnId: string): FieldRef => ({ table: tableId, column: columnId })
  const chart = (fields: FieldRef[], options: Partial<ChartWidget> = {}): ChartWidget => ({
    id: "w_chart",
    type: "chart",
    x: 0,
    y: 0,
    w: 480,
    h: 280,
    title: "Premiums",
    fields,
    show_when: { match: "all", conditions: [] },
    ...CHART_OPTIONS,
    ...options,
  })
  /** The schema with one sheet holding `widget`. */
  const charted = (widget: ChartWidget): FormSpec => ({ ...blank(), schema: schema(), pages: [{ ...blank().pages[0], widgets: [widget] }] })
  const problems = (widget: ChartWidget) => widgetProblems(charted(widget)).map((problem) => problem.message)
  const category = (spec: FormSpec) => {
    const found = findWidget(spec, "w_chart")?.widget
    return found?.type === "chart" ? found.category : undefined
  }

  it("counts the column labelling a Chart's rows as one it shows, and numbers the rows again once it is removed", () => {
    // Plotting premiums, its rows labelled by equipment's items.
    const spec = charted(chart([ref("premiums", "premiums.premium")], { category: ref("equipment", "equipment.item") }))

    expect(fieldUses(spec, "equipment", "equipment.item").map((w) => w.id)).toEqual(["w_chart"])
    expect(fieldUses(spec, "equipment").map((w) => w.id)).toEqual(["w_chart"])
    expect(fieldUses(spec, "equipment", "equipment.value")).toEqual([])

    expect(category(removeSchemaColumn(spec, "equipment", "equipment.value"))).toEqual(ref("equipment", "equipment.item"))
    expect(category(removeSchemaColumn(spec, "equipment", "equipment.item"))).toBeNull()
    expect(category(removeSchemaTable(spec, "equipment"))).toBeNull()
  })

  it("takes a Chart's rows from its first field's table, else from the column labelling them", () => {
    const spec = charted(chart([]))

    expect(chartGrain(spec, chart([ref("equipment", "equipment.value")]))).toBe("many:item")
    expect(chartGrain(spec, chart([ref("policy", "policy.si")], { category: ref("equipment", "equipment.item") }))).toBe("one")
    expect(chartGrain(spec, chart([], { category: ref("premiums", "premiums.item") }))).toBe("many:item")
    expect(chartGrain(spec, chart([ref("gone", "gone.x"), ref("claims", "claims.paid")]))).toBe("many:claim")
    expect(chartGrain(spec, chart([]))).toBeNull()
  })

  it("plots number columns other than the index", () => {
    const types: SchemaColumn["type"][] = ["int", "float", "str", "bool", "date"]
    expect(types.map((type) => plottable(column("a", "a", { type })))).toEqual([true, true, false, false, false])
    expect(plottable(column("a", "row_number", { type: "int", index: true }))).toBe(false)
  })

  it("labels a many-row Chart's rows by the columns of the tables keyed alike, an output's included, but never the index", () => {
    const spec = charted(chart([]))
    const labels = (widget: ChartWidget) =>
      labelColumns(spec, widget).map(({ table: shown, columns }) => [shown.id, columns.map((c) => c.name)])

    expect(labels(chart([ref("equipment", "equipment.value")]))).toEqual([
      ["equipment", ["item", "value"]],
      ["premiums", ["item", "premium"]],
    ])
    expect(labels(chart([ref("policy", "policy.si")]))).toEqual([])
    expect(labels(chart([]))).toEqual([])
  })

  it("says what stops a Chart plotting what it should", () => {
    expect(widgetName(chart([], { title: "" }))).toBe("A Chart")
    expect(problems(chart([]))).toEqual(['Chart "Premiums" shows no fields'])
    expect(problems(chart([ref("policy", "policy.si"), ref("policy", "policy.gone")]))).toEqual([
      'Chart "Premiums" shows a column that is no longer in the schema',
    ])
    expect(problems(chart([ref("policy", "policy.state")]))).toEqual(['Chart "Premiums" plots State, which is not a number'])
    expect(problems(chart([ref("equipment", "equipment.item")]))).toEqual(['Chart "Premiums" plots Item, which is not a number'])
    expect(problems(chart([ref("equipment", "equipment.row")]))).toEqual(['Chart "Premiums" plots Row number, which numbers the rows'])
    expect(problems(chart([ref("equipment", "equipment.value")], { category: ref("equipment", "equipment.gone") }))).toEqual([
      'Chart "Premiums" labels its rows by a column that is no longer in the schema',
    ])
    expect(problems(chart([ref("equipment", "equipment.value"), ref("claims", "claims.paid")]))).toEqual([
      'Chart "Premiums" shows tables whose rows do not line up',
    ])
    expect(problems(chart([ref("equipment", "equipment.value")], { category: ref("claims", "claims.claim") }))).toEqual([
      'Chart "Premiums" shows tables whose rows do not line up',
    ])
    expect(problems(chart([ref("equipment", "equipment.value"), ref("premiums", "premiums.premium")], { chart_type: "pie" }))).toEqual([
      'Chart "Premiums" is a pie of many rows, so it plots only its first field',
    ])
  })

  it("finds nothing wrong with a Chart of one-row tables, or of input and output tables keyed alike", () => {
    expect(problems(chart([ref("policy", "policy.si"), ref("policy", "policy.excess")]))).toEqual([])
    expect(problems(chart([ref("policy", "policy.si"), ref("policy", "policy.excess")], { chart_type: "pie" }))).toEqual([])
    expect(
      problems(chart([ref("equipment", "equipment.value"), ref("premiums", "premiums.premium")], { category: ref("premiums", "premiums.item") })),
    ).toEqual([])
  })
})

describe("output tables lined up with a grid's rows", () => {
  // Ids differ from names: the grid holds rows by id, the pricing answers by name.
  const claims = table("t_claims", "claims", { rows: "many", columns: [column("c_claim", "claim", { key: true })] })
  const equipment = table("t_equipment", "equipment", {
    rows: "many",
    columns: [column("c_item", "item", { key: true }), column("c_value", "value", { type: "float" })],
  })
  const premiums = table("t_premiums", "premiums", { role: "output", rows: "many", columns: [column("c_p_item", "item", { key: true })] })
  const fees = table("t_fees", "fees", { role: "output", rows: "many", columns: [column("c_fee", "fee", { key: true })] })
  const schema: FormSchema = { tables: [claims, equipment, premiums, fees] }
  const price = (typed: Record<string, unknown>[]) => ({
    sample: { equipment: typed },
    tables: {
      premiums: [{ item: "B", premium: 20 }, { item: "A", premium: 10 }],
      fees: [{ fee: "x" }],
    },
  })

  it("lines each many-row output up with the grid's rows through the first input table keyed alike", () => {
    const held: Record<string, Record<string, string>[]> = { t_equipment: [{ c_item: "A", c_value: "1" }, {}, { c_item: "B" }] }
    const lined = linedUpOutputs([claims, equipment], [premiums, fees], held, 4, price([{ item: "A", value: 1 }, { item: "B", value: null }]), schema)

    expect([...lined.keys()]).toEqual(["t_premiums"])
    expect(lined.get("t_premiums")).toEqual([{ item: "A", premium: 10 }, undefined, { item: "B", premium: 20 }, undefined])
    expect(linedUpOutputs([claims, equipment], [premiums], held, 4, null, schema)).toEqual(new Map())
  })

  it("reads the grid's rows as the quote does: a value in a column it leaves out fills no row", () => {
    const quoted: FormSchema = { tables: [{ ...equipment, columns: equipment.columns.filter((c) => c.id !== "c_value") }, premiums] }
    const held: Record<string, Record<string, string>[]> = { t_equipment: [{ c_value: "5" }, { c_item: "A" }] }
    const lined = linedUpOutputs([equipment], [premiums], held, 2, price([{ item: "A" }]), quoted)

    expect(lined.get("t_premiums")).toEqual([undefined, { item: "A", premium: 10 }])
  })
})
