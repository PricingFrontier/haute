/**
 * The conditions on the sheets, sections, components and fields (specs/workbench): the tests
 * each column's type takes and their words, what stops a condition holding as written, and
 * which sheets, sections, components and fields show for values typed into the sheets, none
 * while building, the sample or the underwriter app's quote in Preview, and so which input
 * columns a quote leaves out. The server applies the same rules in src/haute/_workbench_visibility.py, and both
 * are held to the cases in __tests__/fixtures/sheetVisibility.json.
 */
import type { Condition, ConditionGroup, FieldRef, FormSchema, FormSpec, RowCount, RowsCondition, RowTest, RowTotal, SchemaColumn, SchemaTable, Section, ShowWhen } from "../api/types"
import { filled, isDay, parseTypedNumber, presetRows, quoteTyped, rowFilled, withPresetRows, type RowsByTable } from "./sheetValues"

export type ConditionTest = Condition["op"]
export type CountTest = RowCount["op"]
export type TotalKind = RowTotal["total"]
/** One condition: on a one-row table's column, or on a many-row table's rows. */
export type SingleCondition = ConditionGroup["conditions"][number]
/** An entry of a `show_when`: one condition or a group of them. */
export type ShowWhenEntry = ShowWhen["conditions"][number]
/** A condition on a many-row table's rows: in any or every row, how many, or a total over them. */
export type ListCondition = RowsCondition | RowCount | RowTotal
/** A test of a column's value: a one-row table's condition, or a test of a row. */
export type ColumnTest = Condition | RowTest
type ColumnType = SchemaColumn["type"]
type Widget = Section["widgets"][number]
type Row = RowsByTable[string][number]
type Cell = Row[string]

/** A column's key in a set of columns: its table id and column id. */
export const columnKey = (tableId: string, columnId: string): string => `${tableId}:${columnId}`

/** A field's key from its component's id and its column's `columnKey`, the `:` between them in no id. */
const keyOfField = (widgetId: string, column: string): string => `${widgetId}:${column}`

/** A field's key in a set of fields: its component's id and its column's `columnKey`. */
export const fieldKey = (widgetId: string, tableId: string, columnId: string): string => keyOfField(widgetId, columnKey(tableId, columnId))

/** When a new sheet, section, component or field shows: always. */
export const ALWAYS: Readonly<ShowWhen> = { match: "all", conditions: [] }

/** Whether an entry of a `show_when` is a group, told by its fields as the server tells it. */
export const isGroup = (entry: ShowWhenEntry): entry is ConditionGroup => "conditions" in entry

/** Whether a condition is on a many-row table's rows, with the tests a row must pass. */
export const isListCondition = (condition: ShowWhenEntry): condition is ListCondition => "where" in condition

/** Whether a condition counts the rows of a many-row table passing its tests. */
export const isRowCount = (condition: ShowWhenEntry): condition is RowCount => "count" in condition

/** Whether a condition totals a column over the rows of a many-row table passing its tests. */
export const isRowTotal = (condition: ShowWhenEntry): condition is RowTotal => "total" in condition

/** Whether a condition holds in any of a many-row table's rows passing its tests, or in every one. */
export const isRowsCondition = (condition: ShowWhenEntry): condition is RowsCondition => "rows" in condition

/** The ids of the columns a condition on a list's rows tests: its tests of a row's, and a total's. */
export const listColumns = (condition: ListCondition): string[] => {
  const tested = condition.where.map((test) => test.column)
  return isRowTotal(condition) ? [...tested, condition.column] : tested
}

/** A `show_when`'s conditions, each group's in its place. */
export const leafConditions = (showWhen: ShowWhen): SingleCondition[] =>
  showWhen.conditions.flatMap((entry) => (isGroup(entry) ? entry.conditions : [entry]))

/** The values each test takes: none, exactly one, or one or more. */
export const CONDITION_TESTS: Readonly<Record<ConditionTest, { values: "none" | "one" | "some" }>> = {
  ticked: { values: "none" },
  not_ticked: { values: "none" },
  filled: { values: "none" },
  empty: { values: "none" },
  equals: { values: "one" },
  not_equals: { values: "one" },
  one_of: { values: "some" },
  not_one_of: { values: "some" },
  above: { values: "one" },
  at_least: { values: "one" },
  below: { values: "one" },
  at_most: { values: "one" },
}

