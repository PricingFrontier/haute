/**
 * The quote's identity at the head of every schema table but quote_details (specs/workbench):
 * its reference and option, which Haute adds and keeps locked, a request gives in
 * quote_details alone, and the sheets never show or test; and the quote_details columns the
 * app sets, which every request holds whatever the sheets hide. Imported by `workbenchForm`
 * and `sheetVisibility` alike, which import each other, so it imports neither.
 */
import type { SchemaColumn, SchemaTable } from "../api/types"

/**
 * The id and name of Haute's own input table of a quote's details, first in every form
 * (`haute._workbench_form.QUOTE_DETAILS_ID`): the underwriter app keeps its row with each
 * revision, and its Quotes page lists them.
 */
export const QUOTE_DETAILS_ID = "quote_details"

/**
 * The columns the underwriter app sets over whatever is typed, which take no rules and are in
 * every request whatever the sheets hide (`QUOTE_DETAILS_MANAGED`).
 */
export const QUOTE_DETAILS_MANAGED: ReadonlySet<string> = new Set([
  "qd_reference",
  "qd_option",
  "qd_stage",
  "qd_assignee",
  "qd_archived",
  "qd_created_date",
  "qd_created_by",
  "qd_modified_date",
  "qd_modified_by",
  "qd_haute_version",
])

/**
 * The quote's identity at the head of every other table, as id, name and type
 * (`IDENTITY_COLUMNS` in `src/haute/_workbench_form.py`): the same ids in every table.
 */
export const IDENTITY_COLUMNS: ReadonlyArray<readonly [id: string, name: string, type: SchemaColumn["type"]]> = [
  ["quote_reference", "quote_reference", "str"],
  ["quote_option", "quote_option", "int"],
]
const IDENTITY_IDS: ReadonlySet<string> = new Set(IDENTITY_COLUMNS.map(([id]) => id))
/** The names the identity holds in every table, which a table's own column cannot take. */
export const IDENTITY_NAMES: ReadonlySet<string> = new Set(IDENTITY_COLUMNS.map(([, name]) => name))

/** Whether `column` is one of the quote's identity columns Haute keeps at the head of a table. */
export const isIdentityColumn = (column: Pick<SchemaColumn, "id">): boolean => IDENTITY_IDS.has(column.id)

/** How many of the quote's identity columns lead `table`: none in quote_details or a table without them. */
export function identityCount(table: Pick<SchemaTable, "columns">): number {
  let count = 0
  while (count < table.columns.length && isIdentityColumn(table.columns[count])) count += 1
  return count
}

/** A table's columns the sheets can show and test: all but the quote's identity. */
export const sheetColumns = (table: Pick<SchemaTable, "columns">): SchemaColumn[] =>
  table.columns.filter((column) => !isIdentityColumn(column))
