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

import { parsePath, validateOutputPathCore } from "./jsonpath"

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
    // Preserve the recorded format verbatim; do NOT default to "json" — an
    // unset format surfaces the "-- select output format --" placeholder so the
    // editor never bakes in a format choice (non-opinionated; jsonl/jsonseq
    // arrive later). The backend is lenient (assembler only does JSON today).
    outputFormat: typeof fmt === "string" ? fmt : "",
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
    // Written verbatim — "" when the user hasn't picked a format yet (the
    // backend tolerates a missing/empty format; only JSON is built today).
    outputFormat: v2.outputFormat ?? "",
  }
}

/** Empty v2 config — used when the editor opens against a brand-new OUTPUT. The
 * format starts UNSET so the dropdown shows "-- select output format --" rather
 * than silently choosing JSON (non-opinionated). */
export function emptyV2(): OutputConfigV2 {
  return { outputMapping: [], outputFormat: "" }
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
 * Grammar check for an output path. Returns an error string, or null when the
 * path is accepted. The backend is the authority; this is best-effort surfacing
 * so the user sees a rejection in the editor rather than as a 422 on save.
 *
 * The grammar itself now lives in the shared frontend lynchpin
 * ({@link validateOutputPathCore} in `jsonpath.ts`, the mirror of
 * `_jsonpath.py`) — OUTPUT and INPUT route through the one module so the
 * acceptance surface (selectors accepted, §3 rejections, identifier charset,
 * the `['name']` → `.name` bracket normalisation) can no longer drift. This
 * thin wrapper keeps OUTPUT's existing capitalised, period-terminated error
 * messages while delegating the actual accept/reject decision to the core.
 *
 * Accepted subset (core grammar + the §3 root gate):
 *   - must start with the root array `$[:]` (PATH_GRAMMAR.md §3 — every OUTPUT
 *     path enters the array-outer document through `$[:]`; a non-array root like
 *     `$.x` or `$.values[:].a` is rejected, symmetric with INPUT)
 *   - dot-name segments: `.name` (name = [A-Za-z_][A-Za-z0-9_]*)
 *   - bracket-name segments: `['name']` / `["name"]`
 *   - the whole-array selector `[:]` may follow any segment name
 *   - must name at least one leaf segment (not the bare root array)
 *
 * Rejected (mirrors the backend): a non-`$[:]` root, index `[0]`, ranges
 * `[1:2]`, filters `[?(...)]`, wildcards `[*]` / `.*`, descendant `..`, the bare
 * `*`.
 */
const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*/

export function validateOutputPath(path: string): string | null {
  // Delegate the accept/reject decision to the shared grammar core; re-skin its
  // backend-style messages as OUTPUT's existing user-facing strings so the
  // editor UX is unchanged.
  const core = validateOutputPathCore(path)
  if (core === null) {
    // §3 root gate, mirror of the backend `_parse_output_path` check: the core
    // grammar records the root but leaves the decision here. A grammatically
    // valid non-array root (`$.x`, `$.values[:].a`) does not reliably assemble
    // into array-outer JSON, so reject it in-editor rather than as a save-time
    // 422. `validateOutputPathCore` returned null, so this re-parse cannot throw.
    if (!parsePath(path).rootArray) {
      return "Output path must start with the root array '$[:]', e.g. $[:].field."
    }
    return null
  }
  if (core.startsWith("output path must start with '$'")) {
    return "Output path must start with '$'."
  }
  if (core.startsWith("unsupported output-path selector")) {
    return "Unsupported selector — only '.name', \"['name']\", and the whole-array '[:]' are accepted."
  }
  if (core.startsWith("unsupported array selector")) {
    return "Unsupported array selector — index/range/filter/wildcard are rejected; use '[:]' for the whole array."
  }
  if (core.startsWith("output path must name a leaf field")) {
    return "Output path must name a leaf field, not the bare root array."
  }
  return "Malformed output path."
}

// `NAME_RE` is retained for callers/tests that want to validate a bare
// column-name segment; not used internally above.
export { NAME_RE }