const TESTS: Readonly<Record<ColumnType, readonly ConditionTest[]>> = {
  bool: ["ticked", "not_ticked"],
  str: ["equals", "not_equals", "one_of", "not_one_of", "filled", "empty"],
  int: ["equals", "not_equals", "above", "at_least", "below", "at_most", "one_of", "not_one_of", "filled", "empty"],
  float: ["equals", "not_equals", "above", "at_least", "below", "at_most", "one_of", "not_one_of", "filled", "empty"],
  date: ["equals", "not_equals", "above", "at_least", "below", "at_most", "filled", "empty"],
}

/** The tests a column's type takes, in the order the editor offers them. */
export const testsFor = (type: ColumnType): readonly ConditionTest[] => TESTS[type]

/** The tests a count of rows or a total takes, in the order the editor offers them: a number's. */
export const COUNT_TESTS: readonly CountTest[] = ["equals", "not_equals", "above", "at_least", "below", "at_most"]

const COUNTS: Readonly<Record<CountTest, (found: number, wanted: number) => boolean>> = {
  equals: (found, wanted) => found === wanted,
  not_equals: (found, wanted) => found !== wanted,
  above: (found, wanted) => found > wanted,
  at_least: (found, wanted) => found >= wanted,
  below: (found, wanted) => found < wanted,
  at_most: (found, wanted) => found <= wanted,
}

/** A total's kinds and their words, in the order the editor offers them. */
export const TOTALS: ReadonlyArray<{ value: TotalKind; label: string }> = [
  { value: "sum", label: "Sum" },
  { value: "min", label: "Smallest" },
  { value: "max", label: "Largest" },
  { value: "average", label: "Average" },
]

const LABELS: Readonly<Record<ConditionTest, string>> = {
  ticked: "is ticked",
  not_ticked: "is not ticked",
  filled: "is filled",
  empty: "is empty",
  equals: "equals",
  not_equals: "does not equal",
  one_of: "is one of",
  not_one_of: "is not one of",
  above: "is greater than",
  at_least: "is at least",
  below: "is less than",
  at_most: "is at most",
}

const DATE_LABELS: Readonly<Partial<Record<ConditionTest, string>>> = {
  equals: "is on",
  not_equals: "is not on",
  above: "is after",
  at_least: "is on or after",
  below: "is before",
  at_most: "is on or before",
}

/** A test's words for a column of the type: a Date's comparisons read as days. */
export const testLabel = (test: ConditionTest, type: ColumnType): string =>
  (type === "date" ? DATE_LABELS[test] : undefined) ?? LABELS[test]

const TYPE_WORDS: Readonly<Record<ColumnType, string>> = {
  bool: "a True/false",
  str: "a Text",
  int: "an Integer",
  float: "a Decimal",
  date: "a Date",
}

const NOT_A_VALUE: Readonly<Record<ColumnType, string>> = {
  bool: "a value",
  str: "text",
  int: "a whole number",
  float: "a number",
  date: "a day written YYYY-MM-DD",
}

/** Whether stripped text is of a column's type: a number, whole for an Integer; a day; any text. */
function ofType(type: ColumnType, text: string): boolean {
  if (type === "int" || type === "float") {
    const number = parseTypedNumber(text)
    return number !== null && (type === "float" || Number.isInteger(number))
  }
  if (type === "date") return isDay(text)
  return true
}

/**
 * Whether a condition's value is one the column could hold: text other than blank; for a
 * number column a number as the server types one, whole for an Integer; for a Date a real
 * YYYY-MM-DD day. A True/false column holds a tick, never text, and its tests take none.
 */
export function valueFits(column: SchemaColumn, value: string): boolean {
  const text = value.trim()
  if (column.type === "bool" || text === "") return false
  return ofType(column.type, text)
}

const tableOf = (schema: FormSchema, tableId: string): SchemaTable | undefined =>
  schema.tables.find((table) => table.id === tableId)

const columnOf = (schema: FormSchema, tableId: string, columnId: string): SchemaColumn | undefined =>
  tableOf(schema, tableId)?.columns.find((column) => column.id === columnId)

const countFits = (test: ConditionTest, count: number): boolean => {
  const takes = CONDITION_TESTS[test].values
  return takes === "none" ? count === 0 : takes === "one" ? count === 1 : count >= 1
}

/**
 * What stops a condition holding as written, or null when it fits: a clause completing a
 * sentence that starts with its sheet's or component's name. A one-row table's condition
 * is checked by its column, then its table's rows, then as a test (`testFault`); one on a
 * list's rows by its table, then each test of a row, then a total's column and value
 * (`listFault`); the first fault is reported.
 */
