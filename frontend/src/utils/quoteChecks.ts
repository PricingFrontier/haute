/**
 * The checks a quote must pass to be marked final (specs/workbench), made on what the sheets
 * hold, the values typed and the price as last made, by the rules the server makes them by
 * on the quote as stored (`src/haute/_workbench_checks.py`), both held to
 * `__tests__/fixtures/quoteChecks.json`: one check for each rule a column holds, in the
 * schema's order. Required, on an input or an output, that the column holds a value; a
 * range, that a value held is within it; allowed values, that a value held is one of them,
 * compared as the column's type reads them; and a validation, a True/false output the
 * pipeline fills, that it holds true. A check passes when a one-row table's one row passes
 * it, or every row of a many-row table does, a table without rows passing; a failing check
 * of a many-row table names its rows by their index. An empty value passes a range and
 * allowed values. The rules of an input the sheets hide for the values are not checked;
 * an output's always are.
 */
import type { FormSpec, SchemaColumn, SchemaTable } from "../api/types"
import { hasInputs, isOutputColumn, tableColumns, tableIndex } from "./quoteIdentity"
import { KEPT_KEY, rowFilled, typedIndexValue, withPresetRows, type PricedRows, type RowsByTable } from "./sheetValues"
import { columnKey, quotedSchema, sheetVisibility } from "./sheetVisibility"
import { readTypedNumber, readTypedWhole, trimTyped } from "./typedNumbers"
import { readableName, type SampleRow, type SampleValue } from "./workbenchForm"

export type CheckKind = "required" | "range" | "allowed" | "validation"

/** One rule of one column, and whether the quote passes it. */
export interface QuoteCheck {
  kind: CheckKind
  table: string
  column: string
  passed: boolean
  /** The rows of a many-row table failing it, each by its index's value as text; none for a one-row table. */
  rows: string[]
}

/** The checks a column gives, in their order (`_kinds`): none of an index, and none of the rules of an input the sheets hide. */
function kinds(column: SchemaColumn, hidden: boolean): CheckKind[] {
  if (column.index !== null) return []
  const given: CheckKind[] = []
  if (!hidden) {
    if (column.required) given.push("required")
    if (column.min !== null || column.max !== null) given.push("range")
    if (column.options.length > 0) given.push("allowed")
  }
  if (column.validation) given.push("validation")
  return given
}

/** Typed text as a number column's type reads it, to compare two values by: `01` and `1` one Integer. */
const typedSame = (column: SchemaColumn, text: string): string => {
  if (column.type !== "int" && column.type !== "float") return `text:${text}`
  const read = (column.type === "int" ? readTypedWhole(text) : null) ?? readTypedNumber(text)
  return read === null ? `text:${text}` : `number:${String(read)}`
}

/** Whether a typed cell passes a rule of its input column, as the server reads the value stored for it. */
function typedPasses(kind: CheckKind, column: SchemaColumn, value: SampleValue | undefined, rowHeld: boolean): boolean {
  // A tick box in a row holding anything is stored ticked or not, and false is a value; a
  // one-row table's row with nothing typed in it holds null.
  if (column.type === "bool") return rowHeld
  const text = typeof value === "string" ? trimTyped(value) : ""
  if (kind === "required") return text !== ""
  if (text === "") return true
  if (kind === "allowed") return column.options.some((option) => typedSame(column, trimTyped(option)) === typedSame(column, text))
  const number = readTypedNumber(text) ?? (readTypedWhole(text) === null ? null : Number(readTypedWhole(text)))
  if (number === null) return false
  return (column.min === null || number >= column.min) && (column.max === null || number <= column.max)
}

/** Whether a priced value passes a rule of its output column. */
function pricedPasses(kind: CheckKind, value: unknown): boolean {
  if (kind === "validation") return value === true
  // Required, the one other rule an output holds.
  return value !== null && value !== undefined && !(typeof value === "string" && value.trim() === "") && !(typeof value === "number" && !Number.isFinite(value))
}

/** A many-row table's typed row as a check names it: its index's value as the server types it, a numbered index's its place on the sheet. */
function rowName(index: SchemaColumn, row: SampleRow, place: number): string {
  if (index.index === "numbered") return String(place + 1)
  const value = row[index.name]
  return typeof value === "string" ? String(typedIndexValue(index.type, trimTyped(value))) : ""
}

