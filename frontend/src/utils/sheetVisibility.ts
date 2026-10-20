/**
 * The conditions on the sheets, sections and components (specs/workbench): the tests each
 * column's type takes and their words, what stops a condition holding as written, and which
 * sheets, sections and components show for values typed into the sheets, none while
 * building, the sample or the underwriter app's quote in Preview, and so which input
 * columns a quote leaves out. The server applies the same rules in src/haute/_workbench_visibility.py, and both
 * are held to the cases in __tests__/fixtures/sheetVisibility.json.
 */
import type { Condition, ConditionGroup, FormSchema, FormSpec, RowCount, RowsCondition, SchemaColumn, SchemaTable, Section, ShowWhen } from "../api/types"
import { filled, isDay, parseTypedNumber, presetRows, quoteTyped, rowFilled, withPresetRows, type RowsByTable } from "./sheetValues"

export type ConditionTest = Condition["op"]
export type CountTest = RowCount["op"]
/** One condition: on a one-row table's column, on a many-row table's, or a count of a many-row table's rows. */
export type SingleCondition = ConditionGroup["conditions"][number]
/** An entry of a `show_when`: one condition or a group of them. */
export type ShowWhenEntry = ShowWhen["conditions"][number]
/** A condition on a column: a one-row table's, or a many-row table's in any or every row. */
export type ColumnCondition = Condition | RowsCondition
type ColumnType = SchemaColumn["type"]
type Widget = Section["widgets"][number]
type Row = RowsByTable[string][number]
type Cell = Row[string]

/** A column's key in a set of columns: its table id and column id. */
export const columnKey = (tableId: string, columnId: string): string => `${tableId}:${columnId}`

/** Whether an entry of a `show_when` is a group, told by its fields as the server tells it. */
export const isGroup = (entry: ShowWhenEntry): entry is ConditionGroup => "conditions" in entry

/** Whether a condition counts a many-row table's rows. */
export const isRowCount = (condition: ShowWhenEntry): condition is RowCount => "count" in condition

/** Whether a condition tests a many-row table's column, in any row or every one. */
export const isRowsCondition = (condition: ShowWhenEntry): condition is RowsCondition => "rows" in condition

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

/** The tests a count of rows takes, in the order the editor offers them: a number's, read as an Integer's. */
export const COUNT_TESTS: readonly CountTest[] = ["equals", "not_equals", "above", "at_least", "below", "at_most"]

const COUNTS: Readonly<Record<CountTest, (rows: number, count: number) => boolean>> = {
  equals: (rows, count) => rows === count,
  not_equals: (rows, count) => rows !== count,
  above: (rows, count) => rows > count,
  at_least: (rows, count) => rows >= count,
  below: (rows, count) => rows < count,
  at_most: (rows, count) => rows <= count,
}

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
 * sentence that starts with its sheet's or component's name. A count is checked by its
 * table alone; else the column is checked first, then its table's rows, the test, the
 * number of values and each value, and the first fault is reported.
 */
