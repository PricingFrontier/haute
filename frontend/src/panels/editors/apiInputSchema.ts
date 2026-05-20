/**
 * v2 schema-mapping helpers for the ApiInputEditor (commit 5).
 *
 * Mirrors `src/haute/_api_input_schema.py` on the backend. Identifies v2-shape
 * configs, exposes typed access to the tables/columns, and migrates v1
 * (`flattenSchema`) configs to v2 in-memory so the editor can render them
 * with the new surface without rewriting disk state until the user saves.
 *
 * Migration policy mirrors §4d of MULTI_FRAME_PLAN.md:
 * - One table at the root path (`$[*]`) with `emit: true`.
 * - v1 `flattenSchema` leaves become per-column entries; column path is
 *   `$[*].<leaf>`; column name defaults to the leaf (or to the renamed
 *   target if the v1 config had a `column_renames` entry — orphans dropped).
 * - v1 `selected_columns` lift into per-column `selected: true`.
 * - v1 `categorical_levels` lift into per-column `levels`.
 * - v1 `row_id_column` lifts into `tables[0].row_id_column` (dropped if
 *   it doesn't match any migrated column name).
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
  removedTables?: string[]
}

/** Tagged-union representation of a config — used by the editor's first-load
 * detection so it can render the migration banner without losing access to
 * the v1 fields it needs to preserve. */
export type ApiInputConfigShape =
  | { kind: "v2"; v2: ApiInputConfigV2 }
  | { kind: "v1"; raw: Record<string, unknown> }
  | { kind: "empty"; raw: Record<string, unknown> }

const ALLOWED_TYPES: ReadonlySet<ColumnType> = new Set([
  "int",
  "float",
  "str",
  "bool",
  "date",
] as const)

/**
 * Return true iff *config* is in v2 shape. Mirrors the backend's
 * `is_v2_shape`: v2 if `tables` is present AND `flattenSchema` is absent.
 * A config carrying both is corrupt — treat as v1 for safety so the
 * migration path runs.
 */
export function isV2Shape(config: Record<string, unknown> | undefined | null): boolean {
  if (!config) return false
  const hasTables = Array.isArray((config as { tables?: unknown }).tables)
  const hasFlatten =
    typeof (config as { flattenSchema?: unknown }).flattenSchema === "object" &&
    (config as { flattenSchema?: unknown }).flattenSchema !== null
  return hasTables && !hasFlatten
}

/**
 * Classify a config for the editor: v2, v1 (needs migration), or empty
 * (a freshly-added apiInput with nothing on disk yet — the editor shows
 * a clean v2 surface with no tables).
 */
export function classifyConfig(config: Record<string, unknown> | undefined | null): ApiInputConfigShape {
  if (!config || Object.keys(config).length === 0) {
    return { kind: "empty", raw: config ?? {} }
  }
  if (isV2Shape(config)) {
    return { kind: "v2", v2: readV2(config as Record<string, unknown>) }
  }
  return { kind: "v1", raw: config }
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
    removedTables: Array.isArray((config as { removedTables?: unknown }).removedTables)
      ? ((config as { removedTables: string[] }).removedTables)
      : undefined,
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
  if (v2.removedTables && v2.removedTables.length > 0) {
    out.removedTables = v2.removedTables
  }
  return out
}

/**
 * Convert a v1 config to v2 in-memory shape. Mirrors the backend's
 * `legacy_to_v2`. Orphan entries (renames / row_id pointing at a leaf
 * that doesn't appear in flattenSchema) are silently dropped — the
 * caller surfaces the migration banner to the user.
 */
export function legacyToV2(config: Record<string, unknown>): ApiInputConfigV2 {
  const flattenSchema = (config.flattenSchema as Record<string, string> | undefined) ?? {}
  const columnRenames = (config.column_renames as Record<string, string> | undefined) ?? {}
  const selectedColumns =
    Array.isArray(config.selected_columns) ? (config.selected_columns as string[]) : []
  const selectedSet = new Set(selectedColumns)
  const categoricalLevels =
    (config.categorical_levels as Record<string, (string | null)[]> | undefined) ?? {}
  const rowIdColumn = typeof config.row_id_column === "string" ? config.row_id_column : null

  const columns: ApiInputColumnV2[] = []
  for (const [leafPath, leafType] of Object.entries(flattenSchema)) {
    if (typeof leafPath !== "string" || typeof leafType !== "string") continue
    const renameTarget = leafPath in columnRenames ? columnRenames[leafPath] : undefined
    const name = renameTarget && renameTarget.length > 0 ? renameTarget : leafPath
    const type = ALLOWED_TYPES.has(leafType as ColumnType) ? (leafType as ColumnType) : "str"
    const col: ApiInputColumnV2 = {
      name,
      path: `$[*].${leafPath}`,
      type,
      status: "Confirmed",
      selected: selectedSet.size === 0 || selectedSet.has(leafPath),
      levels: null,
    }
    const levels = categoricalLevels[leafPath]
    if (Array.isArray(levels) && levels.length > 0) {
      col.levels = levels
    }
    columns.push(col)
  }

  const table: ApiInputTableV2 = {
    path: "$[*]",
    label: "$[*]",
    displayPath: null,
    emit: true,
    columns,
  }
  if (rowIdColumn && columns.some((c) => c.name === rowIdColumn)) {
    table.row_id_column = rowIdColumn
  }

  return {
    path: typeof config.path === "string" ? config.path : "",
    contract: typeof config.contract === "string" ? config.contract : "opaque",
    tables: [table],
  }
}

/** Empty v2 config — used when the editor opens against a brand-new apiInput. */
export function emptyV2(currentPath?: string): ApiInputConfigV2 {
  return {
    path: currentPath ?? "",
    contract: "opaque",
    tables: [],
  }
}
