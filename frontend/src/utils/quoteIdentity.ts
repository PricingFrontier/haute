/**
 * The quote's identity at the head of every schema table but quote_details (specs/workbench):
 * its reference and option, which Haute adds and keeps locked, a request gives in
 * quote_details alone, and the sheets never show or test; and Haute's quote_details columns,
 * by name, with those the app sets, which every request holds whatever the sheets hide. A
 * table and a column are known by their names (READ-23). Imported by `workbenchForm` and
 * `sheetVisibility` alike, which import each other, so it imports neither.
 */
import type { SchemaColumn, SchemaTable } from "../api/types"

/**
 * The name of Haute's own input table of a quote's details, first in every form
 * (`haute._workbench_form.QUOTE_DETAILS_NAME`): the underwriter app keeps its row with each
 * quote, and its Quotes page lists them.
 */
export const QUOTE_DETAILS_NAME = "quote_details"

/** Its columns, in order, as name and type (`QUOTE_DETAILS_COLUMNS`), a quote's administration; their structure is fixed. */
export const QUOTE_DETAILS_COLUMNS = [
  ["quote_reference", "str"],
  ["quote_option", "int"],
  ["quote_name", "str"],
  ["insured", "str"],
  ["broker", "str"],
  ["inception_date", "date"],
  ["expiry_date", "date"],
  ["is_renewal", "bool"],
  ["stage", "str"],
  ["assignee", "str"],
  ["archived", "bool"],
  ["created_date", "date"],
  ["created_by", "str"],
  ["modified_date", "date"],
  ["modified_by", "str"],
  ["haute_version", "str"],
] as const satisfies ReadonlyArray<readonly [name: string, type: SchemaColumn["type"]]>

type QuoteDetailColumn = (typeof QUOTE_DETAILS_COLUMNS)[number]

/** A quote_details column's name, which a quote's details are keyed by. */
export type QuoteDetailName = QuoteDetailColumn[0]

/**
 * Each quote_details column's name, checked: `QUOTE_DETAILS.is_renewal` is `is_renewal`, and
 * a name the table no longer has does not compile.
 */
export const QUOTE_DETAILS = Object.fromEntries(QUOTE_DETAILS_COLUMNS.map(([name]) => [name, name])) as {
  readonly [Column in QuoteDetailColumn as Column[0]]: Column[0]
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
 * The quote's identity at the head of every other table, as name and type
 * (`IDENTITY_COLUMNS` in `src/haute/_workbench_form.py`), named as quote_details names them.
 */
export const IDENTITY_COLUMNS: ReadonlyArray<readonly [name: string, type: SchemaColumn["type"]]> = [
  ["quote_reference", "str"],
  ["quote_option", "int"],
]
/** The names the identity holds in every table, which a table's own column cannot take. */
export const IDENTITY_NAMES: ReadonlySet<string> = new Set(IDENTITY_COLUMNS.map(([name]) => name))

/** A column named `name` of `type` with nothing of its own set, as Haute makes the identity's. */
export const blankColumn = (name: string, type: SchemaColumn["type"]): SchemaColumn => ({
  name,
  type,
  label: "",
  group: "",
  key: false,
  required: false,
  min: null,
  max: null,
  options: [],
  options_source: null,
  row_per_value: false,
  index: false,
  help: "",
  placeholder: "",
  default_value: "",
  number_format: "general",
  decimal_places: null,
  use_grouping: true,
})

/** Whether `column` is the identity column Haute makes at `at`: its name and type, nothing else set. */
const asHauteMakesIt = (column: SchemaColumn, at: number): boolean => {
  const [name, type] = IDENTITY_COLUMNS[at]
  const blank = blankColumn(name, type)
  return (Object.keys(blank) as (keyof SchemaColumn)[]).every((key) => JSON.stringify(column[key]) === JSON.stringify(blank[key]))
}

/**
 * How many of the quote's identity columns lead `table`: both when its first two are the
 * identity as Haute makes it (`holds_identity`), else none, as in quote_details. A column of
 * either name anywhere else, or changed, is the table's own: a clash the schema's problems
 * name, which the analyst renames.
 */
export function identityCount(table: Pick<SchemaTable, "name" | "columns">): number {
  if (table.name === QUOTE_DETAILS_NAME) return 0
  const held = IDENTITY_COLUMNS.every((_, at) => table.columns[at] !== undefined && asHauteMakesIt(table.columns[at], at))
  return held ? IDENTITY_COLUMNS.length : 0
}

/** Whether `column` of `table` is one of the quote's identity columns Haute keeps at its head. */
export const isIdentityColumn = (table: Pick<SchemaTable, "name" | "columns">, column: Pick<SchemaColumn, "name">): boolean =>
  table.columns.slice(0, identityCount(table)).some((held) => held.name === column.name)

/** A table's columns the sheets can show and test: all but the quote's identity. */
export const sheetColumns = (table: Pick<SchemaTable, "name" | "columns">): SchemaColumn[] => table.columns.slice(identityCount(table))
