/** What the schema editor's parts share: their classes, the form as it is now, and how a column's rules and its uses are said (specs/workbench). */
import { type FocusEvent } from "react"
import type { FormSpec, SchemaColumn, SchemaTable } from "../../api/types"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { formattedNumber, presetColumn } from "../../utils/sheetValues"
import { isNumeric } from "../columnTypes"

/** A number format's name in a column's summary: as Format names it, a currency by its sign and code. */
const FORMAT_NAMES: Readonly<Record<SchemaColumn["number_format"], string>> = {
  general: "General",
  number: "Number",
  percent: "Percentage",
  currency_gbp: "£ GBP",
  currency_usd: "US$ USD",
  currency_eur: "€ EUR",
}

export const CHIP_CLASS = "focus-ring shrink-0 rounded-md px-1.5 py-0.5 text-[11px] font-semibold"

export const HOVER_ONLY = "opacity-0 group-hover:opacity-100 focus-visible:opacity-100"

/** What Haute's quote_details table is, on its lock. */
export const DETAILS_TITLE =
  "Haute's table of a quote's details: the underwriter app keeps it with each quote and lists it on the Quotes page. " +
  "Its columns are fixed; place them on the sheets, and give them labels, help and rules."

/** The form as the store holds it now, for an edit that builds on it. */
export function currentForm(): FormSpec {
  const { form } = useWorkbenchFormStore.getState()
  if (form === null) throw new Error("The workbench's form is not loaded")
  return form
}

/** A number as its column's format shows it, or as it is where the format shows numbers as typed. */
const shown = (column: SchemaColumn, value: number | string): string => formattedNumber(column, value) ?? String(value)

/**
 * A number column's format in a few words: its name, its decimal places when chosen and
 * "no separators" when they are off; nothing for the index, a column of another type, or
 * General with automatic decimals, which shows numbers as typed.
 */
export function formatSummary(column: SchemaColumn): string {
  if (!isNumeric(column.type) || column.index) return ""
  const { number_format: format, decimal_places: places } = column
  if (format === "general" && places === null) return ""
  const parts = [FORMAT_NAMES[format]]
  if (places !== null) parts.push(`${places} ${places === 1 ? "decimal" : "decimals"}`)
  // Past General with automatic decimals, separators apply.
  if (!column.use_grouping) parts.push("no separators")
  return parts.join(", ")
}

/**
 * An input column's rules in a few words: required, its range and its default in its
 * format, its allowed values and whether it gives its table's rows.
 */
function rulesParts(table: SchemaTable, column: SchemaColumn): string[] {
  const parts: string[] = []
  if (column.required) parts.push("required")
  if (isNumeric(column.type) && (column.min !== null || column.max !== null)) {
    parts.push(`${column.min === null ? "…" : shown(column, column.min)} to ${column.max === null ? "…" : shown(column, column.max)}`)
  }
  // A default fills a one-row table's new quote; the index takes none.
  if (table.rows === "one" && !column.index) {
    const value = column.default_value
    if (value === true) parts.push("default ticked")
    else if (value !== false && value !== "") parts.push(`default ${isNumeric(column.type) ? shown(column, value) : value}`)
  }
  if (column.type !== "bool" && column.type !== "date" && column.options.length > 0) {
    const values = `${column.options.length} ${column.options.length === 1 ? "value" : "values"}`
    parts.push(column.options_source === null ? values : `${values} from ${column.options_source.file}`)
  }
  if (presetColumn(table)?.id === column.id) parts.push("a row each")
  return parts
}

/**
 * A column in a few words, shown under its name, "" when there is nothing to say: an input
 * column's rules, then, in any table, a number column's format.
 */
export function columnSummary(table: SchemaTable, column: SchemaColumn): string {
  const parts = table.role === "input" ? rulesParts(table, column) : []
  const format = formatSummary(column)
  if (format !== "") parts.push(format)
  return parts.join(" · ")
}

export const components = (n: number) => `${n} ${n === 1 ? "component" : "components"}`

/** What removing a column or a table takes with it: the components showing it and the conditions testing it. */
export function usedBy(shown: number, tested: number): string {
  const parts = shown > 0 ? [`shown in ${components(shown)}`] : []
  if (tested > 0) parts.push(`tested by the conditions of ${tested} ${tested === 1 ? "sheet, section or component" : "sheets, sections or components"}`)
  return parts.join(" and ")
}

/** The column's name or its label holds the search's text, whatever its case. */
export function columnMatches(column: SchemaColumn, text: string): boolean {
  const needle = text.toLowerCase()
  return column.name.toLowerCase().includes(needle) || column.label.toLowerCase().includes(needle)
}

/** A new table or column gets a placeholder name; selecting it lets typing replace it. */
export const selectAll = (e: FocusEvent<HTMLInputElement>) => e.target.select()

/** The props that focus a just-added name and select it, once. */
export type Fresh = { autoFocus: boolean; onFocus?: (e: FocusEvent<HTMLInputElement>) => void; onBlur: () => void }
