/**
 * Placing components on a sheet (specs/workbench): a snap grid on a sheet that fills the
 * width it is shown in and, like a spreadsheet, has no right or bottom edge: it widens and
 * lengthens to hold what is on it. Positions and sizes are in unzoomed pixels.
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
/** Below everything, and in from the left, where a click-added component goes. */
const INSET = 16
/** Past the sheet's lowest component, so it has room to grow. */
const ROOM_BELOW = 240

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

/** Below everything already on the sheet. */
export function nextFreeSpot(rects: readonly Rect[], size: Size): Rect {
  const bottom = rects.reduce((max, rect) => Math.max(max, rect.y + rect.h), 0)
  return placeAt(INSET, rects.length === 0 ? INSET : bottom + INSET, size)
}

export const contains = (rect: Rect, px: number, py: number): boolean =>
  px >= rect.x && px <= rect.x + rect.w && py >= rect.y && py <= rect.y + rect.h

/** How far right the sheet's components reach. */
export const contentRight = (rects: readonly Rect[]): number => rects.reduce((max, rect) => Math.max(max, rect.x + rect.w), 0)

/** The sheet's height: at least `MIN_SHEET_HEIGHT`, and room below its lowest component. */
export const sheetHeight = (rects: readonly Rect[]): number =>
  Math.max(MIN_SHEET_HEIGHT, rects.reduce((max, rect) => Math.max(max, rect.y + rect.h), 0) + ROOM_BELOW)

/** The sheet's width: the viewport's at this zoom, or wider to hold its components. */
export const sheetWidth = (viewportWidth: number, zoom: number, rects: readonly Rect[]): number =>
  Math.max(Math.floor(viewportWidth / zoom), contentRight(rects) + SHEET_EDGE)

/** The zoom at which everything on the sheet fits the viewport across, never past 100%. */
export function fitZoom(viewportWidth: number, rects: readonly Rect[]): number {
  const right = contentRight(rects)
  if (right === 0) return 1
  return Math.min(1, Math.floor((viewportWidth / (right + SHEET_EDGE)) * 20) / 20)
}
