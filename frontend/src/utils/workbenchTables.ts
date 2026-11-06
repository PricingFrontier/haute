/**
 * A Workbench Input takes its tables, and the sample quote its previews run on, from the
 * project's workbench (specs/workbench), and a Workbench Output takes the response's
 * tables from it: each schema table's inputs are an input side, port `<table>_input`, and
 * its outputs an output side, port `<table>_output`. Neither node holds them: the editor
 * fetches them (`useWorkbenchStore`), and these rules say which of them are a node's ports
 * and which connections a fetch leaves without a table. A Workbench Output's panel also
 * judges, by the same rule as a run, how a frame fills its columns. Whether a pricing's answer
 * fits the rows it priced, each row keyed by its table's index (`outputMisfit`), and the
 * index of values' allowed values that cannot key two rows (`keyCollisions`), are the
 * server's rules (`src/haute/_workbench_tables.py`).
 */
import type { Edge } from "@xyflow/react"
import type { SchemaTable, WorkbenchColumn, WorkbenchTable } from "../api/types"
import type { SubmodelBoundaryEdgeData } from "../types/node"
import { NODE_TYPES } from "./nodeTypes"
import { castFamily } from "./polarsDtypes"
import { IDENTITY_COLUMNS, tableIndex } from "./quoteIdentity"
import { INDEX_TEXT_LENGTH, keyText, listed, parseTypedNumber, parseTypedWhole, textLength } from "./sheetValues"

/** The workbench's tables and sample quote from one fetch, numbered so that only the newest counts. */
export interface WorkbenchTables {
  tables: readonly WorkbenchTable[]
  /** One quote as a request holds it: `{}` for none. */
  sample: Readonly<Record<string, unknown>>
  /** The tables a priced quote fills in, which a Workbench Output fills: `[]` for none. */
  responseTables: readonly WorkbenchTable[]
  fetch: number
}

/**
 * Tables' ports: those with a column, by `port`, in order, a port counting once. A table
 * without a column has no frame to read into or to fill, so no port.
 */
export function workbenchTablePorts(tables: readonly WorkbenchTable[]): string[] {
  const ports: string[] = []
  for (const table of tables) {
    if (table.columns.length > 0 && !ports.includes(table.port)) ports.push(table.port)
  }
  return ports
}

/**
 * The tables a node of *nodeType* takes from *workbench*, the newest fetch: a Workbench
 * Input's input tables and a Workbench Output's response tables; none before the first
 * fetch lands, and none for any other node.
 */
export function workbenchNodeTables(
  nodeType: unknown,
  workbench: WorkbenchTables | null,
): readonly WorkbenchTable[] {
  if (workbench === null) return []
  if (nodeType === NODE_TYPES.WORKBENCH_INPUT) return workbench.tables
  if (nodeType === NODE_TYPES.WORKBENCH_OUTPUT) return workbench.responseTables
  return []
}

/** A node's ports from *workbench*: those of the tables it takes from it (`workbenchNodeTables`). */
export function workbenchNodePorts(nodeType: unknown, workbench: WorkbenchTables | null): string[] {
  return workbenchTablePorts(workbenchNodeTables(nodeType, workbench))
}

/** A workbench node's connections to tables a fetch no longer has. */
export interface WorkbenchDisconnection<N> {
  node: N
  edges: Edge[]
}

/**
 * The connections in *graph* to a port *workbench* does not have, by the node that loses
 * them: each out of a Workbench Input whose `sourceHandle` is none of its ports, and each
 * into a Workbench Output whose `targetHandle` is none of its. A connection follows its
 * port's name however the tables were reordered or replaced. One through a submodel's
 * boundary is the definition's port, which only the boundary's own editing changes, so it
 * is left for the run to refuse, naming its port. Null when every connection has its table.
 */
export function workbenchDisconnections<N extends { id: string; data: Record<string, unknown> }>(
  graph: { nodes: readonly N[]; edges: readonly Edge[] },
  workbench: WorkbenchTables,
): { edges: Edge[]; removed: WorkbenchDisconnection<N>[] } | null {
  const inputPorts = new Set(workbenchTablePorts(workbench.tables))
  const outputPorts = new Set(workbenchTablePorts(workbench.responseTables))
  const typeOf = new Map(graph.nodes.map((node) => [node.id, node.data.nodeType]))
  const lost = new Map<string, Edge[]>()
  for (const edge of graph.edges) {
    if ((edge.data as SubmodelBoundaryEdgeData | undefined)?.submodelBoundary !== undefined) continue
    const owner =
      typeOf.get(edge.source) === NODE_TYPES.WORKBENCH_INPUT && !inputPorts.has(edge.sourceHandle ?? "")
        ? edge.source
        : typeOf.get(edge.target) === NODE_TYPES.WORKBENCH_OUTPUT && !outputPorts.has(edge.targetHandle ?? "")
          ? edge.target
          : null
    if (owner !== null) lost.set(owner, [...(lost.get(owner) ?? []), edge])
  }
  if (lost.size === 0) return null
  const gone = new Set([...lost.values()].flat())
  return {
    edges: graph.edges.filter((edge) => !gone.has(edge)),
    removed: graph.nodes.flatMap((node) => {
      const edges = lost.get(node.id)
      return edges === undefined ? [] : [{ node, edges }]
    }),
  }
}

