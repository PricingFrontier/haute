/**
 * A Workbench Input takes its tables, and the sample quote its previews run on, from the
 * project's workbench (specs/workbench), and a Workbench Output takes the response's
 * tables from it. Their configs hold a copy, so the pipeline runs without the form; these
 * rules say how a copy is read, which of its tables are ports, when it needs updating and
 * what each starts with from the palette. `useWorkbenchTables` applies them. A Workbench
 * Output's panel also judges, by the same rule as a run, how a frame fills its columns.
 */
import type { WorkbenchColumn, WorkbenchTable } from "../api/types"
import { canonicalJson } from "./canonicalJson"
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

const COLUMN_TYPES: readonly WorkbenchColumn["type"][] = ["int", "float", "str", "bool", "date"]

function isColumn(value: unknown): value is WorkbenchColumn {
  if (value === null || typeof value !== "object") return false
  const { name, type } = value as { name?: unknown; type?: unknown }
  return typeof name === "string" && (COLUMN_TYPES as readonly unknown[]).includes(type)
}

/**
 * The tables a workbench node's config holds, as the editor reads them: each with its name,
 * its rows per quote and its columns, each with a name and a type. An entry that is not one
 * is left out here; the server refuses the config when the pipeline runs.
 */
export function readWorkbenchTables(config: Record<string, unknown> | undefined | null): WorkbenchTable[] {
  const tables = config?.tables
  if (!Array.isArray(tables)) return []
  return tables.flatMap((entry: unknown): WorkbenchTable[] => {
    if (entry === null || typeof entry !== "object") return []
    const { name, rows, columns } = entry as { name?: unknown; rows?: unknown; columns?: unknown }
    if (typeof name !== "string" || (rows !== "one" && rows !== "many") || !Array.isArray(columns)) return []
    return [{ name, rows, columns: columns.filter(isColumn).map(({ name: column, type }) => ({ name: column, type })) }]
  })
}

/**
 * A workbench node's ports: its tables with a column, by name, in order, a name counting
 * once. A table without a column has no frame to read into or to fill, so no port.
 */
export function workbenchTablePorts(config: Record<string, unknown> | undefined | null): string[] {
  const ports: string[] = []
  for (const table of readWorkbenchTables(config)) {
    if (table.columns.length > 0 && !ports.includes(table.name)) ports.push(table.name)
  }
  return ports
}

/** JSON with every object's keys sorted, so the order of keys never reads as a change. */
/**
 * The update a Workbench Input's config needs to match the fetched tables and sample, or
 * null when none does. A config without a sample matches an empty one.
 */
export function quoteTablesPatch(
  config: Record<string, unknown>,
  workbench: Pick<WorkbenchTables, "tables" | "sample"> | null,
): { tables: WorkbenchTable[]; sample: Record<string, unknown> } | null {
  if (workbench === null) return null
  if (
    canonicalJson(config.tables) === canonicalJson(workbench.tables) &&
    canonicalJson(config.sample ?? {}) === canonicalJson(workbench.sample)
  ) {
    return null
  }
  return { tables: structuredClone([...workbench.tables]), sample: structuredClone({ ...workbench.sample }) }
}

/** The sample priced on the pipeline: each of the Workbench Output's tables' rows, by the table's name. */
export interface PricedSample {
  tables: Record<string, Record<string, unknown>[]>
}

/**
 * The update a Workbench Output's config needs to match the fetched response tables, or
 * null when none does.
 */
export function responseTablesPatch(
  config: Record<string, unknown>,
  workbench: Pick<WorkbenchTables, "responseTables"> | null,
): { tables: WorkbenchTable[] } | null {
  if (workbench === null) return null
  if (canonicalJson(config.tables) === canonicalJson(workbench.responseTables)) return null
  return { tables: structuredClone([...workbench.responseTables]) }
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
 * Whether a node's copy of a table leads with the quote's reference and option: every table
 * but quote_details (`carries_identity` in `src/haute/_workbench_tables.py`). A copy has no
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

/** The update a workbench node's config needs from a fetch, by its type; others take none. */
export const WORKBENCH_COPY_PATCHES: Readonly<Record<string, typeof quoteTablesPatch | typeof responseTablesPatch>> = {
  [NODE_TYPES.WORKBENCH_INPUT]: quoteTablesPatch,
  [NODE_TYPES.WORKBENCH_OUTPUT]: responseTablesPatch,
}

/** The config a Workbench Input dragged from the palette starts with: the newest tables and sample fetched. */
export function paletteWorkbenchInputConfig(
  defaultConfig: Record<string, unknown>,
  workbench: WorkbenchTables | null,
): Record<string, unknown> {
  return {
    ...defaultConfig,
    tables: workbench === null ? [] : structuredClone([...workbench.tables]),
    sample: workbench === null ? {} : structuredClone({ ...workbench.sample }),
  }
}

/** The config a Workbench Output dragged from the palette starts with: the newest response tables fetched. */
export function paletteWorkbenchOutputConfig(
  defaultConfig: Record<string, unknown>,
  workbench: WorkbenchTables | null,
): Record<string, unknown> {
  return {
    ...defaultConfig,
    tables: workbench === null ? [] : structuredClone([...workbench.responseTables]),
  }
}
