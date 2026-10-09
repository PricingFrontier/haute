/**
 * A Workbench Input takes its tables, and the sample quote its previews run on, from the
 * installed extension that supplies them, such as Obverse's Workbench (specs/extensions),
 * and a Workbench Output takes the response's tables from it. Their configs hold a copy,
 * so the pipeline runs without the extension; these rules say when a copy needs updating
 * and what each starts with from the palette. `useExtensionQuoteTables` applies them, and
 * `priceSample` gives them to the one request it prices the sample with.
 */
import type { ExtensionInfo } from "../api/types"
import { NODE_TYPES } from "./nodeTypes"

/** The supplier's tables and sample quote from one fetch, numbered so that only the newest counts. */
export interface QuoteTables {
  extension: string
  tables: readonly Record<string, unknown>[]
  /** One quote as a request holds it: `{}` for none. */
  sample: Readonly<Record<string, unknown>>
  /** The tables a priced quote fills in, which a Workbench Output fills: `[]` for none. */
  responseTables: readonly Record<string, unknown>[]
  fetch: number
}

/** The installed extension that supplies the Quote Input's tables; the server allows one. */
export function quoteTablesSupplier(extensions: readonly ExtensionInfo[]): ExtensionInfo | null {
  return extensions.find((extension) => extension.quote_tables) ?? null
}

/** The supplier, when it supplies the response's tables too. */
export function responseTablesSupplier(extensions: readonly ExtensionInfo[]): ExtensionInfo | null {
  const supplier = quoteTablesSupplier(extensions)
  return supplier?.response_tables ? supplier : null
}

/** JSON with every object's keys sorted, so the order of keys never reads as a change. */
function canonicalJson(value: unknown): string {
  return JSON.stringify(value ?? null, (_key, entry: unknown) =>
    entry !== null && typeof entry === "object" && !Array.isArray(entry)
      ? Object.fromEntries(Object.entries(entry).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
      : entry,
  )
}

/**
 * The update a Workbench Input's config needs to match the fetched tables and sample, or
 * null when none does. A config without a sample matches an empty one.
 */
export function quoteTablesPatch(
  config: Record<string, unknown>,
  quoteTables: Pick<QuoteTables, "tables" | "sample"> | null,
): { tables: Record<string, unknown>[]; sample: Record<string, unknown> } | null {
  if (quoteTables === null) return null
  if (
    canonicalJson(config.tables) === canonicalJson(quoteTables.tables) &&
    canonicalJson(config.sample ?? {}) === canonicalJson(quoteTables.sample)
  ) {
    return null
  }
  return { tables: structuredClone([...quoteTables.tables]), sample: structuredClone({ ...quoteTables.sample }) }
}

/** A Workbench Output's mapping: by table label, each column's frame column, or null for none. */
export type WorkbenchOutputMapping = Record<string, Record<string, string | null>>

/** The config's mapping, without its entries for tables and columns *tables* lack. */
function mappingFor(mapping: unknown, tables: readonly Record<string, unknown>[]): WorkbenchOutputMapping {
  const kept: WorkbenchOutputMapping = {}
  if (mapping === null || typeof mapping !== "object" || Array.isArray(mapping)) return kept
  for (const table of tables) {
    const label = table.label
    const entries = typeof label === "string" ? (mapping as Record<string, unknown>)[label] : undefined
    if (entries === null || typeof entries !== "object" || Array.isArray(entries)) continue
    const names = new Set(
      (Array.isArray(table.columns) ? table.columns : []).map((column) => (column as { name?: unknown }).name),
    )
    const columns = Object.fromEntries(
      Object.entries(entries as Record<string, string | null>).filter(([name]) => names.has(name)),
    )
    if (Object.keys(columns).length > 0) kept[label as string] = columns
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
  quoteTables: Pick<QuoteTables, "responseTables"> | null,
): { tables: Record<string, unknown>[]; mapping?: WorkbenchOutputMapping } | null {
  if (quoteTables === null) return null
  const mapping = mappingFor(config.mapping, quoteTables.responseTables)
  const mappingChanged = canonicalJson(config.mapping ?? {}) !== canonicalJson(mapping)
  if (canonicalJson(config.tables) === canonicalJson(quoteTables.responseTables) && !mappingChanged) return null
  return {
    tables: structuredClone([...quoteTables.responseTables]),
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
  quoteTables: QuoteTables | null,
): Record<string, unknown> {
  return {
    ...defaultConfig,
    tables: quoteTables === null ? [] : structuredClone([...quoteTables.tables]),
    sample: quoteTables === null ? {} : structuredClone({ ...quoteTables.sample }),
  }
}

/** The config a Workbench Output dragged from the palette starts with: the newest response tables fetched. */
export function paletteWorkbenchOutputConfig(
  defaultConfig: Record<string, unknown>,
  quoteTables: QuoteTables | null,
): Record<string, unknown> {
  return {
    ...defaultConfig,
    tables: quoteTables === null ? [] : structuredClone([...quoteTables.responseTables]),
  }
}

/** A Workbench Output's ports: its tables' labels, in order. */
export function workbenchOutputTableLabels(config: Record<string, unknown> | undefined | null): string[] {
  const tables = config?.tables
  if (!Array.isArray(tables)) return []
  return tables.flatMap((table: unknown) => {
    const label = (table as { label?: unknown } | null)?.label
    return typeof label === "string" && label.length > 0 ? [label] : []
  })
}
