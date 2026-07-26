/**
 * The shared path-grammar core — the frontend mirror of `src/haute/_jsonpath.py`
 * (the single lynchpin, PATH_GRAMMAR.md).
 *
 * A haute path is a mapping from a path string to a position in a tree-structured
 * document (PATH_GRAMMAR.md). The mapping is a **parameter of the transport
 * shape**; this module pins it for **array-outer JSON** — the one transport built
 * in this PR — where the document root is an array of records reached only by the
 * array selector `[:]`.
 *
 * This is the ONE place the frontend grammar lives, deliberately structured as a
 * small, named suite that mirrors the backend's `_jsonpath.py` construct-for-
 * construct so the two cannot drift. It carries the spec's three lynchpin
 * constructs:
 *
 *   - the canonical parser, {@link parsePath};
 *   - the canonical writer, {@link makeOutputPath}.
 *
 * The **transport shape** is the seam for siblings (object-outer JSON, JSONL,
 * … — §5): today its only value is array-outer, captured by the root constructs
 * `ROOT_ARRAY` and the `$[:]` prefix.
 *
 * Backend parity: the backend injects each side's HauteError subclass so a
 * rejected path raises that side's discriminated type. The frontend mirror is a
 * validator surface, so {@link parsePath} throws a plain {@link PathError} that
 * carries the human-readable message verbatim; the editor wrappers
 * ({@link validateOutputPathCore}, {@link validateInputTablePath},
 * {@link validateInputColumnPath}) catch it and return that message as a string,
 * matching the existing `validate(candidate) => string | null` convention.
 */

// ---------------------------------------------------------------------------
// The lynchpin — the grammar IS these named constructs (PATH_GRAMMAR.md)
// ---------------------------------------------------------------------------
//
// Acceptance grammar: the identifier charset and dotted-name object selector.
// The array selector `[:]` and root are matched literally below.
//
// These mirror `_jsonpath.py` `_NAME` / `_DOT_NAME` exactly.
// They are anchored (`^…`) here because they are matched against a tail slice
// (`raw.slice(i)`) rather than via Python's `re.match(raw, i)` positional match.

/** Identifier charset — object key. Mirror of backend `_NAME`. */
export const NAME = /[A-Za-z_][A-Za-z0-9_]*/
/** Object comprehension — mirror of backend `_DOT_NAME`. */
const DOT_NAME = /^\.([A-Za-z_][A-Za-z0-9_]*)/

// Transport shape = array-outer JSON.
const ARRAY = "[:]" // array comprehension — the only array selector
const ROOT_ARRAY = "$[:]" // the canonical (array-outer) data root

/** One path segment: a JSON key, and whether it iterates an array. Mirror of
 * backend `_Seg`. */
export interface Seg {
  name: string
  isArray: boolean
}

/** A parsed path (the `[:]`-only conventional-JSONPath subset). Mirror of
 * backend `_ParsedPath`. */
export interface ParsedPath {
  raw: string
  segments: Seg[]
}

/** The rejection thrown by {@link parsePath} — mirrors the backend's injected
 * `_PathError`, carrying the offending path under `path`. The editor wrappers
 * surface `.message` as the validator's error string. */
export class PathError extends Error {
  path: string
  constructor(message: string, path: string) {
    super(message)
    this.name = "PathError"
    this.path = path
  }
}

/**
 * Parse a path, rejecting every selector outside the accepted subset (§2/§3) —
 * the OUTPUT mode of the grammar, mirror of backend `parse_path`.
 *
 * Accepts the root `$[:]`, dot name selectors (`.name`), and the whole-array selector `[:]`.
 * Rejects (PATH_GRAMMAR.md) index (`[0]`), range (`[0:5]`), filter (`[?(...)]`),
 * descendant (`..`), and non-array wildcard (`.*`, `[*]`) selectors — the
 * dropped `.:` dot form included. Throws {@link PathError} on anything else.
 */
export function parsePath(raw: string): ParsedPath {
  if (!raw.startsWith(ROOT_ARRAY)) {
    throw new PathError("output path must start with '$[:]'", raw)
  }

  let i = ROOT_ARRAY.length

  const segments: Seg[] = []
  while (i < raw.length) {
    const ch = raw[i]
    let name: string
    if (ch === ".") {
      const m = DOT_NAME.exec(raw.slice(i))
      if (m === null) {
        throw new PathError(
          "unsupported output-path selector " +
            "(only '.name' and whole-array '[:]' are accepted)",
          raw,
        )
      }
      name = m[1]
      i += m[0].length
    } else {
      throw new PathError("malformed output path", raw)
    }

    const isArray = raw.slice(i, i + ARRAY.length) === ARRAY
    if (isArray) {
      i += ARRAY.length
    }
    segments.push({ name, isArray })
  }

  if (segments.length === 0) {
    throw new PathError("output path must name a leaf field, not the bare root array", raw)
  }
  return { raw, segments }
}

