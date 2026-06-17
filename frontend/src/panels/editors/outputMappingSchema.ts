/**
 * v2 mapping helpers for the OutputEditor.
 *
 * Mirrors `src/haute/_types.py` (OutputMappingEntry / OutputConfig) and the
 * grammar enforced by `src/haute/_output_assembler.py::_parse_output_path`.
 *
 * The OUTPUT node config moved from v1 `{ fields: string[] }` to v2
 * `{ outputMapping: Entry[], outputFormat: string }`. The backend now REJECTS
 * any OUTPUT config that lacks `outputMapping`, so a v1 config on disk is
 * broken until migrated. Unlike the apiInput editor (which treats v1 as
 * empty), this editor surfaces a migration banner and converts v1 → v2 on the
 * first Save via {@link migrateV1}.
 *
 * On-disk identifier note: the persisted field is `source_port` (kept exactly
 * — never renamed). Only UI LABEL STRINGS say "frame".
 */

export type OutputRowStatus = "Confirmed" | "Inferred"

/** One persisted mapping row. The four-field on-disk shape — nothing else is
 * ever serialised (`status` lives in editor state only, see OutputEditor). */
export interface OutputMappingEntryV2 {
  /** Incoming-frame identifier. KEEP this name — it is the on-disk key. */
  source_port: string
  source_column: string
  output_path: string
  enabled: boolean
}

export interface OutputConfigV2 {
  outputMapping: OutputMappingEntryV2[]
  outputFormat: string // "json" only for now (jsonl/jsonseq later)
}

/** Tagged-union classification of a raw OUTPUT config. */
export type OutputConfigShape =
  | { kind: "v2"; v2: OutputConfigV2 }
  | { kind: "v1"; fields: string[] }
  | { kind: "empty"; raw: Record<string, unknown> }

/**
 * Classify a raw config:
 *   - v2    — has an `outputMapping` array (authoritative; v1 residue ignored)
 *   - v1    — has a `fields` array and NO `outputMapping`
 *   - empty — neither
 */
export function classifyConfig(
  config: Record<string, unknown> | undefined | null,
): OutputConfigShape {
  if (!config) return { kind: "empty", raw: {} }
  const hasMapping = Array.isArray((config as { outputMapping?: unknown }).outputMapping)
  if (hasMapping) {
    return { kind: "v2", v2: readV2(config as Record<string, unknown>) }
  }
  const rawFields = (config as { fields?: unknown }).fields
  if (Array.isArray(rawFields)) {
    const fields = rawFields.filter((f): f is string => typeof f === "string")
    return { kind: "v1", fields }
  }
  return { kind: "empty", raw: config }
}

/** Read a v2 config from a generic record. Tolerant of partial shape. */
export function readV2(config: Record<string, unknown>): OutputConfigV2 {
  const rawEntries = Array.isArray((config as { outputMapping?: unknown }).outputMapping)
    ? ((config as { outputMapping: unknown[] }).outputMapping as unknown[])
    : []
  const outputMapping: OutputMappingEntryV2[] = []
  for (const e of rawEntries) {
    if (!e || typeof e !== "object") continue
    const ee = e as Record<string, unknown>
    const source_port = typeof ee.source_port === "string" ? ee.source_port : ""
    const source_column = typeof ee.source_column === "string" ? ee.source_column : ""
    const output_path = typeof ee.output_path === "string" ? ee.output_path : ""
    const enabled = ee.enabled !== false
    outputMapping.push({ source_port, source_column, output_path, enabled })
  }
  const fmt = (config as { outputFormat?: unknown }).outputFormat
  return {
    outputMapping,
    outputFormat: typeof fmt === "string" && fmt ? fmt : "json",
  }
}

/** Serialise a v2 config back to the raw shape persisted to disk. Emits ONLY
 * the four entry fields + outputFormat — never the editor-only row status, and
 * never any v1 `fields` residue. */
export function writeV2(v2: OutputConfigV2): Record<string, unknown> {
  return {
    outputMapping: v2.outputMapping.map((e) => ({
      source_port: e.source_port,
      source_column: e.source_column,
      output_path: e.output_path,
      enabled: e.enabled,
    })),
    outputFormat: v2.outputFormat || "json",
  }
}

