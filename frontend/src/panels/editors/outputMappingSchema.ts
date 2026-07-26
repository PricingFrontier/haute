/**
 * v2 mapping helpers for the OutputEditor.
 *
 * Mirrors `src/haute/_types.py` (OutputMappingEntry / OutputConfig) and the
 * grammar enforced by `src/haute/_output_assembler.py::_parse_output_path`.
 *
 * On-disk identifier note: the persisted field is `source_port` (kept exactly
 * — never renamed). Only UI LABEL STRINGS say "frame".
 */

import { validateOutputPathCore } from "./jsonpath"

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
  | { kind: "empty"; raw: Record<string, unknown> }

/**
 * Classify a raw config:
 *   - v2    — has an `outputMapping` array
 *   - empty — otherwise
 */
export function classifyConfig(
  config: Record<string, unknown> | undefined | null,
): OutputConfigShape {
  if (!config) return { kind: "empty", raw: {} }
  const hasMapping = Array.isArray((config as { outputMapping?: unknown }).outputMapping)
  if (hasMapping) {
    return { kind: "v2", v2: readV2(config as Record<string, unknown>) }
  }
  return { kind: "empty", raw: config }
}

/** Read the canonical mapping array without alternate field defaults. */
export function readV2(config: Record<string, unknown>): OutputConfigV2 {
  const outputMapping = Array.isArray((config as { outputMapping?: unknown }).outputMapping)
    ? (config.outputMapping as OutputMappingEntryV2[])
    : []
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
 * the four entry fields + outputFormat — never the editor-only row status. */
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
 * Grammar check for an output path. Returns an error string, or null when the
 * path is accepted. The backend is the authority; this is best-effort surfacing
 * so the user sees a rejection in the editor rather than as a 422 on save.
 *
 * The grammar itself now lives in the shared frontend lynchpin
 * ({@link validateOutputPathCore} in `jsonpath.ts`, the mirror of
 * `_jsonpath.py`) — OUTPUT and INPUT route through the one module so the
 * acceptance surface (selectors accepted, §3 rejections, and identifier
 * charset) can no longer drift. This
 * thin wrapper keeps OUTPUT's existing capitalised, period-terminated error
 * messages while delegating the actual accept/reject decision to the core.
 *
 * Accepted subset:
 *   - must start with the root array `$[:]` (PATH_GRAMMAR.md — every OUTPUT
 *     path enters the array-outer document through `$[:]`; a non-array root like
 *     `$.x` or `$.values[:].a` is rejected, symmetric with INPUT)
 *   - dot-name segments: `.name` (name = [A-Za-z_][A-Za-z0-9_]*)
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
    return null
  }
  if (core.startsWith("output path must start with '$[:]'")) {
    return "Output path must start with the root array '$[:]', e.g. $[:].field."
  }
  if (core.startsWith("unsupported output-path selector")) {
    return "Unsupported selector — only '.name' and the whole-array '[:]' are accepted."
  }
  if (core.startsWith("output path must name a leaf field")) {
    return "Output path must name a leaf field, not the bare root array."
  }
  return "Malformed output path."
}

// `NAME_RE` is retained for callers/tests that want to validate a bare
// column-name segment; not used internally above.
export { NAME_RE }