export interface ParseDataPathOptions {
  /** With `allowRoot`, `$[:]` parses to zero segments — the
   * spelling an INPUT *table path* uses for the outermost level. */
  allowRoot?: boolean
  /** The INPUT-only reserved leaf (`$value` — the scalar-array element-itself
   * sentinel, deliberately NOT a JSON identifier). Accepted only as a trailing
   * object hop. */
  reservedLeaf?: string | null
}

/**
 * Parse an array-outer **data path** — the INPUT-side mode of the grammar,
 * mirror of backend `parse_data_path`.
 *
 * INPUT addresses data inside an array-outer document, so it needs three things
 * the bare {@link parsePath} (the OUTPUT mode) does not, all expressed here so
 * the grammar stays single-sourced (PATH_GRAMMAR.md):
 *
 *   - **Mandatory array-outer root** — a data path enters the document only
 *     through `$[:]`. A bare-`$` data root (`$.key` — object-outer, a
 *     *different transport*, §5) is rejected.
 *   - **Root selectable** — with `allowRoot`, `$[:]` is
 *     accepted as the root array itself (zero segments).
 *   - **Reserved leaf sentinel** — `reservedLeaf` (INPUT's `$value`) is accepted
 *     only as a trailing object hop, becoming a final non-array segment.
 *
 * Everything else is delegated to {@link parsePath}. Throws {@link PathError}.
 */
export function parseDataPath(raw: string, opts: ParseDataPathOptions = {}): ParsedPath {
  const { allowRoot = false, reservedLeaf = null } = opts

  // Reserved-leaf sentinel: peel a trailing `.<reservedLeaf>` BEFORE the
  // identifier-pure parse (the sentinel is not an identifier), then re-append it
  // as a final object segment so callers see it as a normal dotted leaf.
  let sentinelSeg: Seg[] = []
  let core = raw
  if (reservedLeaf !== null && raw.endsWith(`.${reservedLeaf}`)) {
    sentinelSeg = [{ name: reservedLeaf, isArray: false }]
    core = raw.slice(0, -`.${reservedLeaf}`.length)
  }

  if (core === ROOT_ARRAY) {
    if (sentinelSeg.length > 0) {
      // `$[:].$value` — the sentinel sits directly on the root array, so it
      // names a leaf (a column path) regardless of `allowRoot`.
      return { raw, segments: sentinelSeg }
    }
    if (allowRoot) {
      // Bare root array — the INPUT root table level; no further segments.
      return { raw, segments: [] }
    }
    // A column path naming no leaf falls through to parsePath's rejection.
  }

  const parsed = parsePath(core)
  return { raw, segments: [...parsed.segments, ...sentinelSeg] }
}

/**
 * The canonical writer — emit the one canonical spelling (§2.1). Mirror of
 * backend `make_output_path`.
 *
 * Renders `$[:]` root + `.name` per segment + `[:]` after each array segment.
 */
export function makeOutputPath(segments: readonly Seg[]): string {
  let out = ROOT_ARRAY
  for (const seg of segments) {
    out += `.${seg.name}`
    if (seg.isArray) out += ARRAY
  }
  return out
}

// ---------------------------------------------------------------------------
// Editor-facing validators — string|null surface over the throwing core.
// ---------------------------------------------------------------------------

/** Run a parse closure, returning its thrown {@link PathError} message (or any
 * Error's message) as the validator string; `null` on success. */
function asError(run: () => void): string | null {
  try {
    run()
    return null
  } catch (e) {
    if (e instanceof PathError) return e.message
    if (e instanceof Error) return e.message
    return "invalid path"
  }
}

/**
 * OUTPUT core grammar validator (mirror of backend `parse_path`). Returns an
 * error string, or null when accepted. Does NOT enforce the OUTPUT editor's
 * additional require-a-`[:]` rule — that stays in the OUTPUT editor layer
 * (`outputMappingSchema.validateOutputPath` composes this with that rule).
 */
export function validateOutputPathCore(path: string): string | null {
  return asError(() => parsePath(path))
}

/** INPUT-side reserved leaf — the `$value` scalar-array element sentinel. Mirror
 * of backend `_RESERVED_LEAF`. */
export const RESERVED_LEAF = "$value"

/**
 * INPUT **table path** validator. A table sits at an array boundary: the root
 * array (`$` / `$[:]` → zero segments) or a `[:]` array of objects, optionally
 * reached through 1-1 object hops. Mirror of backend `parse_table_path`
 * (`parse_data_path(allow_root=True)` + the ends-at-array rule).
 */