/** Empty v2 config — used when the editor opens against a brand-new OUTPUT. */
export function emptyV2(): OutputConfigV2 {
  return { outputMapping: [], outputFormat: "json" }
}

/**
 * Migrate a v1 `{ fields: string[] }` config to v2. Each former field becomes
 * one entry with the source column = the field name, a whole-array path
 * `$[:].<field>`, and the single incoming frame's resolved `source_port`.
 *
 * v1 had no notion of which frame a field came from (single-frame assumption),
 * so the caller passes in `frameId` — the resolved `source_port` of the lone
 * incoming edge (its `sourceHandle`, else the sanitised source-node label),
 * matching the backend's `edge.sourceHandle or sanitize(node-label)` key. This
 * must be a NON-EMPTY, backend-derivable id so a genuine resolve lines up; it
 * mirrors `framePortId` in OutputEditor. When the editor can't see the graph
 * (no `frameId`) it falls back to `""`, relying on the backend's single-frame
 * n==1 rescue (`_build_output`) to bind the lone frame regardless of its name.
 */
export function migrateV1(config: Record<string, unknown>, frameId = ""): OutputConfigV2 {
  const rawFields = (config as { fields?: unknown }).fields
  const fields = Array.isArray(rawFields)
    ? rawFields.filter((f): f is string => typeof f === "string")
    : []
  return {
    outputMapping: fields.map((field) => ({
      source_port: frameId,
      source_column: field,
      output_path: `$[:].${field}`,
      enabled: true,
    })),
    outputFormat: "json",
  }
}

/**
 * Shallow grammar check for an output path, mirroring the backend
 * `_parse_output_path`. Returns an error string, or null when the path is
 * accepted. The backend is the authority; this is best-effort surfacing so the
 * user sees a rejection in the editor rather than as a 422 on save.
 *
 * Accepted subset:
 *   - must start with `$`
 *   - optional root array selector `[:]` immediately after `$`
 *   - dot-name segments: `.name` (name = [A-Za-z_][A-Za-z0-9_]*)
 *   - bracket-name segments: `['name']` / `["name"]`
 *   - the whole-array selector `[:]` may follow any segment name
 *   - must name at least one leaf segment (not the bare root array)
 *
 * Rejected (mirrors the backend): index `[0]`, ranges `[1:2]`, filters
 * `[?(...)]`, wildcards `[*]` / `.*`, descendant `..`, the bare `*`.
 */
const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*/
const DOT_NAME_RE = /^\.([A-Za-z_][A-Za-z0-9_]*)/
const BRACKET_NAME_RE = /^\[(['"])([^'"]+)\1\]/

export function validateOutputPath(path: string): string | null {
  if (!path.startsWith("$")) {
    return "Output path must start with '$'."
  }
  let i = 1
  if (path.slice(i, i + 3) === "[:]") {
    i += 3
  }
  let segments = 0
  while (i < path.length) {
    const ch = path[i]
    if (ch === ".") {
      const m = DOT_NAME_RE.exec(path.slice(i))
      if (m === null) {
        return "Unsupported selector — only '.name', \"['name']\", and the whole-array '[:]' are accepted."
      }
      i += m[0].length
    } else if (ch === "[") {
      const m = BRACKET_NAME_RE.exec(path.slice(i))
      if (m === null) {
        return "Unsupported array selector — index/range/filter/wildcard are rejected; use '[:]' for the whole array."
      }
      i += m[0].length
    } else {
      return "Malformed output path."
    }
    // An optional whole-array selector after the segment name.
    if (path.slice(i, i + 3) === "[:]") {
      i += 3
    }
    segments += 1
  }
  if (segments === 0) {
    return "Output path must name a leaf field, not the bare root array."
  }
  return null
}

/** True when `path` contains a `[:]` whole-array selector anywhere. The
 * Auto-map and Add-row affordances always emit the `$[:].<col>` form; this
 * guards against a user committing a path with no array selector. */
export function hasArraySelector(path: string): boolean {
  return path.includes("[:]")
}

// `NAME_RE` is retained for callers/tests that want to validate a bare
// column-name segment; not used internally above.
export { NAME_RE }
