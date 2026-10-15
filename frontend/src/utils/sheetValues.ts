/**
 * The values typed into a sheet's cells (specs/workbench), the sample in Preview or the
 * underwriter app's quote: which rows count as filled, as the server counts them; the rows
 * a column gives a table, as the server reads them; what breaks a column's rules; how a
 * number column shows a value; what pricing the values depends on; and the rows of a
 * priced output table lined up with a grid's input rows by key.
 */
import type { FormSchema, SchemaColumn, SchemaTable } from "../api/types"
import { formatPivotNumber } from "../panels/explore/pivotNumberFormat"
import type { SampleRow, SampleValue } from "./workbenchForm"

/** Rows by table id, as the sample and a quote hold them. */
export type RowsByTable = Readonly<Record<string, readonly SampleRow[]>>

/** A cell's key in a map of problems: its table, row and column. */
export const cellKey = (tableId: string, row: number, columnId: string): string => `${tableId}:${row}:${columnId}`

/** Whether a value was typed, as the server counts it: text other than blank, or a tick. */
export const filled = (value: SampleValue | undefined): boolean =>
  value === true || (typeof value === "string" && value.trim() !== "")

/**
 * Whether a row has a value typed in any column but the index, as the server counts a
 * row: it leaves the others out of what it types, so a grid's filled rows are its typed
 * rows, in order.
 */
export const rowFilled = (table: SchemaTable, row: SampleRow | undefined): boolean =>
  row !== undefined && table.columns.some((column) => !column.index && filled(row[column.id]))

/**
 * The column giving a table a row for each of its allowed values: in a many-row input
 * table, the first marked `row_per_value` that is not the index and has values; null for
 * none (`preset_column` on the server).
 */
export const presetColumn = (table: SchemaTable): SchemaColumn | null =>
  table.role === "input" && table.rows === "many"
    ? (table.columns.find((column) => column.row_per_value && !column.index && column.options.length > 0) ?? null)
    : null

/**
 * `rows` as `table` holds them: with a column giving it a row for each value, those rows in
 * the values' order, each value's row the first holding it there, the rows holding nothing
 * there filling the rows left in order, each holding its value, and any other row left
 * out; else `rows` as they are. The server reads a table's rows by the same rule
 * (`preset_rows` in `src/haute/_workbench_tables.py`), so a grid's rows are the rows priced.
 */
export function presetRows(table: SchemaTable, rows: readonly SampleRow[]): readonly SampleRow[] {
  const column = presetColumn(table)
  if (column === null) return rows
  const places = new Map<string, number>()
  column.options.forEach((value, place) => {
    if (!places.has(value)) places.set(value, place)
  })
  const placed: (SampleRow | undefined)[] = column.options.map(() => undefined)
  const loose: SampleRow[] = []
  for (const row of rows) {
    const value = row[column.id]
    if (!filled(value)) {
      loose.push(row)
      continue
    }
    const place = typeof value === "string" ? places.get(value.trim()) : undefined
    if (place !== undefined && placed[place] === undefined) placed[place] = row
  }
  let next = 0
  return column.options.map((value, place) => ({ ...(placed[place] ?? loose[next++] ?? {}), [column.id]: value }))
}

/** The values with each table a column gives a row for each value read as `presetRows` reads it; the values themselves while none has one. */
export function withPresetRows(schema: FormSchema, values: RowsByTable): RowsByTable {
  const tables = schema.tables.filter((table) => presetColumn(table) !== null)
  if (tables.length === 0) return values
  const read: Record<string, readonly SampleRow[]> = { ...values }
  for (const table of tables) read[table.id] = presetRows(table, values[table.id] ?? [])
  return read
}

/** A table's columns whose values are typing: all but the index and the column giving the table its rows. */
const typingColumns = (table: SchemaTable): SchemaColumn[] => {
  const given = presetColumn(table)
  return table.columns.filter((column) => !column.index && column !== given)
}

