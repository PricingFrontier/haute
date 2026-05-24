/**
 * v2 schema-mapping helpers for the ApiInputEditor.
 *
 * Mirrors `src/haute/_api_input_schema.py` on the backend. Identifies
 * v2-shape configs and exposes typed access to the tables/columns.
 *
 * v1 configs on disk are treated as **empty** at runtime — there is no
 * migration codec in the editor any more. The user opens the editor
 * against a v1 file, sees the empty v2 surface, clicks Infer Tables,
 * and saves. The v1 keys silently fall off when the strict v2 contract
 * serialises (see backend Pydantic config model).
 */

export type ColumnType = "int" | "float" | "str" | "bool" | "date"
export type ColumnStatus = "Confirmed" | "Inferred"

export interface ApiInputColumnV2 {
  name: string
  path: string
  type: ColumnType
  status: ColumnStatus
  selected: boolean
  levels?: (string | null)[] | null
}

export interface ApiInputTableV2 {
  path: string
  label: string
  displayPath?: string | null
  emit: boolean
  row_id_column?: string | null
  columns: ApiInputColumnV2[]
}

export interface ApiInputConfigV2 {
  path?: string
  contract?: string
  tables: ApiInputTableV2[]
  // Bundle 1 sanitisation: `removedTables` was specified as an
  // editor-side ledger of deleted table labels but never wired
  // (inferTables clobbers `tables` directly). User deletion of
  // tables MUST NOT permanently alter Infer Tables behaviour, so the
  // field is dropped here, in writeV2 (not emitted), in readV2 (not
  // surfaced), and in the Python TypedDicts. Legacy on-disk configs
  // carrying it are silently ignored on read. Contract:
  // frontend/src/__tests__/editors/apiInputSchemaSanitisation.test.ts.
}

/** Tagged-union classification — only v2 and empty kinds. */
export type ApiInputConfigShape =
  | { kind: "v2"; v2: ApiInputConfigV2 }
  | { kind: "empty"; raw: Record<string, unknown> }

const ALLOWED_TYPES: ReadonlySet<ColumnType> = new Set([
  "int",
  "float",
  "str",
  "bool",
  "date",
] as const)

/**
 * v2 shape iff `tables` is a non-empty (or at least present) array.
 * Stray legacy keys alongside (`flattenSchema`, `column_renames`, …)
 * are tolerated silently — the runtime reads only the v2 surface, and
 * a strict v2 serialiser at save time drops unknown keys.
 */
export function isV2Shape(config: Record<string, unknown> | undefined | null): boolean {
  if (!config) return false
  return Array.isArray((config as { tables?: unknown }).tables)
}

/**
 * Classify a config for the editor: v2 or empty.
 *
 * Anything without a `tables[]` array — including v1 configs that only
 * have `flattenSchema` — is treated as empty. The editor renders the
 * v2 surface and the user clicks Infer Tables.
 */
export function classifyConfig(
  config: Record<string, unknown> | undefined | null,
): ApiInputConfigShape {
  if (isV2Shape(config)) {
    return { kind: "v2", v2: readV2(config as Record<string, unknown>) }
  }
  return { kind: "empty", raw: config ?? {} }
}

/** Read a v2 config from a generic record. Tolerant of partial shape. */
export function readV2(config: Record<string, unknown>): ApiInputConfigV2 {
  const rawTables = Array.isArray((config as { tables?: unknown }).tables)
    ? ((config as { tables: unknown[] }).tables as unknown[])
    : []
  const tables: ApiInputTableV2[] = []
  for (const t of rawTables) {
    if (!t || typeof t !== "object") continue
    const tt = t as Record<string, unknown>
    const path = typeof tt.path === "string" ? tt.path : ""
    if (!path) continue
    const label = typeof tt.label === "string" && tt.label ? tt.label : path
    const emit = tt.emit === true
    const displayPath = typeof tt.displayPath === "string" ? tt.displayPath : null
    const row_id_column =
      typeof tt.row_id_column === "string" ? tt.row_id_column : null
    const rawCols = Array.isArray(tt.columns) ? (tt.columns as unknown[]) : []
    const columns: ApiInputColumnV2[] = []
    for (const c of rawCols) {
      if (!c || typeof c !== "object") continue
      const cc = c as Record<string, unknown>
      const cname = typeof cc.name === "string" ? cc.name : ""
      const cpath = typeof cc.path === "string" ? cc.path : ""
      if (!cname || !cpath) continue
      const ctype = (
        typeof cc.type === "string" && ALLOWED_TYPES.has(cc.type as ColumnType)
          ? cc.type
          : "str"
      ) as ColumnType
      const cstatus =
        cc.status === "Confirmed" ? "Confirmed" : "Inferred"
      const cselected = cc.selected !== false
      const clevels =
        cc.levels === null || cc.levels === undefined
          ? null
          : Array.isArray(cc.levels)
          ? (cc.levels as (string | null)[])
          : null
      columns.push({
        name: cname,
        path: cpath,
        type: ctype,
        status: cstatus,
        selected: cselected,
        levels: clevels,
      })
    }
    tables.push({
      path,
      label,
      displayPath,
      emit,
      row_id_column,
      columns,
    })
  }
  return {
    path: typeof (config as { path?: unknown }).path === "string"
      ? ((config as { path: string }).path)
      : undefined,
    contract: typeof (config as { contract?: unknown }).contract === "string"
      ? ((config as { contract: string }).contract)
      : "opaque",
    tables,
    // Sanitisation contract: any `removedTables` in the raw input is
    // silently dropped here (no surface, no error). See the comment on
    // ApiInputConfigV2 above for the full rationale.
  }
}

/** Serialise a v2 config back into the raw shape persisted to disk. */
export function writeV2(v2: ApiInputConfigV2): Record<string, unknown> {
  const out: Record<string, unknown> = {
    path: v2.path ?? "",
    contract: v2.contract ?? "opaque",
    tables: v2.tables.map((t) => ({
      path: t.path,
      label: t.label,
      displayPath: t.displayPath ?? null,
      emit: t.emit,
      row_id_column: t.row_id_column ?? null,
      columns: t.columns.map((c) => ({
        name: c.name,
        path: c.path,
        type: c.type,
        status: c.status,
        selected: c.selected,
        levels: c.levels ?? null,
      })),
    })),
  }
  // Sanitisation contract: `removedTables` is never emitted, even if
  // some upstream caller smuggled it in via an unsafe cast. See the
  // comment on ApiInputConfigV2 above for the full rationale.
  return out
}

/** Empty v2 config — used when the editor opens against a brand-new apiInput. */
export function emptyV2(currentPath?: string): ApiInputConfigV2 {
  return {
    path: currentPath ?? "",
    contract: "opaque",
    tables: [],
  }
}