export function conditionFault(spec: FormSpec, condition: SingleCondition): string | null {
  const table = tableOf(spec.schema, condition.table)
  if (isListCondition(condition)) return listFault(table, condition)
  const column = table?.columns.find((candidate) => candidate.id === condition.column)
  if (table === undefined || column === undefined) return "tests a column that is no longer in the schema"
  if (table.role === "output") return `tests ${column.name}, which is in an output table`
  if (table.rows === "many") return `tests ${column.name}, which is in a table with many rows`
  return testFault(column, condition)
}

/** What stops a test of a column's value holding as written: a test its type does not take, the wrong number of values, or a value it could not hold. */
function testFault(column: SchemaColumn, test: ColumnTest): string | null {
  const name = column.name
  const label = testLabel(test.op, column.type)
  if (!testsFor(column.type).includes(test.op)) {
    return `tests ${name} with "${label}", which ${TYPE_WORDS[column.type]} column cannot take`
  }
  const takes = CONDITION_TESTS[test.op].values
  const count = test.values.length
  if (takes !== "none" && count === 0) return `needs a value for its test of ${name}`
  if (takes === "none" && count > 0) return `tests ${name} with "${label}", which takes no value`
  if (takes === "one" && count > 1) return `tests ${name} with "${label}", which takes one value`
  for (const value of test.values) {
    if (value.trim() === "") return `has a blank value for ${name}`
    if (!valueFits(column, value)) return `tests ${name} against "${value}", which isn't ${NOT_A_VALUE[column.type]}`
  }
  return null
}

/** What stops a condition on a list's rows holding as written: its table not a many-row input table, a test of a row, or a total's column or value. */
function listFault(table: SchemaTable | undefined, condition: ListCondition): string | null {
  const verb = isRowCount(condition) ? "counts" : isRowTotal(condition) ? "totals" : "tests"
  if (table === undefined) return `${verb} the rows of a table that is no longer in the schema`
  if (table.role === "output") return `${verb} the rows of ${table.name}, which is an output table`
  if (table.rows === "one") return `${verb} the rows of ${table.name}, which has one row`
  for (const test of condition.where) {
    const column = table.columns.find((candidate) => candidate.id === test.column)
    if (column === undefined) return "tests a column that is no longer in the schema"
    if (column.index) return `tests ${column.name}, the index, which nothing is typed into`
    const fault = testFault(column, test)
    if (fault !== null) return fault
  }
  if (!isRowTotal(condition)) return null
  const column = table.columns.find((candidate) => candidate.id === condition.column)
  if (column === undefined) return "totals a column that is no longer in the schema"
  if (column.index) return `totals ${column.name}, the index, which nothing is typed into`
  if (column.type !== "int" && column.type !== "float") return `totals ${column.name}, which is not a number column`
  const value = condition.value.trim()
  if (value === "") return `needs a number to compare its total of ${column.name} with`
  if (!ofType("float", value)) return `compares its total of ${column.name} with "${value}", which isn't a number`
  return null
}

/** Stripped text as the server types it for a column: a number column's numbers as numbers. */
function typed(type: ColumnType, text: string): number | string {
  const stripped = text.trim()
  if (type !== "int" && type !== "float") return stripped
  return parseTypedNumber(stripped) ?? stripped
}

/** Whether a cell is filled and equal to the value, typed alike: a tick never equals a value. */
const equals = (type: ColumnType, cell: Cell | undefined, value: string): boolean =>
  typeof cell === "string" && filled(cell) && typed(type, cell) === typed(type, value)

/**
 * How a cell orders against a fitting condition's value: below, at or above zero for a
 * cell less than, equal to or greater than it, numbers compared as numbers and days as
 * their YYYY-MM-DD text, which orders as the days do; NaN for a cell that is not a filled
 * value of the column's type, which no comparison passes.
 */
function order(type: ColumnType, cell: Cell | undefined, value: string): number {
  if (typeof cell !== "string") return NaN
  const text = cell.trim()
  if (!ofType(type, text)) return NaN
  if (type === "date") {
    const day = value.trim()
    return text < day ? -1 : text > day ? 1 : 0
  }
  // Neither is null: the cell is a number by ofType, and a fitting condition's value by valueFits.
  return Math.sign((parseTypedNumber(text) ?? NaN) - (parseTypedNumber(value) ?? NaN))
}

