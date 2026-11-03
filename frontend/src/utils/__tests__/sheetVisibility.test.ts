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
import { BLANK_THEME, COLLECTION_OPTIONS, createWidget, PLAIN_COLUMN_LOOK } from "../workbenchForm"
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
  takesNothingTyped,
  testLabel,
  testsFor,
  totalColumns,
  valueFits,
  withPrice,
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
    const hiding = new Set((testCase.hidden_fields ?? []).map(([widget, table, columnName]) => fieldKey(widget, table, columnName)))
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

const column = (name: string, overrides: Partial<SchemaColumn> = {}): SchemaColumn => ({
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
const ticked = (table: string, columnName: string): Condition => ({ table, column: columnName, op: "ticked", values: [], field: null, days: 0 })
const when = (...conditions: ShowWhen["conditions"]): ShowWhen => ({ match: "all", conditions })

const policy: SchemaTable = {
  name: "policy",
  role: "input",
  rows: "one",
  columns: [
    column("bi", { type: "bool" }),
    column("motor", { type: "bool" }),
    column("class"),
    column("years", { type: "int" }),
    column("sum_insured", { type: "float" }),
    column("start", { type: "date" }),
  ],
}
const items: SchemaTable = { name: "items", role: "input", rows: "many", columns: [column("item")] }
/** `items` with an index, which nothing is typed into, and a number column, which a total takes. */
const valued: SchemaTable = {
  ...items,
  columns: [column("row", { type: "int", index: true }), ...items.columns, column("value", { type: "float" })],
}
const premium: SchemaTable = { name: "premium", role: "output", rows: "one", columns: [column("premium", { type: "float" })] }

const collection = (id: string, fields: [string, string][], showWhen: ShowWhen = ALWAYS): Widget => ({ number: null,
  id,
  type: "collection",
  title: id,
  x: 0,
  y: 0,
  w: 720,
  h: 120,
  columns: 3, ...COLLECTION_OPTIONS,
  fields: fields.map(([table, columnName]) => ({ table, column: columnName })),
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

describe("withPrice", () => {
  const counts: SchemaTable = {
    name: "premium",
    role: "output",
    rows: "one",
    columns: [column("referral_count", { type: "int" }), column("rate_adequacy", { type: "float" })],
  }
  const reasons: SchemaTable = { name: "reasons", role: "output", rows: "many", columns: [column("reason")] }
  const on = (condition: Partial<Condition>): ShowWhen => ({
    match: "all",
    conditions: [{ table: "premium", column: "referral_count", op: "above", values: ["0"], field: null, days: 0, ...condition }],
  })
  const referred = on({})
  const rect = { x: 0, y: 0, w: 400, h: 120 }
  const grid = (id: string, showWhen: ShowWhen = ALWAYS): Widget => ({ ...createWidget("tableInput", rect), id, fields: [{ table: "reasons", column: "reason" }], show_when: showWhen })
  const anyReason: ShowWhen = { match: "all", conditions: [{ table: "reasons", rows: "any", where: [{ column: "reason", op: "filled", values: [], field: null, days: 0 }] }] }
  const heading = (id: string, showWhen: ShowWhen): Widget => ({ ...createWidget("heading", { ...rect, h: 40 }), id, text: "Check the rate", show_when: showWhen })
  const summary: Widget = {
    ...(collection("w_summary", [["premium", "referral_count"], ["premium", "rate_adequacy"]]) as Extract<Widget, { type: "collection" }>),
    column_looks: [{ table: "premium", column: "rate_adequacy", ...PLAIN_COLUMN_LOOK, show_when: referred }],
  }
  const inputBox: Widget = {
    ...(collection("w_inputs", [["policy", "bi"]], referred) as Extract<Widget, { type: "collection" }>),
    column_looks: [{ table: "policy", column: "bi", ...PLAIN_COLUMN_LOOK, show_when: referred }],
  }
  const form = formOf(
    [
      sectioned("p", [
        section("s_policy", [collection("w_policy", [["policy", "class"]])]),
        section("s_referrals", [grid("w_reasons")], referred),
        section("s_notes", [heading("w_note", on({ column: "rate_adequacy", op: "below", values: ["1"] }))]),
        section("s_listed", [grid("w_listed", anyReason)]),
        section("s_summary", [summary]),
      ]),
      // A sheet, a section, a Collection and a field taking values typed: a condition on the price there is a problem, never applied.
      sectioned("p_inputs", [section("s_inputs", [inputBox], referred)], referred),
    ],
    [policy, counts, reasons],
  )
  const rounds = sheetVisibility(form, {})
  const priced = (count: number | null, adequacy: number | null, reason: string[]) => ({
    tables: { premium: [{ referral_count: count, rate_adequacy: adequacy }], reasons: reason.map((each) => ({ reason: each })) },
  })
  const hiddenBy = (price: Parameters<typeof withPrice>[3]) => {
    const shown = withPrice(form, {}, rounds, price)
    return {
      sections: ["s_referrals", "s_notes", "s_listed", "p_inputs:s_inputs"].filter((id) => !shown.sections.has(id.split(":").at(-1) as string)),
      widgets: ["w_reasons", "w_note", "w_listed", "w_inputs"].filter((id) => !shown.widgets.has(id)),
      adequacy: !shown.fields.has(fieldKey("w_summary", "premium", "rate_adequacy")),
      pages: ["p", "p_inputs"].filter((id) => !shown.pages.has(id)),
      hidden: shown.hidden,
    }
  }

  it("decides what shows from the inputs first, a condition on the price holding", () => {
    expect([...rounds.pages]).toEqual(["p", "p_inputs"])
    expect(rounds.widgets.has("w_reasons") && rounds.widgets.has("w_inputs")).toBe(true)
  })

  it("hides what takes nothing typed by the price: none before the first, then as each price says", () => {
    // Before the first price every output is empty.
    expect(hiddenBy(null)).toEqual({ sections: ["s_referrals"], widgets: ["w_reasons", "w_note", "w_listed"], adequacy: true, pages: [], hidden: rounds.hidden })
    expect(hiddenBy(priced(2, 0.95, ["High value"]))).toEqual({ sections: [], widgets: [], adequacy: false, pages: [], hidden: rounds.hidden })
    // A price with no referrals, an adequate rate and no reasons hides them again.
    expect(hiddenBy(priced(0, 1.2, []))).toEqual({ sections: ["s_referrals"], widgets: ["w_reasons", "w_note", "w_listed"], adequacy: true, pages: [], hidden: rounds.hidden })
    // A value the price leaves empty reads as empty.
    expect(hiddenBy(priced(null, null, [])).widgets).toEqual(["w_reasons", "w_note", "w_listed"])
  })

  it("never hides by the price a sheet, section, component or field taking values typed", () => {
    for (const price of [null, priced(0, 2, [])]) {
      const shown = withPrice(form, {}, rounds, price)
      expect([shown.pages.has("p_inputs"), shown.sections.has("s_inputs"), shown.widgets.has("w_inputs")]).toEqual([true, true, true])
      expect(shown.fields.has(fieldKey("w_inputs", "policy", "bi"))).toBe(true)
      expect(shown.hidden).toBe(rounds.hidden)
    }
  })

  it("is the visibility itself while no condition is on the price", () => {
    const plain = formOf([page("p", [collection("w_policy", [["policy", "class"]])])], [policy, counts])
    const visibility = sheetVisibility(plain, {})
    expect(withPrice(plain, {}, visibility, null)).toBe(visibility)
  })

  it("says what takes nothing typed: a Heading, a Chart, outputs alone, and what holds only those", () => {
    const [main, inputs] = form.pages
    expect(takesNothingTyped(form, main.sections[1])).toBe(true)
    expect(takesNothingTyped(form, main.sections[2].widgets[0])).toBe(true)
    expect(takesNothingTyped(form, { table: "premium", column: "referral_count" })).toBe(true)
    expect(takesNothingTyped(form, { table: "policy", column: "bi" })).toBe(false)
    expect(takesNothingTyped(form, main)).toBe(false)
    expect(takesNothingTyped(form, inputs)).toBe(false)
    expect(takesNothingTyped(form, inputs.sections[0])).toBe(false)
    expect(takesNothingTyped(form, { ...createWidget("chart", rect), fields: [{ table: "policy", column: "sum_insured" }] })).toBe(true)
    expect(takesNothingTyped(form, { ...createWidget("export", rect) })).toBe(false)
  })
})

describe("quoteEligible", () => {
  // The fixture spells each column with what it sets, the rest as a new column's.
  const form: FormSpec = {
    ...formOf([]),
    name: typedQuotes.form.name,
    schema: {
      tables: typedQuotes.form.schema.tables.map((table) => ({
        ...table,
        columns: table.columns.map(({ name, ...set }) => column(name, set as Partial<SchemaColumn>)),
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
    const appSet = new Set(locked.map(([table, columnName]) => columnKey(table, columnName)))
    expect(quoteEligible(form, values as RowsByTable, appSet)).toBe(typed)
  })
})

describe("sheetVisibility", () => {
  const form = formOf([
    page("p_main", [collection("w_main", [["policy", "bi"]])]),
    page("p_bi", [collection("w_bi", [["policy", "class"]])], when(ticked("policy", "bi"))),
  ])

  it("gives the same answer for the same form and values, and decides afresh for new values", () => {
    const rows: RowsByTable = { policy: [{ bi: true }] }
    const first = sheetVisibility(form, rows)
    expect(sheetVisibility(form, rows)).toBe(first)
    // Values are told apart by object, as each edit makes new ones: alike but new is decided afresh.
    expect(sheetVisibility(form, { ...rows })).not.toBe(first)
    expect([first.pages.has("p_bi"), first.sections.has("s_p_bi"), first.widgets.has("w_bi")]).toEqual([true, true, true])

    const unticked = sheetVisibility(form, { policy: [{ bi: false }] })
    expect(unticked).not.toBe(first)
    // The sheet's section and the component in it are hidden with it.
    expect([unticked.pages.has("p_bi"), unticked.sections.has("s_p_bi"), unticked.widgets.has("w_bi")]).toEqual([false, false, false])
    expect([...unticked.hidden]).toEqual([columnKey("policy", "class")])
  })
})

describe("quotedSchema", () => {
  it("removes the hidden columns alone, and is the schema itself when nothing is hidden", () => {
    const schema = { tables: [policy, items] }
    const quoted = quotedSchema(schema, new Set([columnKey("policy", "class"), columnKey("policy", "start")]))
    expect(quoted.tables[0]).toEqual({ ...policy, columns: [policy.columns[0], policy.columns[1], policy.columns[3], policy.columns[4]] })
    expect(quoted.tables[1]).toBe(items)
    expect(schema.tables[0]).toBe(policy)
    expect(quotedSchema(schema, new Set())).toBe(schema)
  })

  it("keeps quote_details whole whatever the sheets hide, but for the columns the store keeps alone", () => {
    const details: SchemaTable = {
      name: "quote_details",
      role: "input",
      rows: "one",
      columns: [column("quote_name"), column("stage"), column("insured")],
    }
    const quoted = quotedSchema({ tables: [details] }, new Set([columnKey("quote_details", "quote_name")]))
    expect(quoted.tables[0].columns.map((each) => each.name)).toEqual(["quote_name", "insured"])
  })
})

describe("the tests each type takes", () => {
  it("offers each type its tests in the editor's order, with each test's values", () => {
    expect(testsFor("bool")).toEqual(["ticked", "not_ticked"])
    expect(testsFor("str")).toEqual(["equals", "not_equals", "contains", "not_contains", "starts_with", "ends_with", "one_of", "not_one_of", "filled", "empty"])
    const numbers = ["equals", "not_equals", "above", "at_least", "below", "at_most", "one_of", "not_one_of", "filled", "empty"]
    expect(testsFor("int")).toEqual(numbers)
    expect(testsFor("float")).toEqual(numbers)
    expect(testsFor("date")).toEqual(["equals", "not_equals", "above", "at_least", "below", "at_most", "filled", "empty"])
    expect([CONDITION_TESTS.ticked.values, CONDITION_TESTS.equals.values, CONDITION_TESTS.contains.values, CONDITION_TESTS.one_of.values]).toEqual(["none", "one", "one", "some"])
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
    expect(testsFor("str").map((test) => testLabel(test, "str"))).toEqual([
      "equals",
      "does not equal",
      "contains",
      "does not contain",
      "starts with",
      "ends with",
      "is one of",
      "is not one of",
      "is filled",
      "is empty",
    ])
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
    conditionFault(form, { table: "policy", column: "class", op: "equals", values: ["Motor"], field: null, days: 0, ...condition })

  it("is null for a condition that fits", () => {
    expect(fault({})).toBeNull()
    expect(fault({ column: "bi", op: "ticked", values: [], field: null, days: 0 })).toBeNull()
    expect(fault({ column: "start", op: "at_least", values: ["2026-01-01"], field: null, days: 0 })).toBeNull()
  })

  it("names the first fault: the column, the test, the number of values, then each value", () => {
    expect(fault({ column: "gone" })).toBe("tests a column that is no longer in the schema")
    expect(fault({ table: "gone" })).toBe("tests a column that is no longer in the schema")
    // An output's column is tested on the price, against values typed alone.
    expect(fault({ table: "premium", column: "premium", values: ["100"] })).toBeNull()
    expect(fault({ table: "premium", column: "premium", values: [], field: { table: "policy", column: "sum_insured" } })).toBe(
      "tests premium, a part of the price, against another field: the price is tested against values typed alone",
    )
    expect(fault({ table: "items", column: "item" })).toBe("tests item, which is in a table with many rows")
    expect(fault({ column: "bi", op: "above", values: ["1"], field: null, days: 0 })).toBe('tests bi with "is greater than", which a True/false column cannot take')
    expect(fault({ op: "above" })).toBe('tests class with "is greater than", which a Text column cannot take')
    expect(fault({ column: "years", op: "ticked" })).toBe('tests years with "is ticked", which an Integer column cannot take')
    expect(fault({ column: "sum_insured", op: "ticked" })).toBe('tests sum_insured with "is ticked", which a Decimal column cannot take')
    expect(fault({ column: "start", op: "one_of" })).toBe('tests start with "is one of", which a Date column cannot take')
    expect(fault({ values: [] })).toBe("needs a value for its test of class")
    expect(fault({ op: "one_of", values: [], field: null, days: 0 })).toBe("needs a value for its test of class")
    expect(fault({ op: "filled" })).toBe('tests class with "is filled", which takes no value')
    expect(fault({ values: ["Motor", "Fleet"] })).toBe('tests class with "equals", which takes one value')
    expect(fault({ op: "one_of", values: ["Motor", " "], field: null, days: 0 })).toBe("has a blank value for class")
    expect(fault({ column: "years", values: ["2.5"] })).toBe('tests years against "2.5", which isn\'t a whole number')
    expect(fault({ column: "sum_insured", values: ["lots"] })).toBe('tests sum_insured against "lots", which isn\'t a number')
    expect(fault({ column: "start", op: "below", values: ["01/05/2026"], field: null, days: 0 })).toBe('tests start against "01/05/2026", which isn\'t a day written YYYY-MM-DD')
  })

  it("faults a test against another field by the field, then the test, then its values and days", () => {
    const against = (column: string, table = "policy") => ({ field: { table, column }, values: [] })
    expect(fault({ ...against("class"), column: "class" })).toBeNull()
    expect(fault({ ...against("start"), column: "start", op: "above", days: -30 })).toBeNull()
    expect(fault(against("gone"))).toBe("tests class against a field that is no longer in the schema")
    expect(fault(against("premium", "premium"))).toBe("tests class against premium, which is in an output table")
    expect(fault(against("item", "items"))).toBe("tests class against item, which is in a table with many rows")
    expect(fault({ ...against("class"), op: "filled" })).toBe('tests class with "is filled" against class, where it takes no value')
    expect(fault({ ...against("class"), op: "one_of" })).toBe('tests class with "is one of" against class, where it takes values typed')
    expect(fault({ ...against("class"), values: ["Motor"] })).toBe("tests class against both a value typed and class")
    expect(fault(against("years"))).toBe("tests class against years, which isn't a Text column")
    expect(fault({ ...against("class"), days: 3 })).toBe("adds days to class, which isn't a Date column")
    expect(fault({ column: "start", op: "above", values: ["2026-01-01"], days: 3 })).toBe("adds days to its test of start without another Date to add them to")

    const every: RowsCondition = { table: "items", rows: "any", where: [{ column: "value", op: "above", values: [], field: "value", days: 0 }] }
    const numbered = formOf([page("p", [])], [policy, valued, premium])
    expect(conditionFault(numbered, every)).toBeNull()
    expect(conditionFault(numbered, { ...every, where: [{ ...every.where[0], field: "gone" }] })).toBe("tests value against a column that is no longer in the schema")
    expect(conditionFault(numbered, { ...every, where: [{ ...every.where[0], field: "row" }] })).toBe("tests value against row, the index, which nothing is typed into")
    expect(conditionFault(numbered, { ...every, where: [{ ...every.where[0], field: "item" }] })).toBe("tests value against item, which isn't a Decimal column")
  })

  it("faults a condition on a table's rows by its table, then each test of a row as a one-row table's condition", () => {
    const filledItem: RowTest = { column: "item", op: "filled", values: [], field: null, days: 0 }
    const every: RowsCondition = { table: "items", rows: "every", where: [filledItem] }
    expect(conditionFault(form, every)).toBeNull()
    expect(conditionFault(form, { ...every, table: "policy" })).toBe("tests the rows of policy, which has one row")
    expect(conditionFault(form, { ...every, table: "premium" })).toBe("tests the rows of premium, which has one row")
    expect(conditionFault(form, { ...every, table: "gone" })).toBe("tests the rows of a table that is no longer in the schema")
    // Its second test is checked as its first.
    const second = (test: Partial<RowTest>) => conditionFault(form, { ...every, where: [filledItem, { ...filledItem, ...test }] })
    expect(second({ op: "ticked" })).toBe('tests item with "is ticked", which a Text column cannot take')
    expect(second({ op: "equals" })).toBe("needs a value for its test of item")
    expect(second({ column: "gone" })).toBe("tests a column that is no longer in the schema")
    const numbered = formOf([page("p", [])], [policy, valued, premium])
    expect(conditionFault(numbered, { ...every, where: [{ column: "row", op: "filled", values: [], field: null, days: 0 }] })).toBe("tests row, the index, which nothing is typed into")
  })

  it("faults a count by its table and its tests, and a total by its column and its number too", () => {
    const count: RowCount = { table: "items", where: [], op: "at_least", count: 2 }
    expect(conditionFault(form, count)).toBeNull()
    expect(conditionFault(form, { ...count, table: "policy" })).toBe("counts the rows of policy, which has one row")
    expect(conditionFault(form, { ...count, table: "premium" })).toBe("counts the rows of premium, which has one row")
    expect(conditionFault(form, { ...count, table: "gone" })).toBe("counts the rows of a table that is no longer in the schema")
    expect(conditionFault(form, { ...count, where: [{ column: "item", op: "equals", values: [], field: null, days: 0 }] })).toBe("needs a value for its test of item")

    const priced = formOf([page("p", [])], [policy, valued, premium])
    const sum: RowTotal = { table: "items", total: "sum", column: "value", where: [], op: "above", value: "£1,000" }
    expect(conditionFault(priced, sum)).toBeNull()
    expect(conditionFault(priced, { ...sum, table: "premium" })).toBe("totals the rows of premium, which has one row")
    expect(conditionFault(priced, { ...sum, column: "item" })).toBe("totals item, which is not a number column")
    expect(conditionFault(priced, { ...sum, column: "row" })).toBe("totals row, the index, which nothing is typed into")
    expect(conditionFault(priced, { ...sum, column: "gone" })).toBe("totals a column that is no longer in the schema")
    expect(conditionFault(priced, { ...sum, value: " " })).toBe("needs a number to compare its total of value with")
    expect(conditionFault(priced, { ...sum, value: "lots" })).toBe('compares its total of value with "lots", which isn\'t a number')
  })
})

describe("neverShown", () => {
  const motorBox = collection("w_motor", [["policy", "motor"]])
  /** A Table or a Collection with each of `shown` a field shown as its `show_when` says. */
  const withFields = (widget: Widget, shown: [string, string, ShowWhen][]): Widget =>
    widget.type === "collection" || widget.type === "tableInput"
      ? { ...widget, column_looks: shown.map(([table, columnName, showWhen]) => ({ table, column: columnName, ...PLAIN_COLUMN_LOOK, show_when: showWhen })) }
      : widget

  it("reports a field shown by its own value, but not one shown by another field of its component", () => {
    const own = ticked("policy", "bi")
    const cover = collection("w_cover", [
      ["policy", "bi"],
      ["policy", "motor"],
      ["policy", "class"],
    ])
    const form = formOf([page("p_main", [withFields(cover, [["policy", "bi", when(own)], ["policy", "class", when(ticked("policy", "motor"))]])])])
    expect([...neverShown(form)]).toEqual([[fieldKey("w_cover", "policy", "bi"), own]])
  })

  it("reports two fields each waiting on the other, and not a field waiting on one that shows with it elsewhere", () => {
    const cover = collection("w_cover", [
      ["policy", "bi"],
      ["policy", "motor"],
    ])
    const waiting = withFields(cover, [
      ["policy", "bi", when(ticked("policy", "motor"))],
      ["policy", "motor", when(ticked("policy", "bi"))],
    ])
    expect([...neverShown(formOf([page("p_main", [waiting])]))]).toEqual([
      [fieldKey("w_cover", "policy", "bi"), ticked("policy", "motor")],
      [fieldKey("w_cover", "policy", "motor"), ticked("policy", "bi")],
    ])
    // The motor box shown on another component as well, the bi field can show again, and so the motor field.
    expect(neverShown(formOf([page("p_main", [waiting, motorBox])])).size).toBe(0)
  })

  it("leaves a field of a component that can never show to its component, and counts no field that cannot show again", () => {
    const own = ticked("policy", "bi")
    const never = withFields(collection("w_never", [["policy", "class"]], when(ticked("policy", "motor"))), [["policy", "class", when(own)]])
    // The motor box hidden by its own value: a section waiting on it waits on a field that cannot show again.
    const motorField = withFields(motorBox, [["policy", "motor", when(ticked("policy", "motor"))]])
    const form = formOf([sectioned("p_main", [section("s_main", [never, motorField, collection("w_bi", [["policy", "bi"]])]), section("s_motor", [], when(ticked("policy", "motor")))])])
    expect([...neverShown(form)]).toEqual([
      ["w_never", ticked("policy", "motor")],
      [fieldKey("w_motor", "policy", "motor"), ticked("policy", "motor")],
      ["s_motor", ticked("policy", "motor")],
    ])
  })

  it("reports a sheet shown by a box on the very sheet, in any of its sections, with the condition it waits on", () => {
    const waits = ticked("policy", "bi")
    const form = formOf([page("p_main", []), sectioned("p_bi", [section("s_limits", []), section("s_bi", [collection("w_bi", [["policy", "bi"]])])], when(waits))])
    expect([...neverShown(form)]).toEqual([["p_bi", waits]])
  })

  it("reports a section shown by a box in the very section, but not one shown by a box in another section of its sheet", () => {
    const waits = ticked("policy", "bi")
    const own = formOf([sectioned("p_main", [section("s_main", [motorBox]), section("s_bi", [collection("w_bi", [["policy", "bi"]])], when(waits))])])
    expect([...neverShown(own)]).toEqual([["s_bi", waits]])

    const elsewhere = formOf([
      sectioned("p_main", [section("s_main", [collection("w_main", [["policy", "bi"]])]), section("s_bi", [collection("w_bi", [["policy", "class"]])], when(waits))]),
    ])
    expect(neverShown(elsewhere).size).toBe(0)
  })

  it("reports two sheets each waiting on a box on the other", () => {
    const form = formOf([
      page("p_a", [collection("w_a", [["policy", "bi"]])], when(ticked("policy", "motor"))),
      page("p_b", [collection("w_b", [["policy", "motor"]])], when(ticked("policy", "bi"))),
    ])
    expect([...neverShown(form)]).toEqual([
      ["p_a", ticked("policy", "motor")],
      ["p_b", ticked("policy", "bi")],
    ])
  })

  it("does not report what a box on a sheet that always shows could show, or any with one condition reachable", () => {
    const form = formOf([
      page("p_main", [motorBox]),
      page("p_motor", [collection("w_x", [["policy", "class"]])], when(ticked("policy", "motor"))),
      page("p_any", [collection("w_bi", [["policy", "bi"]])], { match: "any", conditions: [ticked("policy", "bi"), ticked("policy", "motor")] }),
    ])
    expect(neverShown(form).size).toBe(0)
  })

  it("leaves a component on a sheet that can never show to its sheet, and reports a component waiting on itself", () => {
    const own = ticked("policy", "bi")
    const form = formOf([
      page("p_main", [collection("w_self", [["policy", "bi"]], when(own))]),
      page("p_never", [collection("w_never", [["policy", "class"]], when(own))], when(ticked("policy", "motor"))),
    ])
    expect([...neverShown(form)]).toEqual([
      ["w_self", own],
      ["p_never", ticked("policy", "motor")],
    ])
  })

  it("leaves a component in a section that can never show to its section, and a section on a sheet that can never show to its sheet", () => {
    const own = ticked("policy", "bi")
    const inSection = formOf([
      sectioned("p_main", [
        section("s_main", [collection("w_self", [["policy", "bi"]], when(own))]),
        section("s_never", [collection("w_never", [["policy", "class"]], when(own))], when(ticked("policy", "motor"))),
      ]),
    ])
    expect([...neverShown(inSection)]).toEqual([
      ["w_self", own],
      ["s_never", ticked("policy", "motor")],
    ])

    const onSheet = formOf([page("p_main", []), sectioned("p_never", [section("s_waiting", [], when(own))], when(ticked("policy", "motor")))])
    expect([...neverShown(onSheet)]).toEqual([["p_never", ticked("policy", "motor")]])
  })

  it("reports a sheet waiting on a group's condition, with that condition", () => {
    const waits = ticked("policy", "bi")
    const form = formOf([page("p_main", [motorBox]), page("p_bi", [collection("w_bi", [["policy", "bi"]])], { match: "all", conditions: [{ match: "any", conditions: [waits] }] })])
    expect([...neverShown(form)]).toEqual([["p_bi", waits]])

    const reachable = { match: "all" as const, conditions: [{ match: "any" as const, conditions: [waits, ticked("policy", "motor")] }] }
    expect(neverShown(formOf([page("p_main", [motorBox]), page("p_bi", [collection("w_bi", [["policy", "bi"]])], reachable)])).size).toBe(0)
  })

  it("reports a sheet needing at least more conditions than it can reach, with the one it waits on", () => {
    const own = ticked("policy", "bi")
    const atLeast = (match: number) =>
      formOf([page("p_main", [motorBox]), page("p_bi", [collection("w_bi", [["policy", "bi"]])], { match, conditions: [ticked("policy", "motor"), own] })])
    expect([...neverShown(atLeast(2))]).toEqual([["p_bi", own]])
    expect(neverShown(atLeast(1)).size).toBe(0)
  })

  it("reaches a count through any of its table's columns on a component outside it, and reports one only on itself", () => {
    const count: RowCount = { table: "items", where: [], op: "at_least", count: 1 }
    const own = formOf([page("p_main", [motorBox]), page("p_items", [collection("w_items", [["items", "item"]])], when(count))])
    expect([...neverShown(own)]).toEqual([["p_items", count]])

    const elsewhere = formOf([page("p_main", [collection("w_items", [["items", "item"]])]), page("p_more", [motorBox], when(count))])
    expect(neverShown(elsewhere).size).toBe(0)
  })

  it("reaches a condition on rows only while each column it tests is on a component outside it", () => {
    const tested: RowsCondition = {
      table: "items",
      rows: "any",
      where: [
        { column: "item", op: "filled", values: [], field: null, days: 0 },
        { column: "value", op: "filled", values: [], field: null, days: 0 },
      ],
    }
    const onMain = (fields: [string, string][]) =>
      formOf([page("p_main", [motorBox, collection("w_items", fields)]), page("p_more", [collection("w_more", [["policy", "class"]])], when(tested))], [policy, valued, premium])
    expect([...neverShown(onMain([["items", "item"]]))]).toEqual([["p_more", tested]])
    expect(neverShown(onMain([["items", "item"], ["items", "value"]])).size).toBe(0)
  })

  it("leaves out a sheet whose condition does not fit, as that is reported already", () => {
    const form = formOf([page("p_main", [motorBox]), page("p_bad", [], when({ table: "policy", column: "gone", op: "ticked", values: [], field: null, days: 0 }))])
    expect(neverShown(form).size).toBe(0)
  })
})

describe("the editor's steps", () => {
  const form = formOf([page("p", [])])

  it("starts a condition on the first one-row input table's first column, else in any row of a many-row one's, or none without one", () => {
    expect(newCondition(form)).toEqual({ table: "policy", column: "bi", op: "ticked", values: [], field: null, days: 0 })
    expect(newCondition(formOf([page("p", [])], [items, premium, { ...policy, columns: [] }]))).toEqual({
      table: "items",
      rows: "any",
      where: [{ column: "item", op: "equals", values: [], field: null, days: 0 }],
    })
    // A many-row table's index takes no typing, so it is never tested.
    const numbered = { ...items, columns: [column("row", { type: "int", index: true })] }
    expect(newCondition(formOf([page("p", [])], [numbered, premium, { ...policy, columns: [] }]))).toBeNull()
  })

  it("moves a condition to another column, its test and values kept while they fit", () => {
    const years: Condition = { table: "policy", column: "years", op: "above", values: ["3"], field: null, days: 0 }
    expect(withConditionColumn(form, years, "policy", "sum_insured")).toEqual({ table: "policy", column: "sum_insured", op: "above", values: ["3"], field: null, days: 0 })
    // A Text column does not take "above": the test becomes its first, and the value, which fits it, is kept.
    expect(withConditionColumn(form, years, "policy", "class")).toEqual({ table: "policy", column: "class", op: "equals", values: ["3"], field: null, days: 0 })
    expect(withConditionColumn(form, years, "policy", "bi")).toEqual({ table: "policy", column: "bi", op: "ticked", values: [], field: null, days: 0 })
    const fraction: Condition = { table: "policy", column: "sum_insured", op: "equals", values: ["2.5"], field: null, days: 0 }
    expect(withConditionColumn(form, fraction, "policy", "years")).toEqual({ table: "policy", column: "years", op: "equals", values: [], field: null, days: 0 })
    expect(() => withConditionColumn(form, years, "policy", "gone")).toThrow(/gone/)
    expect(years).toEqual({ table: "policy", column: "years", op: "above", values: ["3"], field: null, days: 0 })
  })

  it("moves a condition to a table's rows and back, keeping its test of a column of that table", () => {
    const years: Condition = { table: "policy", column: "years", op: "equals", values: ["3"], field: null, days: 0 }
    // Another table's rows start in any row with a test of their first column.
    expect(withConditionRowsOf(form, years, "items")).toEqual({ table: "items", rows: "any", where: [{ column: "item", op: "equals", values: [], field: null, days: 0 }] })
    // A one-row table's condition on a table switched to many rows becomes a test of a row as it is.
    const switched: Condition = { table: "items", column: "item", op: "filled", values: [], field: null, days: 0 }
    expect(withConditionRowsOf(form, switched, "items")).toEqual({ table: "items", rows: "any", where: [{ column: "item", op: "filled", values: [], field: null, days: 0 }] })
    const every: RowsCondition = { table: "items", rows: "every", where: [{ column: "item", op: "filled", values: [], field: null, days: 0 }] }
    expect(withConditionRowsOf(form, every, "items")).toBe(every)
    expect(() => withConditionRowsOf(formOf([page("p", [])], [policy, { ...items, columns: [] }]), years, "items")).toThrow(/items/)

    // Back to a one-row table's column, its test of that column kept, else its first test of a row's.
    expect(withConditionColumn(form, every, "policy", "class")).toEqual({ table: "policy", column: "class", op: "filled", values: [], field: null, days: 0 })
    const onPolicy: RowsCondition = {
      table: "policy",
      rows: "any",
      where: [
        { column: "sum_insured", op: "above", values: ["9"], field: null, days: 0 },
        { column: "class", op: "equals", values: ["Motor"], field: null, days: 0 },
      ],
    }
    expect(withConditionColumn(form, onPolicy, "policy", "class")).toEqual({ table: "policy", column: "class", op: "equals", values: ["Motor"], field: null, days: 0 })
    const count: RowCount = { table: "items", where: [], op: "at_least", count: 1 }
    expect(withConditionColumn(form, count, "policy", "years")).toEqual({ table: "policy", column: "years", op: "equals", values: [], field: null, days: 0 })
  })

  it("moves a test against another field to its table's rows and back, keeping the field while it is a column of the table", () => {
    const cover: SchemaTable = { name: "cover", role: "input", rows: "one", columns: [column("from", { type: "date" })] }
    const dated: SchemaTable = { ...policy, columns: [...policy.columns, column("end", { type: "date" })] }
    const one = formOf([page("p", [])], [dated, cover, premium])
    // The table switched to many rows since the condition was made.
    const many = formOf([page("p", [])], [{ ...dated, rows: "many" }, cover, premium])
    const later: Condition = { table: "policy", column: "end", op: "above", values: [], field: { table: "policy", column: "start" }, days: 365 }
    const anyLater: RowsCondition = { table: "policy", rows: "any", where: [{ column: "end", op: "above", values: [], field: "start", days: 365 }] }
    expect(withConditionRowsOf(many, later, "policy")).toEqual(anyLater)
    // A field of another table is no column of these rows.
    expect(withConditionRowsOf(many, { ...later, field: { table: "cover", column: "from" } }, "policy")).toEqual({
      ...anyLater,
      where: [{ column: "end", op: "above", values: [], field: null, days: 0 }],
    })
    // Switched back to one row, the column of the row is that column of the table.
    expect(withConditionColumn(one, anyLater, "policy", "end")).toEqual(later)
    // A column is never tested against itself, nor a one-row table's against a column of many rows.
    expect(withConditionColumn(one, anyLater, "policy", "start")).toEqual({ table: "policy", column: "start", op: "above", values: [], field: null, days: 0 })
    expect(withConditionColumn(many, anyLater, "cover", "from")).toEqual({ table: "cover", column: "from", op: "above", values: [], field: null, days: 0 })
  })

  it("gives a condition on rows which rows, keeping its tests", () => {
    const priced = formOf([page("p", [])], [policy, valued, premium])
    const filledItem: RowTest = { column: "item", op: "filled", values: [], field: null, days: 0 }
    const any: RowsCondition = { table: "items", rows: "any", where: [filledItem] }
    expect(withListKind(priced, any, "every")).toEqual({ ...any, rows: "every" })
    const count = withListKind(priced, any, "count")
    expect(count).toEqual({ table: "items", where: [filledItem], op: "at_least", count: 1 })
    expect(withListKind(priced, count, "count")).toBe(count)
    const sum = withListKind(priced, count, "sum")
    expect(sum).toEqual({ table: "items", total: "sum", column: "value", where: [filledItem], op: "above", value: "" })
    expect(withListKind(priced, { ...(sum as RowTotal), value: "5" }, "max")).toEqual({ ...sum, value: "5", total: "max" })
    // Any or every row from a count without tests starts with a test of the table's first column.
    expect(withListKind(priced, { table: "items", where: [], op: "equals", count: 0 }, "every")).toEqual({
      table: "items",
      rows: "every",
      where: [{ column: "item", op: "equals", values: [], field: null, days: 0 }],
    })
    // Without a column to test, or a number column to total, there is none to start.
    expect(() => withListKind(form, any, "sum")).toThrow(/number column/)
    const bare = formOf([page("p", [])], [policy, { ...items, columns: [] }])
    expect(() => withListKind(bare, { table: "items", where: [], op: "equals", count: 0 }, "any")).toThrow(/items/)
    expect(totalColumns(valued).map((column) => column.name)).toEqual(["value"])
    expect(newRowTest(valued)).toEqual({ column: "item", op: "equals", values: [], field: null, days: 0 })
    expect(newRowTest({ ...valued, columns: [valued.columns[0]] })).toBeNull()
  })

  it("moves a test of a row to another column of its table, its test and values kept while they fit", () => {
    const priced = formOf([page("p", [])], [policy, valued, premium])
    expect(withTestColumn(priced, "items", { column: "item", op: "equals", values: ["3"], field: null, days: 0 }, "value")).toEqual({ column: "value", op: "equals", values: ["3"], field: null, days: 0 })
    expect(withTestColumn(priced, "items", { column: "item", op: "equals", values: ["Pump"], field: null, days: 0 }, "value")).toEqual({ column: "value", op: "equals", values: [], field: null, days: 0 })
    expect(() => withTestColumn(priced, "items", { column: "item", op: "filled", values: [], field: null, days: 0 }, "gone")).toThrow(/gone/)
  })

  it("gives a condition another test, its values kept while their number and the column take them", () => {
    const oneOf: Condition = { table: "policy", column: "years", op: "one_of", values: ["2.5", "3", "4"], field: null, days: 0 }
    const years = policy.columns.find((candidate) => candidate.name === "years")
    expect(withConditionTest(years, oneOf, "filled").values).toEqual([])
    expect(withConditionTest(years, oneOf, "equals").values).toEqual([])
    expect(withConditionTest(years, { ...oneOf, values: ["3", "4"] }, "equals")).toEqual({ ...oneOf, op: "equals", values: ["3"], field: null, days: 0 })
    expect(withConditionTest(years, oneOf, "not_one_of").values).toEqual(["3", "4"])
    // A test whose column is gone keeps its values by number alone.
    const gone: Condition = { table: "policy", column: "gone", op: "one_of", values: ["a", "b"], field: null, days: 0 }
    expect(withConditionTest(undefined, gone, "equals").values).toEqual(["a"])
    expect(withConditionTest(undefined, gone, "not_one_of").values).toEqual(["a", "b"])
    expect(withConditionTest(undefined, gone, "empty").values).toEqual([])
    expect(oneOf.values).toEqual(["2.5", "3", "4"])
  })
})
