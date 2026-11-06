/**
 * The quote's identity at the head of every schema table but quote_details (specs/workbench):
 * its reference and option, which Haute adds and keeps locked at the head of a table's first
 * block, a request gives in quote_details alone, and the sheets never show or test; a table's
 * two blocks, its inputs and its outputs, as the server's `SchemaTable` reads them; and Haute's
 * quote_details columns, by name, with those the app sets, which every request holds whatever
 * the sheets hide. A table and a column are known by their names (READ-23). Imported by
 * `workbenchForm` and `sheetVisibility` alike, which import each other, so it imports neither.
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

/** A table's two blocks: its inputs, which an underwriter types, and its outputs, which pricing fills. */
export type Block = "inputs" | "outputs"
/** A table as the block helpers read it: its name and its two blocks. */
type Blocks = Pick<SchemaTable, "name" | "inputs" | "outputs">

/** How many of the quote's identity columns lead `block`, a block of the table named `name`: both as Haute makes them, else none. */
const blockIdentity = (name: string, block: readonly SchemaColumn[]): number =>
  name !== QUOTE_DETAILS_NAME && IDENTITY_COLUMNS.every((_, at) => block[at] !== undefined && asHauteMakesIt(block[at], at)) ? IDENTITY_COLUMNS.length : 0

/** A table's columns, its inputs then its outputs: what a reader of every column takes (`SchemaTable.columns`). */
export const tableColumns = (table: Pick<SchemaTable, "inputs" | "outputs">): SchemaColumn[] => [...table.inputs, ...table.outputs]

/** Whether `table`'s column of that name is one of its outputs (`SchemaTable.is_output`). */
export const isOutputColumn = (table: Pick<SchemaTable, "outputs">, column: Pick<SchemaColumn, "name"> | string): boolean => {
  const name = typeof column === "string" ? column : column.name
  return table.outputs.some((held) => held.name === name)
}

/** The block holding `table`'s column of that name: its inputs, else its outputs. */
export const blockOf = (table: Pick<SchemaTable, "outputs">, column: Pick<SchemaColumn, "name"> | string): Block =>
  isOutputColumn(table, column) ? "outputs" : "inputs"

/** A table's inputs but the quote's identity Haute leads them with (`own_inputs`). */
export const ownInputs = (table: Blocks): SchemaColumn[] => table.inputs.slice(blockIdentity(table.name, table.inputs))

/** A table's outputs but the quote's identity Haute leads them with (`own_outputs`). */
export const ownOutputs = (table: Blocks): SchemaColumn[] => table.outputs.slice(blockIdentity(table.name, table.outputs))

/** A table's own inputs but the index: what an underwriter types into it (`typed_inputs`). */
export const typedInputs = (table: Blocks): SchemaColumn[] => ownInputs(table).filter((column) => !column.index)

/** Whether a table is an input table: it holds an input an underwriter types (`has_inputs`). */
export const hasInputs = (table: Blocks): boolean => typedInputs(table).length > 0

/** Whether a table is an output table: it holds an output of its own (`has_outputs`). */
export const hasOutputs = (table: Blocks): boolean => ownOutputs(table).length > 0

/**
 * How many of the quote's identity columns lead `table`'s first block: both when the first
 * two of its inputs, or of its outputs, are the identity as Haute makes it (`holds_identity`),
 * else none, as in quote_details. A column of either name anywhere else, or changed, is the
 * table's own: a clash the schema's problems name, which the analyst renames.
 */
export function identityCount(table: Blocks): number {
  return Math.max(blockIdentity(table.name, table.inputs), blockIdentity(table.name, table.outputs))
}

/** Whether `column` of `table` is one of the quote's identity columns Haute keeps at the head of a block. */
export const isIdentityColumn = (table: Blocks, column: Pick<SchemaColumn, "name">): boolean =>
  [...table.inputs.slice(0, blockIdentity(table.name, table.inputs)), ...table.outputs.slice(0, blockIdentity(table.name, table.outputs))].some(
    (held) => held.name === column.name,
  )

/** A table's columns the sheets can show and test: its own inputs then its own outputs, the quote's identity left out. */
export const sheetColumns = (table: Blocks): SchemaColumn[] => [...ownInputs(table), ...ownOutputs(table)]

/** The quote's identity as Haute makes it, a fresh copy for a table's head. */
const identityColumns = (): SchemaColumn[] => IDENTITY_COLUMNS.map(([name, type]) => blankColumn(name, type))

/**
 * `table` with the quote's identity at the head of its first block, as the server's
 * before-validator places it (`_gives_every_table_the_quote_identity`): taken from the head of
 * either block, then given at the head of its inputs while it has inputs of its own or no
 * outputs, else at the head of its outputs; a table holding a column of its own under either
 * name goes without it, and quote_details is left as it is. The table itself while nothing moves.
 */
export function withIdentityPlaced<T extends Blocks>(table: T): T {
  if (table.name === QUOTE_DETAILS_NAME) return table
  const inputs = table.inputs.slice(blockIdentity(table.name, table.inputs))
  const outputs = table.outputs.slice(blockIdentity(table.name, table.outputs))
  const placed =
    [...inputs, ...outputs].some((column) => IDENTITY_NAMES.has(column.name))
      ? { inputs, outputs }
      : inputs.length > 0 || outputs.length === 0
        ? { inputs: [...identityColumns(), ...inputs], outputs }
        : { inputs, outputs: [...identityColumns(), ...outputs] }
  const same = (a: readonly SchemaColumn[], b: readonly SchemaColumn[]) => a.length === b.length && a.every((column, at) => column.name === b[at].name)
  return same(placed.inputs, table.inputs) && same(placed.outputs, table.outputs) ? table : { ...table, ...placed }
}
