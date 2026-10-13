/**
 * The values typed into a sheet's cells (specs/workbench), the sample in Preview or the
 * underwriter app's quote: which rows count as filled, as the server counts
 * them; what breaks a column's rules; how a number column shows a value; what pricing the
 * values depends on; and the rows of a priced output table lined up with a grid's input
 * rows by key.
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
 * Whether anything is typed in the input tables the schema has now, as the server counts
 * it: a value in a column or a table since removed from the schema, or in a row past the
 * first of a table now with one row per quote, is left out of the quote the server types,
 * so it does not count.
 */
export const quoteFilled = (schema: FormSchema, rows: RowsByTable): boolean =>
  schema.tables.some((table) => table.role === "input" && quotedRows(table, rows).some((row) => rowFilled(table, row)))

/** An input table's rows as the server reads them: a one-row table's first alone. */
const quotedRows = (table: SchemaTable, rows: RowsByTable): readonly SampleRow[] => {
  const held = rows[table.id] ?? []
  return table.rows === "one" ? held.slice(0, 1) : held
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
 * Whether anything is typed beyond what a new quote starts with: as `quoteFilled` counts
 * it, a cell holding its column's default aside, so a new quote waits for the
 * underwriter's typing as a blank one does.
 */
export const quoteTyped = (schema: FormSchema, rows: RowsByTable): boolean =>
  schema.tables.some(
    (table) =>
      table.role === "input" &&
      quotedRows(table, rows).some((row) =>
        table.columns.some((column) => !column.index && filled(row[column.id]) && row[column.id] !== startingValue(table, column)),
      ),
  )

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

/**
 * What breaks an input column's rules in a typed value, or null: required and empty; a
 * number column holding text that is not a number, or a whole-number column a fraction
 * (the server would keep either as typed, for the pipeline to refuse); a number outside
 * the column's range; a value that is not one of the allowed values. A tick box and the
 * index break nothing.
 */
export function cellProblem(column: SchemaColumn, value: SampleValue | undefined): string | null {
  if (column.type === "bool" || column.index) return null
  const text = typeof value === "string" ? value.trim() : ""
  if (text === "") return column.required ? "Required" : null
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
