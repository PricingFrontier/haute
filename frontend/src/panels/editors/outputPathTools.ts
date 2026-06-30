/**
 * Pure path-string tools for the OUTPUT editor's mass-edit affordances.
 *
 * Storage is LITERAL — an entry's `output_path` is a plain string with no
 * header/suffix split — so both transforms below operate on literal path
 * strings, exactly as specified. Kept in their own module (no React) so the
 * OutputEditor file stays a components-only module (react-refresh) and the
 * transforms are unit-testable in isolation.
 */

/**
 * PATH-EDIT substitution (the pencil/apply affordance). For a path that STARTS
 * WITH `oldPrefix`, replace that leading run with `newPrefix`; otherwise return
 * the path unchanged. Applied across a frame's rows, it can miss columns higher
 * in the tree whose path doesn't share the old prefix — accepted per spec; this
 * is uniform mass-edit, not exhaustive.
 */
export function substitutePrefix(path: string, oldPrefix: string, newPrefix: string): string {
  if (oldPrefix === "" || !path.startsWith(oldPrefix)) return path
  return newPrefix + path.slice(oldPrefix.length)
}

/**
 * PREFIX-COMPOSE (the prefix-helper "apply" affordance). Distribute a reusable
 * prefix across a column path: insert the prefix segments AFTER the leading
 * `$[:]` (or `$`) root marker and BEFORE the rest, composing
 * `$[:].<prefix>...<column>`. The prefix is given in segment form (no leading
 * `$`/dot), e.g. `addr` or `addr.geo`; an empty prefix is a no-op.
 *
 * Examples (prefix = `addr`):
 *   `$[:].city`        → `$[:].addr.city`
 *   `$[:].geo[:].lat`  → `$[:].addr.geo[:].lat`
 *   `$.city`           → `$.addr.city`
 */
export function composePrefix(path: string, prefix: string): string {
  const clean = prefix.replace(/^\$?\.?/, "").replace(/\.$/, "")
  if (clean === "") return path
  // Find the root marker length: `$[:]` (4) or bare `$` (1).
  const rootLen = path.startsWith("$[:]") ? 4 : path.startsWith("$") ? 1 : 0
  const root = path.slice(0, rootLen)
  let rest = path.slice(rootLen)
  // Drop a single leading dot on the remainder so we don't double it.
  if (rest.startsWith(".")) rest = rest.slice(1)
  const composed = rest ? `${clean}.${rest}` : clean
  return `${root}.${composed}`
}

/**
 * The longest literal-string common prefix of a set of output paths, trimmed
 * back to a path boundary (`.` / `[`). Used as the per-frame "header path"
 * (the prefix the user substitutes) and as the frames-paths table's `root_path`
 * hint. `$[:]` when there are no rows.
 */
export function commonRootPath(paths: string[]): string {
  if (paths.length === 0) return "$[:]"
  let prefix = paths[0]
  for (const p of paths.slice(1)) {
    let i = 0
    while (i < prefix.length && i < p.length && prefix[i] === p[i]) i++
    prefix = prefix.slice(0, i)
  }
  const lastBoundary = Math.max(prefix.lastIndexOf("."), prefix.lastIndexOf("["))
  return lastBoundary > 0 ? prefix.slice(0, lastBoundary) : prefix || "$[:]"
}

/**
 * Drop a recognised header row from a pasted column-mapping grid. The OUTPUT
 * Copy affordance emits `column<TAB>path<TAB>enabled`, so a first row matching
 * that header (case-insensitive, tolerating `source_column`/`output_path`) is
 * dropped; otherwise every row is data.
 */
export function dropMappingHeader(grid: string[][]): string[][] {
  if (grid.length === 0) return grid
  const first = grid[0].map((c) => c.trim().toLowerCase())
  const looksLikeHeader =
    (first[0] === "column" || first[0] === "source_column") &&
    (first[1] === "path" || first[1] === "output_path")
  return looksLikeHeader ? grid.slice(1) : grid
}
