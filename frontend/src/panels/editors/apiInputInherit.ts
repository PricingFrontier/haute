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
import type { ApiInputColumnV2, ApiInputTableV2, ColumnType } from "./apiInputSchema"
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
 * Derive a column name from a key path by salting its full leaf: every run of
 * characters that are not a letter, digit, or underscore collapses to a single
 * underscore. `$[:].customer.id` → `customer_id` (so it never clashes with a
 * sibling `order.id`, and arbitrary-unicode JSON keys still yield a valid name).
 */
export function inheritedColumnName(path: string): string {
  return parseColumnPathFull(path).leaf.replace(/[^A-Za-z0-9_]+/g, "_")
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
