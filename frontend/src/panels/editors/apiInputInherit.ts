/**
 * Pure logic for the inherit / cascade key system on the API Input editor.
 *
 * The whole feature rests on a **path inventory**: a flat list of every key the
 * editor knows about (the union of every column across the current frames and
 * the columns of the most-recent inference snapshot), keyed by path and carrying
 * the name it should reuse across frames plus its type. From the inventory,
 * the two directions fall out of one structural prefix comparison:
 *
 *   - **inherit (pull)** — keys at a strictly shallower level on a frame's branch
 *     can be pulled onto that frame;
 *   - **cascade (push)** — a key can be pushed into the frames at strictly deeper
 *     levels on its branch.
 *
 * No UI here. The prefix/leaf primitives all live in `jsonpath.ts` (the one
 * grammar core); this module composes them. `$value` scalar-array keys are not
 * valid inherit/cascade material and are excluded / rejected.
 */
import type { ApiInputColumnV2, ApiInputTableV2, ColumnOrigin, ColumnType } from "./apiInputSchema"
import {
  type Seg,
  RESERVED_LEAF,
  frameSegments,
  makeOutputPath,
  parseColumnPathFull,
  segmentPrefix,
} from "./jsonpath"

/** One key in the path inventory: its source path, the name it should carry
 * (reused across frames so the same field reads identically everywhere), its
 * type, and its category levels (carried verbatim). */
export interface InventoryKey {
  path: string
  name: string
  type: ColumnType
  levels: (string | null)[] | null
}

/** The dotted leaf of a path, or null if the path doesn't parse / names no leaf.
 * Defensive wrapper so a malformed path in the inventory is skipped, not thrown. */
function leafOf(path: string): string | null {
  try {
    return parseColumnPathFull(path).leaf
  } catch {
    return null
  }
}

/** True iff `path`'s leaf is the reserved `$value` scalar-array marker. Such keys
 * are a whole-frame scalar mode, not material to inherit a value out of. */
export function isReservedLeafPath(path: string): boolean {
  return leafOf(path) === RESERVED_LEAF
}

/**
 * Build the path inventory: the union of every column across `tables` and the
 * columns of the most-recent inference snapshot `lastInfer` (a single snapshot,
 * or null when nothing has been inferred). Keyed by path; a key present on a
 * current frame wins over the inference snapshot (the user may have edited its
 * name or type). `$value` keys are excluded.
 *
 * Iteration order is preserved as insertion order (inference snapshot first,
 * then current frames) so consumers see keys roughly in file-encounter order.
 */
export function buildPathInventory(
  tables: readonly ApiInputTableV2[],
  lastInfer: readonly ApiInputTableV2[] | null,
): Map<string, InventoryKey> {
  const inv = new Map<string, InventoryKey>()
  const ingest = (cols: readonly ApiInputColumnV2[]): void => {
    for (const c of cols) {
      if (!c.path || isReservedLeafPath(c.path)) continue
      // A Map.set on an existing key updates the value but keeps the original
      // insertion position — so frames override the snapshot's name/type without
      // reordering the inventory.
      inv.set(c.path, { path: c.path, name: c.name, type: c.type, levels: c.levels ?? null })
    }
  }
  if (lastInfer) for (const t of lastInfer) ingest(t.columns)
  for (const t of tables) ingest(t.columns)
  return inv
}

/** One ancestor level's worth of inherit candidates for a frame. */
export interface InheritGroup {
  /** Canonical path of the ancestor level (`$[:]`, `$[:].orders[:]`, …) — the
   * group's stable identity and its lexical sort key. */
  ancestorPath: string
  /** The array key at that level (`orders`), or `"root"` for the document root. */
  ancestorLabel: string
  candidates: InventoryKey[]
}

/** The array key naming a level: the deepest step's name, or "root" for `$[:]`. */
function levelLabel(locating: readonly Seg[]): string {
  return locating.length === 0 ? "root" : locating[locating.length - 1].name
}