/**
 * An input table's rows as the server reads them: a one-row table's first alone, and a
 * table a column gives its rows those rows (`presetRows`). A table whose column giving its
 * rows the sheets hide has lost it from a quoted schema, so a caller leaving hidden columns
 * out gives values already read through `withPresetRows` of the whole schema.
 */
const quotedRows = (table: SchemaTable, rows: RowsByTable): readonly SampleRow[] => {
  const held = rows[table.id] ?? []
  return table.rows === "one" ? held.slice(0, 1) : presetRows(table, held)
}

/**
 * What a new quote starts a column with: its default, in a one-row input table, as if
 * typed; null for none, and for any other table's column, which takes none.
 */
export const startingValue = (table: SchemaTable, column: SchemaColumn): SampleValue | null =>
  table.role === "input" && table.rows === "one" && !column.index && filled(column.default_value) ? column.default_value : null

const startingValues = new WeakMap<FormSchema, RowsByTable>()

/**
 * What a new quote starts with: each one-row input table's row holding its columns'
 * defaults, a table with none left out; the same for the same schema, so what reads it
 * once reads it again unchanged.
 */
export function newQuoteValues(schema: FormSchema): RowsByTable {
  let values = startingValues.get(schema)
  if (values === undefined) {
    const started: Record<string, SampleRow[]> = {}
    for (const table of schema.tables) {
      const row: SampleRow = {}
      for (const column of table.columns) {
        const value = startingValue(table, column)
        if (value !== null) row[column.id] = value
      }
      if (Object.keys(row).length > 0) started[table.id] = [row]
    }
    values = started
    startingValues.set(schema, values)
  }
  return values
}

/**
 * Whether anything is typed in the input tables the schema has now, as the server counts
 * it: a value in a column or a table since removed from the schema, or in a row past the
 * first of a table now with one row per quote, is left out of the quote the server types,
 * so it does not count; nor does a row's value in the column giving its table's rows, nor a
 * cell holding the default a new quote starts its column with, so a new quote waits for the
 * underwriter's typing as a blank one does. `quoteEligible` asks it of the columns the
 * sheets show, the app's own aside.
 */
export const quoteTyped = (schema: FormSchema, rows: RowsByTable): boolean =>
  schema.tables.some((table) => {
    if (table.role !== "input") return false
    const columns = typingColumns(table)
    return quotedRows(table, rows).some((row) =>
      columns.some((column) => filled(row[column.id]) && row[column.id] !== startingValue(table, column)),
    )
  })

/** A decimal number as the server reads one, once a currency sign, separators and spaces are gone. */
const NUMBER = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/

/**
 * A typed number as the server types it, or null for text that is not a number, one too
 * large to be finite included, as the server keeps such text as typed.
 */
export function parseTypedNumber(text: string): number | null {
  const cleaned = text.replace(/[$£€,\s]/g, "")
  if (!NUMBER.test(cleaned)) return null
  const number = Number(cleaned)
  return Number.isFinite(number) ? number : null
}

/**
 * A value as its number column's format shows it, as an Explore pivot shows a value (a
 * percent the value times 100), typed text read as the server reads it; or null when the
 * column shows numbers as they are, is not a number column, or the value is not a number.
 */
export function formattedNumber(column: SchemaColumn, value: unknown): string | null {
  if (column.type !== "int" && column.type !== "float") return null
  if (column.number_format === "general" && column.decimal_places === null) return null
  return formatPivotNumber(typeof value === "string" ? parseTypedNumber(value) : value, column)
}

const DAY = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/

/**
 * Whether stripped text names a real calendar day as YYYY-MM-DD, as the server's dates
 * do: year 1 or later. JavaScript's Date is no use here, as it reads years before 100 as
 * the 1900s and takes the year 0.
 */
export function isDay(text: string): boolean {
  if (!DAY.test(text)) return false
  const [year, month, day] = text.split("-").map(Number)
  if (year < 1 || month < 1 || month > 12 || day < 1) return false
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
  return day <= days
}