/** Whether a fitting condition's test passes the cell typed into its column. */
function passes(type: ColumnType, condition: ColumnTest, cell: Cell | undefined): boolean {
  const [first] = condition.values
  switch (condition.op) {
    case "ticked":
      return cell === true
    case "not_ticked":
      return cell !== true
    case "filled":
      return filled(cell)
    case "empty":
      return !filled(cell)
    case "equals":
      return equals(type, cell, first)
    case "not_equals":
      return !equals(type, cell, first)
    case "one_of":
      return condition.values.some((value) => equals(type, cell, value))
    case "not_one_of":
      return !condition.values.some((value) => equals(type, cell, value))
    case "above":
      return order(type, cell, first) > 0
    case "at_least":
      return order(type, cell, first) >= 0
    case "below":
      return order(type, cell, first) < 0
    case "at_most":
      return order(type, cell, first) <= 0
  }
}

/** Which sheets, sections, components and fields show for the values, and the input columns a quote leaves out. */
export interface SheetVisibility {
  /** The ids of the sheets that show. */
  pages: ReadonlySet<string>
  /** The ids of the sections that show, each on a sheet that shows. */
  sections: ReadonlySet<string>
  /** The ids of the components that show, each in a section that shows. */
  widgets: ReadonlySet<string>
  /** The fields that show, by `fieldKey`, each of a Table or a Collection that shows. */
  fields: ReadonlySet<string>
  /** The input columns left out of the quote, by `columnKey`. */
  hidden: ReadonlySet<string>
}

/**
 * The columns a component has values typed into: a Table's and a Collection's fields, none
 * of a Chart's, which it only plots, and none of a Heading, which has no fields.
 */
export const fieldKeys = (widget: Widget): string[] =>
  widget.type === "tableInput" || widget.type === "collection" ? widget.fields.map((field) => columnKey(field.table, field.column)) : []

/** When a Table's or a Collection's field shows with its component: as its look's `show_when` says, always without one (`_field_show_when` on the server). */
export const fieldShowWhen = (widget: Widget, field: FieldRef): ShowWhen =>
  widget.type === "tableInput" || widget.type === "collection"
    ? (widget.column_looks.find((look) => look.table === field.table && look.column === field.column)?.show_when ?? ALWAYS)
    : ALWAYS

/** A component's fields that take values typed, each by its `fieldKey` and its column's `columnKey`, with when it shows. */
const enteredFields = (widget: Widget): { key: string; column: string; showWhen: ShowWhen }[] =>
  widget.type === "tableInput" || widget.type === "collection"
    ? widget.fields.map((field) => ({ key: fieldKey(widget.id, field.table, field.column), column: columnKey(field.table, field.column), showWhen: fieldShowWhen(widget, field) }))
    : []

/** Whether all of `entries` hold, any, or at least `match` of them (`_matches` on the server). */
const matches = <T,>(match: ShowWhen["match"], entries: readonly T[], holds: (entry: T) => boolean): boolean => {
  if (match === "all") return entries.every(holds)
  if (match === "any") return entries.some(holds)
  return entries.filter(holds).length >= match
}

/** Whether a `show_when` passes: with no conditions, always; else as its `match` says, a group as its own does. */
const showWhenPasses = (showWhen: ShowWhen, holds: (condition: SingleCondition) => boolean): boolean =>
  showWhen.conditions.length === 0 ||
  matches(showWhen.match, showWhen.conditions, (entry) => (isGroup(entry) ? matches(entry.match, entry.conditions, holds) : holds(entry)))

/**
 * A many-row table's rows as a grid holds them (`presetRows`), each with anything but the
 * index typed in it, whatever the sheets hide: counting only what shows would let hiding a
 * column drop a row and show something again (`_typed_rows` on the server).
 */
const typedRows = (table: SchemaTable, rows: readonly Row[]): readonly Row[] => presetRows(table, rows).filter((row) => rowFilled(table, row))

/** The rows passing every test of a condition on a list's rows, in the same row, its columns fitting (`_passing_rows` on the server). */
function passingRows(table: SchemaTable, where: readonly RowTest[], rows: readonly Row[]): readonly Row[] {
  const types = new Map(table.columns.map((column) => [column.id, column.type]))
  // A condition that fits tests columns its table has.
  return rows.filter((row) => where.every((test) => passes(types.get(test.column) as ColumnType, test, row[test.column])))
}

/**
 * The sum, least, greatest or average of the numbers a total's column holds in the rows, a
 * cell blank or not a number of the column's type left out, added in order and taken to
 * 15 significant digits so that 0.1, 0.2 and 0.3 sum to 0.6: the sum of none is 0, and the
 * others of none are null, which no test passes (`_total` on the server).
 */
