/**
 * A Workbench Input takes its tables, and the sample quote its previews run on, from the
 * project's workbench (specs/workbench), and a Workbench Output takes the response's
 * tables from it. Their configs hold a copy, so the pipeline runs without the form; these
 * rules say how a copy is read, which of its tables are ports, when it needs updating and
 * what each starts with from the palette. `useWorkbenchTables` applies them.
 */
import type { WorkbenchColumn, WorkbenchTable } from "../api/types"
import { canonicalJson } from "./canonicalJson"
import { NODE_TYPES } from "./nodeTypes"

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

/** A Workbench Output's mapping: by table name, each column's frame column, or null for none. */
export type WorkbenchOutputMapping = Record<string, Record<string, string | null>>

/** The sample priced on the pipeline: each of the Workbench Output's tables' rows, by the table's name. */
export interface PricedSample {
  tables: Record<string, Record<string, unknown>[]>
}

/** The config's mapping, without its entries for tables and columns *tables* lack. */
function mappingFor(mapping: unknown, tables: readonly WorkbenchTable[]): WorkbenchOutputMapping {
  const kept: WorkbenchOutputMapping = {}
  if (mapping === null || typeof mapping !== "object" || Array.isArray(mapping)) return kept
  for (const table of tables) {
    const entries = (mapping as Record<string, unknown>)[table.name]
    if (entries === null || typeof entries !== "object" || Array.isArray(entries)) continue
    const names = new Set(table.columns.map((column) => column.name))
    const columns = Object.fromEntries(
      Object.entries(entries as Record<string, string | null>).filter(([name]) => names.has(name)),
    )
    if (Object.keys(columns).length > 0) kept[table.name] = columns
  }
  return kept
}

/**
 * The update a Workbench Output's config needs to match the fetched response tables, or
 * null when none does: the tables, and the mapping without the entries of tables and
 * columns they no longer have.
 */
export function responseTablesPatch(
  config: Record<string, unknown>,
  workbench: Pick<WorkbenchTables, "responseTables"> | null,
): { tables: WorkbenchTable[]; mapping?: WorkbenchOutputMapping } | null {
  if (workbench === null) return null
  const mapping = mappingFor(config.mapping, workbench.responseTables)
  const mappingChanged = canonicalJson(config.mapping ?? {}) !== canonicalJson(mapping)
  if (canonicalJson(config.tables) === canonicalJson(workbench.responseTables) && !mappingChanged) return null
  return {
    tables: structuredClone([...workbench.responseTables]),
    ...(mappingChanged ? { mapping } : {}),
  }
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
