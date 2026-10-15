/**
 * The pure operations on the workbench's form (specs/workbench): the schema's tables and
 * columns, the sheets, their sections and what the components in them show of the schema,
 * and what would stop the schema being the pipeline's tables.
 */
import { describe, expect, it } from "vitest"
import type { ChartWidget, CollectionWidget, Condition, FieldRef, FormSchema, FormSpec, SchemaColumn, SchemaTable, Section, ShowWhen } from "../../api/types"
import {
  addPage,
  addSchemaColumn,
  addSchemaTable,
  addSection,
  addSpacer,
  addWidget,
  BLANK_THEME,
  changeSchemaColumnType,
  CHART_OPTIONS,
  chartGrain,
  COLLECTION_OPTIONS,
  conditionText,
  conditionUses,
  createIndexColumn,
  createPage,
  createSchemaColumn,
  createSchemaTable,
  createSection,
  createWidget,
  duplicateWidget,
  fieldColumn,
  fieldUses,
  findSection,
  findWidget,
  fitSection,
  HEADING_OPTIONS,
  keptInside,
  labelColumns,
  linedUpOutputs,
  moveInOrder,
  movePage,
  moveSchemaColumn,
  moveSection,
  moveWidget,
  orderItems,
  pageProblems,
  pasteWidget,
  placeWidget,
  plottable,
  pricingBasis,
  readableName,
  removePage,
  removeSchemaColumn,
  removeSchemaTable,
  removeSection,
  removeSpacer,
  removeWidget,
  renamePage,
  renameSection,
  rowsShown,
  schemaProblems,
  sectionName,
  sectionProblems,
  setPageShowWhen,
  setPageWidth,
  setRowPerValue,
  setSchemaTableRows,
  setSectionHeight,
  setSectionShowWhen,
  setTableRows,
  setThemeColour,
  shownFields,
  showsFields,
  showWhenText,
  splitSection,
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
  withSample,
  withSampleCell,
  withSampleRows,
  type Widget,
} from "../workbenchForm"
import { columnKey, sheetVisibility } from "../sheetVisibility"

/** A section `height` tall holding `widgets`, always shown unless `showWhen` says otherwise. */
const section = (id: string, title: string, height: number, widgets: Widget[] = [], showWhen: ShowWhen = { match: "all", conditions: [] }): Section => ({
  id,
  title,
  height,
  show_when: showWhen,
  widgets,
})

/** A blank form: one sheet, `p`, 1280 wide, holding one empty section, `s`, 560 tall. */
const blank = (): FormSpec => ({
  version: 1,
  name: "t",
  theme: { ...BLANK_THEME },
  schema: { tables: [] },
  pages: [{ id: "p", title: "Sheet 1", width: 1280, show_when: { match: "all", conditions: [] }, sections: [section("s", "Section 1", 560)] }],
  sample: {},
})