function totalOf(table: SchemaTable, condition: RowTotal, rows: readonly Row[]): number | null {
  // A total that fits takes a number column its table has.
  const type = (table.columns.find((column) => column.id === condition.column) as SchemaColumn).type
  const numbers = rows.flatMap((row) => {
    const cell = row[condition.column]
    const text = typeof cell === "string" ? cell.trim() : ""
    return typeof cell === "string" && ofType(type, text) ? [parseTypedNumber(text) as number] : []
  })
  if (numbers.length === 0 && condition.total !== "sum") return null
  let added = 0
  for (const number of numbers) added += number
  const found =
    condition.total === "sum"
      ? added
      : condition.total === "min"
        ? Math.min(...numbers)
        : condition.total === "max"
          ? Math.max(...numbers)
          : added / numbers.length
  return Number(found.toPrecision(15))
}

/** The keys of a counted table's columns other than the index, any of which on a component that shows lets a count without tests hold. */
const countedKeys = (table: SchemaTable): string[] => table.columns.filter((column) => !column.index).map((column) => columnKey(table.id, column.id))

function decide(spec: FormSpec, rows: RowsByTable): SheetVisibility {
  // Whether each condition fits: a condition that does not fit never holds.
  const fitted = new Map<SingleCondition, boolean>()
  const fits = (condition: SingleCondition): boolean => {
    let fit = fitted.get(condition)
    if (fit === undefined) {
      fit = conditionFault(spec, condition) === null
      fitted.set(condition, fit)
    }
    return fit
  }
  // A many-row table's typed rows, read once: they do not change as the rounds hide.
  const typed = new Map<SchemaTable, readonly Row[]>()
  const typedOf = (table: SchemaTable): readonly Row[] => {
    let held = typed.get(table)
    if (held === undefined) {
      held = typedRows(table, rows[table.id] ?? [])
      typed.set(table, held)
    }
    return held
  }

  /**
   * Whether a fitting condition on a list's rows holds: each column it tests on a component
   * that shows (for a count without tests, one of its table's but the index), and its rows
   * passing as it says.
   */
  const listHolds = (table: SchemaTable, condition: ListCondition, shown: ReadonlySet<string>): boolean => {
    const tested = listColumns(condition)
    const showing = tested.length > 0 ? tested.every((id) => shown.has(columnKey(table.id, id))) : countedKeys(table).some((key) => shown.has(key))
    if (!showing) return false
    const typed = typedOf(table)
    const passing = passingRows(table, condition.where, typed)
    if (isRowsCondition(condition)) return condition.rows === "any" ? passing.length > 0 : typed.length > 0 && passing.length === typed.length
    if (isRowCount(condition)) return COUNTS[condition.op](passing.length, condition.count)
    const found = totalOf(table, condition, passing)
    // A total that fits compares with a number.
    return found !== null && COUNTS[condition.op](found, parseTypedNumber(condition.value.trim()) as number)
  }

  const placed = spec.pages.flatMap((page) => page.sections.flatMap((section) => section.widgets.map((widget) => ({ section, widget }))))
  const entered = placed.flatMap(({ widget }) => enteredFields(widget).map((field) => ({ widgetId: widget.id, ...field })))
  let pages = new Set(spec.pages.map((page) => page.id))
  let sections = new Set(spec.pages.flatMap((page) => page.sections.map((section) => section.id)))
  let widgets = new Set(placed.map(({ widget }) => widget.id))
  let fields = new Set(entered.map(({ key }) => key))
  // A field still shown is of a component, in a section and on a sheet, still shown: each
  // round hides a sheet's sections, a section's components and a component's fields with it.
  const shownColumns = (): Set<string> => new Set(entered.filter(({ key }) => fields.has(key)).map(({ column }) => column))

  // Hiding only ever hides more, so a condition that fails stays failed and the rounds end.
  let hiding = true
  while (hiding) {
    const shown = shownColumns()
    const holds = (condition: SingleCondition): boolean => {
      if (!fits(condition)) return false
      // A condition that fits names a table, and the columns of it it tests, the schema has.
      const table = tableOf(spec.schema, condition.table) as SchemaTable
      if (isListCondition(condition)) return listHolds(table, condition, shown)
      if (!shown.has(columnKey(condition.table, condition.column))) return false
      const column = columnOf(spec.schema, condition.table, condition.column) as SchemaColumn
      return passes(column.type, condition, rows[condition.table]?.[0]?.[condition.column])
    }
    const nextPages = new Set(spec.pages.filter((page) => pages.has(page.id) && showWhenPasses(page.show_when, holds)).map((page) => page.id))
    const nextSections = new Set(
      spec.pages.flatMap((page) =>
        nextPages.has(page.id)
          ? page.sections.filter((section) => sections.has(section.id) && showWhenPasses(section.show_when, holds)).map((section) => section.id)
          : [],
      ),
    )
    const nextWidgets = new Set(
      placed
        .filter(({ section, widget }) => nextSections.has(section.id) && widgets.has(widget.id) && showWhenPasses(widget.show_when, holds))
        .map(({ widget }) => widget.id),
    )
    const nextFields = new Set(
      entered.filter(({ widgetId, key, showWhen }) => nextWidgets.has(widgetId) && fields.has(key) && showWhenPasses(showWhen, holds)).map(({ key }) => key),
    )
    hiding = nextPages.size < pages.size || nextSections.size < sections.size || nextWidgets.size < widgets.size || nextFields.size < fields.size
    pages = nextPages
    sections = nextSections
    widgets = nextWidgets
    fields = nextFields
  }

  const shown = shownColumns()
  const onWidgets = new Set(entered.map(({ column }) => column))
  const hidden = new Set<string>()
  for (const table of spec.schema.tables) {
    if (table.role !== "input") continue
    for (const column of table.columns) {
      const key = columnKey(table.id, column.id)
      if (!column.index && onWidgets.has(key) && !shown.has(key)) hidden.add(key)
    }
  }
  return { pages, sections, widgets, fields, hidden }
}