export function conditionFault(spec: FormSpec, condition: SingleCondition): string | null {
  const table = tableOf(spec.schema, condition.table)
  if (isRowCount(condition)) {
    if (table === undefined) return "counts the rows of a table that is no longer in the schema"
    if (table.role === "output") return `counts the rows of ${table.name}, which is an output table`
    if (table.rows === "one") return `counts the rows of ${table.name}, which has one row`
    return null
  }
  const column = table?.columns.find((candidate) => candidate.id === condition.column)
  if (table === undefined || column === undefined) return "tests a column that is no longer in the schema"
  const name = column.name
  if (table.role === "output") return `tests ${name}, which is in an output table`
  if (isRowsCondition(condition)) {
    if (table.rows === "one") return `tests ${name} in ${condition.rows} row, which is in a table with one row`
  } else if (table.rows === "many") return `tests ${name}, which is in a table with many rows`
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
function passes(type: ColumnType, condition: ColumnCondition, cell: Cell | undefined): boolean {
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

/** Which sheets, sections and components show for the values, and the input columns a quote leaves out. */
export interface SheetVisibility {
  /** The ids of the sheets that show. */
  pages: ReadonlySet<string>
  /** The ids of the sections that show, each on a sheet that shows. */
  sections: ReadonlySet<string>
  /** The ids of the components that show, each in a section that shows. */
  widgets: ReadonlySet<string>
  /** The input columns left out of the quote, by `columnKey`. */
  hidden: ReadonlySet<string>
}

/**
 * The columns a component has values typed into: a Table's and a Collection's fields, none
 * of a Chart's, which it only plots, and none of a Heading, which has no fields.
 */
export const fieldKeys = (widget: Widget): string[] =>
  widget.type === "tableInput" || widget.type === "collection" ? widget.fields.map((field) => columnKey(field.table, field.column)) : []

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

/** The keys of a counted table's columns other than the index, any of which on a component that shows lets a count hold. */
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

  const placed = spec.pages.flatMap((page) => page.sections.flatMap((section) => section.widgets.map((widget) => ({ section, widget }))))
  let pages = new Set(spec.pages.map((page) => page.id))
  let sections = new Set(spec.pages.flatMap((page) => page.sections.map((section) => section.id)))
  let widgets = new Set(placed.map(({ widget }) => widget.id))
  // A component still shown is in a section and on a sheet still shown: each round hides a
  // sheet's sections and a section's components with it.
  const shownColumns = (): Set<string> => new Set(placed.filter(({ widget }) => widgets.has(widget.id)).flatMap(({ widget }) => fieldKeys(widget)))

  // Hiding only ever hides more, so a condition that fails stays failed and the rounds end.
  let hiding = true
  while (hiding) {
    const shown = shownColumns()
    const holds = (condition: SingleCondition): boolean => {
      if (!fits(condition)) return false
      // A condition that fits names a table, and a column of it, the schema has.
      const table = tableOf(spec.schema, condition.table) as SchemaTable
      if (isRowCount(condition)) {
        return countedKeys(table).some((key) => shown.has(key)) && COUNTS[condition.op](typedOf(table).length, condition.count)
      }
      if (!shown.has(columnKey(condition.table, condition.column))) return false
      const column = columnOf(spec.schema, condition.table, condition.column) as SchemaColumn
      if (!isRowsCondition(condition)) return passes(column.type, condition, rows[condition.table]?.[0]?.[condition.column])
      const cells = typedOf(table).map((row) => row[condition.column])
      const pass = (cell: Cell | undefined): boolean => passes(column.type, condition, cell)
      return condition.rows === "any" ? cells.some(pass) : cells.length > 0 && cells.every(pass)
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
    hiding = nextPages.size < pages.size || nextSections.size < sections.size || nextWidgets.size < widgets.size
    pages = nextPages
    sections = nextSections
    widgets = nextWidgets
  }

  const shown = shownColumns()
  const onWidgets = new Set(placed.flatMap(({ widget }) => fieldKeys(widget)))
  const hidden = new Set<string>()
  for (const table of spec.schema.tables) {
    if (table.role !== "input") continue
    for (const column of table.columns) {
      const key = columnKey(table.id, column.id)
      if (!column.index && onWidgets.has(key) && !shown.has(key)) hidden.add(key)
    }
  }
  return { pages, sections, widgets, hidden }
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
 * The sheets, sections and components that nothing could show again once hidden, each
 * with the first condition it waits on that nothing could make hold. Which could show
 * again is a least fixed point: a sheet, section or component can when its conditions pass
 * counting only those that fit and whose column is on a component that can show again
 * other than itself (and, for a sheet or a section, not one of its own components); a
 * section needs its sheet too, and a component its section. One in a sheet or section that
 * can never show is left to it, and one with a condition that does not fit is left out, as
 * that fault is reported already.
 */
export function neverShown(spec: FormSpec): ReadonlyMap<string, SingleCondition> {
  const canShow = new Set<string>()
  const fits = (condition: SingleCondition): boolean => conditionFault(spec, condition) === null
  const widgets = spec.pages.flatMap((page) => page.sections.flatMap((section) => section.widgets))
  // The columns a condition needs on a component that shows: its own, or any of a counted table's but the index.
  const needs = (condition: SingleCondition): string[] => {
    if (!isRowCount(condition)) return [columnKey(condition.table, condition.column)]
    const table = tableOf(spec.schema, condition.table)
    return table === undefined ? [] : countedKeys(table)
  }
  const reachable = (condition: SingleCondition, holder: string, own: ReadonlySet<string>): boolean => {
    if (!fits(condition)) return false
    const keys = needs(condition)
    return widgets.some(
      (widget) => canShow.has(widget.id) && widget.id !== holder && !own.has(widget.id) && fieldKeys(widget).some((key) => keys.includes(key)),
    )
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
        for (const widget of section.widgets) shownAgain(widget.id, widget.show_when, none)
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
        if (!canShow.has(widget.id)) report(widget.id, widget.show_when, none)
      }
    }
  }
  return causes
}

/** The columns of an input table a condition can test: a many-row table's but its index, which nothing is typed into. */
export const testableColumns = (table: SchemaTable): SchemaColumn[] =>
  table.role === "input" ? table.columns.filter((column) => !(table.rows === "many" && column.index)) : []

/**
 * A new condition for the editor: the first column of the first one-row input table with
 * a column, else the first column but the index of the first many-row input table with
 * one, in any row; its type's first test and no value; or null when the schema has none.
 */
export function newCondition(spec: FormSpec): ColumnCondition | null {
  for (const rows of ["one", "many"] as const) {
    const table = spec.schema.tables.find((candidate) => candidate.rows === rows && testableColumns(candidate).length > 0)
    if (table === undefined) continue
    const column = testableColumns(table)[0]
    const op = testsFor(column.type)[0]
    return rows === "one" ? { table: table.id, column: column.id, op, values: [] } : { table: table.id, column: column.id, rows: "any", op, values: [] }
  }
  return null
}

/** A new count for the editor: the table's rows at least 1. */
export const newRowCount = (tableId: string): RowCount => ({ table: tableId, op: "at_least", count: 1 })

/**
 * A condition moved to another column: a one-row table's condition for a one-row table's
 * column, and a many-row table's for a many-row table's, its rows kept, else any row; its
 * test kept while the new column's type takes it, else that type's first; its values
 * kept while their number fits the test and each fits the new column, else none. A count
 * keeps its test while the column's type takes it, with no value.
 */
export function withConditionColumn(spec: FormSpec, condition: SingleCondition, tableId: string, columnId: string): ColumnCondition {
  const table = tableOf(spec.schema, tableId)
  const column = table?.columns.find((candidate) => candidate.id === columnId)
  if (table === undefined || column === undefined) throw new Error(`Column "${columnId}" of table "${tableId}" is not in the schema`)
  const tests = testsFor(column.type)
  const op = tests.includes(condition.op) ? condition.op : tests[0]
  const previous = isRowCount(condition) ? [] : condition.values
  const values = countFits(op, previous.length) && previous.every((value) => valueFits(column, value)) ? previous : []
  if (table.rows === "one") return { table: tableId, column: columnId, op, values }
  return { table: tableId, column: columnId, rows: isRowsCondition(condition) ? condition.rows : "any", op, values }
}

/** A condition given which rows it tests: any or every row making it a many-row table's, the one row a one-row table's. */
export function withConditionRows(condition: ColumnCondition, rows: RowsCondition["rows"] | "one"): ColumnCondition {
  const { table, column, op, values } = condition
  return rows === "one" ? { table, column, op, values } : { table, column, rows, op, values }
}

/**
 * A condition given another test: no value for a test without values, the first for a
 * test of one and all for a test of one or more, each kept only while it fits the
 * column. A condition whose column is gone keeps its values by number alone.
 */
export function withConditionTest<C extends ColumnCondition>(spec: FormSpec, condition: C, test: ConditionTest): C {
  const column = columnOf(spec.schema, condition.table, condition.column)
  const fits = (value: string): boolean => column === undefined || valueFits(column, value)
  const takes = CONDITION_TESTS[test].values
  const [first] = condition.values
  const values =
    takes === "none" ? [] : takes === "one" ? (first !== undefined && fits(first) ? [first] : []) : condition.values.filter(fits)
  return { ...condition, op: test, values }
}
