/**
 * Placing sections and components on a sheet (specs/workbench): a snap grid on a sheet
 * that fills the width it is shown in and, like a spreadsheet, has no right or bottom edge:
 * it widens and lengthens to hold what is on it. Its sections are strips across it stacked
 * one under the next, and a component sits in one, placed from its top-left corner.
 * Positions and sizes are in unzoomed pixels.
 */

export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

export interface Size {
  w: number
  h: number
}

export const GRID = 8
export const MIN_SHEET_HEIGHT = 800
/** Around the sheet, inside its viewport. */
export const SHEET_PADDING = 32
/** The sheet reaches at least this far past its rightmost component. */
export const SHEET_EDGE = GRID * 4
/** Past the sheet's last section, so it has room to grow. */
const ROOM_BELOW = 240
/** The least a section can be, holding components or none. */
export const MIN_SECTION_HEIGHT = 48
/** What Fit leaves under a section's lowest component. */
export const SECTION_FIT_GAP = GRID * 2
/** A section added to a sheet. */
export const NEW_SECTION_HEIGHT = 160
/** The bar above each section while building, naming it; Preview has none. */
export const SECTION_HEADER = 24
/**
 * A new sheet's one section: the least sheet height less the room below, so the whole
 * sheet is one section until another is added, as the server's blank form has it
 * (`BLANK_SECTION_HEIGHT` in `src/haute/_workbench_form.py`).
 */
export const BLANK_SECTION_HEIGHT = MIN_SHEET_HEIGHT - ROOM_BELOW

export const snap = (value: number): number => Math.round(value / GRID) * GRID
const clamp = (value: number, low: number, high: number): number => Math.min(Math.max(value, low), Math.max(low, high))

export type Handle = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw"
export const HANDLES: readonly Handle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"]

/** Where a component lands when dragged by (dx, dy): snapped, and kept off the sheet's top and left edges. */
export function moveRect(rect: Rect, dx: number, dy: number): Rect {
  return { ...rect, x: Math.max(0, snap(rect.x + dx)), y: Math.max(0, snap(rect.y + dy)) }
}

/** A component resized by dragging one handle by (dx, dy); the opposite edges stay put. */
export function resizeRect(rect: Rect, handle: Handle, dx: number, dy: number, min: Size): Rect {
  let left = rect.x
  let top = rect.y
  let right = rect.x + rect.w
  let bottom = rect.y + rect.h
  if (handle.includes("e")) right = Math.max(snap(right + dx), left + min.w)
  if (handle.includes("w")) left = clamp(snap(left + dx), 0, right - min.w)
  if (handle.includes("s")) bottom = Math.max(snap(bottom + dy), top + min.h)
  if (handle.includes("n")) top = clamp(snap(top + dy), 0, bottom - min.h)
  return { x: left, y: top, w: right - left, h: bottom - top }
}

/** A component of `size` with its top-left corner near (px, py) on the sheet. */
export function placeAt(px: number, py: number, size: Size): Rect {
  return { x: Math.max(0, snap(px)), y: Math.max(0, snap(py)), ...size }
}

/** How far right the sheet's components reach. */
export const contentRight = (rects: readonly Rect[]): number => rects.reduce((max, rect) => Math.max(max, rect.x + rect.w), 0)

/** How far down components reach: the lowest one's bottom, 0 for none. */
export const contentBottom = (rects: readonly Rect[]): number => rects.reduce((max, rect) => Math.max(max, rect.y + rect.h), 0)

/** The least a section holding `rects` can be: as far down as they reach, and never under `MIN_SECTION_HEIGHT`. */
export const leastSectionHeight = (rects: readonly Rect[]): number => Math.max(MIN_SECTION_HEIGHT, contentBottom(rects))

/** A section fitted to `rects`: `SECTION_FIT_GAP` under the lowest, never under `MIN_SECTION_HEIGHT`, which an empty one is. */
export const fittedSectionHeight = (rects: readonly Rect[]): number =>
  rects.length === 0 ? MIN_SECTION_HEIGHT : Math.max(MIN_SECTION_HEIGHT, contentBottom(rects) + SECTION_FIT_GAP)

