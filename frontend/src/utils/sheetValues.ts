/**
 * The values typed into a sheet's cells (specs/workbench), the sample in Preview or the
 * underwriter app's quote: which rows count as filled, as the server counts them; the rows
 * an index of values gives a table, as the server reads them; what breaks a column's rules,
 * a typed index's among them; how a number column shows a value; what pricing the values
 * depends on; and the rows of a pricing's answer lined up with a grid's input rows by index.
 */
import type { FormSchema, SchemaColumn, SchemaTable } from "../api/types"
import { formatPivotNumber } from "../panels/explore/pivotNumberFormat"
import type { SampleRow, SampleValue } from "./workbenchForm"
import { isDay } from "./isoDate"
import { hasInputs, isOutputColumn, tableIndex } from "./quoteIdentity"

/** Rows by table id, as the sample and a quote hold them. */
export type RowsByTable = Readonly<Record<string, readonly SampleRow[]>>

/** Whether a column's value is read, keeping the original schema's table classification. */
type ReadsColumn = (table: SchemaTable, column: SchemaColumn) => boolean

/** A cell's key in a map of problems: its table, row and column. */
export const cellKey = (tableId: string, row: number, columnId: string): string => `${tableId}:${row}:${columnId}`

/** Whether a value was typed, as the server counts it: text other than blank, or a tick. */
export const filled = (value: SampleValue | undefined): boolean =>
  value === true || (typeof value === "string" && value.trim() !== "")

/**
 * Whether a row holds a value in any input but a numbered index, as the server sends a row
 * (`_record`): it leaves the others out, so a grid's filled rows are the rows priced, in
 * order, every row an index of values gives among them.
 */
export const rowFilled = (table: SchemaTable, row: SampleRow | undefined): boolean =>
  row !== undefined && table.inputs.some((column) => column.index !== "numbered" && filled(row[column.name]))

/**
 * Whether a value in `column` is the underwriter's typing: in any column but a numbered
 * index and an index of values, whose values the sheets give (`_is_typing`).
 */
export const isTyping = (column: SchemaColumn): boolean => column.index === null || column.index === "typed"

/** A table's index of values, giving an input table a row for each of its allowed values; null for none (`_values_index`). */
export const valuesIndex = (table: SchemaTable): SchemaColumn | null => {
  const index = tableIndex(table)
  return index !== null && index.index === "values" && hasInputs(table) ? index : null
}

/**
 * `rows` as `table` holds them: with an index of values, a row for each of its values, in
 * their order, each value's row the first holding it in the index's type, the rows holding nothing there
 * filling the rows left in order, each holding its value, and any other row left out; else
 * `rows` as they are. The server reads a table's rows by the same rule (`preset_rows` in
 * `src/haute/_workbench_tables.py`), so a grid's rows are the rows priced.
 */
export function presetRows(table: SchemaTable, rows: readonly SampleRow[]): readonly SampleRow[] {
  const column = valuesIndex(table)
  if (column === null) return rows
  const identity = (value: string): string => indexIdentity(typedIndexValue(column.type, value.trim()))
  const places = new Map<string, number>()
  column.options.forEach((value, place) => {
    const key = identity(value)
    if (!places.has(key)) places.set(key, place)
  })
  const placed: (SampleRow | undefined)[] = column.options.map(() => undefined)
  const loose: SampleRow[] = []
  for (const row of rows) {
    const value = row[column.name]
    if (!filled(value)) {
      loose.push(row)
      continue
    }
    const place = typeof value === "string" ? places.get(identity(value)) : undefined
    if (place !== undefined && placed[place] === undefined) placed[place] = row
  }
  let next = 0
  return column.options.map((value, place) => ({ ...(placed[place] ?? loose[next++] ?? {}), [column.name]: value }))
}

/** The values with each table an index of values gives its rows read as `presetRows` reads it; the values themselves while none has one. */
export function withPresetRows(schema: FormSchema, values: RowsByTable): RowsByTable {
  const tables = schema.tables.filter((table) => valuesIndex(table) !== null)
  if (tables.length === 0) return values
  const read: Record<string, readonly SampleRow[]> = { ...values }
  for (const table of tables) read[table.name] = presetRows(table, values[table.name] ?? [])
  return read
}

