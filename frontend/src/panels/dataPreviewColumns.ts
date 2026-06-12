/**
 * Variable-width column virtualisation for DataPreview.
 *
 * Replaces the previous uniform-width assumptions (`startIdx =
 * floor(scrollLeft / columnWidth)`, `totalWidth = columnCount * columnWidth`)
 * with a cumulative-offset (prefix-sum) array plus binary search, so
 * per-column width overrides from the draggable resize handles stay
 * compatible with the windowed rendering.
 *
 * Pure module — exported for direct unit testing
 * (src/__tests__/panels/dataPreviewColumns.test.ts).
 *
 * The window derivation is deliberately NOT byte-identical to the old
 * ceil-based uniform formulae; the three divergences (misaligned-scroll
 * off-by-one, beyond-max clamp shape, gate boundary) are user-invisible and
 * pinned as named fixtures in the test file. See the design notes for
 * `datapreview-column-resize` §3.2.
 */

export const ROW_NUMBER_WIDTH = 48
export const COLUMN_OVERSCAN = 3

/** Clamp bounds for user-dragged column width overrides (px). */
export const COL_MIN_OVERRIDE = 60
export const COL_MAX_OVERRIDE = 640

export type ColumnWindow = {
  startIdx: number
  endIdx: number
  leftPad: number
  rightPad: number
  totalWidth: number
}

export type ColumnOffsets = {
  /** offsets[i] = sum of widths of columns [0, i); length = columnCount + 1 */
  offsets: number[]
  /** = offsets[columnCount] */
  totalColumnsWidth: number
  /** true when every column is at the responsive default (informational fast-path flag) */
  uniform: boolean
  /** the responsive default width the offsets were built against */
  defaultWidth: number
}

export function clampColumnWidth(width: number): number {
  return Math.max(COL_MIN_OVERRIDE, Math.min(COL_MAX_OVERRIDE, width))
}

export function buildColumnOffsets(
  columns: readonly { name: string }[],
  defaultWidth: number,
  overrides: Readonly<Record<string, number>>,
  dragOverride: { column: string; width: number } | null,
): ColumnOffsets {
  const offsets = new Array<number>(columns.length + 1)
  offsets[0] = 0
  let uniform = true
  for (let i = 0; i < columns.length; i++) {
    const name = columns[i].name
    let width = overrides[name] ?? defaultWidth
    if (dragOverride !== null && dragOverride.column === name) width = dragOverride.width
    if (width !== defaultWidth) uniform = false
    offsets[i + 1] = offsets[i] + width
  }
  return {
    offsets,
    totalColumnsWidth: offsets[columns.length],
    uniform,
    defaultWidth,
  }
}

/**
 * Largest i with offsets[i] <= x (standard upper-bound binary search minus
 * one). Saturates at 0 for negative x and at columnCount for x past the end.
 */
export function findColumnIndex(offsets: number[], x: number): number {
  if (offsets.length === 0) return 0
  let lo = 0
  let hi = offsets.length - 1
  // Invariant: offsets[lo] <= x < offsets[hi + 1] (conceptually).
  if (offsets[hi] <= x) return hi
  if (x < offsets[0]) return 0
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (offsets[mid] <= x) lo = mid
    else hi = mid - 1
  }
  return lo
}

export function getColumnWindowVariable(
  off: ColumnOffsets,
  columnCount: number,
  scrollLeft: number,
  viewWidth: number,
): ColumnWindow {
  const totalWidth = ROW_NUMBER_WIDTH + off.totalColumnsWidth
  const visibleWidth = Math.max(off.defaultWidth, viewWidth - ROW_NUMBER_WIDTH)
  // Width-aware virtualise-or-not gate (close to the old count gate in the
  // uniform case, but not identical at the boundary — divergence 3).
  const shouldVirtualizeColumns =
    off.totalColumnsWidth > visibleWidth + COLUMN_OVERSCAN * 2 * off.defaultWidth
  if (!shouldVirtualizeColumns) {
    return {
      startIdx: 0,
      endIdx: columnCount,
      leftPad: 0,
      rightPad: 0,
      totalWidth,
    }
  }

  const dataScrollLeft = Math.max(0, scrollLeft - ROW_NUMBER_WIDTH)
  const rawStart = findColumnIndex(off.offsets, dataScrollLeft)
  const rawEnd = findColumnIndex(off.offsets, dataScrollLeft + visibleWidth) + 1
  const startIdx = Math.min(columnCount, Math.max(0, rawStart - COLUMN_OVERSCAN))
  const endIdx = Math.min(columnCount, Math.max(startIdx, rawEnd + COLUMN_OVERSCAN))

  return {
    startIdx,
    endIdx,
    leftPad: off.offsets[startIdx],
    rightPad: Math.max(0, off.totalColumnsWidth - off.offsets[endIdx]),
    totalWidth,
  }
}