/**
 * Every check of `form` on the `values` typed into its sheets and the `tables` their price
 * gave, by table name (null for no price), passed or not.
 */
export function quoteChecks(form: FormSpec, values: RowsByTable, tables: Readonly<Record<string, PricedRows>> | null): QuoteCheck[] {
  const hidden = sheetVisibility(form, values).hidden
  const rows = withPresetRows(form.schema, values)
  // The tables as a request holds them, the columns the sheets hide left out: a value typed
  // into a hidden column is stored nowhere, so it makes no row and fills none.
  const sent = new Map(quotedSchema(form.schema, hidden).tables.map((table) => [table.name, table]))
  const checks: QuoteCheck[] = []
  for (const table of form.schema.tables) {
    const index = tableIndex(table)
    // Whether the store writes the row's cells: something is typed in a column it is sent.
    const written = (row: SampleRow | undefined): boolean => rowFilled(sent.get(table.name) ?? table, row)
    const typed = rows[table.name] ?? []
    const answered = tables?.[table.name] ?? []
    // The rows the quote holds: a one-row table's one, a many-row input table's filled
    // rows, each by its name, and a table of outputs alone's rows as pricing gave them.
    // `filled` is whether the store writes the row's cells, a tick box among them.
    const held: { name: string; typed: SampleRow; filled: boolean; priced: Record<string, unknown> | undefined }[] = []
    if (table.rows === "one") {
      held.push({ name: "", typed: typed[0] ?? {}, filled: written(typed[0]), priced: answered[0] })
    } else if (index !== null && hasInputs(table)) {
      typed.forEach((row, place) => {
        // A row holding values in store columns the form no longer holds is a row the store
        // keeps too, its cells empty.
        const filled = written(row)
        if (!filled && !(KEPT_KEY in row)) return
        const name = rowName(index, row, place)
        held.push({ name, typed: row, filled, priced: answered.find((candidate) => candidate[index.name] !== null && candidate[index.name] !== undefined && String(candidate[index.name]) === name) })
      })
    } else if (index !== null) {
      for (const row of answered) held.push({ name: String(row[index.name]), typed: {}, filled: true, priced: row })
    }
    for (const column of tableColumns(table)) {
      const output = isOutputColumn(table, column)
      for (const kind of kinds(column, !output && hidden.has(columnKey(table.name, column.name)))) {
        const failing = held.filter((row) => !(output ? pricedPasses(kind, row.priced?.[column.name]) : typedPasses(kind, column, row.typed[column.name], row.filled)))
        checks.push({ kind, table: table.name, column: column.name, passed: failing.length === 0, rows: table.rows === "many" ? failing.map((row) => row.name) : [] })
      }
    }
  }
  return checks
}

/** A range's bound as its column shows a number. */
const bound = (value: number): string => value.toLocaleString()

/** A check in words, for the list: its table, its column's label and what it asks for. */
export interface CheckLine {
  /** `policy · Age`. */
  name: string
  /** What the rule asks: `Needs a value`, `17 to 99`, `One of its allowed values`, or a validation's message. */
  rule: string
  /** The rows failing it, said: `Rows 2 and 5`; "" for none. */
  rows: string
}

export function checkLine(form: FormSpec, check: QuoteCheck): CheckLine {
  const table = form.schema.tables.find((held) => held.name === check.table) as SchemaTable
  const column = tableColumns(table).find((held) => held.name === check.column) as SchemaColumn
  const low = column.min === null ? "" : bound(column.min)
  const high = column.max === null ? "" : bound(column.max)
  const rule =
    check.kind === "required"
      ? "Needs a value"
      : check.kind === "allowed"
        ? "One of its allowed values"
        : check.kind === "validation"
          ? column.validation_message
          : low !== "" && high !== ""
            ? `${low} to ${high}`
            : low !== ""
              ? `At least ${low}`
              : `At most ${high}`
  const rows = check.rows.length === 0 ? "" : `${check.rows.length === 1 ? "Row" : "Rows"} ${check.rows.join(", ")}`
  return { name: `${table.name} · ${column.label || readableName(column.name)}`, rule, rows }
}