/** A table's columns whose values are typing: its inputs but a numbered index and an index of values (`isTyping`). */
const typingColumns = (table: SchemaTable): SchemaColumn[] => table.inputs.filter(isTyping)

/**
 * An input table's rows as the server reads them: a one-row table's first alone, and a
 * table an index of values gives its rows those rows (`presetRows`). A table whose index the
 * sheets hide keeps it in a quoted schema (`quotedSchema`), so its rows read alike.
 */
const quotedRows = (table: SchemaTable, rows: RowsByTable): readonly SampleRow[] => {
  const held = rows[table.name] ?? []
  return table.rows === "one" ? held.slice(0, 1) : presetRows(table, held)
}

/**
 * What a new quote starts a column with: its default, for an input of a one-row table, as
 * if typed; null for none, and for any other column, which takes none (`starting_value`).
 */
export const startingValue = (table: SchemaTable, column: SchemaColumn): SampleValue | null =>
  !isOutputColumn(table, column) && table.rows === "one" && !column.index && filled(column.default_value) ? column.default_value : null

const startingValues = new WeakMap<FormSchema, RowsByTable>()

/**
 * What a new quote starts with: each one-row table's row holding its inputs' defaults, a
 * table with none left out; the same for the same schema, so what reads it once reads it
 * again unchanged.
 */
