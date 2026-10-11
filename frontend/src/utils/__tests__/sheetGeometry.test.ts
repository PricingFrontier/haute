/**
 * Placing components on a sheet (specs/workbench): the snap grid, moves kept on the sheet,
 * resizes from each handle, the sheet's size and fit, and the order components are read in.
 */
import { describe, expect, it } from "vitest"
import {
  GRID,
  MIN_SHEET_HEIGHT,
  SHEET_EDGE,
  contentRight,
  fitZoom,
  moveRect,
  placeAt,
  readingOrder,
  resizeRect,
  sheetHeight,
  sheetWidth,
  snap,
} from "../sheetGeometry"

const rect = { x: 96, y: 96, w: 200, h: 64 }
const min = { w: 80, h: 56 }

describe("sheetGeometry", () => {
  it("snaps to an 8px grid", () => {
    expect(GRID).toBe(8)
    expect([snap(3), snap(4), snap(13), snap(-5)]).toEqual([0, 8, 16, -8])
  })

  it("snaps moves to the grid, keeping components off the top and left edges but not the right", () => {
    expect(moveRect(rect, 13, -5)).toEqual({ x: 112, y: 88, w: 200, h: 64 })
    expect(moveRect(rect, -500, -500)).toMatchObject({ x: 0, y: 0 })
    // The sheet widens to hold whatever is dragged to its right.
    expect(moveRect(rect, 5000, 0).x).toBe(5096)
  })

  it("resizes from any handle, keeping the opposite edges and a minimum size", () => {
    expect(resizeRect(rect, "se", 37, 21, min)).toEqual({ x: 96, y: 96, w: 240, h: 88 })
    expect(resizeRect(rect, "e", 3000, 0, min).w).toBe(3200)
    // The top edge stops where the component would get shorter than its minimum.
    expect(resizeRect(rect, "nw", 16, 16, min)).toEqual({ x: 112, y: 104, w: 184, h: 56 })
    expect(resizeRect(rect, "w", 400, 0, min)).toMatchObject({ x: 216, w: 80 })
    expect(resizeRect(rect, "n", -200, -200, min)).toMatchObject({ y: 0, h: 160 })
    expect(resizeRect(rect, "s", 0, 7, min)).toMatchObject({ y: 96, h: 72 })
  })

  it("places a component near a point, and tells how far right the components reach", () => {
    expect(placeAt(13, -20, { w: 240, h: 64 })).toEqual({ x: 16, y: 0, w: 240, h: 64 })
    expect(contentRight([rect, { x: 0, y: 0, w: 400, h: 10 }])).toBe(400)
    expect(contentRight([])).toBe(0)
  })

  it("sizes the sheet to its viewport at the zoom, or wider and taller to hold its components", () => {
    expect(sheetWidth(1000, 1, [])).toBe(1000)
    expect(sheetWidth(1000, 0.5, [])).toBe(2000)
    expect(sheetWidth(1000, 1, [{ x: 1200, y: 0, w: 300, h: 10 }])).toBe(1500 + SHEET_EDGE)
    expect(sheetHeight([])).toBe(MIN_SHEET_HEIGHT)
    expect(sheetHeight([{ x: 0, y: 900, w: 10, h: 100 }])).toBe(1240)
  })

  it("fits the components to the viewport across, in steps of 5%, never past 100%", () => {
    expect(fitZoom(1000, [])).toBe(1)
    expect(fitZoom(1000, [{ x: 0, y: 0, w: 400, h: 10 }])).toBe(1)
    expect(fitZoom(1000, [{ x: 0, y: 0, w: 1968, h: 10 }])).toBe(0.5)
    expect(fitZoom(1000, [{ x: 0, y: 0, w: 3000, h: 10 }])).toBe(0.3)
  })

  it("reads components row by row from the top, each row left to right, a tie in the order given, leaving its input as it was", () => {
    const at = (id: string, x: number, y: number, h = 40) => ({ id, x, y, w: 100, h })
    // A title; a label a little lower than the box it names; a Table a grid step higher than the
    // Collection to its left, as on a sheet laid out by hand; and a note exactly over the Table.
    const added = [at("table", 864, 592, 200), at("title", 0, 0), at("box", 300, 200, 120), at("collection", 8, 608, 96), at("label", 16, 216, 24), at("note", 864, 592, 200)]

    expect(readingOrder(added).map((item) => item.id)).toEqual(["title", "label", "box", "collection", "table", "note"])
    expect(added.map((item) => item.id)).toEqual(["table", "title", "box", "collection", "label", "note"])
    // A component starting at or below the middle of the row's topmost one starts the next row.
    expect(readingOrder([at("upper", 400, 0), at("lower", 0, 20)]).map((item) => item.id)).toEqual(["upper", "lower"])
    expect(readingOrder([at("upper", 400, 0), at("beside", 0, 19)]).map((item) => item.id)).toEqual(["beside", "upper"])
    expect(readingOrder([])).toEqual([])
  })
})
