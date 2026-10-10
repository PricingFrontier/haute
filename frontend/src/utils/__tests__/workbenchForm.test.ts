/**
 * The pure operations on the workbench's form (specs/workbench): the schema's tables and
 * columns, what the sheets show of them, and what would stop the schema being the
 * pipeline's tables.
 */
import { describe, expect, it } from "vitest"
import type { FormSpec, SchemaColumn, SchemaTable } from "../../api/types"
import {
  addPage,
  addSchemaColumn,
  addSchemaTable,
  addWidget,
  changeSchemaColumnType,
  createIndexColumn,
  createPage,
  createSchemaColumn,
  createSchemaTable,
  createWidget,
  duplicateWidget,
  fieldColumn,
  fieldUses,
  findWidget,
  moveField,
  moveSchemaColumn,
  pricingBasis,
  readableName,
  removePage,
  removeSchemaColumn,
  removeSchemaTable,
  removeWidget,
  renamePage,
  rowsShown,
  schemaProblems,
  setSchemaTableRows,
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
  pages: [{ id: "p", title: "Sheet 1", widgets: [] }],
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
    expect(spec.pages[0].widgets[0].fields).toEqual([])
    expect(spec.sample).toEqual({ t: [{ a: "x" }] })
  })

  it("changes a column's type, dropping the rules the new type cannot have", () => {
    let spec = withTable(column("a", "value", { type: "float", min: 1, max: 5, options: ["1", "2"] }))

    spec = changeSchemaColumnType(spec, "t", "a", "str")
    expect(spec.schema.tables[0].columns[0]).toMatchObject({ type: "str", min: null, max: null, options: ["1", "2"] })
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
          widgets: [
            { id: "w_boxes", type: "collection", x: 0, y: 0, w: 720, h: 120, title: "Policy", columns: 3, fields: [{ table: "policy", column: "policy.state" }] },
            {
              id: "w_grid",
              type: "tableInput",
              x: 0,
              y: 160,
              w: 720,
              h: 200,
              title: "Equipment",
              rows: 3,
              fields: [{ table: "equipment", column: "equipment.item" }, { table: "equipment", column: "equipment.value" }],
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
    spec.pages[0].widgets.find((widget) => widget.id === widgetId)?.fields.map((field) => field.column)

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

  it("adds, updates, duplicates and removes components, each kind with its own layout setting", () => {
    let spec = blank()
    const grid = createWidget("tableInput", { x: 0, y: 0, w: 720, h: 200 })
    const boxes = createWidget("collection", { x: 0, y: 240, w: 720, h: 120 })
    expect(grid).toMatchObject({ type: "tableInput", title: "", fields: [], rows: 3 })
    expect(boxes).toMatchObject({ type: "collection", title: "", fields: [], columns: 3 })
    expect(grid).not.toHaveProperty("columns")
    // In the file's order, so a component the view made compares equal to it saved.
    expect(Object.keys(grid)).toEqual(["id", "x", "y", "w", "h", "type", "title", "fields", "rows"])

    spec = addWidget(addWidget(spec, "p", grid), "p", boxes)
    spec = updateWidget(spec, grid.id, { title: "Equipment", rows: 5, x: 16 })
    expect(findWidget(spec, grid.id)).toMatchObject({ index: 0, widget: { title: "Equipment", rows: 5, x: 16 } })
    expect(() => updateWidget(spec, grid.id, { columns: 2 })).toThrow("Only a Collection has columns")
    expect(() => updateWidget(spec, boxes.id, { rows: 2 })).toThrow("Only a Table has rows")
    expect(() => updateWidget(spec, "missing", { title: "x" })).toThrow("No sheet has a component missing")

    const copied = duplicateWidget(spec, grid.id)
    const copy = findWidget(copied.spec, copied.id)
    expect(copy).toMatchObject({ index: 1, widget: { type: "tableInput", title: "Equipment", rows: 5, x: 32, y: 16 } })
    expect(copied.id).not.toBe(grid.id)

    spec = removeWidget(copied.spec, grid.id)
    expect(spec.pages[0].widgets.map((widget) => widget.id)).toEqual([copied.id, boxes.id])
    expect(findWidget(spec, grid.id)).toBeNull()
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
    expect(widgetName({ ...spec.pages[0].widgets[1], title: "" })).toBe("A Table")
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