const decided = new WeakMap<FormSpec, WeakMap<RowsByTable, SheetVisibility>>()

/**
 * Which sheets, components and fields show for the values typed into the sheets, and so
 * which input columns a quote leaves out: everything starts shown, then each round hides a
 * sheet whose conditions fail, a component whose sheet is hidden or whose conditions fail
 * and a field whose component is hidden or whose conditions fail, a condition holding only
 * while its column is a field still shown, until a round hides nothing. Memoised per form
 * and values, so the same pair gives the same answer.
 */
export function sheetVisibility(spec: FormSpec, rows: RowsByTable): SheetVisibility {
  let byRows = decided.get(spec)
  if (byRows === undefined) {
    byRows = new WeakMap()
    decided.set(spec, byRows)
  }
  let visibility = byRows.get(rows)
  if (visibility === undefined) {
    visibility = decide(spec, rows)
    byRows.set(rows, visibility)
  }
  return visibility
}

/** The schema without the hidden columns, the quote's tables as the server reads them. */
export function quotedSchema(schema: FormSchema, hidden: ReadonlySet<string>): FormSchema {
  if (hidden.size === 0) return schema
  return {
    ...schema,
    tables: schema.tables.map((table) => {
      const columns = table.columns.filter((column) => !hidden.has(columnKey(table.id, column.id)))
      return columns.length === table.columns.length ? table : { ...table, columns }
    }),
  }
}

const NO_COLUMNS: ReadonlySet<string> = new Set()

/**
 * Whether a quote has anything typed to price or save, as the server counts it: a value in
 * a column the sheets show, other than a column the app sets (`locked`, by `columnKey`),
 * the index, the column giving a table its rows (`withPresetRows`) and the default a new
 * quote starts a column with (`quoteTyped`). Preview, the app's pricing and its Save, and
 * the test quotes all ask this one rule, which the server's is held to by a shared fixture.
 */
export function quoteEligible(form: FormSpec, values: RowsByTable, locked: ReadonlySet<string> = NO_COLUMNS): boolean {
  const { hidden } = sheetVisibility(form, values)
  return quoteTyped(quotedSchema(quotedSchema(form.schema, hidden), locked), withPresetRows(form.schema, values))
}

/**
 * The sheets, sections, components and fields (by `fieldKey`) that nothing could show again
 * once hidden, each with the first condition it waits on that nothing could make hold.
 * Which could show again is a least fixed point: a sheet, section, component or field can
 * when its conditions pass counting only those that fit and whose column is a field that can
 * show again other than itself, of a component other than itself (and, for a sheet or a
 * section, not one of its own components); a section needs its sheet too, a component its
 * section and a field its component. One in a sheet, section or component that can never
 * show is left to it, and one with a condition that does not fit is left out, as that fault
 * is reported already.
 */
