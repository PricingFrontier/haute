/**
 * A Workbench Input takes its tables, and the sample quote its previews run on, from the
 * project's workbench (specs/workbench), and a Workbench Output takes the response's
 * tables from it. Neither node holds them: the editor fetches them (`useWorkbenchStore`),
 * and these rules say which of them are a node's ports and which connections a fetch
 * leaves without a table. A Workbench Output's panel also judges, by the same rule as a
 * run, how a frame fills its columns.
 */
import type { Edge } from "@xyflow/react"
import type { WorkbenchColumn, WorkbenchTable } from "../api/types"
import type { SubmodelBoundaryEdgeData } from "../types/node"
import { NODE_TYPES } from "./nodeTypes"
import { castFamily } from "./polarsDtypes"
import { IDENTITY_COLUMNS } from "./quoteIdentity"

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
 * Tables' ports: those with a column, by name, in order, a name counting once. A table
 * without a column has no frame to read into or to fill, so no port.
 */
export function workbenchTablePorts(tables: readonly WorkbenchTable[]): string[] {
  const ports: string[] = []
  for (const table of tables) {
    if (table.columns.length > 0 && !ports.includes(table.name)) ports.push(table.name)
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
 * The connections in *graph* to a table *workbench* does not have, by the node that loses
 * them: each out of a Workbench Input whose `sourceHandle` is none of its ports, and each
 * into a Workbench Output whose `targetHandle` is none of its. A connection follows its
 * table's name however the tables were reordered or replaced. One through a submodel's
 * boundary is the definition's port, which only the boundary's own editing changes, so it
 * is left for the run to refuse, naming its table. Null when every connection has its table.
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

/** The sample priced on the pipeline: each of the Workbench Output's tables' rows, by the table's name. */
export interface PricedSample {
  tables: Record<string, Record<string, unknown>[]>
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
 * holds.
 */
export type ColumnFill = { kind: "filled" | "missing" | "unknown" | "haute" } | { kind: "mistyped"; dtype: string }

/**
 * Whether a table leads with the quote's reference and option: every table but
 * quote_details (`carries_identity` in `src/haute/_workbench_tables.py`). A table has no
 * ids, so the columns are known by their place, names and types.
 */
export const carriesIdentity = (table: WorkbenchTable): boolean =>
  table.name !== "quote_details" &&
  IDENTITY_COLUMNS.every(([, name, type], at) => table.columns[at]?.name === name && table.columns[at]?.type === type)

/**
 * How *table*'s column at *at* is filled: `haute` for the quote's identity, `missing` while
 * nothing is connected to its table or the frame has no column of exactly its name,
 * `mistyped` when the frame's column is of a type that does not fit, else `filled`.
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
  if (!connected) return { kind: "missing" }
  if (frameColumns === undefined) return { kind: "unknown" }
  const held = frameColumns.find((each) => each.name === column.name)
  if (held === undefined) return { kind: "missing" }
  return dtypeFits(column.type, held.dtype) ? { kind: "filled" } : { kind: "mistyped", dtype: held.dtype }
}