/** A section with its top on the sheet. */
export interface Stacked<T> {
  section: T
  top: number
}

/**
 * Sections one under the next, each with its top on the sheet: the heights above it summed,
 * with a `header` above each, the bar that names it while building (none in Preview).
 */
export function stackSections<T extends { height: number }>(sections: readonly T[], header = 0): Stacked<T>[] {
  let top = 0
  return sections.map((section) => {
    top += header
    const stacked = { section, top }
    top += section.height
    return stacked
  })
}

/**
 * The run of overlapping components among `rects`, a section's, that a line across the
 * section at `y` would cut through: from the top of the highest to the bottom of the lowest,
 * or `y` to `y` while it cuts through none. A line along a component's edge cuts nothing.
 */
export function cutRun(rects: readonly Rect[], y: number): { top: number; bottom: number } {
  let top = y
  let bottom = y
  // Widen the run until no component reaches across either of its edges.
  for (let grew = true; grew; ) {
    grew = false
    for (const rect of rects) {
      if (rect.y < bottom && rect.y + rect.h > top && (rect.y < top || rect.y + rect.h > bottom)) {
        top = Math.min(top, rect.y)
        bottom = Math.max(bottom, rect.y + rect.h)
        grew = true
      }
    }
  }
  return { top, bottom }
}

/**
 * The line across a section nearest `y` that cuts through none of `rects`, the section's
 * components: `y` itself when it is clear, else the nearer edge, the top on a tie, of the run
 * of overlapping components it would cut through (`cutRun`).
 */
export function clearLine(rects: readonly Rect[], y: number): number {
  const { top, bottom } = cutRun(rects, y)
  return y - top <= bottom - y ? top : bottom
}

/** Where a stack of sections ends: its heights summed. */
export const stackBottom = (stack: readonly Stacked<{ height: number }>[]): number => {
  const last = stack.at(-1)
  return last === undefined ? 0 : last.top + last.section.height
}

/**
 * The stacked section whose band holds `y` on the sheet: the first while `y` is above them
 * all, the last while it is below them all; null for no sections.
 */
export function sectionAt<T extends { height: number }>(stack: readonly Stacked<T>[], y: number): Stacked<T> | null {
  return stack.find(({ section, top }) => y < top + section.height) ?? stack.at(-1) ?? null
}

/** The sheet's height: at least `MIN_SHEET_HEIGHT`, and room below the bottom of its sections. */
export const sheetHeight = (bottom: number): number => Math.max(MIN_SHEET_HEIGHT, bottom + ROOM_BELOW)

/** The sheet's width: the viewport's at this zoom, or wider to hold its components. */
export const sheetWidth = (viewportWidth: number, zoom: number, rects: readonly Rect[]): number =>
  Math.max(Math.floor(viewportWidth / zoom), contentRight(rects) + SHEET_EDGE)

/** The zoom at which everything on the sheet fits the viewport across, never past 100%. */
export function fitZoom(viewportWidth: number, rects: readonly Rect[]): number {
  const right = contentRight(rects)
  if (right === 0) return 1
  return Math.min(1, Math.floor((viewportWidth / (right + SHEET_EDGE)) * 20) / 20)
}

/**
 * Components in the order they are read: row by row from the top, each row left to right,
 * a tie kept in the order given. A row is the topmost component not yet read and every
 * component beside it, one whose top is above its middle, so a label a little lower than
 * the box it names, or a Table a little higher than the Collection to its left, is read
 * in its row. A new array; the input is untouched.
 */
export function readingOrder<T extends Rect>(rects: readonly T[]): T[] {
  const rows: T[][] = []
  for (const rect of [...rects].sort((a, b) => a.y - b.y)) {
    const row = rows.at(-1)
    if (row !== undefined && rect.y < row[0].y + row[0].h / 2) row.push(rect)
    else rows.push([rect])
  }
  return rows.flatMap((row) => row.sort((a, b) => a.x - b.x))
}
