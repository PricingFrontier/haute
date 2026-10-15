/** What the schema editor's parts share: their classes, the form as it is now, and how a column's rules and its uses are said (specs/workbench). */
import { type FocusEvent } from "react"
import type { FormSpec, SchemaColumn, SchemaTable } from "../../api/types"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { presetColumn } from "../../utils/sheetValues"
import { isNumeric } from "../columnTypes"

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

/** An input column's rules in a few words, shown beside its name, and whether it gives its table's rows. */
export function rulesSummary(table: SchemaTable, column: SchemaColumn): string {
  const parts: string[] = []
  if (column.required) parts.push("required")
  if (isNumeric(column.type) && (column.min !== null || column.max !== null)) {
    parts.push(`${column.min ?? "…"} to ${column.max ?? "…"}`)
  }
  if (column.type !== "bool" && column.type !== "date" && column.options.length > 0) {
    const values = `${column.options.length} ${column.options.length === 1 ? "value" : "values"}`
    parts.push(column.options_source === null ? values : `${values} from ${column.options_source.file}`)
  }
  if (presetColumn(table)?.id === column.id) parts.push("a row each")
  return parts.join(" · ")
}

export const components = (n: number) => `${n} ${n === 1 ? "component" : "components"}`

/** What removing a column or a table takes with it: the components showing it and the conditions testing it. */
export function usedBy(shown: number, tested: number): string {
  const parts = shown > 0 ? [`shown in ${components(shown)}`] : []
  if (tested > 0) parts.push(`tested by the conditions of ${tested} ${tested === 1 ? "sheet, section or component" : "sheets, sections or components"}`)
  return parts.join(" and ")
}

/** A new table or column gets a placeholder name; selecting it lets typing replace it. */
export const selectAll = (e: FocusEvent<HTMLInputElement>) => e.target.select()

/** The props that focus a just-added name and select it, once. */
export type Fresh = { autoFocus: boolean; onFocus?: (e: FocusEvent<HTMLInputElement>) => void; onBlur: () => void }
