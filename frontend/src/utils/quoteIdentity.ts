/**
 * The quote's identity at the head of every schema table but quote_details (specs/workbench):
 * its reference and option, which Haute adds and keeps locked, a request gives in
 * quote_details alone, and the sheets never show or test; and Haute's quote_details columns,
 * by id and by name, with those the app sets, which every request holds whatever the sheets
 * hide. Imported by `workbenchForm` and `sheetVisibility` alike, which import each other, so
 * it imports neither.
 */
import type { SchemaColumn, SchemaTable } from "../api/types"

/**
 * The id and name of Haute's own input table of a quote's details, first in every form
 * (`haute._workbench_form.QUOTE_DETAILS_ID`): the underwriter app keeps its row with each
 * revision, and its Quotes page lists them.
 */
export const QUOTE_DETAILS_ID = "quote_details"

/** Its columns, in order, as id, name and type (`QUOTE_DETAILS_COLUMNS`), a quote's administration; their structure is fixed. */
export const QUOTE_DETAILS_COLUMNS = [
  ["qd_reference", "quote_reference", "str"],
  ["qd_option", "quote_option", "int"],
  ["qd_quote_name", "quote_name", "str"],
  ["qd_insured", "insured", "str"],
  ["qd_broker", "broker", "str"],
  ["qd_inception_date", "inception_date", "date"],
  ["qd_expiry_date", "expiry_date", "date"],
  ["qd_is_renewal", "is_renewal", "bool"],
  ["qd_stage", "stage", "str"],
  ["qd_assignee", "assignee", "str"],
  ["qd_archived", "archived", "bool"],
  ["qd_created_date", "created_date", "date"],
  ["qd_created_by", "created_by", "str"],
  ["qd_modified_date", "modified_date", "date"],
  ["qd_modified_by", "modified_by", "str"],
  ["qd_haute_version", "haute_version", "str"],
] as const satisfies ReadonlyArray<readonly [id: string, name: string, type: SchemaColumn["type"]]>

type QuoteDetailColumn = (typeof QUOTE_DETAILS_COLUMNS)[number]

/** A quote_details column's name, which a quote's details are keyed by. */
export type QuoteDetailName = QuoteDetailColumn[1]

/**
 * Each quote_details column's id by its name: `QUOTE_DETAILS.is_renewal` is `qd_is_renewal`,
 * and a name the table no longer has does not compile.
 */
export const QUOTE_DETAILS = Object.fromEntries(QUOTE_DETAILS_COLUMNS.map(([id, name]) => [name, id])) as {
  readonly [Column in QuoteDetailColumn as Column[1]]: Column[0]
}

/**
 * The columns the underwriter app sets over whatever is typed, which take no rules and are in
 * every request whatever the sheets hide (`QUOTE_DETAILS_MANAGED`).
 */
export const QUOTE_DETAILS_MANAGED: ReadonlySet<string> = new Set([
  QUOTE_DETAILS.quote_reference,
  QUOTE_DETAILS.quote_option,
  QUOTE_DETAILS.stage,
  QUOTE_DETAILS.assignee,
  QUOTE_DETAILS.archived,
  QUOTE_DETAILS.created_date,
  QUOTE_DETAILS.created_by,
  QUOTE_DETAILS.modified_date,
  QUOTE_DETAILS.modified_by,
  QUOTE_DETAILS.haute_version,
])

/**
 * The columns of quote_details the app changes over a quote's life, which the quote store
 * keeps but no request holds (`QUOTE_DETAILS_STORE_ONLY`): changing one never leaves a stored
 * price that its stored inputs no longer give.
 */
export const QUOTE_DETAILS_STORE_ONLY: ReadonlySet<string> = new Set([
  QUOTE_DETAILS.stage,
  QUOTE_DETAILS.assignee,
  QUOTE_DETAILS.archived,
  QUOTE_DETAILS.modified_date,
  QUOTE_DETAILS.modified_by,
  QUOTE_DETAILS.haute_version,
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