/**
 * What breaks an input column's rules in a typed value, or null: required and empty; a
 * number column holding text that is not a number, or a whole-number column a fraction
 * (the server would keep either as typed, for the pipeline to refuse); a Date column
 * holding text that is not a day, as a column retyped to a Date may; a number outside
 * the column's range; a value that is not one of the allowed values. A tick box and the
 * index break nothing.
 */
export function cellProblem(column: SchemaColumn, value: SampleValue | undefined): string | null {
  if (column.index) return null
  // A value of another type, left in a column since retyped: the server sends it as it is,
  // and the Workbench Input refuses it, so it is marked where the sheet cannot show it.
  if (column.type === "bool") return typeof value === "string" && value.trim() !== "" ? "Not a tick" : null
  if (value === true) return "A tick, not a value"
  const text = typeof value === "string" ? value.trim() : ""
  if (text === "") return column.required ? "Required" : null
  if (column.type === "date" && !isDay(text)) return "Not a date"
  if (column.type === "int" || column.type === "float") {
    const number = parseTypedNumber(text)
    if (number === null) return "Not a number"
    if (column.type === "int" && !Number.isInteger(number)) return "Not a whole number"
    if (column.min !== null && number < column.min) return `At least ${column.min.toLocaleString()}`
    if (column.max !== null && number > column.max) return `At most ${column.max.toLocaleString()}`
  }
  if (column.options.length > 0 && !column.options.includes(text)) return "Not one of the allowed values"
  return null
}

/**
 * What breaks the columns' rules in the values typed, by cell: each input table's columns
 * over its rows, a one-row table's one row always (a quote has one) and a many-row
 * table's filled rows only, as the server leaves its empty rows out. Output tables have
 * no rules, and a value typed for a column no longer in the schema is nobody's.
 */
export function quoteProblems(schema: FormSchema, rows: RowsByTable): Record<string, string> {
  const problems: Record<string, string> = {}
  for (const table of schema.tables) {
    if (table.role !== "input") continue
    const typed = rows[table.id] ?? []
    const checked = table.rows === "one" ? [typed[0] ?? {}] : typed
    checked.forEach((row, index) => {
      if (table.rows === "many" && !rowFilled(table, row)) return
      for (const column of table.columns) {
        const problem = cellProblem(column, row[column.id])
        if (problem !== null) problems[cellKey(table.id, index, column.id)] = problem
      }
    })
  }
  return problems
}

/**
 * What pricing the values depends on, as one string to compare: the whole schema (the
 * server declares a column the sheets hide too, its value left out), which columns the
 * sheets hide, and the values. A sheet laid out differently prices the same; a column
 * renamed or retyped, hidden or shown, or a value typed does not, and a price for another
 * basis is out of date.
 */
export const valuesBasis = (schema: FormSchema, hidden: ReadonlySet<string>, rows: RowsByTable): string =>
  JSON.stringify([schema, [...hidden].sort(), rows])

/** A priced table's rows, as the Workbench Output's preview gives them. */
export type PricedRows = readonly Record<string, unknown>[]

const keyed = (row: Record<string, unknown>, name: string): boolean => row[name] !== undefined && row[name] !== null

/**
 * The rows of a priced output table lined up with a grid's rows of an input table keyed
 * alike: for each grid row, the output row whose key columns hold what the server typed
 * for the grid row's (an index column numbered as the grid numbers it); undefined for a
 * grid row with nothing typed, or none keyed like it. `typedRows` are the input table's
 * rows as the server typed them, the filled rows in order.
 */
export function pricedRows(
  input: SchemaTable,
  gridRows: readonly (SampleRow | undefined)[],
  typedRows: PricedRows,
  outputRows: PricedRows,
): (Record<string, unknown> | undefined)[] {
  const keys = input.columns.filter((column) => column.key).map((column) => column.name)
  let next = 0
  return gridRows.map((row) => {
    if (!rowFilled(input, row)) return undefined
    const typed = typedRows[next]
    next += 1
    if (typed === undefined || keys.length === 0) return undefined
    return outputRows.find((candidate) =>
      keys.every((name) => keyed(typed, name) && keyed(candidate, name) && String(candidate[name]) === String(typed[name])),
    )
  })
}