/** The sample priced on the pipeline: each of the Workbench Output's tables' rows, by its schema table's name, not its port's. */
export interface PricedSample {
  tables: Record<string, Record<string, unknown>[]>
}

/** An index of values' allowed value as the server types it for an Integer (`_typed`): a whole number exactly, else a number, else the text itself. */
const typedOption = (value: string): string => {
  const read = parseTypedWhole(value) ?? parseTypedNumber(value)
  return read === null ? `text:${value}` : `number:${String(read)}`
}

/**
 * A table's index of values' allowed values that read as one value in its Integer type, each
 * group said with what to correct (`key_collisions`): `1` and `01` cannot key two rows. A
 * Text index's values are distinct as the params reader gives them.
 */
export function keyCollisions(table: SchemaTable): string[] {
  const column = tableIndex(table)
  if (column === null || column.index !== "values" || column.type !== "int") return []
  const groups = new Map<string, string[]>()
  for (const value of column.options) {
    const at = typedOption(value)
    groups.set(at, [...(groups.get(at) ?? []), value])
  }
  return [...groups.values()]
    .filter((values) => values.length > 1)
    .map((values) => `${table.name}.${column.name}'s allowed values ${listed(values)} are one Integer, so they cannot key its rows: correct the params file`)
}

/** The most key values a misfit names in one list before saying how many more there are. */
const MISFIT_LISTED = 10

/** How many characters a misfit quotes of a Text index's value too long to key a row. */
const MISFIT_TEXT_SHOWN = 40

/** A key's value as the misfit's sets compare it: a number by its value and text as itself, never the one for the other. */
const keyIdentity = (value: unknown): string => `${typeof value === "bigint" ? "number" : typeof value}:${String(value)}`

/** The values `held` takes in `values`' order, each once, by `keyIdentity`. */
const onceEach = (values: readonly unknown[], held: (value: unknown) => boolean): unknown[] => {
  const kept = new Map<string, unknown>()
  for (const value of values) {
    const at = keyIdentity(value)
    if (held(value) && !kept.has(at)) kept.set(at, value)
  }
  return [...kept.values()]
}

/**
 * Why a many-row output side's answer does not fit its rows, or null when it does
 * (`output_misfit`, the same cases in the same words): `answer` is each answer row's index
 * value and `request` each of the request's rows', in order, null or undefined for none. A
 * numbered index of outputs alone always fits, as the Workbench Output numbers it. Else the
 * side's port and its table, then each way it does not fit, in this order and each only when
 * it holds: rows with no index value, a value given twice, then for a table with inputs a
 * value on no row priced, for an index of values one none of its values, and for a typed
 * index of outputs alone a Text value longer than a key is held, cut to forty characters:
 * "ce_equipment_output does not fit ce_equipment's rows: 2 rows have no item_number;
 * item_number 3 is given twice; item_number 7, 8 are on no row priced.", each list at most
 * ten values, in the answer's order, then "and <n> more".
 */
