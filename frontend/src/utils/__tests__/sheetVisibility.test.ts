/**
 * The conditions on the sheets, sections and components (specs/workbench): which show for
 * the values typed, held to the cases the server's visibility is held to too, the schema a
 * quote reads, the tests each type takes, what stops a condition holding, what nothing could
 * show again, and the editor's steps.
 */
import { describe, expect, it } from "vitest"
import fixture from "./fixtures/sheetVisibility.json"
import typedQuotes from "./fixtures/typedQuotes.json"
import type { Condition, FormSpec, Page, RowCount, RowsCondition, RowTest, RowTotal, SchemaColumn, SchemaTable, Section, ShowWhen } from "../../api/types"
import type { RowsByTable } from "../sheetValues"
import { BLANK_THEME, COLLECTION_OPTIONS, PLAIN_COLUMN_LOOK } from "../workbenchForm"
import {
  CONDITION_TESTS,
  columnKey,
  conditionFault,
  fieldKey,
  neverShown,
  newCondition,
  newRowTest,
  quoteEligible,
  quotedSchema,
  sheetVisibility,
  testLabel,
  testsFor,
  totalColumns,
  valueFits,
  withConditionColumn,
  withConditionRowsOf,
  withConditionTest,
  withListKind,
  withTestColumn,
} from "../sheetVisibility"

type Widget = Section["widgets"][number]

interface VisibilityCase {
  name: string
  form: string
  show_when?: Record<string, ShowWhen>
  field_show_when?: { widget: string; table: string; column: string; show_when: ShowWhen }[]
  values: RowsByTable
  hidden_pages: string[]
  hidden_sections: string[]
  hidden_widgets: string[]
  hidden_fields?: [string, string, string][]
  hidden_columns: [string, string][]
}

// The JSON's literal types widen a test to string and the version to number.
const forms = fixture.forms as unknown as Record<string, FormSpec>
const cases = fixture.cases as unknown as VisibilityCase[]

const byText = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0)
const byPair = (a: readonly [string, string], b: readonly [string, string]): number => byText(a[0], b[0]) || byText(a[1], b[1])