export function neverShown(spec: FormSpec): ReadonlyMap<string, SingleCondition> {
  const canShow = new Set<string>()
  const fits = (condition: SingleCondition): boolean => conditionFault(spec, condition) === null
  const widgets = spec.pages.flatMap((page) => page.sections.flatMap((section) => section.widgets))
  // A condition needs as a field that can show again each column it tests, or for a count without tests any of its table's but the index.
  const reachable = (condition: SingleCondition, holder: string, own: ReadonlySet<string>): boolean => {
    if (!fits(condition)) return false
    // A field can show again only while its component can, so it stands for both.
    const onShown = (key: string): boolean =>
      widgets.some((widget) => widget.id !== holder && !own.has(widget.id) && fieldKeys(widget).includes(key) && canShow.has(keyOfField(widget.id, key)))
    if (!isListCondition(condition)) return onShown(columnKey(condition.table, condition.column))
    const tested = listColumns(condition)
    if (tested.length > 0) return tested.every((id) => onShown(columnKey(condition.table, id)))
    // A count that fits counts a table the schema has.
    return countedKeys(tableOf(spec.schema, condition.table) as SchemaTable).some(onShown)
  }
  // A sheet's own components are those in all its sections; a section's, those in it.
  const owned = new Map<string, ReadonlySet<string>>()
  for (const page of spec.pages) {
    owned.set(page.id, new Set(page.sections.flatMap((section) => section.widgets.map((widget) => widget.id))))
    for (const section of page.sections) owned.set(section.id, new Set(section.widgets.map((widget) => widget.id)))
  }
  const none: ReadonlySet<string> = new Set()
  const own = (id: string): ReadonlySet<string> => owned.get(id) ?? none
  let grew = true
  const shownAgain = (id: string, showWhen: ShowWhen, mine: ReadonlySet<string>): void => {
    if (!canShow.has(id) && showWhenPasses(showWhen, (condition) => reachable(condition, id, mine))) {
      canShow.add(id)
      grew = true
    }
  }

  while (grew) {
    grew = false
    for (const page of spec.pages) {
      shownAgain(page.id, page.show_when, own(page.id))
      if (!canShow.has(page.id)) continue
      for (const section of page.sections) {
        shownAgain(section.id, section.show_when, own(section.id))
        if (!canShow.has(section.id)) continue
        for (const widget of section.widgets) {
          shownAgain(widget.id, widget.show_when, none)
          if (!canShow.has(widget.id)) continue
          for (const field of enteredFields(widget)) shownAgain(field.key, field.showWhen, none)
        }
      }
    }
  }

  const causes = new Map<string, SingleCondition>()
  const report = (id: string, showWhen: ShowWhen, mine: ReadonlySet<string>): void => {
    const conditions = leafConditions(showWhen)
    if (conditions.some((condition) => !fits(condition))) return
    const waiting = conditions.find((condition) => !reachable(condition, id, mine))
    if (waiting !== undefined) causes.set(id, waiting)
  }
  for (const page of spec.pages) {
    if (!canShow.has(page.id)) {
      report(page.id, page.show_when, own(page.id))
      continue
    }
    for (const section of page.sections) {
      if (!canShow.has(section.id)) {
        report(section.id, section.show_when, own(section.id))
        continue
      }
      for (const widget of section.widgets) {
        if (!canShow.has(widget.id)) {
          report(widget.id, widget.show_when, none)
          continue
        }
        for (const field of enteredFields(widget)) {
          if (!canShow.has(field.key)) report(field.key, field.showWhen, none)
        }
      }
    }
  }
  return causes
}

/** The columns of an input table a condition can test: a many-row table's but its index, which nothing is typed into. */
export const testableColumns = (table: SchemaTable): SchemaColumn[] =>
  table.role === "input" ? table.columns.filter((column) => !(table.rows === "many" && column.index)) : []

/** The columns of a many-row table a total takes: its number columns but the index. */
export const totalColumns = (table: SchemaTable): SchemaColumn[] =>
  testableColumns(table).filter((column) => column.type === "int" || column.type === "float")

/** A new test of a row for the editor: the table's first column but the index, its type's first test and no value; null without one. */
export function newRowTest(table: SchemaTable): RowTest | null {
  const column = testableColumns(table)[0]
  return column === undefined ? null : { column: column.id, op: testsFor(column.type)[0], values: [] }
}

/**
 * A new condition for the editor: the first column of the first one-row input table with
 * a column, else any row of the first many-row input table with one, a test of its first
 * column but the index; its type's first test and no value; or null when the schema has
 * neither.
 */
export function newCondition(spec: FormSpec): SingleCondition | null {
  const one = spec.schema.tables.find((table) => table.rows === "one" && testableColumns(table).length > 0)
  if (one !== undefined) {
    const column = testableColumns(one)[0]
    return { table: one.id, column: column.id, op: testsFor(column.type)[0], values: [] }
  }
  for (const table of spec.schema.tables) {
    const test = table.rows === "many" ? newRowTest(table) : null
    if (test !== null) return { table: table.id, rows: "any", where: [test] }
  }
  return null
}