export function outputMisfit(table: WorkbenchTable, answer: readonly unknown[], request: readonly unknown[]): string | null {
  const key = table.key
  if (key === null || table.index_kind === "numbered") return null
  const type = table.columns.find((column) => column.name === key)?.type ?? "str"
  const values = (held: readonly unknown[]): string => {
    const shown = held.slice(0, MISFIT_LISTED).map((value) => keyText(value, type))
    const more = held.length - shown.length
    return shown.join(", ") + (more > 0 ? ` and ${more} more` : "")
  }
  const verb = (held: readonly unknown[]): string => (held.length === 1 ? "is" : "are")
  const parts: string[] = []
  const hasIndex = (value: unknown): boolean => value !== null && value !== undefined &&
    (typeof value !== "string" || value.trim() !== "")
  const given = answer.filter(hasIndex)
  const missing = answer.length - given.length
  if (missing > 0) parts.push(missing === 1 ? `1 row has no ${key}` : `${missing} rows have no ${key}`)
  const seen = new Set<string>()
  const twice = onceEach(given, (value) => {
    const at = keyIdentity(value)
    const again = seen.has(at)
    seen.add(at)
    return again
  })
  if (twice.length > 0) parts.push(`${key} ${values(twice)} ${verb(twice)} given twice`)
  if (table.index_kind === null) {
    const held = new Set(request.filter(hasIndex).map(keyIdentity))
    const stray = onceEach(given, (value) => !held.has(keyIdentity(value)))
    if (stray.length > 0) parts.push(`${key} ${values(stray)} ${verb(stray)} on no row priced`)
  } else if (table.index_kind === "values") {
    const allowed = new Set(table.index_values.map(keyIdentity))
    const stray = onceEach(given, (value) => !allowed.has(keyIdentity(value)))
    if (stray.length > 0) parts.push(`${key} ${values(stray)} ${verb(stray)} none of its values`)
  } else {
    const long = onceEach(given, (value) => typeof value === "string" && textLength(value) > INDEX_TEXT_LENGTH)
    const cut = long.map((value) => [...(value as string)].slice(0, MISFIT_TEXT_SHOWN).join(""))
    if (long.length > 0) parts.push(`${key} ${values(cut)} ${verb(long)} longer than ${INDEX_TEXT_LENGTH} characters`)
  }
  return parts.length === 0 ? null : `${table.port} does not fit ${table.name}'s rows: ${parts.join("; ")}.`
}

/**
 * Whether an answer's value is one the quote store keeps (`holds_output`): neither null nor
 * a number that is not finite, which a JSON answer holds as the sentinel the server's
 * `to_json_safe` writes for it (`{"__haute_type__": "non_finite_float", "value": "nan"}`).
 */
export function holdsOutput(value: unknown): boolean {
  if (value === null || value === undefined) return false
  if (typeof value === "number") return Number.isFinite(value)
  return !(typeof value === "object" && (value as Record<string, unknown>).__haute_type__ === "non_finite_float")
}

/**
 * Whether a frame's column of *dtype*, as `str(dtype)` renders it, fills a column declared
 * *declared*: the backend's `column_fits`. A column of nulls fills any.
 */
export function dtypeFits(declared: WorkbenchColumn["type"], dtype: string): boolean {
  const family = castFamily(dtype)
  if (family === "null") return true
  switch (declared) {
    case "int":
      return family === "integer"
    case "float":
      return family === "integer" || family === "float" || family === "decimal"
    case "str":
      return family === "text" || family === "category"
    case "bool":
      return family === "boolean"
    case "date":
      return family === "date"
  }
}

/**
 * How a Workbench Output's column is filled from the frame connected to its table, which a
 * run fills with nulls unless it is `filled`; `unknown` while the frame's columns are; and
 * `haute` for the quote's reference and option, which the response gives whatever the frame
 * holds, and for a numbered index of outputs alone, which the Workbench Output numbers.
 */
export type ColumnFill = { kind: "filled" | "missing" | "unknown" | "haute" } | { kind: "mistyped"; dtype: string }

/**
 * Whether a table leads with the quote's reference and option: every table but
 * quote_details (`carries_identity` in `src/haute/_workbench_tables.py`). A table has no
 * ids, so the columns are known by their place, names and types.
 */
export const carriesIdentity = (table: WorkbenchTable): boolean =>
  table.name !== "quote_details" &&
  IDENTITY_COLUMNS.every(([name, type], at) => table.columns[at]?.name === name && table.columns[at]?.type === type)

/**
 * How *table*'s column at *at* is filled: `haute` for the quote's identity and for a numbered
 * index of outputs alone (`index_kind`), which the Workbench Output numbers whatever the frame
 * holds under its name; a typed index or an index of values of outputs alone, and an input
 * table's index carried on its output side, are filled from the frame as any column is:
 * `missing` while nothing is connected to its table or the frame has no column of exactly
 * its name, `mistyped` when the frame's column is of a type that does not fit, else `filled`.
 * *frameColumns* are the connected frame's columns as its last preview recorded them,
 * undefined until it has one.
 */
export function columnFill(
  table: WorkbenchTable,
  at: number,
  connected: boolean,
  frameColumns: readonly { name: string; dtype: string }[] | undefined,
): ColumnFill {
  const column = table.columns[at]
  if (at < IDENTITY_COLUMNS.length && carriesIdentity(table)) return { kind: "haute" }
  if (table.index_kind === "numbered" && column.name === table.key) return { kind: "haute" }
  if (!connected) return { kind: "missing" }
  if (frameColumns === undefined) return { kind: "unknown" }
  const held = frameColumns.find((each) => each.name === column.name)
  if (held === undefined) return { kind: "missing" }
  return dtypeFits(column.type, held.dtype) ? { kind: "filled" } : { kind: "mistyped", dtype: held.dtype }
}