export function newQuoteValues(schema: FormSchema): RowsByTable {
  let values = startingValues.get(schema)
  if (values === undefined) {
    const started: Record<string, SampleRow[]> = {}
    for (const table of schema.tables) {
      const row: SampleRow = {}
      for (const column of table.inputs) {
        const value = startingValue(table, column)
        if (value !== null) row[column.name] = value
      }
      if (Object.keys(row).length > 0) started[table.name] = [row]
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
 * so it does not count; nor does a numbered index's value or an index of values', which the
 * sheets give, nor a cell holding the default a new quote starts its column with, so a new
 * quote waits for the underwriter's typing as a blank one does; a typed index's value is
 * typing. `quoteEligible` supplies `reads` to exclude hidden values and the app's own
 * columns while the original schema still determines which tables have inputs.
 */
export const quoteTyped = (schema: FormSchema, rows: RowsByTable, reads: ReadsColumn = () => true): boolean =>
  schema.tables.some((table) => {
    if (!hasInputs(table)) return false
    const columns = typingColumns(table).filter((column) => reads(table, column))
    return quotedRows(table, rows).some((row) =>
      columns.some((column) => filled(row[column.name]) && row[column.name] !== startingValue(table, column)),
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

/** Digits alone with a sign, which the server reads as a whole number exactly. */
const WHOLE = /^[+-]?\d+$/

/**
 * A typed whole number as the server types one for an Integer column: digits alone read
 * exactly however large, not through a Number; any other number that is whole as the
 * Number it reads as; or null for text that is not a whole number.
 */
export function parseTypedWhole(text: string): bigint | null {
  const cleaned = text.replace(/[$£€,\s]/g, "")
  if (WHOLE.test(cleaned)) return BigInt(cleaned)
  const number = parseTypedNumber(cleaned)
  return number !== null && Number.isInteger(number) ? BigInt(number) : null
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
 * (the server would keep either as typed, for the pipeline to refuse); a Date column
 * holding text that is not a day, as a column retyped to a Date may; a number outside
 * the column's range; a value that is not one of the allowed values. A tick box, a
 * numbered index and an index of values, whose values the sheets give, break nothing; a
 * typed index's own problems on the rows are `typedIndexProblems`'.
 */
export function cellProblem(column: SchemaColumn, value: SampleValue | undefined): string | null {
  if (!isTyping(column)) return null
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
 * What breaks the columns' rules in the values typed, by cell: each input table's inputs
 * over its rows, a one-row table's one row always (a quote has one) and a many-row
 * table's filled rows only, as the server leaves its empty rows out. Outputs have no rules
 * a value typed breaks, and a value typed for a column no longer in the schema is nobody's.
 */
export function quoteProblems(schema: FormSchema, rows: RowsByTable): Record<string, string> {
  const problems: Record<string, string> = {}
  for (const table of schema.tables) {
    if (!hasInputs(table)) continue
    const typed = rows[table.name] ?? []
    const checked = table.rows === "one" ? [typed[0] ?? {}] : typed
    checked.forEach((row, index) => {
      if (table.rows === "many" && !rowFilled(table, row)) return
      for (const column of table.inputs) {
        const problem = cellProblem(column, row[column.name])
        if (problem !== null) problems[cellKey(table.name, index, column.name)] = problem
      }
    })
  }
  return problems
}

/** The longest a Text index's value is, as the server's `INDEX_TEXT_LENGTH`: the store keys a many-row table's rows by its index. */
export const INDEX_TEXT_LENGTH = 255

/** How many characters text holds as the server counts them: code points, not UTF-16 units. */
export const textLength = (text: string): number => [...text].length

/** Items as a message lists them: "1", "1 and 01", "1, 01 and 001" (`_listed`). */
export const listed = (items: readonly string[]): string => (items.length === 1 ? items[0] : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`)

/**
 * A float as Python's `repr` writes it, as the server's messages do: the shortest digits that
 * read back as it, in fixed notation from 1e-4 up to 1e16, else in exponent notation (`1e-05`,
 * `1.5e+16`).
 */
function pythonFloat(value: number): string {
  const [mantissa, exponentText] = value.toExponential().split("e")
  const exponent = Number(exponentText)
  const sign = mantissa.startsWith("-") ? "-" : ""
  const digits = mantissa.replace("-", "").replace(".", "")
  if (exponent < -4 || exponent >= 16) {
    const shown = digits.length > 1 ? `${digits[0]}.${digits.slice(1)}` : digits
    return `${sign}${shown}e${exponent < 0 ? "-" : "+"}${String(Math.abs(exponent)).padStart(2, "0")}`
  }
  if (exponent < 0) return `${sign}0.${"0".repeat(-exponent - 1)}${digits}`
  return `${sign}${digits.slice(0, exponent + 1).padEnd(exponent + 1, "0")}.${digits.slice(exponent + 1) || "0"}`
}

/**
 * A key's value as a message says it (`key_text`), by its column's type: a whole number
 * without a point, any other number as its shortest text, text in double quotes and a Date
 * as its ISO text, which a JSON answer holds it as.
 */
export function keyText(value: unknown, type: SchemaColumn["type"]): string {
  if (typeof value === "boolean") return value ? "true" : "false"
  if (typeof value === "bigint") return value.toString()
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return String(value)
    return Number.isInteger(value) ? BigInt(value).toString() : pythonFloat(value)
  }
  if (typeof value === "string") return type === "str" ? JSON.stringify(value) : value
  return JSON.stringify(value) ?? String(value)
}

/**
 * A typed index's text as the server types it for its column (`_typed`): an Integer's whole
 * number exactly however large, another number as a Number, and text that is not a number,
 * or a Text index's, as typed.
 */
export function typedIndexValue(type: SchemaColumn["type"], text: string): string | number | bigint {
  if (type !== "int") return text
  return parseTypedWhole(text) ?? parseTypedNumber(text) ?? text
}

/** A typed index's value as the server's sets compare it: a number by its value and text as itself, never the one for the other. */
export const indexIdentity = (value: string | number | bigint | boolean): string =>
  typeof value === "string" ? `text:${value}` : typeof value === "boolean" ? `tick:${String(value)}` : `number:${String(value)}`

/** A typed index's problem on a quote's rows: its table, the index, the rows as the sheet numbers them, from 1, and what is wrong, said whole. */
export interface IndexProblem {
  table: string
  column: string
  rows: readonly number[]
  message: string
}

/**
 * Each typed index's problems over its input table's rows, as the sheets number them: a row
 * holding anything in its other inputs but the index blank, two rows holding one value
 * (compared as typed), or a value of Text longer than `INDEX_TEXT_LENGTH`, in the server's
 * order and words (`typed_index_problems`): the store keys a quote's rows by the index, so
 * while one stands the quote is neither priced nor saved. `reads` excludes hidden ordinary
 * inputs while the original schema identifies input tables; the index stays whatever hides.
 */
export function typedIndexProblems(schema: FormSchema, rows: RowsByTable, reads: ReadsColumn = () => true): IndexProblem[] {
  const problems: IndexProblem[] = []
  for (const table of schema.tables) {
    const index = tableIndex(table)
    if (index === null || index.index !== "typed" || !hasInputs(table)) continue
    const read = table.inputs.filter((column) => column !== index && reads(table, column))
    const blank: number[] = []
    const long: number[] = []
    const places = new Map<string, { value: string | number | bigint | boolean; rows: number[] }>()
    ;(rows[table.name] ?? []).forEach((row, at) => {
      const number = at + 1
      const raw = row[index.name]
      if (!filled(raw)) {
        if (read.some((column) => filled(row[column.name]))) blank.push(number)
        return
      }
      const value = typeof raw === "string" ? typedIndexValue(index.type, raw.trim()) : true
      if (typeof value === "string" && textLength(value) > INDEX_TEXT_LENGTH) long.push(number)
      const identity = indexIdentity(value)
      const place = places.get(identity)
      if (place === undefined) places.set(identity, { value, rows: [number] })
      else place.rows.push(number)
    })
    const name = index.name
    const problem = (held: readonly number[], message: string) => problems.push({ table: table.name, column: name, rows: held, message })
    for (const number of blank) problem([number], `${table.name}'s row ${number} holds no ${name}`)
    for (const { value, rows: held } of places.values()) {
      if (held.length > 1) problem(held, `${table.name}'s rows ${listed(held.map(String))} hold ${name} ${keyText(value, "str")}; each row's ${name} is its own`)
    }
    for (const number of long) problem([number], `${table.name}'s row ${number} holds a ${name} longer than ${INDEX_TEXT_LENGTH} characters`)
  }
  return problems
}

/** A typed index's problems by cell (`cellKey`, the grid's row from 0), the first on a cell standing for it. */
export function indexProblemCells(problems: readonly IndexProblem[]): Record<string, string> {
  const cells: Record<string, string> = {}
  for (const { table, column, rows, message } of problems) {
    for (const number of rows) {
      const key = cellKey(table, number - 1, column)
      if (!(key in cells)) cells[key] = message
    }
  }
  return cells
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

/** The index's name in each of two tables lined up: in the input table's typed rows, and in the answer's rows. */
export interface IndexNames {
  typed: string
  answer: string
}

const keyed = (row: Record<string, unknown>, name: string): boolean => row[name] !== undefined && row[name] !== null

/**
 * The rows of a priced table lined up with a grid's rows of an input table by index, its
 * name in each (`keys`): for each grid row, the answer's row whose index holds, as text,
 * what the server typed for the grid row's (a numbered index the row's number, a typed
 * one the value typed, an index of values the row's value), never by place; undefined for
 * a grid row holding nothing the server sends, or none keyed like it, and for every row
 * without `keys`. `typedRows` are the input table's rows as the server typed them, the
 * filled rows in order, and `outputRows` the answer: the input table's own, or that of a
 * table of outputs alone whose index lines up with it.
 */
export function pricedRows(
  input: SchemaTable,
  keys: IndexNames | null,
  gridRows: readonly (SampleRow | undefined)[],
  typedRows: PricedRows,
  outputRows: PricedRows,
): (Record<string, unknown> | undefined)[] {
  let next = 0
  return gridRows.map((row) => {
    if (!rowFilled(input, row)) return undefined
    const typed = typedRows[next]
    next += 1
    if (typed === undefined || keys === null || !keyed(typed, keys.typed)) return undefined
    return outputRows.find((candidate) => keyed(candidate, keys.answer) && String(candidate[keys.answer]) === String(typed[keys.typed]))
  })
}