/**
 * The keys that can be **inherited** into `framePath` (pull), grouped by ancestor
 * level. A key qualifies when its locating steps are a strictly-shallower
 * segment-prefix of the frame. Groups are sorted lexically by their level path
 * (which clusters structurally-similar levels and puts the root first);
 * candidates within a group keep inventory order.
 */
export function buildInheritGroups(
  framePath: string,
  inventory: ReadonlyMap<string, InventoryKey>,
): InheritGroup[] {
  let target: Seg[]
  try {
    target = frameSegments(framePath)
  } catch {
    return []
  }
  const groups = new Map<string, InheritGroup>()
  for (const key of inventory.values()) {
    let locating: Seg[]
    try {
      locating = parseColumnPathFull(key.path).locating
    } catch {
      continue
    }
    if (!segmentPrefix(locating, target, { proper: true })) continue
    const ancestorPath = makeOutputPath(locating)
    let group = groups.get(ancestorPath)
    if (!group) {
      group = { ancestorPath, ancestorLabel: levelLabel(locating), candidates: [] }
      groups.set(ancestorPath, group)
    }
    group.candidates.push(key)
  }
  return [...groups.values()].sort((a, b) =>
    a.ancestorPath < b.ancestorPath ? -1 : a.ancestorPath > b.ancestorPath ? 1 : 0,
  )
}

/**
 * The indices of frames in `tables` into which `keyPath` can **cascade** (push):
 * frames whose path has the key's own locating level as a strictly-deeper
 * segment-prefix (descendants on the same branch). Siblings are never returned —
 * the engine would reject them.
 */
export function getCascadeDestinations(
  keyPath: string,
  tables: readonly ApiInputTableV2[],
): number[] {
  let locating: Seg[]
  try {
    locating = parseColumnPathFull(keyPath).locating
  } catch {
    return []
  }
  const out: number[] = []
  for (let i = 0; i < tables.length; i++) {
    let frame: Seg[]
    try {
      frame = frameSegments(tables[i].path)
    } catch {
      continue
    }
    if (segmentPrefix(locating, frame, { proper: true })) out.push(i)
  }
  return out
}

/**
 * Derive a column name from a key path. With `salt` (the default) the full
 * dotted leaf is salted: every run of characters that are not a letter, digit,
 * or underscore collapses to a single underscore — `$[:].customer.id` →
 * `customer_id` (so it never clashes with a sibling `order.id`, and
 * arbitrary-unicode JSON keys still yield a valid name). With `salt` off only
 * the final segment is used (`id`), relying on numeric de-dup for collisions.
 */
export function inheritedColumnName(path: string, salt = true): string {
  const leaf = parseColumnPathFull(path).leaf
  const base = salt ? leaf : leaf.split(".").pop() ?? leaf
  return base.replace(/[^A-Za-z0-9_]+/g, "_")
}

/**
 * Group EVERY inventory key by its locating level — the candidate source for
 * the cascade picker, which offers the whole inventory rather than one frame's
 * ancestors. Same group shape and lexical level ordering as
 * {@link buildInheritGroups} so the two feed the same dialog.
 */
export function buildAllKeyGroups(
  inventory: ReadonlyMap<string, InventoryKey>,
): InheritGroup[] {
  const groups = new Map<string, InheritGroup>()
  for (const key of inventory.values()) {
    let locating: Seg[]
    try {
      locating = parseColumnPathFull(key.path).locating
    } catch {
      continue
    }
    const ancestorPath = makeOutputPath(locating)
    let group = groups.get(ancestorPath)
    if (!group) {
      group = { ancestorPath, ancestorLabel: levelLabel(locating), candidates: [] }
      groups.set(ancestorPath, group)
    }
    group.candidates.push(key)
  }
  return [...groups.values()].sort((a, b) =>
    a.ancestorPath < b.ancestorPath ? -1 : a.ancestorPath > b.ancestorPath ? 1 : 0,
  )
}

/**
 * Make `name` unique against the `taken` names by appending `_2`, `_3`, … Only
 * the name is ever changed — never the path — so the engine's per-frame
 * unique-name rule holds while the path stays the one it accepts.
 */