/**
 * A test kept for a column: its test while the column's type takes it, else that type's
 * first; its values while their number fits the test and each fits the column, else none.
 */
function keptFor(column: SchemaColumn, kept: { op: ConditionTest; values: string[] } | undefined): { op: ConditionTest; values: string[] } {
  const tests = testsFor(column.type)
  const op = kept !== undefined && tests.includes(kept.op) ? kept.op : tests[0]
  const previous = kept?.values ?? []
  const values = countFits(op, previous.length) && previous.every((value) => valueFits(column, value)) ? previous : []
  return { op, values }
}

/**
 * A condition moved to a one-row table's column, keeping its test as `keptFor` keeps one:
 * a one-row table's condition its own, one on a list's rows its test of a row on that
 * column (its table switched to one row since), else its first.
 */
export function withConditionColumn(spec: FormSpec, condition: SingleCondition, tableId: string, columnId: string): Condition {
  const column = columnOf(spec.schema, tableId, columnId)
  if (column === undefined) throw new Error(`Column "${columnId}" of table "${tableId}" is not in the schema`)
  const kept = isListCondition(condition) ? (condition.where.find((test) => test.column === columnId) ?? condition.where[0]) : condition
  return { table: tableId, column: columnId, ...keptFor(column, kept) }
}

/**
 * A condition moved to a many-row table's rows: one on them kept; a one-row table's
 * condition on a column of the table (its table switched to many rows since) a test of a
 * row in any row, as it has it; else any row with the table's first test of a row.
 */
export function withConditionRowsOf(spec: FormSpec, condition: SingleCondition, tableId: string): ListCondition {
  if (condition.table === tableId) {
    if (isListCondition(condition)) return condition
    return { table: tableId, rows: "any", where: [{ column: condition.column, op: condition.op, values: condition.values }] }
  }
  const table = tableOf(spec.schema, tableId)
  const test = table === undefined ? null : newRowTest(table)
  if (test === null) throw new Error(`Table "${tableId}" has no column a condition can test`)
  return { table: tableId, rows: "any", where: [test] }
}

/**
 * A condition on a list's rows given which rows: any or every keeping its tests, with the
 * table's first while it has none; a count at least 1; a total of the table's first number
 * column, greater than no value yet, or a total kept with its kind changed; each keeping
 * the tests it has.
 */
export function withListKind(spec: FormSpec, condition: ListCondition, kind: RowsCondition["rows"] | "count" | TotalKind): ListCondition {
  const { table: tableId, where } = condition
  const table = tableOf(spec.schema, tableId)
  if (kind === "any" || kind === "every") {
    const test = where.length > 0 || table === undefined ? null : newRowTest(table)
    if (where.length === 0 && test === null) throw new Error(`Table "${tableId}" has no column a condition can test`)
    return { table: tableId, rows: kind, where: test === null ? where : [test] }
  }
  if (kind === "count") return isRowCount(condition) ? condition : { table: tableId, where, op: "at_least", count: 1 }
  if (isRowTotal(condition)) return { ...condition, total: kind }
  const column = table === undefined ? undefined : totalColumns(table)[0]
  if (column === undefined) throw new Error(`Table "${tableId}" has no number column to total`)
  return { table: tableId, total: kind, column: column.id, where, op: "above", value: "" }
}

/** A test of a row moved to another column of its table, keeping its test as `keptFor` keeps one. */
export function withTestColumn(spec: FormSpec, tableId: string, test: RowTest, columnId: string): RowTest {
  const column = columnOf(spec.schema, tableId, columnId)
  if (column === undefined) throw new Error(`Column "${columnId}" of table "${tableId}" is not in the schema`)
  return { column: columnId, ...keptFor(column, test) }
}

/**
 * A test of a column given another test: no value for a test without values, the first
 * for a test of one and all for a test of one or more, each kept only while it fits the
 * column. One whose column is gone (`column` undefined) keeps its values by number alone.
 */
export function withConditionTest<C extends ColumnTest>(column: SchemaColumn | undefined, condition: C, test: ConditionTest): C {
  const fits = (value: string): boolean => column === undefined || valueFits(column, value)
  const takes = CONDITION_TESTS[test].values
  const [first] = condition.values
  const values =
    takes === "none" ? [] : takes === "one" ? (first !== undefined && fits(first) ? [first] : []) : condition.values.filter(fits)
  return { ...condition, op: test, values }
}
