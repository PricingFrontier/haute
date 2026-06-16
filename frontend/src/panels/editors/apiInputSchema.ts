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

/** Options for {@link readV2}. */
export interface ReadV2Options {
  /**
   * Whether to DROP structurally-incomplete entries — tables with a
   * blank `path`, and columns with a blank `name` or `path`.
   *
   * - `false` (the **default**, used by every disk/render read path via
   *   {@link classifyConfig}): such entries are KEPT verbatim. A
   *   persisted entry that renders nowhere is still active at execute
   *   time and the user has no surface to repair it — dropping it on
   *   read and then re-serialising the filtered view on the next edit is
   *   silent data loss. The editor renders the kept entry in an invalid
   *   state (inline error) so it can be repaired or explicitly deleted.
   *   This upholds the 1:1 JSON↔UI render-gate invariant (every
   *   persisted entry must surface somewhere visible).
   *
   * - `true` (used ONLY for the Infer-Tables response): a malformed
   *   entry there is fresh backend output that was never user-persisted
   *   state, so discarding it before it reaches config is correct — we
   *   don't inject errored rows from an inference quirk.
   *
   * Asymmetry with the backend twin (`_api_input_schema.py`): the
   * backend has no read-into-structure step that could drop — it
   * `validate_v2_schema`-rejects a blank `name`/`path` LOUDLY (raises →
   * 422). Both sides refuse to silently drop; the frontend surfaces a
   * persisted blank via render+flag, the backend via loud rejection.
   */
  dropIncomplete?: boolean
}

/**
 * Read a v2 config from a generic record. Tolerant of partial shape.
 *
 * By default KEEPS structurally-incomplete tables/columns (blank
 * `path`/`name`) so the editor can surface them for repair; pass
 * `{ dropIncomplete: true }` on the infer path to discard them. See
 * {@link ReadV2Options.dropIncomplete}.
 */
export function readV2(
  config: Record<string, unknown>,
  options: ReadV2Options = {},
): ApiInputConfigV2 {
  const { dropIncomplete = false } = options
  const rawTables = Array.isArray((config as { tables?: unknown }).tables)
    ? ((config as { tables: unknown[] }).tables as unknown[])
    : []
  const tables: ApiInputTableV2[] = []
  for (const t of rawTables) {
    if (!t || typeof t !== "object") continue
    const tt = t as Record<string, unknown>
    const path = typeof tt.path === "string" ? tt.path : ""
    // Keep a blank label verbatim (mirrors `path` below and the column
    // `name`/`path`) so the editor surfaces it via validateTableLabel.
    // Only a MISSING label key defaults to `path` — the inference
    // convention where the label is simply omitted. A persisted `label:
    // ""` must NOT be masked as the path: it's backend-invalid (the label
    // is the runtime port name) and would otherwise render as valid and
    // be silently rewritten to the path on the next edit.
    const label = typeof tt.label === "string" ? tt.label : path
    // Drop a structurally-incomplete table (blank path OR blank label)
    // only on the infer path. On the disk/render path such a table is a
    // persisted entry that must surface (render-gate invariant) — the
    // editor flags the blank field (requireNonBlank / validateTableLabel).
    if (dropIncomplete && (!path || !label)) continue
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
      // Drop a blank-name/blank-path column only on the infer path. On
      // the disk/render path it's a persisted entry that must surface
      // (render-gate invariant) — the editor flags it via columnNameError
      // / requireNonBlank so the user repairs or deletes it instead of it
      // silently vanishing (and being re-serialised away on the next edit).
      if (dropIncomplete && (!cname || !cpath)) continue
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