export function dedupName(name: string, taken: ReadonlySet<string>): string {
  if (!taken.has(name)) return name
  let i = 2
  while (taken.has(`${name}_${i}`)) i += 1
  return `${name}_${i}`
}

/**
 * Validate a column path against the frame it would live on (the hand-entry
 * guard). The column's locating must be a **prefix-or-equal** of the frame's
 * segments — the frame's own level (a normal column) or an ancestor of it (a
 * broadcast column) — never deeper and never sideways. A `$value` leaf is
 * rejected unless the frame is itself that scalar-array frame, because mixing a
 * `$value` column into an object frame silently empties it at flatten time.
 * Returns an error string, or null when the path is acceptable.
 */
export function validateColumnPathAgainstFrame(
  columnPath: string,
  framePath: string,
): string | null {
  let parsed: { locating: Seg[]; leaf: string }
  try {
    parsed = parseColumnPathFull(columnPath)
  } catch (e) {
    return e instanceof Error ? e.message : "invalid column path"
  }
  let frame: Seg[]
  try {
    frame = frameSegments(framePath)
  } catch (e) {
    return e instanceof Error ? e.message : "invalid frame path"
  }
  const prefixOrEqual = segmentPrefix(parsed.locating, frame)
  if (parsed.leaf === RESERVED_LEAF) {
    const sameLevel = prefixOrEqual && parsed.locating.length === frame.length
    return sameLevel
      ? null
      : "a $value scalar-array key cannot be added to this frame (it would empty the frame)"
  }
  return prefixOrEqual
    ? null
    : "this path points deeper than, or sideways from, this frame — only this frame's level or an ancestor of it can be added here"
}

/**
 * Make `name` unique against `taken` by walking UP the key's enclosing array
 * levels (ruled 2026-07-14): on a collision the nearest enclosing array key is
 * prepended (`id` → `orders_id`), then the next-shallower one
 * (`customer_orders_id`), until unique; the numeric suffix of {@link dedupName}
 * is the last resort once the path's levels are exhausted (a root-level key has
 * none to prepend). The path only supplies the prefixes — the name itself may
 * be the inventory's transported name, not the leaf.
 */
export function dedupNameByPath(
  name: string,
  path: string,
  taken: ReadonlySet<string>,
): string {
  if (!taken.has(name)) return name
  let locating: Seg[]
  try {
    locating = parseColumnPathFull(path).locating
  } catch {
    return dedupName(name, taken)
  }
  const levels = locating.filter((s) => s.isArray).map((s) => s.name)
  let candidate = name
  for (let i = levels.length - 1; i >= 0; i--) {
    candidate = `${levels[i].replace(/[^A-Za-z0-9_]+/g, "_")}_${candidate}`
    if (!taken.has(candidate)) return candidate
  }
  return dedupName(candidate, taken)
}

/**
 * The names that are AMBIGUOUS across the current frames: carried by more than
 * one distinct path (ruled 2026-07-14 — different fields sharing a name is the
 * warning case; the same path reusing its name across frames is the point of
 * name transport and is fine). The editor shades such names as a warning.
 */
export function ambiguousNames(
  tables: readonly ApiInputTableV2[],
): Map<string, string[]> {
  const pathsByName = new Map<string, Set<string>>()
  for (const t of tables) {
    for (const c of t.columns) {
      if (!c.name || !c.path) continue
      let set = pathsByName.get(c.name)
      if (!set) {
        set = new Set()
        pathsByName.set(c.name, set)
      }
      set.add(c.path)
    }
  }
  const out = new Map<string, string[]>()
  for (const [name, paths] of pathsByName) {
    if (paths.size > 1) out.set(name, [...paths])
  }
  return out
}

/**
 * Turn a set of selected key `paths` into the column rows to insert onto a frame
 * — the pure core shared by inherit, cascade, and inherit-attributes. Each row
 * reuses the inventory's name for that key (so a field reads the same across
 * frames — "name transport"), falling back to the salted leaf, then de-duplicated
 * against `existingNames` (and the rows already built in this batch). The type
 * and category levels are carried from the inventory. Every row arrives
 * `Confirmed` (a deliberate user act) with the given `origin` ("inherited" for
 * inherit/cascade, "manual" for hand entry). Rows are ordered shallowest level
 * first, then by the order the paths were given — the caller inserts the block
 * at the top of the frame. A path that does not parse is skipped.
 */