export function validateInputTablePath(path: string): string | null {
  return asError(() => {
    const parsed = parseDataPath(path, { allowRoot: true, reservedLeaf: RESERVED_LEAF })
    const last = parsed.segments[parsed.segments.length - 1]
    if (parsed.segments.length > 0 && last && !last.isArray) {
      throw new PathError(
        "table path must end at an array '[:]' — a bare object key is not a " +
          "table (its leaves are columns of the enclosing array level)",
        path,
      )
    }
  })
}

/**
 * INPUT **column path** validator. A column path must name a leaf — the bare
 * root iterator (`$` / `$[:]`) is rejected (allowRoot left false), and a path
 * ending at `[:]` (naming no leaf) is rejected. The `$value` reserved leaf is
 * permitted as the trailing leaf. Mirror of backend `parse_column_path_full`
 * (`parse_data_path` + the names-a-leaf rule).
 */
export function validateInputColumnPath(path: string): string | null {
  return asError(() => {
    const parsed = parseDataPath(path, { allowRoot: false, reservedLeaf: RESERVED_LEAF })
    // The leaf is the maximal trailing run of object (non-array) hops; reject a
    // path whose deepest segment is an array (it names no leaf field).
    const last = parsed.segments[parsed.segments.length - 1]
    if (parsed.segments.length === 0 || !last || last.isArray) {
      throw new PathError("column path names no leaf field (it ends at an array iterator)", path)
    }
  })
}

// ---------------------------------------------------------------------------
// Structural helpers for the inherit/cascade key system (apiInputInherit.ts).
// Each is a thin reduction over parseDataPath/Seg[] — NOT a second parser — so
// the inherit/cascade prefix logic cannot drift from the grammar core. They
// mirror the backend's `array_depth` / `parse_column_path_full` / the
// segment-tuple prefix compare in `parse_column_path` (`_api_input_schema.py`).
// ---------------------------------------------------------------------------

/** A frame's own `(key, isArray)` steps — a table/prefix path parsed for the
 * inherit/cascade comparisons. The root array `$[:]` yields `[]`. Throws
 * {@link PathError} on a malformed path. */
export function frameSegments(framePath: string): Seg[] {
  return parseDataPath(framePath, { allowRoot: true, reservedLeaf: RESERVED_LEAF }).segments
}

/** Number of array (`[:]`) hops in a path — its relational depth. `$[:]` is 0
 * segments (the root array is the document root, not a step); `$[:].orders[:]`
 * is depth 1. Mirror of backend `array_depth`. Throws on a malformed path. */
export function arrayDepth(path: string): number {
  return frameSegments(path).reduce((n, seg) => n + (seg.isArray ? 1 : 0), 0)
}

/** Split a column path into its **locating** steps (up to and including the
 * deepest array hop — the frame the column belongs to) and its dotted **leaf**
 * (the trailing object hops). The `$value` reserved leaf surfaces as the leaf
 * string `"$value"`. Mirror of backend `parse_column_path_full`. Throws
 * {@link PathError} on a path that names no leaf. */
export function parseColumnPathFull(path: string): { locating: Seg[]; leaf: string } {
  const { segments } = parseDataPath(path, { allowRoot: false, reservedLeaf: RESERVED_LEAF })
  let lastArray = -1
  for (let i = 0; i < segments.length; i++) {
    if (segments[i].isArray) lastArray = i
  }
  const leafSegs = segments.slice(lastArray + 1)
  if (leafSegs.length === 0) {
    throw new PathError("column path names no leaf field (it ends at an array iterator)", path)
  }
  return { locating: segments.slice(0, lastArray + 1), leaf: leafSegs.map((s) => s.name).join(".") }
}

/** Whether `prefix` is a structural segment-prefix of `target`: every step
 * (name AND isArray) equal up to `prefix.length`. With `{proper: true}` it must
 * also be strictly shorter (a true ancestor/descendant, not the same level).
 * The step-by-step compare — not depth, not text — is why a sibling branch
 * (`$[:].drivers[:]` vs `$[:].vehicles[:]`, equal depth) is never a prefix.
 * Mirror of the backend `tuple(locating) == tuple(table[:len(locating)])` rule. */
export function segmentPrefix(
  prefix: readonly Seg[],
  target: readonly Seg[],
  opts: { proper?: boolean } = {},
): boolean {
  if (opts.proper ? prefix.length >= target.length : prefix.length > target.length) return false
  for (let i = 0; i < prefix.length; i++) {
    if (prefix[i].name !== target[i].name || prefix[i].isArray !== target[i].isArray) return false
  }
  return true
}
