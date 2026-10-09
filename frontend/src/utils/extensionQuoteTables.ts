/**
 * A Workbench Input takes its tables, and the sample quote its previews run on, from the
 * installed extension that supplies them, such as Obverse's Workbench (specs/extensions).
 * Its config holds a copy of both, so the pipeline runs without the extension; these
 * rules say when the copy needs updating and what a Workbench Input from the palette
 * starts with. `useExtensionQuoteTables` applies them.
 */
import type { ExtensionInfo } from "../api/types"

/** The supplier's tables and sample quote from one fetch, numbered so that only the newest counts. */
export interface QuoteTables {
  extension: string
  tables: readonly Record<string, unknown>[]
  /** One quote as a request holds it: `{}` for none. */
  sample: Readonly<Record<string, unknown>>
  fetch: number
}

/** The installed extension that supplies the Quote Input's tables; the server allows one. */
export function quoteTablesSupplier(extensions: readonly ExtensionInfo[]): ExtensionInfo | null {
  return extensions.find((extension) => extension.quote_tables) ?? null
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
  quoteTables: QuoteTables | null,
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