export function buildInsertedColumns(
  paths: readonly string[],
  inventory: ReadonlyMap<string, InventoryKey>,
  existingNames: ReadonlySet<string>,
  origin: ColumnOrigin = "inherited",
  salt = true,
): ApiInputColumnV2[] {
  const ordered = paths
    .map((path, idx) => {
      try {
        const depth = arrayDepthOf(parseColumnPathFull(path).locating)
        return { path, idx, depth }
      } catch {
        return null
      }
    })
    .filter((r): r is { path: string; idx: number; depth: number } => r !== null)
    .sort((a, b) => a.depth - b.depth || a.idx - b.idx)

  const taken = new Set(existingNames)
  const out: ApiInputColumnV2[] = []
  for (const { path } of ordered) {
    const entry = inventory.get(path)
    const name = dedupNameByPath(entry?.name ?? inheritedColumnName(path, salt), path, taken)
    taken.add(name)
    out.push({
      name,
      path,
      type: entry?.type ?? "str",
      status: "Confirmed",
      selected: true,
      levels: entry?.levels ?? null,
      origin,
      // Anything arriving through the key machinery is tracked as a key
      // (ruled 2026-07-09).
      key: true,
    })
  }
  return out
}

/** Array (`[:]`) count of a parsed segment list — local helper for ordering. */
function arrayDepthOf(segments: readonly Seg[]): number {
  return segments.reduce((n, s) => n + (s.isArray ? 1 : 0), 0)
}

/** Structurally incomplete: a blank name or path. Such a column is a persisted
 * entry mid-repair (or mid-typing) — reconciliation must not eat it. */
function isBlankColumn(c: ApiInputColumnV2): boolean {
  return !c.name || !c.path
}

/** A column's array depth (its locating steps' `[:]` count), or MAX for a
 * malformed/blank path so such rows sort after well-formed ones. */
function columnDepth(c: ApiInputColumnV2): number {
  try {
    return arrayDepthOf(parseColumnPathFull(c.path).locating)
  } catch {
    return Number.MAX_SAFE_INTEGER
  }
}

/**
 * The frame column ordering (ruled 2026-07-09): keys form a section at the top
 * — shallower (less-deep) keys first, keys at the same depth in JSON order,
 * full-depth keys (which are always deliberate adds) in the order they became
 * keys — and non-keys follow in data-model (JSON appearance) order. The only
 * violations of JSON order are therefore the depth grouping of keys and the
 * add-ordering of full-depth keys.
 *
 * `jsonOrder` maps path → JSON-appearance index (the inventory's insertion
 * order is the working proxy); paths it doesn't know keep their relative
 * order after known ones. Sorts are stable, so equal ranks preserve the
 * existing arrangement — for full-depth keys that IS the add order.
 */
export function orderFrameColumns(
  columns: readonly ApiInputColumnV2[],
  framePath: string,
  jsonOrder: ReadonlyMap<string, number>,
): ApiInputColumnV2[] {
  let frameDepth: number
  try {
    frameDepth = arrayDepthOf(frameSegments(framePath))
  } catch {
    return [...columns] // an invalid frame has no depth to order against
  }
  const rank = (c: ApiInputColumnV2) => jsonOrder.get(c.path) ?? Number.MAX_SAFE_INTEGER
  const keys = columns.filter((c) => c.key === true)
  const nonKeys = columns.filter((c) => c.key !== true)
  const shallow = keys.filter((c) => columnDepth(c) < frameDepth)
  const fullDepth = keys.filter((c) => columnDepth(c) >= frameDepth) // add order (stable)
  shallow.sort((a, b) => columnDepth(a) - columnDepth(b) || rank(a) - rank(b))
  nonKeys.sort((a, b) => rank(a) - rank(b))
  return [...shallow, ...fullDepth, ...nonKeys]
}