/** The components of a sheet's section, the first of the first sheet unless said otherwise. */
const widgetsOf = (spec: FormSpec, page = 0, at = 0): Widget[] => spec.pages[page].sections[at].widgets

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
    expect(spec.schema.tables[0].columns.map((c) => [c.name, c.type, c.index, c.group, c.default_value])).toEqual([
      ["column", "str", false, "", ""],
      ["column_2", "str", false, "", ""],
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
    let spec = setSchemaTableRows(withTable(column("a", "item", { default_value: "Laptop" }), column("b", "value", { group: "Cover" })), "t", "many")
    expect(messages(spec)).toEqual(['Table "t" has many rows, so it needs a key column'])
    // A many-row table's columns take no default; a group stays.
    expect(spec.schema.tables[0].columns.map((c) => [c.default_value, c.group])).toEqual([
      ["", ""],
      ["", "Cover"],
    ])

    spec = updateSchemaColumn(spec, "t", "a", { key: true })
    expect(messages(spec)).toEqual([])
    const index = createIndexColumn(spec, "t")
    spec = addSchemaColumn(spec, "t", index, 0)
    spec = withSampleCell(spec, "t", 0, "a", "x")
    spec = addWidget(spec, "s", { ...createWidget("tableInput", { x: 0, y: 0, w: 720, h: 200 }), fields: [{ table: "t", column: index.id }] })

    spec = setSchemaTableRows(spec, "t", "one")
    expect(spec.schema.tables[0].columns.map((c) => [c.name, c.key])).toEqual([["item", false], ["value", false]])
    expect(widgetsOf(spec)[0]).toMatchObject({ fields: [] })
    expect(spec.sample).toEqual({ t: [{ a: "x" }] })
  })

  it("changes a column's type, dropping the rules the new type cannot have", () => {
    let spec = withTable(
      column("a", "value", {
        type: "float",
        min: 1,
        max: 5,
        options: ["1", "2"],
        options_source: { file: "bands.csv", column: "band" },
        number_format: "percent",
        decimal_places: 1,
        use_grouping: false,
      }),
    )

    // Between Decimal and Integer, a number keeps its range, its allowed values, the file
    // they came from, and its format.
    spec = changeSchemaColumnType(spec, "t", "a", "int")
    expect(spec.schema.tables[0].columns[0]).toMatchObject({
      type: "int",
      min: 1,
      max: 5,
      options: ["1", "2"],
      options_source: { file: "bands.csv", column: "band" },
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
    expect(spec.schema.tables[0].columns[0]).toMatchObject({ type: "date", options: [], options_source: null })
    spec = changeSchemaColumnType(spec, "t", "a", "int")
    expect(spec.schema.tables[0].columns[0]).toMatchObject({ type: "int", min: null, max: null, options: [] })

    // A default is text, kept between the types that take text, or a tick, kept while a
    // tick box stays one.
    spec = updateSchemaColumn(spec, "t", "a", { default_value: "5" })
    spec = changeSchemaColumnType(spec, "t", "a", "str")
    expect(spec.schema.tables[0].columns[0].default_value).toBe("5")
    spec = changeSchemaColumnType(spec, "t", "a", "bool")
    expect(spec.schema.tables[0].columns[0].default_value).toBe("")
    spec = updateSchemaColumn(spec, "t", "a", { default_value: true })
    spec = changeSchemaColumnType(spec, "t", "a", "date")
    expect(spec.schema.tables[0].columns[0].default_value).toBe("")
    // A date's box shows a day alone: text that is not one goes as the column becomes a
    // Date, and a day stays.
    spec = updateSchemaColumn(changeSchemaColumnType(spec, "t", "a", "str"), "t", "a", { default_value: "not-a-date" })
    spec = changeSchemaColumnType(spec, "t", "a", "date")
    expect(spec.schema.tables[0].columns[0].default_value).toBe("")
    spec = updateSchemaColumn(changeSchemaColumnType(spec, "t", "a", "str"), "t", "a", { default_value: "2026-01-01" })
    spec = changeSchemaColumnType(spec, "t", "a", "date")
    expect(spec.schema.tables[0].columns[0].default_value).toBe("2026-01-01")
  })

  it("checks an input column's rules, which an output table ignores", () => {
    const spec = withTable(
      column("a", "value", { type: "float", min: 10, max: 5 }),
      column("b", "year", { type: "int", options: ["2020", "2020.5"], options_source: { file: "years.csv", column: "year" } }),
      column("c", "deductible", { type: "float", options: ["500", "1e3", "lots"], options_source: { file: "deductibles.csv", column: "deductible" } }),
      column("d", "row_number", { type: "int", index: true, min: 10, max: 5 }),
      // Allowed values come from a params file: ones typed in, as an older form holds them, do not.
      column("e", "state", { options: ["CA", "NY"] }),
    )

    expect(messages(spec)).toEqual([
      'Column "value" in Table "t" has a minimum above its maximum',
      `Column "year" in Table "t" allows "2020.5", which isn't a whole number`,
      `Column "deductible" in Table "t" allows "lots", which isn't a number`,
      'Column "state" in Table "t" has allowed values typed in, not read from a params file',
    ])
    expect(messages(updateSchemaTable(spec, "t", { role: "output" }))).toEqual([])
  })

  it("checks a one-row input column's default against its rules, as a cell typed into it is", () => {
    const spec = withTable(
      column("a", "value", { type: "float", min: 10, default_value: "5" }),
      column("b", "years", { type: "int", default_value: "2.5" }),
      column("c", "cover", { options: ["ACV", "RC"], options_source: { file: "covers.csv", column: "cover" }, default_value: "New" }),
      column("d", "smoker", { type: "bool", default_value: "yes" }),
      column("e", "name", { default_value: true }),
      // Kept: a number as the server reads one, an allowed value, a tick, a blank or an unticked box.
      column("f", "limit", { type: "float", min: 0, default_value: "£250,000" }),
      column("g", "basis", { options: ["ACV", "RC"], options_source: { file: "covers.csv", column: "cover" }, default_value: "RC" }),
      column("h", "vat", { type: "bool", default_value: true }),
      column("i", "note", { required: true, default_value: " " }),
      column("j", "flag", { type: "bool", default_value: false }),
      column("k", "expiry", { type: "date", default_value: "2026-12-31" }),
      // Refused: a Date's default that is not a day, as a hand-edited file may hold.
      column("l", "inception", { type: "date", default_value: "2026-02-30" }),
    )

    expect(messages(spec)).toEqual([
      'Column "value" in Table "t" has a default its rules refuse: At least 10',
      'Column "years" in Table "t" has a default its rules refuse: Not a whole number',
      'Column "cover" in Table "t" has a default its rules refuse: Not one of the allowed values',
      'Column "smoker" in Table "t" has a default its rules refuse: Not a tick',
      'Column "name" in Table "t" has a default its rules refuse: Not text',
      'Column "inception" in Table "t" has a default its rules refuse: Not a date',
    ])
    // An output table takes no default to check, as it takes no rules.
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

  it("gives a table a row for each of one column's allowed values, until the table has one row or the column's type takes none", () => {
    const values = { options: ["AOP", "EQ"], options_source: { file: "perils.csv", column: "peril" } }
    let spec = setSchemaTableRows(withTable(column("a", "peril", { key: true, ...values }), column("b", "zone", values)), "t", "many")
    const given = () => spec.schema.tables[0].columns.map((c) => c.row_per_value)

    spec = setRowPerValue(spec, "t", "a", true)
    expect(given()).toEqual([true, false])
    // Ticking another unticks the first, as one column gives a table its rows.
    spec = setRowPerValue(spec, "t", "b", true)
    expect(given()).toEqual([false, true])
    spec = setRowPerValue(spec, "t", "b", false)
    expect(given()).toEqual([false, false])

    spec = setRowPerValue(spec, "t", "a", true)
    expect(given()).toEqual([true, false])
    expect(setSchemaTableRows(spec, "t", "one").schema.tables[0].columns.map((c) => c.row_per_value)).toEqual([false, false])
    expect(changeSchemaColumnType(spec, "t", "a", "date").schema.tables[0].columns[0]).toMatchObject({ options: [], row_per_value: false })
    expect(changeSchemaColumnType(spec, "t", "a", "int").schema.tables[0].columns[0].row_per_value).toBe(true)
  })

  it("refuses to edit a table or column that is not in the schema", () => {
    expect(() => updateSchemaTable(blank(), "missing", { name: "x" })).toThrow("no table missing")
    expect(() => updateSchemaColumn(withTable(), "t", "missing", { name: "x" })).toThrow("no column missing")
  })
})

describe("what the sheets show", () => {
  /** Policy details (one row) shown by a Collection, equipment (many rows) by a Table, in the sheet's one section, with a sample. */
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
          width: 1280,
          show_when: { match: "all", conditions: [] },
          sections: [
            section("s", "Section 1", 560, [
              { id: "w_boxes", type: "collection", x: 0, y: 0, w: 720, h: 120, title: "Policy", columns: 3, ...COLLECTION_OPTIONS, fields: [{ table: "policy", column: "policy.state" }], show_when: { match: "all", conditions: [] } },
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
            ]),
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
    widgetsOf(spec).filter(showsFields).find((widget) => widget.id === widgetId)?.fields.map((field) => field.column)

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
    removeSpacer(addSpacer(before, "w_boxes"), "w_boxes", 1)
    toggleField(addSpacer(before, "w_boxes"), "w_boxes", { table: "policy", column: "policy.state" })
    moveInOrder(before, "w_grid", 0, 1)

    expect(JSON.stringify(before)).toBe(snapshot)
  })

  it("names new sheets uniquely, each as wide as the last and holding one section 560 tall, renames them, and keeps the last one", () => {
    let spec = blank()
    const second = createPage(spec)
    expect(second.title).toBe("Sheet 2")
    expect(second.width).toBe(1280)
    // The whole sheet is one section until another is added.
    expect(second.sections).toEqual([
      { id: expect.stringMatching(/^section_/), title: "Section 1", height: 560, show_when: { match: "all", conditions: [] }, widgets: [] },
    ])
    spec = addPage(spec, second)
    spec = renamePage(spec, second.id, "Equipment")
    expect(spec.pages.map((page) => page.title)).toEqual(["Sheet 1", "Equipment"])
    expect(createPage(spec).title).toBe("Sheet 3")
    expect(createPage(setPageWidth(spec, second.id, 1600)).width).toBe(1600)
    expect(createPage(setPageWidth(spec, "p", 1600)).width).toBe(1280)

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
    // A Table as the server's defaults have it: numbered, compact, lined between rows, striped, not
    // fixed, its header as written and framed; a Collection labelled above its boxes, comfortable
    // and framed.
    expect(grid).toMatchObject({ type: "tableInput", title: "", fields: [], rows: 3, row_numbers: true, density: "compact", gridlines: "rows", striped: true, fixed_rows: false, header: "plain", frame: true })
    expect(boxes).toMatchObject({ type: "collection", title: "", fields: [], columns: 3, labels: "above", density: "comfortable", frame: true, spacers: [] })
    // Spacers of its own, not the options' one list.
    expect(boxes.spacers).not.toBe(COLLECTION_OPTIONS.spacers)
    expect(grid).not.toHaveProperty("columns")
    expect(boxes).not.toHaveProperty("gridlines")
    // In the file's order, so a component the view made compares equal to it saved.
    expect(Object.keys(grid)).toEqual(["id", "x", "y", "w", "h", "type", "title", "fields", "show_when", "rows", "row_numbers", "density", "gridlines", "striped", "fixed_rows", "header", "frame", "transposed", "groups"])
    expect(Object.keys(boxes)).toEqual(["id", "x", "y", "w", "h", "type", "title", "fields", "show_when", "columns", "labels", "density", "frame", "spacers"])

    spec = addWidget(addWidget(spec, "s", grid), "s", boxes)
    spec = updateWidget(spec, grid.id, { title: "Equipment", rows: 5, x: 16, gridlines: "all", fixed_rows: true })
    expect(findWidget(spec, grid.id)).toMatchObject({
      page: { id: "p" },
      section: { id: "s" },
      index: 0,
      widget: { title: "Equipment", rows: 5, x: 16, gridlines: "all", fixed_rows: true },
    })
    expect(() => updateWidget(spec, grid.id, { columns: 2 })).toThrow("Only a Collection has columns")
    expect(() => updateWidget(spec, boxes.id, { rows: 2 })).toThrow("Only a Table has rows")
    // A Collection takes its own look, the density and the frame a Table has too, and none of a Table's own.
    spec = updateWidget(spec, boxes.id, { labels: "beside", density: "compact", frame: false })
    expect(findWidget(spec, boxes.id)?.widget).toMatchObject({ labels: "beside", density: "compact", frame: false })
    expect(() => updateWidget(spec, boxes.id, { header: "caps" })).toThrow("Only a Table has header")
    expect(() => updateWidget(spec, boxes.id, { transposed: true })).toThrow("Only a Table has transposed")
    expect(() => updateWidget(spec, boxes.id, { groups: true })).toThrow("Only a Table has groups")
    expect(() => updateWidget(spec, grid.id, { labels: "beside" })).toThrow("Only a Collection has labels")
    expect(() => updateWidget(spec, "missing", { title: "x" })).toThrow("No sheet has a component missing")

    const copied = duplicateWidget(spec, grid.id)
    const copy = findWidget(copied.spec, copied.id)
    expect(copy).toMatchObject({ index: 1, widget: { type: "tableInput", title: "Equipment", rows: 5, x: 32, y: 16 } })
    expect(copied.id).not.toBe(grid.id)

    spec = removeWidget(copied.spec, grid.id)
    expect(widgetsOf(spec).map((widget) => widget.id)).toEqual([copied.id, boxes.id])
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

    let spec = [chart, grid, boxes].reduce((current, widget) => addWidget(current, "s", widget), blank())
    const options = { category: { table: "t", column: "c" }, chart_type: "pie", orientation: "horizontal", stacked: true, legend: "right", data_labels: true } as const
    spec = updateWidget(spec, chart.id, { title: "Premiums", ...options })
    expect(findWidget(spec, chart.id)?.widget).toMatchObject({ title: "Premiums", ...options })
    expect(() => updateWidget(spec, grid.id, { chart_type: "line" })).toThrow("Only a Chart has chart_type")
    expect(() => updateWidget(spec, boxes.id, { chart_type: "line" })).toThrow("Only a Chart has chart_type")
    expect(() => updateWidget(spec, chart.id, { rows: 2 })).toThrow("Only a Table has rows")
    expect(() => updateWidget(spec, chart.id, { columns: 2 })).toThrow("Only a Collection has columns")
    expect(() => updateWidget(spec, chart.id, { frame: false })).toThrow("Only a Table or a Collection has frame")
    expect(() => updateWidget(spec, chart.id, { density: "compact" })).toThrow("Only a Table or a Collection has density")

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

    let spec = [heading, grid].reduce((current, widget) => addWidget(current, "s", widget), blank())
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
    expect(() => moveInOrder(spec, heading.id, 0, 1)).toThrow("A Heading shows no fields")
    expect(shownFields(spec, heading)).toEqual([])

    const copied = duplicateWidget(spec, heading.id)
    expect(findWidget(copied.spec, copied.id)).toMatchObject({ index: 1, widget: { type: "heading", ...look, x: 32, y: 16 } })
  })

  it("names a Heading by its text, says when it has none, and passes it by when a column goes", () => {
    let spec = addWidget(sheet(), "s", { ...createWidget("heading", { x: 0, y: 400, w: 480, h: 40 }), id: "w_head" })
    const head = (form: FormSpec) => widgetsOf(form)[2]
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

    spec = moveInOrder(spec, "w_grid", 1, 0)
    expect(fields(spec, "w_grid")).toEqual(["equipment.value", "equipment.item"])
    // A Table's order is its fields alone.
    expect(orderItems(widgetsOf(spec)[1] as Exclude<Widget, { type: "heading" }>)).toEqual([
      { kind: "field", field: { table: "equipment", column: "equipment.value" } },
      { kind: "field", field: { table: "equipment", column: "equipment.item" } },
    ])
  })

  it("puts spacers among a Collection's fields, moves and removes them, and keeps their places as fields come and go", () => {
    // The policy's boxes showing its state, year and region.
    let spec = addSchemaColumn(addSchemaColumn(sheet(), "policy", column("policy.year", "year", { type: "int" })), "policy", column("policy.region", "region"))
    spec = toggleField(toggleField(spec, "w_boxes", { table: "policy", column: "policy.year" }), "w_boxes", { table: "policy", column: "policy.region" })
    const boxes = (form: FormSpec) => widgetsOf(form)[0] as CollectionWidget
    const order = (form: FormSpec) => orderItems(boxes(form)).map((item) => (item.kind === "field" ? item.field.column : "spacer"))

    // Added after the last field, then moved between the first two; a second there makes the gap wider.
    spec = addSpacer(spec, "w_boxes")
    expect(order(spec)).toEqual(["policy.state", "policy.year", "policy.region", "spacer"])
    expect(boxes(spec).spacers).toEqual([3])
    spec = moveInOrder(spec, "w_boxes", 3, 1)
    expect(order(spec)).toEqual(["policy.state", "spacer", "policy.year", "policy.region"])
    expect(boxes(spec).spacers).toEqual([1])
    spec = moveInOrder(addSpacer(spec, "w_boxes"), "w_boxes", 4, 2)
    expect(order(spec)).toEqual(["policy.state", "spacer", "spacer", "policy.year", "policy.region"])
    expect(boxes(spec).spacers).toEqual([1, 1])

    // A field moved past the spacers leaves them where they are in the order.
    expect(order(moveInOrder(spec, "w_boxes", 0, 4))).toEqual(["spacer", "spacer", "policy.year", "policy.region", "policy.state"])
    expect(boxes(moveInOrder(spec, "w_boxes", 0, 4)).spacers).toEqual([0, 0])

    // A field it stops showing, unticked or gone from the schema, takes its place in the count
    // with it; a field ticked goes after any spacer at the end.
    const unticked = toggleField(spec, "w_boxes", { table: "policy", column: "policy.year" })
    expect(order(unticked)).toEqual(["policy.state", "spacer", "spacer", "policy.region"])
    expect(boxes(removeSchemaColumn(spec, "policy", "policy.state")).spacers).toEqual([0, 0])
    const ticked = toggleField(addSpacer(unticked, "w_boxes"), "w_boxes", { table: "policy", column: "policy.year" })
    expect(order(ticked)).toEqual(["policy.state", "spacer", "spacer", "policy.region", "spacer", "policy.year"])
    // Left showing no fields, it keeps no spacers.
    expect(boxes(removeSchemaTable(spec, "policy"))).toMatchObject({ fields: [], spacers: [] })

    // Removed at its place in the order; a place holding a field holds no spacer.
    spec = removeSpacer(spec, "w_boxes", 2)
    expect(order(spec)).toEqual(["policy.state", "spacer", "policy.year", "policy.region"])
    expect(() => removeSpacer(spec, "w_boxes", 0)).toThrow("No spacer is at 0")
    expect(() => moveInOrder(spec, "w_boxes", 9, 0)).toThrow("Nothing is at 9 in its order")

    // Only a Collection has spacers.
    expect(() => addSpacer(spec, "w_grid")).toThrow("Only a Collection has spacers")
    expect(() => removeSpacer(spec, "w_grid", 0)).toThrow("Only a Collection has spacers")
    expect(() => updateWidget(spec, "w_grid", { spacers: [0] })).toThrow("Only a Collection has spacers")
  })

  it("leaves out of a copy pasted what it shows or tests of a column the schema has lost since, as removing the column does", () => {
    // The policy's boxes showing its state, year and region, a spacer before the region,
    // shown while the year and the state are as said.
    let spec = addSchemaColumn(addSchemaColumn(sheet(), "policy", column("policy.year", "year", { type: "int" })), "policy", column("policy.region", "region"))
    spec = toggleField(toggleField(spec, "w_boxes", { table: "policy", column: "policy.year" }), "w_boxes", { table: "policy", column: "policy.region" })
    spec = moveInOrder(addSpacer(spec, "w_boxes"), "w_boxes", 3, 2)
    const onYear: Condition = { table: "policy", column: "policy.year", op: "equals", values: ["2024"] }
    const onState: Condition = { table: "policy", column: "policy.state", op: "equals", values: ["NY"] }
    spec = updateWidget(spec, "w_boxes", { show_when: { match: "all", conditions: [onYear, onState] } })
    const copied = findWidget(spec, "w_boxes")!.widget

    // The year removed after the copy: pasted, the copy is as the original is now.
    const lost = removeSchemaColumn(spec, "policy", "policy.year")
    const pasted = pasteWidget(lost, copied, "s")
    expect(findWidget(pasted.spec, pasted.id)?.widget).toEqual({ ...findWidget(lost, "w_boxes")!.widget, id: pasted.id, x: 16, y: 16 })
    expect(findWidget(pasted.spec, pasted.id)?.widget).toMatchObject({
      fields: [{ table: "policy", column: "policy.state" }, { table: "policy", column: "policy.region" }],
      spacers: [1],
      show_when: { match: "all", conditions: [onState] },
    })
    // The whole table removed: no fields, so no spacers, and no conditions.
    const emptied = pasteWidget(removeSchemaTable(spec, "policy"), copied, "s")
    expect(findWidget(emptied.spec, emptied.id)?.widget).toMatchObject({ fields: [], spacers: [], show_when: { match: "all", conditions: [] } })
  })

  it("sets one of the sheets' colours, leaving the others the editor's own, and the editor's own again", () => {
    expect(BLANK_THEME).toEqual({ sheet_color: null, background_color: null, border_color: null, font_color: null, input_cell_color: null, output_cell_color: null })
    const spec = sheet()
    const coloured = setThemeColour(spec, "border_color", "#2563EB")
    expect(coloured.theme).toEqual({ ...BLANK_THEME, border_color: "#2563EB" })
    expect(spec.theme).toEqual(BLANK_THEME)
    expect(setThemeColour(coloured, "border_color", null).theme).toEqual(BLANK_THEME)
  })

  it("replaces the sample whole, as a test quote opened does, leaving the values given untouched", () => {
    const values = { equipment: [{ "equipment.item": "Z" }] }
    const spec = withSample(sheet(), values)
    expect(spec.sample).toEqual({ equipment: [{ "equipment.item": "Z" }] })
    expect(spec.sample).not.toBe(values)
    spec.sample.equipment[0]["equipment.item"] = "Y"
    expect(values.equipment[0]["equipment.item"]).toBe("Z")
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

  it("sets the rows a Table's grid starts with, starting its grid in the sample again at them and keeping every value typed", () => {
    // Its table keeps its rows up to the last with a value typed, made up to the rows with empty ones.
    let spec = withSampleRows(sheet(), { equipment: [{ "equipment.item": "A" }, {}, { "equipment.item": "C" }, { "equipment.item": " " }, {}] })
    expect(setTableRows(spec, "w_grid", 1).sample.equipment).toEqual([{ "equipment.item": "A" }, {}, { "equipment.item": "C" }])
    const more = setTableRows(spec, "w_grid", 5)
    expect(more.sample.equipment).toEqual([{ "equipment.item": "A" }, {}, { "equipment.item": "C" }, {}, {}])
    expect(widgetsOf(more)[1]).toMatchObject({ rows: 5 })
    // A value in a column the grid does not show is typed too.
    expect(setTableRows(withSampleRows(spec, { equipment: [{}, { "equipment.gone": "x" }, {}] }), "w_grid", 1).sample.equipment).toEqual([{}, { "equipment.gone": "x" }])

    // With nothing typed it holds no rows, so the grid shows those it starts with; the policy's row stays.
    spec = withSampleRows(spec, { equipment: [{}, { "equipment.value": "" }] })
    expect(setTableRows(spec, "w_grid", 4).sample).toEqual({ policy: [{ "policy.state": "NY" }] })
    expect(() => setTableRows(spec, "w_boxes", 2)).toThrow("Only a Table has rows")
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
    expect(widgetName(widgetsOf(spec)[0])).toBe('Collection "Policy"')
    expect(widgetName(widgetsOf(updateWidget(spec, "w_grid", { title: "" }))[1])).toBe("A Table")
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

  it("says a Table's tables do not line up when a column gives one its rows and another has rows of its own, or other values", () => {
    const values = (options: string[]) => ({ options, options_source: { file: "plant.csv", column: "item" }, row_per_value: true })
    let spec = updateSchemaColumn(sheet(), "equipment", "equipment.item", values(["Crane", "Lift"]))
    // An input table keyed alike, shown beside it, its rows its own.
    spec = addSchemaTable(spec, table("hire", "hire", { rows: "many", columns: [column("hire.item", "item", { key: true }), column("hire.days", "days", { type: "int" })] }))
    spec = toggleField(spec, "w_grid", { table: "hire", column: "hire.days" })
    const apart = ['Table "Equipment" shows tables whose rows do not line up']

    expect(widgetProblems(spec).map((p) => p.message)).toEqual(apart)
    // Given the same rows by its own column, in the same order, it lines up; given others, it does not.
    expect(widgetProblems(updateSchemaColumn(spec, "hire", "hire.item", values(["Crane", "Lift"])))).toEqual([])
    expect(widgetProblems(updateSchemaColumn(spec, "hire", "hire.item", values(["Lift", "Crane"]))).map((p) => p.message)).toEqual(apart)
  })
})

describe("sections", () => {
  const always = (): ShowWhen => ({ match: "all", conditions: [] })
  /**
   * A sheet of three sections: "Policy", the policy's boxes, as tall as they reach (136);
   * "Equipment", the equipment's Table, 16 below it (232); and "Notes", empty and 48 tall.
   */
  const stacked = (): FormSpec => ({
    ...blank(),
    pages: [
      {
        ...blank().pages[0],
        sections: [
          section("s_policy", "Policy", 136, [{ ...createWidget("collection", { x: 16, y: 16, w: 400, h: 120 }), id: "w_boxes" }]),
          section("s_equipment", "Equipment", 232, [{ ...createWidget("tableInput", { x: 16, y: 16, w: 720, h: 200 }), id: "w_grid" }]),
          section("s_notes", "Notes", 48),
        ],
      },
    ],
  })
  /** `stacked` with Equipment 272 tall, holding a note under its Table, 232 to 272. */
  const noted = (): FormSpec =>
    addWidget(setSectionHeight(stacked(), "s_equipment", 272), "s_equipment", { ...createWidget("heading", { x: 16, y: 232, w: 480, h: 40 }), id: "w_note" })
  const titles = (spec: FormSpec) => spec.pages[0].sections.map((s) => s.title)
  const heights = (spec: FormSpec) => spec.pages[0].sections.map((s) => s.height)
  /** The ids of the components in each of the first sheet's sections, in its order. */
  const placed = (spec: FormSpec) => spec.pages[0].sections.map((s) => s.widgets.map((widget) => widget.id))
  const nowhere = { x: 0, y: 0, w: 80, h: 24 }

  it("creates sections named in turn on their sheet, 160 tall unless said otherwise, always shown and empty", () => {
    let spec = blank()
    const second = createSection(spec.pages[0])
    expect(second).toEqual({ id: expect.stringMatching(/^section_/), title: "Section 2", height: 160, show_when: always(), widgets: [] })
    spec = addSection(spec, "p", second)
    expect(createSection(spec.pages[0]).title).toBe("Section 3")
    expect(createSection(spec.pages[0]).id).not.toBe(createSection(spec.pages[0]).id)
    expect(createSection(spec.pages[0], 300).height).toBe(300)
    // Counted on its own sheet: a new sheet's next section is its second.
    expect(createSection(createPage(spec)).title).toBe("Section 2")
  })

  it("adds a section at a place on its sheet, or at the end", () => {
    const spec = stacked()
    const cover = { ...createSection(spec.pages[0]), title: "Cover" }

    expect(titles(addSection(spec, "p", cover))).toEqual(["Policy", "Equipment", "Notes", "Cover"])
    expect(titles(addSection(spec, "p", cover, 0))).toEqual(["Cover", "Policy", "Equipment", "Notes"])
    expect(titles(addSection(spec, "p", cover, 2))).toEqual(["Policy", "Equipment", "Cover", "Notes"])
    expect(titles(spec)).toEqual(["Policy", "Equipment", "Notes"])
    expect(() => addSection(spec, "missing", cover)).toThrow("The form has no sheet missing")
  })

  it("splits a section at a line down it, the components there or below moving into the new section just after it, keeping their places", () => {
    // Equipment holds its Table, 16 to 216, and a note under it, 232 to 272, and is 272 tall.
    const spec = noted()
    const cover = { ...createSection(spec.pages[0]), title: "Cover" }

    const split = splitSection(spec, "s_equipment", 232, cover)
    expect(titles(split)).toEqual(["Policy", "Equipment", "Cover", "Notes"])
    expect(placed(split)).toEqual([["w_boxes"], ["w_grid"], ["w_note"], []])
    expect(findSection(split, cover.id)?.section).toMatchObject({ id: cover.id, title: "Cover", show_when: always() })
    // The note at the line moves to the new section's top, 232 down the sheet as before;
    // Equipment keeps the height above the line, and the new section takes the rest, 40, but never under 48.
    expect(findWidget(split, "w_note")?.widget).toMatchObject({ x: 16, y: 0, w: 480, h: 40 })
    expect(findWidget(split, "w_grid")?.widget).toMatchObject({ x: 16, y: 16 })
    expect(heights(split)).toEqual([136, 232, 48, 48])

    // Split right after the first section, the new one goes in second.
    expect(titles(splitSection(spec, "s_policy", 64, cover))).toEqual(["Policy", "Cover", "Equipment", "Notes"])
    expect(() => splitSection(spec, "missing", 64, cover)).toThrow("No sheet has a section missing")
  })

  it("keeps a section split across a component as tall as what stays in it reaches, pushing what moved down by as much", () => {
    const spec = noted()
    const cover = { ...createSection(spec.pages[0]), title: "Cover" }

    // The line at 100 crosses the Table, 16 to 216, which stays: Equipment keeps 216, not 100,
    // so the note, 132 down the new section, sits 116 lower on the sheet than it did.
    const split = splitSection(spec, "s_equipment", 100, cover)
    expect(placed(split)).toEqual([["w_boxes"], ["w_grid"], ["w_note"], []])
    expect(findWidget(split, "w_note")?.widget).toMatchObject({ y: 132 })
    // The new section takes the rest of the height, 272 less 100, as far as the note reaches.
    expect(heights(split)).toEqual([136, 216, 172, 48])
  })

  it("gives either part of a split section with nothing in it 48", () => {
    const spec = stacked()
    const cover = { ...createSection(spec.pages[0]), title: "Cover" }

    // Above the Table's top, everything moves: Equipment, left empty, is 48, and the Table keeps its place 8 down.
    const above = splitSection(spec, "s_equipment", 8, cover)
    expect(placed(above)).toEqual([["w_boxes"], [], ["w_grid"], []])
    expect(findWidget(above, "w_grid")?.widget).toMatchObject({ y: 8 })
    expect(heights(above)).toEqual([136, 48, 224, 48])

    // Below the boxes' top, nothing moves: the new section, the 8 left under the line, is 48.
    const below = splitSection(spec, "s_policy", 128, cover)
    expect(placed(below)).toEqual([["w_boxes"], [], ["w_grid"], []])
    expect(heights(below)).toEqual([136, 48, 232, 48])
  })

  it("renames a section and sets when it shows, leaving its input untouched", () => {
    const spec = stacked()
    const whileCovered: ShowWhen = { match: "any", conditions: [{ table: "policy", column: "policy.cover", op: "ticked", values: [] }] }

    expect(titles(renameSection(spec, "s_notes", "Endorsements"))).toEqual(["Policy", "Equipment", "Endorsements"])
    const next = setSectionShowWhen(spec, "s_equipment", whileCovered)
    expect(findSection(next, "s_equipment")?.section.show_when).toEqual(whileCovered)
    expect(findSection(next, "s_equipment")?.section.show_when).not.toBe(whileCovered)
    expect(findSection(spec, "s_equipment")?.section.show_when).toEqual(always())
    expect(titles(spec)).toEqual(["Policy", "Equipment", "Notes"])
    expect(() => renameSection(spec, "missing", "Cover")).toThrow("No sheet has a section missing")
    expect(() => setSectionShowWhen(spec, "missing", whileCovered)).toThrow("No sheet has a section missing")
  })

  it("sets a section's height in whole pixels, never below what its components reach or 48, moving nothing in it", () => {
    const spec = stacked()
    const heightOf = (form: FormSpec, id: string) => findSection(form, id)?.section.height

    expect(heightOf(setSectionHeight(spec, "s_policy", 400), "s_policy")).toBe(400)
    // Never under its lowest component's bottom, 16 + 120, nor under 48, empty or not; and in whole pixels.
    expect(heightOf(setSectionHeight(spec, "s_policy", 100), "s_policy")).toBe(136)
    expect(heightOf(setSectionHeight(spec, "s_notes", 10), "s_notes")).toBe(48)
    expect(heightOf(setSectionHeight(spec, "s_notes", -50), "s_notes")).toBe(48)
    expect(heightOf(setSectionHeight(spec, "s_notes", 200.4), "s_notes")).toBe(200)
    expect(heightOf(setSectionHeight(spec, "s_notes", 200.5), "s_notes")).toBe(201)

    // Its components are placed from its top, so nothing but its height changes.
    const taller = setSectionHeight(spec, "s_equipment", 480)
    expect(heights(taller)).toEqual([136, 480, 48])
    expect(taller.pages[0].sections.map((s) => s.widgets)).toEqual(spec.pages[0].sections.map((s) => s.widgets))
    expect(heights(spec)).toEqual([136, 232, 48])
    expect(() => setSectionHeight(spec, "missing", 100)).toThrow("No sheet has a section missing")
  })

  it("fits a section 16 below its lowest component, or to 48 with none, whether that shrinks or grows it", () => {
    const spec = stacked()

    expect(heights(fitSection(spec, "s_policy"))).toEqual([152, 232, 48])
    expect(heights(fitSection(setSectionHeight(spec, "s_policy", 400), "s_policy"))).toEqual([152, 232, 48])
    expect(heights(fitSection(setSectionHeight(spec, "s_notes", 300), "s_notes"))).toEqual([136, 232, 48])
    expect(() => fitSection(spec, "missing")).toThrow("No sheet has a section missing")
  })

  it("gives the form as given for a height or a fit that leaves a section as tall as it is, so no edit is recorded", () => {
    const spec = stacked()

    // Fitted already: 16 below the Table's bottom, and empty at 48.
    expect(fitSection(spec, "s_equipment")).toBe(spec)
    expect(fitSection(spec, "s_notes")).toBe(spec)
    // The height it has, or one rounded or kept up to it: what the boxes reach, or 48.
    expect(setSectionHeight(spec, "s_policy", 136)).toBe(spec)
    expect(setSectionHeight(spec, "s_policy", 100)).toBe(spec)
    expect(setSectionHeight(spec, "s_equipment", 232.4)).toBe(spec)
    expect(setSectionHeight(spec, "s_notes", 10)).toBe(spec)
  })

  it("gives the form as given for a height that is not a safe whole number, which the file cannot hold", () => {
    const spec = stacked()

    expect(setSectionHeight(spec, "s_notes", Infinity)).toBe(spec)
    expect(setSectionHeight(spec, "s_notes", NaN)).toBe(spec)
    expect(setSectionHeight(spec, "s_notes", 2 ** 53)).toBe(spec)
    // The largest safe whole number is a height like any other.
    expect(findSection(setSectionHeight(spec, "s_notes", Number.MAX_SAFE_INTEGER), "s_notes")?.section.height).toBe(Number.MAX_SAFE_INTEGER)
  })

  it("moves a section to another place on its sheet with its components, a move to where it is changing nothing", () => {
    const spec = stacked()

    expect(titles(moveSection(spec, "p", 0, 2))).toEqual(["Equipment", "Notes", "Policy"])
    expect(titles(moveSection(spec, "p", 2, 0))).toEqual(["Notes", "Policy", "Equipment"])
    // Its components are placed from its top, so only the order of the sections changes.
    const moved = moveSection(spec, "p", 0, 1)
    expect(placed(moved)).toEqual([["w_grid"], ["w_boxes"], []])
    expect(moved.pages[0].sections[1]).toEqual(spec.pages[0].sections[0])
    expect(titles(spec)).toEqual(["Policy", "Equipment", "Notes"])
    expect(moveSection(spec, "p", 1, 1)).toBe(spec)
    expect(() => moveSection(spec, "p", 0, 3)).toThrow("Cannot move the section at 0 to 3: the sheet has 3 sections")
    expect(() => moveSection(spec, "p", -1, 0)).toThrow("Cannot move the section at -1 to 0: the sheet has 3 sections")
    expect(() => moveSection(spec, "missing", 0, 1)).toThrow("The form has no sheet missing")
  })

  it("removes a section with its components, the sample kept, but never a sheet's last", () => {
    const spec = { ...stacked(), sample: { equipment: [{ "equipment.item": "Crane" }] } }

    const without = removeSection(spec, "s_equipment")
    expect(titles(without)).toEqual(["Policy", "Notes"])
    expect(findWidget(without, "w_grid")).toBeNull()
    expect(without.sample).toEqual(spec.sample)
    expect(titles(spec)).toEqual(["Policy", "Equipment", "Notes"])

    const last = removeSection(removeSection(spec, "s_policy"), "s_equipment")
    expect(titles(last)).toEqual(["Notes"])
    expect(() => removeSection(last, "s_notes")).toThrow("A sheet's last section cannot be removed")
    // A sheet's last, however many the other sheets hold.
    const other = createPage(spec)
    expect(() => removeSection(addPage(spec, other), other.sections[0].id)).toThrow("A sheet's last section cannot be removed")
    expect(() => removeSection(spec, "missing")).toThrow("No sheet has a section missing")
  })

  it("finds a section with its sheet and its place on it, and a component with its sheet, its section and its place in it", () => {
    const other = createPage(stacked())
    const spec = addPage(stacked(), other)

    expect(findSection(spec, "s_equipment")).toMatchObject({ page: { id: "p" }, section: { id: "s_equipment", title: "Equipment" }, index: 1 })
    expect(findSection(spec, "s_equipment")?.section).toBe(spec.pages[0].sections[1])
    expect(findSection(spec, other.sections[0].id)).toMatchObject({ page: { id: other.id }, index: 0 })
    expect(findSection(spec, "missing")).toBeNull()
    expect(findWidget(spec, "w_grid")).toMatchObject({ page: { id: "p" }, section: { id: "s_equipment" }, index: 0, widget: { id: "w_grid" } })
    expect(findWidget(spec, "w_grid")?.section).toBe(spec.pages[0].sections[1])
  })

  it("keeps a component inside a section as near where it is put as it can, at the section's top when taller than it", () => {
    // A component 120 tall in a section 136 tall: anywhere from 0 to 16 down it.
    expect(keptInside(8, 120, 136)).toBe(8)
    expect(keptInside(0, 120, 136)).toBe(0)
    // Its bottom at the section's, or put past there: it sits with its bottom at the section's.
    expect(keptInside(16, 120, 136)).toBe(16)
    expect(keptInside(240, 120, 232)).toBe(112)
    // Put above the top: at the top.
    expect(keptInside(-24, 120, 136)).toBe(0)
    // As tall as the section, or taller: at the top, wherever it is put.
    expect(keptInside(16, 136, 136)).toBe(0)
    expect(keptInside(16, 300, 232)).toBe(0)
    expect(keptInside(-8, 300, 232)).toBe(0)
  })

  it("adds a component to a section kept inside it, growing the section only for a component taller than it", () => {
    const spec = stacked()
    const note = { ...createWidget("heading", { x: 16, y: 16, w: 480, h: 48 }), id: "w_note" }

    // 16 + 48 would reach 64, past the empty section's 48: it sits at the top, and Notes stays 48.
    const added = addWidget(spec, "s_notes", note)
    expect(findWidget(added, "w_note")).toMatchObject({ section: { id: "s_notes" }, widget: { x: 16, y: 0, w: 480, h: 48 } })
    expect(heights(added)).toEqual([136, 232, 48])
    // Where it fits it stays where it is put, last in the section.
    expect(heights(addWidget(spec, "s_equipment", note))).toEqual([136, 232, 48])
    expect(placed(addWidget(spec, "s_equipment", note))).toEqual([["w_boxes"], ["w_grid", "w_note"], []])
    expect(findWidget(addWidget(spec, "s_equipment", note), "w_note")?.widget.y).toBe(16)
    // Past Equipment's bottom it stops there, 232 less 48 down; above its top, at the top; Equipment stays 232.
    const past = addWidget(spec, "s_equipment", { ...note, y: 300 })
    expect(findWidget(past, "w_note")?.widget.y).toBe(184)
    expect(heights(past)).toEqual([136, 232, 48])
    expect(findWidget(addWidget(spec, "s_equipment", { ...note, y: -40 }), "w_note")?.widget.y).toBe(0)
    // Taller than Notes, it sits at the top and Notes grows to hold it, and only Notes.
    const tall = addWidget(spec, "s_notes", { ...note, h: 64 })
    expect(findWidget(tall, "w_note")?.widget).toMatchObject({ y: 0, h: 64 })
    expect(heights(tall)).toEqual([136, 232, 64])
    expect(() => addWidget(spec, "missing", note)).toThrow("No sheet has a section missing")
  })

  it("grows a section to hold a component a change takes past its bottom, as a resize does, and never shrinks it", () => {
    const spec = stacked()

    // Its top moved down to end at 200, or made taller to end at 216.
    expect(heights(updateWidget(spec, "w_boxes", { y: 80 }))).toEqual([200, 232, 48])
    expect(heights(updateWidget(spec, "w_boxes", { h: 200 }))).toEqual([216, 232, 48])
    // Resized from a corner as the sheet does, its whole rect given: the Table now reaches 276.
    expect(heights(updateWidget(spec, "w_grid", { x: 16, y: 16, w: 800, h: 260 }))).toEqual([136, 276, 48])
    // Moved up, made shorter or retitled, or the lowest one removed: the section keeps its height.
    expect(heights(updateWidget(spec, "w_grid", { y: 0, h: 96, title: "Plant" }))).toEqual([136, 232, 48])
    expect(heights(removeWidget(spec, "w_grid"))).toEqual([136, 232, 48])
  })

  it("places a component into another section last, drawn over the components there, kept inside it, growing it only for a component taller than it", () => {
    const spec = stacked()

    // Dropped 240 down Equipment, 232 tall, the boxes, 120 tall, stop at its bottom, 112 down.
    const moved = placeWidget(spec, "w_boxes", "s_equipment", { x: 16, y: 240, w: 400, h: 120 })
    expect(placed(moved)).toEqual([[], ["w_grid", "w_boxes"], []])
    expect(findWidget(moved, "w_boxes")).toMatchObject({ section: { id: "s_equipment" }, index: 1, widget: { x: 16, y: 112, w: 400, h: 120 } })
    // Nothing grows: the section it left keeps its height, and so does the one it went into.
    expect(heights(moved)).toEqual([136, 232, 48])
    // Dropped above its top, at the top.
    expect(findWidget(placeWidget(spec, "w_boxes", "s_equipment", { x: 16, y: -40, w: 400, h: 120 }), "w_boxes")?.widget.y).toBe(0)
    // Placed where it fits, it stays there, and nothing grows.
    const beside = placeWidget(spec, "w_boxes", "s_equipment", { x: 752, y: 16, w: 400, h: 120 })
    expect(findWidget(beside, "w_boxes")?.widget).toMatchObject({ x: 752, y: 16 })
    expect(heights(beside)).toEqual([136, 232, 48])
    // The Table, 200 tall, into Notes, 48 tall: at its top, Notes growing to 200; Equipment, left empty, keeps 232.
    const tall = placeWidget(spec, "w_grid", "s_notes", { x: 16, y: 24, w: 720, h: 200 })
    expect(placed(tall)).toEqual([["w_boxes"], [], ["w_grid"]])
    expect(findWidget(tall, "w_grid")?.widget).toMatchObject({ x: 16, y: 0, w: 720, h: 200 })
    expect(heights(tall)).toEqual([136, 232, 200])
    expect(() => placeWidget(spec, "missing", "s_policy", nowhere)).toThrow("No sheet has a component missing")
    expect(() => placeWidget(spec, "w_boxes", "missing", nowhere)).toThrow("No sheet has a section missing")
  })

  it("places a component in its own section keeping its place in the order, kept inside it, growing it only for a component taller than it and never shrinking it", () => {
    const spec = addWidget(stacked(), "s_policy", { ...createWidget("heading", { x: 432, y: 16, w: 200, h: 48 }), id: "w_title" })

    // Policy is 136 tall: the boxes, 120 tall, dropped 64 down, stop at its bottom, 16 down.
    const lower = placeWidget(spec, "w_boxes", "s_policy", { x: 24, y: 64, w: 400, h: 120 })
    expect(placed(lower)).toEqual([["w_boxes", "w_title"], ["w_grid"], []])
    expect(findWidget(lower, "w_boxes")?.widget).toMatchObject({ x: 24, y: 16 })
    expect(heights(lower)).toEqual([136, 232, 48])
    // Moved up to its top, the section keeps its height.
    const top = placeWidget(spec, "w_boxes", "s_policy", { x: 16, y: 0, w: 400, h: 120 })
    expect(findWidget(top, "w_boxes")?.widget.y).toBe(0)
    expect(heights(top)).toEqual([136, 232, 48])
    // Placed taller than the section, it sits at the top and the section grows to hold it, the order kept.
    const taller = placeWidget(spec, "w_boxes", "s_policy", { x: 16, y: 64, w: 400, h: 200 })
    expect(findWidget(taller, "w_boxes")?.widget).toMatchObject({ y: 0, h: 200 })
    expect(heights(taller)).toEqual([200, 232, 48])
    expect(placed(taller)).toEqual([["w_boxes", "w_title"], ["w_grid"], []])
  })

  it("moves a component within its section, kept inside it and off the sheet's left edge, never growing it", () => {
    const spec = addWidget(stacked(), "s_policy", { ...createWidget("heading", { x: 432, y: 16, w: 200, h: 48 }), id: "w_title" })

    // Within Policy, 136 tall: where it fits it goes, keeping its size and its place in the order.
    const moved = moveWidget(spec, "w_boxes", { x: 40, y: 8 })
    expect(findWidget(moved, "w_boxes")).toMatchObject({ section: { id: "s_policy" }, index: 0, widget: { x: 40, y: 8, w: 400, h: 120 } })
    expect(heights(moved)).toEqual([136, 232, 48])
    // Past its bottom it stops there, above its top at the top, and left of the sheet's edge at the edge.
    expect(findWidget(moveWidget(spec, "w_boxes", { x: 16, y: 64 }), "w_boxes")?.widget).toMatchObject({ x: 16, y: 16 })
    expect(findWidget(moveWidget(spec, "w_boxes", { x: -8, y: -24 }), "w_boxes")?.widget).toMatchObject({ x: 0, y: 0 })
    // However far down, it stays in its section, which keeps its height.
    const far = moveWidget(spec, "w_boxes", { x: 16, y: 400 })
    expect(placed(far)).toEqual([["w_boxes", "w_title"], ["w_grid"], []])
    expect(findWidget(far, "w_boxes")?.widget.y).toBe(16)
    expect(heights(far)).toEqual([136, 232, 48])
    expect(() => moveWidget(spec, "missing", { x: 0, y: 0 })).toThrow("No sheet has a component missing")
  })

  it("moves a component taller than its section to its top, the section growing to hold it", () => {
    // Notes, 48 tall, written holding a note 80 tall.
    const spec = stacked()
    spec.pages[0].sections[2] = section("s_notes", "Notes", 48, [{ ...createWidget("heading", { x: 16, y: 16, w: 480, h: 80 }), id: "w_note" }])

    const moved = moveWidget(spec, "w_note", { x: 24, y: 24 })
    expect(findWidget(moved, "w_note")?.widget).toMatchObject({ x: 24, y: 0, h: 80 })
    expect(heights(moved)).toEqual([136, 232, 80])
  })

  it("copies a component into its own section just after it, 16 right and down but kept inside it, never growing it", () => {
    const spec = stacked()

    // 16 below the boxes would reach 152, past Policy's 136: the copy stops at its bottom, 16 down, and Policy stays 136.
    const copied = duplicateWidget(spec, "w_boxes")
    expect(findWidget(copied.spec, copied.id)).toMatchObject({ section: { id: "s_policy" }, index: 1, widget: { x: 32, y: 16 } })
    expect(heights(copied.spec)).toEqual([136, 232, 48])
    // The Table's copy, 32 down, reaches 232, its section's bottom already.
    const gridCopy = duplicateWidget(spec, "w_grid")
    expect(findWidget(gridCopy.spec, gridCopy.id)?.widget).toMatchObject({ x: 32, y: 32 })
    expect(heights(gridCopy.spec)).toEqual([136, 232, 48])
    // With room below, the copy goes the whole 16 down.
    const roomy = duplicateWidget(setSectionHeight(spec, "s_policy", 400), "w_boxes")
    expect(findWidget(roomy.spec, roomy.id)?.widget).toMatchObject({ x: 32, y: 32 })
    expect(heights(roomy.spec)).toEqual([400, 232, 48])
  })

  it("pastes a copy where its component sat, past one already there, into any section, kept inside it, the original untouched", () => {
    const spec = stacked()
    const boxes = findWidget(spec, "w_boxes")!.widget
    const at = (pasted: { spec: FormSpec; id: string }) => findWidget(pasted.spec, pasted.id)

    // Into its own section, over its original: 16 right, and as far down as Policy's 136 lets it, last there.
    const once = pasteWidget(spec, boxes, "s_policy")
    expect(at(once)).toMatchObject({ section: { id: "s_policy" }, index: 1, widget: { ...boxes, id: once.id, x: 32, y: 16 } })
    expect(once.id).not.toBe("w_boxes")
    expect(findWidget(once.spec, "w_boxes")?.widget).toEqual(boxes)
    expect(heights(once.spec)).toEqual([136, 232, 48])
    // Again, past that copy too.
    const twice = pasteWidget(once.spec, boxes, "s_policy")
    expect(at(twice)?.widget).toMatchObject({ x: 48, y: 16 })
    // With room below, the whole 16 down.
    expect(at(pasteWidget(setSectionHeight(spec, "s_policy", 400), boxes, "s_policy"))?.widget).toMatchObject({ x: 32, y: 32 })

    // Into another section where it sat, kept inside it: Notes, 48 tall, grows to hold the 120 it is.
    const notes = pasteWidget(spec, boxes, "s_notes")
    expect(at(notes)).toMatchObject({ section: { id: "s_notes" }, widget: { x: 16, y: 0 } })
    expect(heights(notes.spec)).toEqual([136, 232, 120])
    // Onto another sheet's section, nothing there: where it sat.
    const sheets = addPage(spec, createPage(spec))
    const elsewhere = pasteWidget(sheets, boxes, sheets.pages[1].sections[0].id)
    expect(at(elsewhere)).toMatchObject({ page: { id: sheets.pages[1].id }, widget: { x: 16, y: 16 } })

    // Held at its section's bottom right corner, it sits over the one there.
    const full = { ...boxes, id: "w_full", x: 0, y: 0, w: 1280, h: 48 }
    const corner = addWidget(spec, "s_notes", full)
    expect(at(pasteWidget(corner, full, "s_notes"))?.widget).toMatchObject({ x: 0, y: 0 })
    expect(() => pasteWidget(spec, boxes, "missing")).toThrow("No sheet has a section missing")
  })

  it("keeps a component inside its sheet's width as it is added, placed, moved or copied, the sheet widening only for one wider than it or resized past its right edge", () => {
    // 1280 wide.
    const spec = stacked()
    const width = (form: FormSpec) => form.pages[0].width
    const note = { ...createWidget("heading", { x: 1000, y: 0, w: 480, h: 48 }), id: "w_note" }

    // Past the right edge it stops there, 1280 less its width across, and the sheet keeps its width.
    expect(findWidget(addWidget(spec, "s_notes", note), "w_note")?.widget.x).toBe(800)
    expect(findWidget(placeWidget(spec, "w_boxes", "s_equipment", { x: 1000, y: 16, w: 400, h: 120 }), "w_boxes")?.widget.x).toBe(880)
    const moved = moveWidget(spec, "w_grid", { x: 900, y: 16 })
    expect(findWidget(moved, "w_grid")?.widget.x).toBe(560)
    expect(width(moved)).toBe(1280)
    // A copy of one at the edge stays at it, 16 below its original.
    const copied = duplicateWidget(moved, "w_grid")
    expect(findWidget(copied.spec, copied.id)?.widget).toMatchObject({ x: 560, y: 32 })
    // Wider than the sheet, it sits at its left edge, and the sheet widens to hold it.
    const wide = addWidget(spec, "s_notes", { ...note, w: 1500 })
    expect(findWidget(wide, "w_note")?.widget.x).toBe(0)
    expect(width(wide)).toBe(1500)
    // Resized past the right edge, the sheet widens to hold it, and never narrows of its own accord.
    const resized = updateWidget(spec, "w_grid", { x: 16, y: 16, w: 1400, h: 200 })
    expect(width(resized)).toBe(1416)
    expect(width(updateWidget(resized, "w_grid", { w: 400 }))).toBe(1416)
  })

  it("sets a sheet's width in whole pixels, never under what its components reach or 240, the form as given while it is that already or for a width the file cannot hold", () => {
    // Its Table reaches 736 across.
    const spec = stacked()
    const width = (form: FormSpec) => form.pages[0].width

    expect(width(setPageWidth(spec, "p", 1600))).toBe(1600)
    expect(width(setPageWidth(spec, "p", 900.4))).toBe(900)
    expect(width(setPageWidth(spec, "p", 500))).toBe(736)
    expect(width(setPageWidth(blank(), "p", 100))).toBe(240)
    // Its components stay where they are.
    expect(setPageWidth(spec, "p", 1600).pages[0].sections).toEqual(spec.pages[0].sections)
    expect(setPageWidth(spec, "p", 1280)).toBe(spec)
    expect(setPageWidth(spec, "p", Infinity)).toBe(spec)
    expect(setPageWidth(spec, "p", NaN)).toBe(spec)
    expect(setPageWidth(spec, "p", 2 ** 53)).toBe(spec)
    expect(() => setPageWidth(spec, "missing", 1000)).toThrow("The form has no sheet missing")
  })

  it("leaves its input untouched", () => {
    const before = stacked()
    const snapshot = JSON.stringify(before)

    addSection(before, "p", createSection(before.pages[0]), 0)
    renameSection(before, "s_policy", "Cover")
    setSectionShowWhen(before, "s_policy", { match: "any", conditions: [] })
    setSectionHeight(before, "s_policy", 400)
    fitSection(before, "s_equipment")
    moveSection(before, "p", 0, 2)
    splitSection(before, "s_equipment", 8, createSection(before.pages[0]))
    removeSection(before, "s_notes")
    addWidget(before, "s_notes", { ...createWidget("heading", { x: 0, y: 0, w: 480, h: 480 }), id: "w_note" })
    updateWidget(before, "w_boxes", { y: 400 })
    placeWidget(before, "w_boxes", "s_notes", { x: 0, y: 400, w: 400, h: 120 })
    moveWidget(before, "w_boxes", { x: 0, y: 400 })
    duplicateWidget(before, "w_grid")
    pasteWidget(before, findWidget(before, "w_grid")!.widget, "s_equipment")
    removeWidget(before, "w_grid")
    setPageWidth(before, "p", 2000)

    expect(JSON.stringify(before)).toBe(snapshot)
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
    columns: 3, ...COLLECTION_OPTIONS,
  })
  /**
   * A tick box and a class on the main sheet, in its section "Policy", and a sheet of
   * business interruption shown while the box is ticked, its limit in its section "Limits".
   */
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
      { id: "p", title: "Main", width: 1280, show_when: always(), sections: [section("s_main", "Policy", 136, [collection("w_policy", "Policy", [["policy", "policy.bi"], ["policy", "policy.class"]])])] },
      { id: "p_bi", title: "BI", width: 1280, show_when: { match: "all", conditions: [ticked] }, sections: [section("s_bi", "Limits", 136, [collection("w_bi", "Limits", [["bi", "bi.limit"]])])] },
    ],
    sample: { bi: [{ "bi.limit": "500,000" }] },
  })

  it("creates sheets, sections and components always shown, each with a show_when of its own", () => {
    const spec = covered()
    expect(createPage(spec).show_when).toEqual(always())
    expect(createSection(spec.pages[0]).show_when).toEqual(always())
    const first = createWidget("collection", { x: 0, y: 0, w: 200, h: 100 })
    const second = createWidget("tableInput", { x: 0, y: 0, w: 200, h: 100 })
    const third = createWidget("heading", { x: 0, y: 0, w: 200, h: 40 })
    expect([first.show_when, second.show_when, third.show_when]).toEqual([always(), always(), always()])
    expect(first.show_when).not.toBe(second.show_when)
  })

  it("sets when a sheet, a section or a component shows, leaving its input untouched", () => {
    const spec = covered()
    const either: ShowWhen = { match: "any", conditions: [ticked, { table: "policy", column: "policy.class", op: "equals", values: ["Fleet"] }] }

    const next = setPageShowWhen(spec, "p_bi", either)
    expect(next.pages[1].show_when).toEqual(either)
    expect(next.pages[1].show_when).not.toBe(either)
    expect(spec.pages[1].show_when.conditions).toEqual([ticked])
    expect(() => setPageShowWhen(spec, "missing", either)).toThrow("The form has no sheet missing")
    expect(findSection(setSectionShowWhen(spec, "s_bi", either), "s_bi")?.section.show_when).toEqual(either)
    expect(findWidget(updateWidget(spec, "w_bi", { show_when: either }), "w_bi")?.widget.show_when).toEqual(either)
  })

  it("takes the conditions testing a column or a table out with it, wherever they are, a section's among them", () => {
    const onClass: Condition = { table: "policy", column: "policy.class", op: "equals", values: ["Fleet"] }
    let spec = updateWidget(covered(), "w_bi", { show_when: { match: "all", conditions: [onClass] } })
    spec = setSectionShowWhen(spec, "s_bi", { match: "any", conditions: [ticked, onClass] })
    expect(conditionUses(spec, "policy", "policy.bi").map((holder) => holder.id)).toEqual(["p_bi", "s_bi"])
    expect(conditionUses(spec, "policy", "policy.class").map((holder) => holder.id)).toEqual(["s_bi", "w_bi"])
    expect(conditionUses(spec, "policy").map((holder) => holder.id)).toEqual(["p_bi", "s_bi", "w_bi"])
    expect(conditionUses(spec, "bi")).toEqual([])

    const withoutBox = removeSchemaColumn(spec, "policy", "policy.bi")
    expect(withoutBox.pages[1].show_when.conditions).toEqual([])
    // The section keeps its other condition, and its match.
    expect(findSection(withoutBox, "s_bi")?.section.show_when).toEqual({ match: "any", conditions: [onClass] })
    expect(findWidget(withoutBox, "w_bi")?.widget.show_when.conditions).toEqual([onClass])

    const withoutPolicy = removeSchemaTable(spec, "policy")
    expect(withoutPolicy.pages[1].show_when.conditions).toEqual([])
    expect(findSection(withoutPolicy, "s_bi")?.section.show_when.conditions).toEqual([])
    expect(findWidget(withoutPolicy, "w_bi")?.widget.show_when.conditions).toEqual([])
  })

  it("counts a Heading's conditions as any component's, and takes them out with their column or table", () => {
    const onClass: Condition = { table: "policy", column: "policy.class", op: "equals", values: ["Fleet"] }
    const heading = { ...createWidget("heading", { x: 0, y: 0, w: 480, h: 40 }), id: "w_head", text: "Fleet", show_when: { match: "all" as const, conditions: [onClass] } }
    const spec = addWidget(covered(), "s_main", heading)
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
    const plotted = addWidget(changeSchemaColumnType(spec, "policy", "policy.extra", "float"), "s_main", {
      ...createWidget("chart", { x: 0, y: 240, w: 480, h: 280 }),
      fields: [{ table: "policy", column: "policy.extra" }],
    })
    expect(pageProblems(setPageShowWhen(plotted, "p_bi", { match: "all", conditions: [{ table: "policy", column: "policy.extra", op: "filled", values: [] }] })).map((p) => p.message)).toEqual([
      'Sheet "BI" waits on extra, which is on no Table or Collection',
    ])
    // A Heading's conditions are checked as any component's, and it is named by its text.
    const headed = addWidget(spec, "s_bi", {
      ...createWidget("heading", { x: 0, y: 160, w: 480, h: 40 }),
      text: "Business interruption",
      show_when: { match: "all", conditions: [{ table: "policy", column: "policy.gone", op: "filled", values: [] }] },
    })
    expect(widgetProblems(headed).map((p) => p.message)).toEqual(['Heading "Business interruption" tests a column that is no longer in the schema'])
  })

  it("says what stops a section's conditions doing what they say, naming the section by its title", () => {
    const spec = covered()
    const showing = (id: string, condition: Condition) => sectionProblems(setSectionShowWhen(spec, id, { match: "all", conditions: [condition] }))
    expect(sectionName(spec.pages[1].sections[0])).toBe('Section "Limits"')
    expect(sectionProblems(spec)).toEqual([])

    // A test its column's type does not take.
    expect(showing("s_main", { table: "policy", column: "policy.class", op: "ticked", values: [] })).toEqual([
      { sectionId: "s_main", message: 'Section "Policy" tests class with "is ticked", which a Text column cannot take' },
    ])
    // A value no allowed value can match.
    expect(showing("s_bi", { table: "policy", column: "policy.class", op: "equals", values: ["Property"] })).toEqual([
      { sectionId: "s_bi", message: 'Section "Limits" tests class against "Property", which isn\'t one of its allowed values' },
    ])
    // Tested only inside the section it shows, or on no component: nothing could show the section again.
    expect(showing("s_bi", { table: "bi", column: "bi.limit", op: "filled", values: [] })).toEqual([
      { sectionId: "s_bi", message: 'Section "Limits" waits on limit, which is only on components that hide with it, so it could not show again once hidden' },
    ])
    expect(showing("s_main", { table: "policy", column: "policy.extra", op: "filled", values: [] })).toEqual([
      { sectionId: "s_main", message: 'Section "Policy" waits on extra, which is on no Table or Collection' },
    ])
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
    // A section's conditions hide its components' columns too, while its height changes nothing.
    const onFleet: ShowWhen = { match: "all", conditions: [{ table: "policy", column: "policy.class", op: "equals", values: ["Fleet"] }] }
    expect(pricingBasis(setSectionShowWhen(spec, "s_main", onFleet))).not.toBe(pricingBasis(spec))
    expect(pricingBasis(setSectionHeight(spec, "s_main", 400))).toBe(pricingBasis(spec))
    // A column the sheets hide is still declared to the pipeline, so one renamed or retyped
    // is priced anew, though the quote reads the same.
    expect(sheetVisibility(spec, spec.sample).hidden).toContain(columnKey("bi", "bi.limit"))
    expect(pricingBasis(updateSchemaColumn(spec, "bi", "bi.limit", { name: "cover_limit" }))).not.toBe(pricingBasis(spec))
    expect(pricingBasis(updateSchemaColumn(spec, "bi", "bi.limit", { type: "int" }))).not.toBe(pricingBasis(spec))
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
  /** The schema with one sheet holding `widget` in its section. */
  const charted = (widget: ChartWidget): FormSpec => ({
    ...blank(),
    schema: schema(),
    pages: [{ ...blank().pages[0], sections: [section("s", "Section 1", 560, [widget])] }],
  })
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
