/**
 * The conditions on the sheets and components (specs/workbench): the tests each column's
 * type takes and their words, what stops a condition holding as written, and which sheets
 * and components show for values typed into the sheets, the sample while building or an
 * underwriter's quote in Preview, and so which input columns a quote leaves out. The
 * server applies the same rules in src/haute/_workbench_visibility.py, and both are held
 * to the cases in __tests__/fixtures/sheetVisibility.json.
 */
import type { Condition, FormSchema, FormSpec, Page, SchemaColumn, SchemaTable, ShowWhen } from "../api/types"
import { filled, parseTypedNumber, type RowsByTable } from "./sheetValues"

export type ConditionTest = Condition["op"]
type ColumnType = SchemaColumn["type"]
type Widget = Page["widgets"][number]
type Cell = RowsByTable[string][number][string]

/** A column's key in a set of columns: its table id and column id. */
export const columnKey = (tableId: string, columnId: string): string => `${tableId}:${columnId}`

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

const DAY = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/

/**
 * Whether stripped text names a real calendar day as YYYY-MM-DD, as the server's dates
 * do: year 1 or later. JavaScript's Date is no use here, as it reads years before 100 as
 * the 1900s and takes the year 0.
 */
function isDay(text: string): boolean {
  if (!DAY.test(text)) return false
  const [year, month, day] = text.split("-").map(Number)
  if (year < 1 || month < 1 || month > 12 || day < 1) return false
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
  return day <= days
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
 * sentence that starts with its sheet's or component's name. The column is checked first,
 * then the test, the number of values and each value, and the first fault is reported.
 */
export function conditionFault(spec: FormSpec, condition: Condition): string | null {
  const table = tableOf(spec.schema, condition.table)
  const column = table?.columns.find((candidate) => candidate.id === condition.column)
  if (table === undefined || column === undefined) return "tests a column that is no longer in the schema"
  const name = column.name
  if (table.role === "output") return `tests ${name}, which is in an output table`
  if (table.rows === "many") return `tests ${name}, which is in a table with many rows`
  const label = testLabel(condition.op, column.type)
  if (!testsFor(column.type).includes(condition.op)) {
    return `tests ${name} with "${label}", which ${TYPE_WORDS[column.type]} column cannot take`
  }
  const takes = CONDITION_TESTS[condition.op].values
  const count = condition.values.length
  if (takes !== "none" && count === 0) return `needs a value for its test of ${name}`
  if (takes === "none" && count > 0) return `tests ${name} with "${label}", which takes no value`
  if (takes === "one" && count > 1) return `tests ${name} with "${label}", which takes one value`
  for (const value of condition.values) {
    if (value.trim() === "") return `has a blank value for ${name}`
    if (!valueFits(column, value)) return `tests ${name} against "${value}", which isn't ${NOT_A_VALUE[column.type]}`
  }
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
function passes(type: ColumnType, condition: Condition, cell: Cell | undefined): boolean {
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

/** Which sheets and components show for the values, and the input columns a quote leaves out. */
export interface SheetVisibility {
  /** The ids of the sheets that show. */
  pages: ReadonlySet<string>
  /** The ids of the components that show, each on a sheet that shows. */
  widgets: ReadonlySet<string>
  /** The input columns left out of the quote, by `columnKey`. */
  hidden: ReadonlySet<string>
}

/** The columns a component has values typed into: a Table's and a Collection's fields, and none of a Chart's, which it only plots. */
const fieldKeys = (widget: Widget): string[] =>
  widget.type === "chart" ? [] : widget.fields.map((field) => columnKey(field.table, field.column))

const showWhenPasses = (showWhen: ShowWhen, holds: (condition: Condition) => boolean): boolean => {
  if (showWhen.conditions.length === 0) return true
  return showWhen.match === "all" ? showWhen.conditions.every(holds) : showWhen.conditions.some(holds)
}

function decide(spec: FormSpec, rows: RowsByTable): SheetVisibility {
  // Each condition's column while it fits, else null: a condition that does not fit never holds.
  const fitted = new Map<Condition, SchemaColumn | null>()
  const fittedColumn = (condition: Condition): SchemaColumn | null => {
    let column = fitted.get(condition)
    if (column === undefined) {
      column = conditionFault(spec, condition) === null ? (columnOf(spec.schema, condition.table, condition.column) ?? null) : null
      fitted.set(condition, column)
    }
    return column
  }

  let pages = new Set(spec.pages.map((page) => page.id))
  let widgets = new Set(spec.pages.flatMap((page) => page.widgets.map((widget) => widget.id)))
  const shownColumns = (): Set<string> =>
    new Set(spec.pages.flatMap((page) => page.widgets.filter((widget) => widgets.has(widget.id)).flatMap(fieldKeys)))

  // Hiding only ever hides more, so a condition that fails stays failed and the rounds end.
  let hiding = true
  while (hiding) {
    const shown = shownColumns()
    const holds = (condition: Condition): boolean => {
      const column = fittedColumn(condition)
      if (column === null || !shown.has(columnKey(condition.table, condition.column))) return false
      return passes(column.type, condition, rows[condition.table]?.[0]?.[condition.column])
    }
    const nextPages = new Set(spec.pages.filter((page) => pages.has(page.id) && showWhenPasses(page.show_when, holds)).map((page) => page.id))
    const nextWidgets = new Set(
      spec.pages.flatMap((page) =>
        nextPages.has(page.id)
          ? page.widgets.filter((widget) => widgets.has(widget.id) && showWhenPasses(widget.show_when, holds)).map((widget) => widget.id)
          : [],
      ),
    )
    hiding = nextPages.size < pages.size || nextWidgets.size < widgets.size
    pages = nextPages
    widgets = nextWidgets
  }

  const shown = shownColumns()
  const onWidgets = new Set(spec.pages.flatMap((page) => page.widgets.flatMap(fieldKeys)))
  const hidden = new Set<string>()
  for (const table of spec.schema.tables) {
    if (table.role !== "input") continue
    for (const column of table.columns) {
      const key = columnKey(table.id, column.id)
      if (!column.index && onWidgets.has(key) && !shown.has(key)) hidden.add(key)
    }
  }
  return { pages, widgets, hidden }
}

const decided = new WeakMap<FormSpec, WeakMap<RowsByTable, SheetVisibility>>()

/**
 * Which sheets and components show for the values typed into the sheets, and so which
 * input columns a quote leaves out: everything starts shown, then each round hides a
 * sheet whose conditions fail and a component whose sheet is hidden or whose conditions
 * fail, a condition holding only while its column is on a component still shown, until a
 * round hides nothing. Memoised per form and values, so the same pair gives the same answer.
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

/**
 * The sheets and components that nothing could show again once hidden, each with the
 * first condition it waits on that nothing could make hold. Which could show again is a
 * least fixed point: a sheet or component can when its conditions pass counting only
 * those that fit and whose column is on a component that can show again other than
 * itself (and, for a sheet, not one of its own components); a component needs its sheet
 * too. A component on a sheet that can never show is left to its sheet, and one with a
 * condition that does not fit is left out, as that fault is reported already.
 */
export function neverShown(spec: FormSpec): ReadonlyMap<string, Condition> {
  const canShow = new Set<string>()
  const fits = (condition: Condition): boolean => conditionFault(spec, condition) === null
  const reachable = (condition: Condition, holder: string, own: ReadonlySet<string>): boolean => {
    if (!fits(condition)) return false
    const key = columnKey(condition.table, condition.column)
    return spec.pages.some((page) =>
      page.widgets.some((widget) => canShow.has(widget.id) && widget.id !== holder && !own.has(widget.id) && fieldKeys(widget).includes(key)),
    )
  }
  const ownWidgets = new Map(spec.pages.map((page) => [page.id, new Set(page.widgets.map((widget) => widget.id))]))
  const none: ReadonlySet<string> = new Set()
  const pageOwn = (page: Page): ReadonlySet<string> => ownWidgets.get(page.id) ?? none

  let grew = true
  while (grew) {
    grew = false
    for (const page of spec.pages) {
      if (!canShow.has(page.id) && showWhenPasses(page.show_when, (condition) => reachable(condition, page.id, pageOwn(page)))) {
        canShow.add(page.id)
        grew = true
      }
      if (!canShow.has(page.id)) continue
      for (const widget of page.widgets) {
        if (!canShow.has(widget.id) && showWhenPasses(widget.show_when, (condition) => reachable(condition, widget.id, none))) {
          canShow.add(widget.id)
          grew = true
        }
      }
    }
  }

  const causes = new Map<string, Condition>()
  const report = (id: string, showWhen: ShowWhen, own: ReadonlySet<string>): void => {
    if (showWhen.conditions.some((condition) => !fits(condition))) return
    const waiting = showWhen.conditions.find((condition) => !reachable(condition, id, own))
    if (waiting !== undefined) causes.set(id, waiting)
  }
  for (const page of spec.pages) {
    if (!canShow.has(page.id)) {
      report(page.id, page.show_when, pageOwn(page))
      continue
    }
    for (const widget of page.widgets) {
      if (!canShow.has(widget.id)) report(widget.id, widget.show_when, none)
    }
  }
  return causes
}

/**
 * A new condition for the editor: the first column of the first one-row input table with
 * a column, its type's first test and no value; or null when the schema has none.
 */
export function newCondition(spec: FormSpec): Condition | null {
  const table = spec.schema.tables.find((candidate) => candidate.role === "input" && candidate.rows === "one" && candidate.columns.length > 0)
  if (table === undefined) return null
  const column = table.columns[0]
  return { table: table.id, column: column.id, op: testsFor(column.type)[0], values: [] }
}

/**
 * A condition moved to another column: its test kept while the new column's type takes
 * it, else that type's first; its values kept while their number fits the test and each
 * fits the new column, else none.
 */
export function withConditionColumn(spec: FormSpec, condition: Condition, tableId: string, columnId: string): Condition {
  const column = columnOf(spec.schema, tableId, columnId)
  if (column === undefined) throw new Error(`Column "${columnId}" of table "${tableId}" is not in the schema`)
  const tests = testsFor(column.type)
  const op = tests.includes(condition.op) ? condition.op : tests[0]
  const keep = countFits(op, condition.values.length) && condition.values.every((value) => valueFits(column, value))
  return { ...condition, table: tableId, column: columnId, op, values: keep ? condition.values : [] }
}

/**
 * A condition given another test: no value for a test without values, the first for a
 * test of one and all for a test of one or more, each kept only while it fits the
 * column. A condition whose column is gone keeps its values by number alone.
 */
export function withConditionTest(spec: FormSpec, condition: Condition, test: ConditionTest): Condition {
  const column = columnOf(spec.schema, condition.table, condition.column)
  const fits = (value: string): boolean => column === undefined || valueFits(column, value)
  const takes = CONDITION_TESTS[test].values
  const [first] = condition.values
  const values =
    takes === "none" ? [] : takes === "one" ? (first !== undefined && fits(first) ? [first] : []) : condition.values.filter(fits)
  return { ...condition, op: test, values }
}