/**
 * The re-infer reconciliation (replaces the whole-column-array adoption of the
 * old merge): applied when the user confirms "Replace tables" after a re-infer.
 *
 * Per frame whose path exists on both sides, the user's curated
 * label/emit/displayPath/row-id survive, and columns reconcile:
 *   - **confirmed columns survive** (any origin — confirming is the user's act);
 *   - **structurally-incomplete columns survive** (blank name/path: in-progress
 *     user work; removing it on re-infer would be silent loss — the render-gate
 *     keeps it visible as invalid instead);
 *   - other non-confirmed columns survive only while their path is still in the
 *     fresh inference; stale ones are removed;
 *   - freshly-detected columns (paths not already on the frame) are appended,
 *     de-duplicated against the surviving names — the FRESH side is suffixed,
 *     never a column the user kept;
 *   - a row-id nomination naming a column that no longer exists is cleared.
 *
 * A frame that is new in this inference arrives whole, with the user's
 * previously-cascaded keys (inherited-origin columns on the existing frames)
 * prepended where the new frame is a valid cascade destination. Frames the
 * inference no longer produces are dropped, as before.
 */
export function reconcileInferredTables(
  existing: readonly ApiInputTableV2[],
  inferred: readonly ApiInputTableV2[],
  inventory: ReadonlyMap<string, InventoryKey>,
  salt = true,
): ApiInputTableV2[] {
  const byPath = new Map(existing.map((t) => [t.path, t]))
  // The cascade-all set: every key the user has inherited/cascaded somewhere.
  const cascadedPaths: string[] = []
  const seenCascaded = new Set<string>()
  for (const t of existing) {
    for (const c of t.columns) {
      if (c.origin === "inherited" && c.path && !seenCascaded.has(c.path)) {
        seenCascaded.add(c.path)
        cascadedPaths.push(c.path)
      }
    }
  }

  // JSON-appearance ranks for the section ordering, from the inventory's
  // insertion order (the working proxy for data-model order).
  const jsonOrder = new Map<string, number>()
  let rank = 0
  for (const path of inventory.keys()) jsonOrder.set(path, rank++)

  return inferred.map((inf) => {
    const prev = byPath.get(inf.path)
    if (!prev) {
      // New frame: fresh columns, with the relevant cascaded keys prepended.
      const applicable = cascadedPaths.filter((p) => {
        if (inf.columns.some((c) => c.path === p)) return false
        try {
          return isCascadeDestinationOf(p, inf.path)
        } catch {
          return false
        }
      })
      const prepended = buildInsertedColumns(
        applicable,
        inventory,
        new Set(inf.columns.map((c) => c.name)),
        "inherited",
        salt,
      )
      return {
        ...inf,
        columns: orderFrameColumns([...prepended, ...inf.columns], inf.path, jsonOrder),
      }
    }

    const freshPaths = new Set(inf.columns.map((c) => c.path))
    const kept = prev.columns.filter(
      (c) => c.status === "Confirmed" || isBlankColumn(c) || freshPaths.has(c.path),
    )
    const keptPaths = new Set(kept.map((c) => c.path))
    const taken = new Set(kept.map((c) => c.name))
    const appended: ApiInputColumnV2[] = []
    for (const c of inf.columns) {
      if (keptPaths.has(c.path)) continue
      const name = dedupName(c.name, taken)
      taken.add(name)
      appended.push({ ...c, name })
    }
    const columns = orderFrameColumns([...kept, ...appended], inf.path, jsonOrder)
    const row_id_column =
      prev.row_id_column && columns.some((c) => c.name === prev.row_id_column)
        ? prev.row_id_column
        : null
    return {
      ...inf,
      label: prev.label,
      emit: prev.emit,
      displayPath: prev.displayPath,
      row_id_column,
      columns,
    }
  })
}

/** True iff `keyPath`'s locating level is a strictly-shallower segment-prefix of
 * the frame at `framePath` — i.e. the frame is a valid cascade destination. */
function isCascadeDestinationOf(keyPath: string, framePath: string): boolean {
  return segmentPrefix(parseColumnPathFull(keyPath).locating, frameSegments(framePath), {
    proper: true,
  })
}