describe("sheetVisibility, held to the shared cases", () => {
  it.each(cases.map((testCase) => [testCase.name, testCase] as const))("%s", (_name, testCase) => {
    const form = structuredClone(forms[testCase.form])
    const sections = form.pages.flatMap((page) => page.sections)
    const widgets = sections.flatMap((section) => section.widgets)
    const holders: (Page | Section | Widget)[] = [...form.pages, ...sections, ...widgets]
    for (const [id, showWhen] of Object.entries(testCase.show_when ?? {})) {
      const holder = holders.find((candidate) => candidate.id === id)
      if (holder === undefined) throw new Error(`Case "${testCase.name}" names "${id}", which is no sheet, section or component of the form`)
      holder.show_when = showWhen
    }
    for (const field of testCase.field_show_when ?? []) {
      const widget = widgets.find((candidate) => candidate.id === field.widget)
      if (widget?.type !== "tableInput" && widget?.type !== "collection") throw new Error(`Case "${testCase.name}" gives a field of "${field.widget}", which is no Table or Collection of the form`)
      widget.column_looks.push({ table: field.table, column: field.column, ...PLAIN_COLUMN_LOOK, show_when: field.show_when })
    }

    const visibility = sheetVisibility(form, testCase.values)

    expect(form.pages.map((page) => page.id).filter((id) => !visibility.pages.has(id)).sort(byText)).toEqual(testCase.hidden_pages)
    expect(sections.map((section) => section.id).filter((id) => !visibility.sections.has(id)).sort(byText)).toEqual(testCase.hidden_sections)
    expect(widgets.map((widget) => widget.id).filter((id) => !visibility.widgets.has(id)).sort(byText)).toEqual(testCase.hidden_widgets)
    // The fields of the components that show, but those the case says hide.
    const hiding = new Set((testCase.hidden_fields ?? []).map(([widget, table, columnId]) => fieldKey(widget, table, columnId)))
    const fields = widgets.flatMap((widget) =>
      visibility.widgets.has(widget.id) && (widget.type === "tableInput" || widget.type === "collection")
        ? widget.fields.map((field) => fieldKey(widget.id, field.table, field.column))
        : [],
    )
    expect(fields.filter((key) => hiding.has(key)).length, "the case hides a field no component that shows has").toBe(hiding.size)
    expect([...visibility.fields].sort(byText)).toEqual(fields.filter((key) => !hiding.has(key)).sort(byText))
    const hidden = [...visibility.hidden].map((key): [string, string] => {
      const [table, column] = key.split(":")
      return [table, column]
    })
    expect(hidden.sort(byPair)).toEqual(testCase.hidden_columns)
  })
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

const ALWAYS: ShowWhen = { match: "all", conditions: [] }
const ticked = (table: string, columnId: string): Condition => ({ table, column: columnId, op: "ticked", values: [] })
const when = (...conditions: ShowWhen["conditions"]): ShowWhen => ({ match: "all", conditions })

const policy: SchemaTable = {
  id: "t_policy",
  name: "policy",
  role: "input",
  rows: "one",
  columns: [
    column("c_bi", "bi", { type: "bool" }),
    column("c_motor", "motor", { type: "bool" }),
    column("c_class", "class"),
    column("c_years", "years", { type: "int" }),
    column("c_si", "sum_insured", { type: "float" }),
    column("c_start", "start", { type: "date" }),
  ],
}
const items: SchemaTable = { id: "t_items", name: "items", role: "input", rows: "many", columns: [column("c_item", "item")] }
/** `items` with an index, which nothing is typed into, and a number column, which a total takes. */
const valued: SchemaTable = {
  ...items,
  columns: [column("c_row", "row", { type: "int", index: true }), ...items.columns, column("c_value", "value", { type: "float" })],
}
const premium: SchemaTable = { id: "t_out", name: "premium", role: "output", rows: "one", columns: [column("c_premium", "premium", { type: "float" })] }

const collection = (id: string, fields: [string, string][], showWhen: ShowWhen = ALWAYS): Widget => ({
  id,
  type: "collection",
  title: id,
  x: 0,
  y: 0,
  w: 720,
  h: 120,
  columns: 3, ...COLLECTION_OPTIONS,
  fields: fields.map(([table, columnId]) => ({ table, column: columnId })),
  show_when: showWhen,
})
/** A section holding `widgets`, as tall as they reach. */
const section = (id: string, widgets: Widget[], showWhen: ShowWhen = ALWAYS): Section => ({
  id,
  title: id,
  height: Math.max(48, ...widgets.map((widget) => widget.y + widget.h)),
  show_when: showWhen,
  widgets,
})
/** A sheet of `sections`. */
const sectioned = (id: string, sections: Section[], showWhen: ShowWhen = ALWAYS): Page => ({ id, title: id, width: 1280, show_when: showWhen, sections })
/** A sheet of one section, `s_` and its id, holding `widgets`. */
const page = (id: string, widgets: Widget[], showWhen: ShowWhen = ALWAYS): Page => sectioned(id, [section(`s_${id}`, widgets)], showWhen)
const formOf = (pages: Page[], tables: SchemaTable[] = [policy, items, premium]): FormSpec => ({
  version: 1,
  name: "form",
  theme: { ...BLANK_THEME },
  schema: { tables },
  pages,
  sample: {},
})

describe("quoteEligible", () => {
  // The fixture spells each column with what it sets, the rest as a new column's.
  const form: FormSpec = {
    ...formOf([]),
    name: typedQuotes.form.name,
    schema: {
      tables: typedQuotes.form.schema.tables.map((table) => ({
        ...table,
        columns: table.columns.map(({ id, name, ...set }) => column(id, name, set as Partial<SchemaColumn>)),
      })) as SchemaTable[],
    },
    pages: typedQuotes.form.pages.map((sheet) =>
      sectioned(
        sheet.id,
        sheet.sections.map((each) => section(each.id, each.widgets.map((widget) => ({ title: "", show_when: ALWAYS, column_looks: [], ...widget }) as unknown as Widget))),
      ),
    ),
  }

  it.each(typedQuotes.cases)("says whether $name is typed as the server does", ({ values, locked, typed }) => {
    const appSet = new Set(locked.map(([table, columnId]) => columnKey(table, columnId)))
    expect(quoteEligible(form, values as RowsByTable, appSet)).toBe(typed)
  })
})

describe("sheetVisibility", () => {
  const form = formOf([
    page("p_main", [collection("w_main", [["t_policy", "c_bi"]])]),
    page("p_bi", [collection("w_bi", [["t_policy", "c_class"]])], when(ticked("t_policy", "c_bi"))),
  ])

  it("gives the same answer for the same form and values, and decides afresh for new values", () => {
    const rows: RowsByTable = { t_policy: [{ c_bi: true }] }
    const first = sheetVisibility(form, rows)
    expect(sheetVisibility(form, rows)).toBe(first)
    // Values are told apart by object, as each edit makes new ones: alike but new is decided afresh.
    expect(sheetVisibility(form, { ...rows })).not.toBe(first)
    expect([first.pages.has("p_bi"), first.sections.has("s_p_bi"), first.widgets.has("w_bi")]).toEqual([true, true, true])

    const unticked = sheetVisibility(form, { t_policy: [{ c_bi: false }] })
    expect(unticked).not.toBe(first)
    // The sheet's section and the component in it are hidden with it.
    expect([unticked.pages.has("p_bi"), unticked.sections.has("s_p_bi"), unticked.widgets.has("w_bi")]).toEqual([false, false, false])
    expect([...unticked.hidden]).toEqual([columnKey("t_policy", "c_class")])
  })
})

describe("quotedSchema", () => {
  it("removes the hidden columns alone, and is the schema itself when nothing is hidden", () => {
    const schema = { tables: [policy, items] }
    const quoted = quotedSchema(schema, new Set([columnKey("t_policy", "c_class"), columnKey("t_policy", "c_start")]))
    expect(quoted.tables[0]).toEqual({ ...policy, columns: [policy.columns[0], policy.columns[1], policy.columns[3], policy.columns[4]] })
    expect(quoted.tables[1]).toBe(items)
    expect(schema.tables[0]).toBe(policy)
    expect(quotedSchema(schema, new Set())).toBe(schema)
  })
})

describe("the tests each type takes", () => {
  it("offers each type its tests in the editor's order, with each test's values", () => {
    expect(testsFor("bool")).toEqual(["ticked", "not_ticked"])
    expect(testsFor("str")).toEqual(["equals", "not_equals", "one_of", "not_one_of", "filled", "empty"])
    const numbers = ["equals", "not_equals", "above", "at_least", "below", "at_most", "one_of", "not_one_of", "filled", "empty"]
    expect(testsFor("int")).toEqual(numbers)
    expect(testsFor("float")).toEqual(numbers)
    expect(testsFor("date")).toEqual(["equals", "not_equals", "above", "at_least", "below", "at_most", "filled", "empty"])
    expect([CONDITION_TESTS.ticked.values, CONDITION_TESTS.equals.values, CONDITION_TESTS.one_of.values]).toEqual(["none", "one", "some"])
  })

  it("words each test for its type, a Date's comparisons as days", () => {
    const tests = testsFor("int")
    expect(tests.map((test) => testLabel(test, "int"))).toEqual([
      "equals",
      "does not equal",
      "is greater than",
      "is at least",
      "is less than",
      "is at most",
      "is one of",
      "is not one of",
      "is filled",
      "is empty",
    ])
    expect(testsFor("date").map((test) => testLabel(test, "date"))).toEqual([
      "is on",
      "is not on",
      "is after",
      "is on or after",
      "is before",
      "is on or before",
      "is filled",
      "is empty",
    ])
    expect(testsFor("bool").map((test) => testLabel(test, "bool"))).toEqual(["is ticked", "is not ticked"])
    expect(testsFor("str").map((test) => testLabel(test, "str"))).toEqual(["equals", "does not equal", "is one of", "is not one of", "is filled", "is empty"])
    expect(testsFor("float").map((test) => testLabel(test, "float"))).toEqual(tests.map((test) => testLabel(test, "int")))
  })
})

describe("valueFits", () => {
  const [, , text, whole, decimal, day] = policy.columns

  it("takes text other than blank, numbers as the server types them, whole for an Integer, and real days", () => {
    expect([valueFits(text, "Motor"), valueFits(text, "  "), valueFits(text, "")]).toEqual([true, false, false])
    expect([valueFits(whole, "3"), valueFits(whole, "2.0"), valueFits(whole, "2.5"), valueFits(whole, "lots")]).toEqual([true, true, false, false])
    expect([valueFits(decimal, "1,000"), valueFits(decimal, "£2.5"), valueFits(decimal, "1e999"), valueFits(decimal, "  ")]).toEqual([true, true, false, false])
    expect([valueFits(day, " 2024-02-29 "), valueFits(day, "2026-02-30"), valueFits(day, "0000-01-01"), valueFits(day, "2026-1-5"), valueFits(day, "2023-02-29")]).toEqual([
      true,
      false,
      false,
      false,
      false,
    ])
  })
})

describe("conditionFault", () => {
  const form = formOf([page("p", [])])
  const fault = (condition: Partial<Condition>): string | null =>
    conditionFault(form, { table: "t_policy", column: "c_class", op: "equals", values: ["Motor"], ...condition })

  it("is null for a condition that fits", () => {
    expect(fault({})).toBeNull()
    expect(fault({ column: "c_bi", op: "ticked", values: [] })).toBeNull()
    expect(fault({ column: "c_start", op: "at_least", values: ["2026-01-01"] })).toBeNull()
  })

  it("names the first fault: the column, the test, the number of values, then each value", () => {
    expect(fault({ column: "c_gone" })).toBe("tests a column that is no longer in the schema")
    expect(fault({ table: "t_gone" })).toBe("tests a column that is no longer in the schema")
    expect(fault({ table: "t_out", column: "c_premium" })).toBe("tests premium, which is in an output table")
    expect(fault({ table: "t_items", column: "c_item" })).toBe("tests item, which is in a table with many rows")
    expect(fault({ column: "c_bi", op: "above", values: ["1"] })).toBe('tests bi with "is greater than", which a True/false column cannot take')
    expect(fault({ op: "above" })).toBe('tests class with "is greater than", which a Text column cannot take')
    expect(fault({ column: "c_years", op: "ticked" })).toBe('tests years with "is ticked", which an Integer column cannot take')
    expect(fault({ column: "c_si", op: "ticked" })).toBe('tests sum_insured with "is ticked", which a Decimal column cannot take')
    expect(fault({ column: "c_start", op: "one_of" })).toBe('tests start with "is one of", which a Date column cannot take')
    expect(fault({ values: [] })).toBe("needs a value for its test of class")
    expect(fault({ op: "one_of", values: [] })).toBe("needs a value for its test of class")
    expect(fault({ op: "filled" })).toBe('tests class with "is filled", which takes no value')
    expect(fault({ values: ["Motor", "Fleet"] })).toBe('tests class with "equals", which takes one value')
    expect(fault({ op: "one_of", values: ["Motor", " "] })).toBe("has a blank value for class")
    expect(fault({ column: "c_years", values: ["2.5"] })).toBe('tests years against "2.5", which isn\'t a whole number')
    expect(fault({ column: "c_si", values: ["lots"] })).toBe('tests sum_insured against "lots", which isn\'t a number')
    expect(fault({ column: "c_start", op: "below", values: ["01/05/2026"] })).toBe('tests start against "01/05/2026", which isn\'t a day written YYYY-MM-DD')
  })

  it("faults a condition on a table's rows by its table, then each test of a row as a one-row table's condition", () => {
    const filledItem: RowTest = { column: "c_item", op: "filled", values: [] }
    const every: RowsCondition = { table: "t_items", rows: "every", where: [filledItem] }
    expect(conditionFault(form, every)).toBeNull()
    expect(conditionFault(form, { ...every, table: "t_policy" })).toBe("tests the rows of policy, which has one row")
    expect(conditionFault(form, { ...every, table: "t_out" })).toBe("tests the rows of premium, which is an output table")
    expect(conditionFault(form, { ...every, table: "t_gone" })).toBe("tests the rows of a table that is no longer in the schema")
    // Its second test is checked as its first.
    const second = (test: Partial<RowTest>) => conditionFault(form, { ...every, where: [filledItem, { ...filledItem, ...test }] })
    expect(second({ op: "ticked" })).toBe('tests item with "is ticked", which a Text column cannot take')
    expect(second({ op: "equals" })).toBe("needs a value for its test of item")
    expect(second({ column: "c_gone" })).toBe("tests a column that is no longer in the schema")
    const numbered = formOf([page("p", [])], [policy, valued, premium])
    expect(conditionFault(numbered, { ...every, where: [{ column: "c_row", op: "filled", values: [] }] })).toBe("tests row, the index, which nothing is typed into")
  })

  it("faults a count by its table and its tests, and a total by its column and its number too", () => {
    const count: RowCount = { table: "t_items", where: [], op: "at_least", count: 2 }
    expect(conditionFault(form, count)).toBeNull()
    expect(conditionFault(form, { ...count, table: "t_policy" })).toBe("counts the rows of policy, which has one row")
    expect(conditionFault(form, { ...count, table: "t_out" })).toBe("counts the rows of premium, which is an output table")
    expect(conditionFault(form, { ...count, table: "t_gone" })).toBe("counts the rows of a table that is no longer in the schema")
    expect(conditionFault(form, { ...count, where: [{ column: "c_item", op: "equals", values: [] }] })).toBe("needs a value for its test of item")

    const priced = formOf([page("p", [])], [policy, valued, premium])
    const sum: RowTotal = { table: "t_items", total: "sum", column: "c_value", where: [], op: "above", value: "£1,000" }
    expect(conditionFault(priced, sum)).toBeNull()
    expect(conditionFault(priced, { ...sum, table: "t_out" })).toBe("totals the rows of premium, which is an output table")
    expect(conditionFault(priced, { ...sum, column: "c_item" })).toBe("totals item, which is not a number column")
    expect(conditionFault(priced, { ...sum, column: "c_row" })).toBe("totals row, the index, which nothing is typed into")
    expect(conditionFault(priced, { ...sum, column: "c_gone" })).toBe("totals a column that is no longer in the schema")
    expect(conditionFault(priced, { ...sum, value: " " })).toBe("needs a number to compare its total of value with")
    expect(conditionFault(priced, { ...sum, value: "lots" })).toBe('compares its total of value with "lots", which isn\'t a number')
  })
})

describe("neverShown", () => {
  const motorBox = collection("w_motor", [["t_policy", "c_motor"]])
  /** A Table or a Collection with each of `shown` a field shown as its `show_when` says. */
  const withFields = (widget: Widget, shown: [string, string, ShowWhen][]): Widget =>
    widget.type === "collection" || widget.type === "tableInput"
      ? { ...widget, column_looks: shown.map(([table, columnId, showWhen]) => ({ table, column: columnId, ...PLAIN_COLUMN_LOOK, show_when: showWhen })) }
      : widget

  it("reports a field shown by its own value, but not one shown by another field of its component", () => {
    const own = ticked("t_policy", "c_bi")
    const cover = collection("w_cover", [
      ["t_policy", "c_bi"],
      ["t_policy", "c_motor"],
      ["t_policy", "c_class"],
    ])
    const form = formOf([page("p_main", [withFields(cover, [["t_policy", "c_bi", when(own)], ["t_policy", "c_class", when(ticked("t_policy", "c_motor"))]])])])
    expect([...neverShown(form)]).toEqual([[fieldKey("w_cover", "t_policy", "c_bi"), own]])
  })

  it("reports two fields each waiting on the other, and not a field waiting on one that shows with it elsewhere", () => {
    const cover = collection("w_cover", [
      ["t_policy", "c_bi"],
      ["t_policy", "c_motor"],
    ])
    const waiting = withFields(cover, [
      ["t_policy", "c_bi", when(ticked("t_policy", "c_motor"))],
      ["t_policy", "c_motor", when(ticked("t_policy", "c_bi"))],
    ])
    expect([...neverShown(formOf([page("p_main", [waiting])]))]).toEqual([
      [fieldKey("w_cover", "t_policy", "c_bi"), ticked("t_policy", "c_motor")],
      [fieldKey("w_cover", "t_policy", "c_motor"), ticked("t_policy", "c_bi")],
    ])
    // The motor box shown on another component as well, the bi field can show again, and so the motor field.
    expect(neverShown(formOf([page("p_main", [waiting, motorBox])])).size).toBe(0)
  })

  it("leaves a field of a component that can never show to its component, and counts no field that cannot show again", () => {
    const own = ticked("t_policy", "c_bi")
    const never = withFields(collection("w_never", [["t_policy", "c_class"]], when(ticked("t_policy", "c_motor"))), [["t_policy", "c_class", when(own)]])
    // The motor box hidden by its own value: a section waiting on it waits on a field that cannot show again.
    const motorField = withFields(motorBox, [["t_policy", "c_motor", when(ticked("t_policy", "c_motor"))]])
    const form = formOf([sectioned("p_main", [section("s_main", [never, motorField, collection("w_bi", [["t_policy", "c_bi"]])]), section("s_motor", [], when(ticked("t_policy", "c_motor")))])])
    expect([...neverShown(form)]).toEqual([
      ["w_never", ticked("t_policy", "c_motor")],
      [fieldKey("w_motor", "t_policy", "c_motor"), ticked("t_policy", "c_motor")],
      ["s_motor", ticked("t_policy", "c_motor")],
    ])
  })

  it("reports a sheet shown by a box on the very sheet, in any of its sections, with the condition it waits on", () => {
    const waits = ticked("t_policy", "c_bi")
    const form = formOf([page("p_main", []), sectioned("p_bi", [section("s_limits", []), section("s_bi", [collection("w_bi", [["t_policy", "c_bi"]])])], when(waits))])
    expect([...neverShown(form)]).toEqual([["p_bi", waits]])
  })

  it("reports a section shown by a box in the very section, but not one shown by a box in another section of its sheet", () => {
    const waits = ticked("t_policy", "c_bi")
    const own = formOf([sectioned("p_main", [section("s_main", [motorBox]), section("s_bi", [collection("w_bi", [["t_policy", "c_bi"]])], when(waits))])])
    expect([...neverShown(own)]).toEqual([["s_bi", waits]])

    const elsewhere = formOf([
      sectioned("p_main", [section("s_main", [collection("w_main", [["t_policy", "c_bi"]])]), section("s_bi", [collection("w_bi", [["t_policy", "c_class"]])], when(waits))]),
    ])
    expect(neverShown(elsewhere).size).toBe(0)
  })

  it("reports two sheets each waiting on a box on the other", () => {
    const form = formOf([
      page("p_a", [collection("w_a", [["t_policy", "c_bi"]])], when(ticked("t_policy", "c_motor"))),
      page("p_b", [collection("w_b", [["t_policy", "c_motor"]])], when(ticked("t_policy", "c_bi"))),
    ])
    expect([...neverShown(form)]).toEqual([
      ["p_a", ticked("t_policy", "c_motor")],
      ["p_b", ticked("t_policy", "c_bi")],
    ])
  })

  it("does not report what a box on a sheet that always shows could show, or any with one condition reachable", () => {
    const form = formOf([
      page("p_main", [motorBox]),
      page("p_motor", [collection("w_x", [["t_policy", "c_class"]])], when(ticked("t_policy", "c_motor"))),
      page("p_any", [collection("w_bi", [["t_policy", "c_bi"]])], { match: "any", conditions: [ticked("t_policy", "c_bi"), ticked("t_policy", "c_motor")] }),
    ])
    expect(neverShown(form).size).toBe(0)
  })

  it("leaves a component on a sheet that can never show to its sheet, and reports a component waiting on itself", () => {
    const own = ticked("t_policy", "c_bi")
    const form = formOf([
      page("p_main", [collection("w_self", [["t_policy", "c_bi"]], when(own))]),
      page("p_never", [collection("w_never", [["t_policy", "c_class"]], when(own))], when(ticked("t_policy", "c_motor"))),
    ])
    expect([...neverShown(form)]).toEqual([
      ["w_self", own],
      ["p_never", ticked("t_policy", "c_motor")],
    ])
  })

  it("leaves a component in a section that can never show to its section, and a section on a sheet that can never show to its sheet", () => {
    const own = ticked("t_policy", "c_bi")
    const inSection = formOf([
      sectioned("p_main", [
        section("s_main", [collection("w_self", [["t_policy", "c_bi"]], when(own))]),
        section("s_never", [collection("w_never", [["t_policy", "c_class"]], when(own))], when(ticked("t_policy", "c_motor"))),
      ]),
    ])
    expect([...neverShown(inSection)]).toEqual([
      ["w_self", own],
      ["s_never", ticked("t_policy", "c_motor")],
    ])

    const onSheet = formOf([page("p_main", []), sectioned("p_never", [section("s_waiting", [], when(own))], when(ticked("t_policy", "c_motor")))])
    expect([...neverShown(onSheet)]).toEqual([["p_never", ticked("t_policy", "c_motor")]])
  })

  it("reports a sheet waiting on a group's condition, with that condition", () => {
    const waits = ticked("t_policy", "c_bi")
    const form = formOf([page("p_main", [motorBox]), page("p_bi", [collection("w_bi", [["t_policy", "c_bi"]])], { match: "all", conditions: [{ match: "any", conditions: [waits] }] })])
    expect([...neverShown(form)]).toEqual([["p_bi", waits]])

    const reachable = { match: "all" as const, conditions: [{ match: "any" as const, conditions: [waits, ticked("t_policy", "c_motor")] }] }
    expect(neverShown(formOf([page("p_main", [motorBox]), page("p_bi", [collection("w_bi", [["t_policy", "c_bi"]])], reachable)])).size).toBe(0)
  })

  it("reports a sheet needing at least more conditions than it can reach, with the one it waits on", () => {
    const own = ticked("t_policy", "c_bi")
    const atLeast = (match: number) =>
      formOf([page("p_main", [motorBox]), page("p_bi", [collection("w_bi", [["t_policy", "c_bi"]])], { match, conditions: [ticked("t_policy", "c_motor"), own] })])
    expect([...neverShown(atLeast(2))]).toEqual([["p_bi", own]])
    expect(neverShown(atLeast(1)).size).toBe(0)
  })

  it("reaches a count through any of its table's columns on a component outside it, and reports one only on itself", () => {
    const count: RowCount = { table: "t_items", where: [], op: "at_least", count: 1 }
    const own = formOf([page("p_main", [motorBox]), page("p_items", [collection("w_items", [["t_items", "c_item"]])], when(count))])
    expect([...neverShown(own)]).toEqual([["p_items", count]])

    const elsewhere = formOf([page("p_main", [collection("w_items", [["t_items", "c_item"]])]), page("p_more", [motorBox], when(count))])
    expect(neverShown(elsewhere).size).toBe(0)
  })

  it("reaches a condition on rows only while each column it tests is on a component outside it", () => {
    const tested: RowsCondition = {
      table: "t_items",
      rows: "any",
      where: [
        { column: "c_item", op: "filled", values: [] },
        { column: "c_value", op: "filled", values: [] },
      ],
    }
    const onMain = (fields: [string, string][]) =>
      formOf([page("p_main", [motorBox, collection("w_items", fields)]), page("p_more", [collection("w_more", [["t_policy", "c_class"]])], when(tested))], [policy, valued, premium])
    expect([...neverShown(onMain([["t_items", "c_item"]]))]).toEqual([["p_more", tested]])
    expect(neverShown(onMain([["t_items", "c_item"], ["t_items", "c_value"]])).size).toBe(0)
  })

  it("leaves out a sheet whose condition does not fit, as that is reported already", () => {
    const form = formOf([page("p_main", [motorBox]), page("p_bad", [], when({ table: "t_policy", column: "c_gone", op: "ticked", values: [] }))])
    expect(neverShown(form).size).toBe(0)
  })
})

describe("the editor's steps", () => {
  const form = formOf([page("p", [])])

  it("starts a condition on the first one-row input table's first column, else in any row of a many-row one's, or none without one", () => {
    expect(newCondition(form)).toEqual({ table: "t_policy", column: "c_bi", op: "ticked", values: [] })
    expect(newCondition(formOf([page("p", [])], [items, premium, { ...policy, columns: [] }]))).toEqual({
      table: "t_items",
      rows: "any",
      where: [{ column: "c_item", op: "equals", values: [] }],
    })
    // A many-row table's index takes no typing, so it is never tested.
    const numbered = { ...items, columns: [column("c_row", "row", { type: "int", index: true })] }
    expect(newCondition(formOf([page("p", [])], [numbered, premium, { ...policy, columns: [] }]))).toBeNull()
  })

  it("moves a condition to another column, its test and values kept while they fit", () => {
    const years: Condition = { table: "t_policy", column: "c_years", op: "above", values: ["3"] }
    expect(withConditionColumn(form, years, "t_policy", "c_si")).toEqual({ table: "t_policy", column: "c_si", op: "above", values: ["3"] })
    // A Text column does not take "above": the test becomes its first, and the value, which fits it, is kept.
    expect(withConditionColumn(form, years, "t_policy", "c_class")).toEqual({ table: "t_policy", column: "c_class", op: "equals", values: ["3"] })
    expect(withConditionColumn(form, years, "t_policy", "c_bi")).toEqual({ table: "t_policy", column: "c_bi", op: "ticked", values: [] })
    const fraction: Condition = { table: "t_policy", column: "c_si", op: "equals", values: ["2.5"] }
    expect(withConditionColumn(form, fraction, "t_policy", "c_years")).toEqual({ table: "t_policy", column: "c_years", op: "equals", values: [] })
    expect(() => withConditionColumn(form, years, "t_policy", "c_gone")).toThrow(/c_gone/)
    expect(years).toEqual({ table: "t_policy", column: "c_years", op: "above", values: ["3"] })
  })

  it("moves a condition to a table's rows and back, keeping its test of a column of that table", () => {
    const years: Condition = { table: "t_policy", column: "c_years", op: "equals", values: ["3"] }
    // Another table's rows start in any row with a test of their first column.
    expect(withConditionRowsOf(form, years, "t_items")).toEqual({ table: "t_items", rows: "any", where: [{ column: "c_item", op: "equals", values: [] }] })
    // A one-row table's condition on a table switched to many rows becomes a test of a row as it is.
    const switched: Condition = { table: "t_items", column: "c_item", op: "filled", values: [] }
    expect(withConditionRowsOf(form, switched, "t_items")).toEqual({ table: "t_items", rows: "any", where: [{ column: "c_item", op: "filled", values: [] }] })
    const every: RowsCondition = { table: "t_items", rows: "every", where: [{ column: "c_item", op: "filled", values: [] }] }
    expect(withConditionRowsOf(form, every, "t_items")).toBe(every)
    expect(() => withConditionRowsOf(formOf([page("p", [])], [policy, { ...items, columns: [] }]), years, "t_items")).toThrow(/t_items/)

    // Back to a one-row table's column, its test of that column kept, else its first test of a row's.
    expect(withConditionColumn(form, every, "t_policy", "c_class")).toEqual({ table: "t_policy", column: "c_class", op: "filled", values: [] })
    const onPolicy: RowsCondition = {
      table: "t_policy",
      rows: "any",
      where: [
        { column: "c_si", op: "above", values: ["9"] },
        { column: "c_class", op: "equals", values: ["Motor"] },
      ],
    }
    expect(withConditionColumn(form, onPolicy, "t_policy", "c_class")).toEqual({ table: "t_policy", column: "c_class", op: "equals", values: ["Motor"] })
    const count: RowCount = { table: "t_items", where: [], op: "at_least", count: 1 }
    expect(withConditionColumn(form, count, "t_policy", "c_years")).toEqual({ table: "t_policy", column: "c_years", op: "equals", values: [] })
  })

  it("gives a condition on rows which rows, keeping its tests", () => {
    const priced = formOf([page("p", [])], [policy, valued, premium])
    const filledItem: RowTest = { column: "c_item", op: "filled", values: [] }
    const any: RowsCondition = { table: "t_items", rows: "any", where: [filledItem] }
    expect(withListKind(priced, any, "every")).toEqual({ ...any, rows: "every" })
    const count = withListKind(priced, any, "count")
    expect(count).toEqual({ table: "t_items", where: [filledItem], op: "at_least", count: 1 })
    expect(withListKind(priced, count, "count")).toBe(count)
    const sum = withListKind(priced, count, "sum")
    expect(sum).toEqual({ table: "t_items", total: "sum", column: "c_value", where: [filledItem], op: "above", value: "" })
    expect(withListKind(priced, { ...(sum as RowTotal), value: "5" }, "max")).toEqual({ ...sum, value: "5", total: "max" })
    // Any or every row from a count without tests starts with a test of the table's first column.
    expect(withListKind(priced, { table: "t_items", where: [], op: "equals", count: 0 }, "every")).toEqual({
      table: "t_items",
      rows: "every",
      where: [{ column: "c_item", op: "equals", values: [] }],
    })
    // Without a column to test, or a number column to total, there is none to start.
    expect(() => withListKind(form, any, "sum")).toThrow(/number column/)
    const bare = formOf([page("p", [])], [policy, { ...items, columns: [] }])
    expect(() => withListKind(bare, { table: "t_items", where: [], op: "equals", count: 0 }, "any")).toThrow(/t_items/)
    expect(totalColumns(valued).map((column) => column.id)).toEqual(["c_value"])
    expect(newRowTest(valued)).toEqual({ column: "c_item", op: "equals", values: [] })
    expect(newRowTest({ ...valued, columns: [valued.columns[0]] })).toBeNull()
  })

  it("moves a test of a row to another column of its table, its test and values kept while they fit", () => {
    const priced = formOf([page("p", [])], [policy, valued, premium])
    expect(withTestColumn(priced, "t_items", { column: "c_item", op: "equals", values: ["3"] }, "c_value")).toEqual({ column: "c_value", op: "equals", values: ["3"] })
    expect(withTestColumn(priced, "t_items", { column: "c_item", op: "equals", values: ["Pump"] }, "c_value")).toEqual({ column: "c_value", op: "equals", values: [] })
    expect(() => withTestColumn(priced, "t_items", { column: "c_item", op: "filled", values: [] }, "c_gone")).toThrow(/c_gone/)
  })

  it("gives a condition another test, its values kept while their number and the column take them", () => {
    const oneOf: Condition = { table: "t_policy", column: "c_years", op: "one_of", values: ["2.5", "3", "4"] }
    const years = policy.columns.find((candidate) => candidate.id === "c_years")
    expect(withConditionTest(years, oneOf, "filled").values).toEqual([])
    expect(withConditionTest(years, oneOf, "equals").values).toEqual([])
    expect(withConditionTest(years, { ...oneOf, values: ["3", "4"] }, "equals")).toEqual({ ...oneOf, op: "equals", values: ["3"] })
    expect(withConditionTest(years, oneOf, "not_one_of").values).toEqual(["3", "4"])
    // A test whose column is gone keeps its values by number alone.
    const gone: Condition = { table: "t_policy", column: "c_gone", op: "one_of", values: ["a", "b"] }
    expect(withConditionTest(undefined, gone, "equals").values).toEqual(["a"])
    expect(withConditionTest(undefined, gone, "not_one_of").values).toEqual(["a", "b"])
    expect(withConditionTest(undefined, gone, "empty").values).toEqual([])
    expect(oneOf.values).toEqual(["2.5", "3", "4"])
  })
})
