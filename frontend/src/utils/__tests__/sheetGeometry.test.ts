/**
 * Placing sections and components on a sheet (specs/workbench): the snap grid, moves kept
 * on the sheet, resizes from each handle, the sheet's size and fit, the sections stacked,
 * with a bar above each and without, and the one at a height, a section's least and fitted
 * heights, the line across a section clear of its components, and the order components are
 * read in.
 */
import { describe, expect, it } from "vitest"
import {
  BLANK_SECTION_HEIGHT,
  GRID,
  MIN_SECTION_HEIGHT,
  MIN_SHEET_HEIGHT,
  NEW_SECTION_HEIGHT,
  SECTION_FIT_GAP,
  SECTION_HEADER,
  SHEET_EDGE,
  clearLine,
  contentBottom,
  contentRight,
  cutRun,
  fittedSectionHeight,
  fitZoom,
  leastSectionHeight,
  moveRect,
  placeAt,
  readingOrder,
  resizeRect,
  sectionAt,
  sheetHeight,
  sheetWidth,
  snap,
  stackBottom,
  stackSections,
} from "../sheetGeometry"

const rect = { x: 96, y: 96, w: 200, h: 64 }
const min = { w: 80, h: 56 }
/** A sheet's sections in their order: the policy's, a flood cover's and a note's. */
const sections = [
  { id: "policy", height: 152 },
  { id: "flood", height: 232 },
  { id: "notes", height: 72 },
]
/** A component across a section from `y`, `h` tall: only its top and height count for a line across. */
const band = (y: number, h: number) => ({ x: 16, y, w: 400, h })

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

  it("tells how far down components reach, and the least and the fitted height of a section holding them", () => {
    const boxes = { x: 16, y: 16, w: 400, h: 120 }
    const label = { x: 0, y: 0, w: 80, h: 24 }
    expect(contentBottom([label, boxes])).toBe(136)
    expect(contentBottom([])).toBe(0)

    // Never less than its components reach, nor than 48, which an empty section is.
    expect(MIN_SECTION_HEIGHT).toBe(48)
    expect(leastSectionHeight([label, boxes])).toBe(136)
    expect(leastSectionHeight([{ x: 0, y: 0, w: 80, h: 50 }])).toBe(50)
    expect(leastSectionHeight([label])).toBe(MIN_SECTION_HEIGHT)
    expect(leastSectionHeight([])).toBe(MIN_SECTION_HEIGHT)

    // Fitted, 16 below its lowest component, and still never under 48.
    expect(SECTION_FIT_GAP).toBe(16)
    expect(fittedSectionHeight([label, boxes])).toBe(152)
    expect(fittedSectionHeight([{ x: 0, y: 16, w: 80, h: 24 }])).toBe(56)
    expect(fittedSectionHeight([label])).toBe(MIN_SECTION_HEIGHT)
    expect(fittedSectionHeight([])).toBe(MIN_SECTION_HEIGHT)
  })

  it("adds a section 160 tall, and starts a sheet with one as tall as the least sheet less the room below it", () => {
    expect(NEW_SECTION_HEIGHT).toBe(160)
    // The server's blank form holds one section this tall too.
    expect(BLANK_SECTION_HEIGHT).toBe(560)
    expect(sheetHeight(BLANK_SECTION_HEIGHT)).toBe(MIN_SHEET_HEIGHT)
  })

  it("stacks sections one under the next, each one's top the heights above it summed", () => {
    const stack = stackSections(sections)

    expect(stack.map(({ section, top }) => [section.id, top])).toEqual([
      ["policy", 0],
      ["flood", 152],
      ["notes", 384],
    ])
    expect(stack[1].section).toBe(sections[1])
    expect(stackBottom(stack)).toBe(456)
    expect(stackSections([])).toEqual([])
    expect(stackBottom([])).toBe(0)
  })

  it("stacks sections each under a bar while building, a bar's height more above each than the one before, the stack ending under the last", () => {
    expect(SECTION_HEADER).toBe(24)
    const stack = stackSections(sections, SECTION_HEADER)

    expect(stack.map(({ section, top }) => [section.id, top])).toEqual([
      ["policy", 24],
      ["flood", 200],
      ["notes", 456],
    ])
    expect(stack[1].section).toBe(sections[1])
    expect(stackBottom(stack)).toBe(528)
    expect(stackSections([], SECTION_HEADER)).toEqual([])
  })

  it("finds, over a bar, the section under it: a bar is in its section's band", () => {
    const stack = stackSections(sections, SECTION_HEADER)
    const at = (y: number) => sectionAt(stack, y)?.section.id

    // Each bar runs from the bottom of the section above, or the sheet's top, to its own section's top.
    expect([at(0), at(23), at(176), at(199), at(432), at(455)]).toEqual(["policy", "policy", "flood", "flood", "notes", "notes"])
    // Just above a bar is the section above it.
    expect([at(175), at(431)]).toEqual(["policy", "flood"])
    expect([at(-40), at(528)]).toEqual(["policy", "notes"])
  })

  it("finds the section at a height on the sheet: the one whose band holds it, the first above them all and the last below them all", () => {
    const stack = stackSections(sections)
    const at = (y: number) => sectionAt(stack, y)?.section.id

    // Each section's band runs from its top to just above the next one's.
    expect([at(0), at(152), at(384)]).toEqual(["policy", "flood", "notes"])
    expect([at(100), at(151), at(383), at(455)]).toEqual(["policy", "policy", "flood", "notes"])
    expect([at(-40), at(456), at(5000)]).toEqual(["policy", "notes", "notes"])
    // The section with its top, as stacked.
    expect(sectionAt(stack, 200)).toBe(stack[1])
    expect(sectionAt([], 0)).toBeNull()
  })

  it("takes a line across a section where it is when it cuts through none of the section's components, a line along an edge included", () => {
    const boxes = band(16, 120)
    const grid = band(160, 200)

    expect(clearLine([], 100)).toBe(100)
    // Between the boxes and the Table, or above or below them all.
    expect(clearLine([boxes, grid], 148)).toBe(148)
    expect(clearLine([boxes, grid], 0)).toBe(0)
    expect(clearLine([boxes, grid], 400)).toBe(400)
    // Along a component's top or bottom.
    expect([clearLine([boxes, grid], 16), clearLine([boxes, grid], 136), clearLine([boxes, grid], 160), clearLine([boxes, grid], 360)]).toEqual([16, 136, 160, 360])
  })

  it("gives the run of overlapping components a line would cut through, or the line itself where it cuts none", () => {
    const chain = [band(16, 120), band(100, 100), band(180, 120)]
    expect(cutRun(chain, 190)).toEqual({ top: 16, bottom: 300 })
    expect(cutRun(chain, 48)).toEqual({ top: 16, bottom: 300 })
    expect(cutRun([band(16, 120)], 136)).toEqual({ top: 136, bottom: 136 })
    expect(cutRun([], 48)).toEqual({ top: 48, bottom: 48 })
  })

  it("moves a line cutting through components to the nearer edge of the run of them it cuts, the top on a tie", () => {
    // Inside the boxes, 16 to 136: nearer their top, or nearer their bottom; midway, the top.
    expect(clearLine([band(16, 120)], 40)).toBe(16)
    expect(clearLine([band(16, 120)], 120)).toBe(136)
    expect(clearLine([band(16, 120)], 76)).toBe(16)
    expect(clearLine([band(0, 100)], 50)).toBe(0)

    // Two overlapping, 0 to 100 and 80 to 200, are one run, 0 to 200: 90 is nearer 100, the
    // first's bottom, but that cuts the second, so the run's top it is.
    const overlapping = [band(0, 100), band(80, 120)]
    expect(clearLine(overlapping, 90)).toBe(0)
    expect(clearLine(overlapping, 150)).toBe(200)
    expect(clearLine(overlapping, 60)).toBe(0)

    // Overlapping one to the next, 16 to 136, 100 to 200 and 180 to 300, are one run, 16 to 300,
    // whichever order they come in: 190, in the last two, is nearer 300. Without the first,
    // the run is 100 to 300 and 190 nearer 100.
    const chain = [band(16, 120), band(100, 100), band(180, 120)]
    expect(clearLine(chain, 190)).toBe(300)
    expect(clearLine([...chain].reverse(), 190)).toBe(300)
    expect(clearLine(chain.slice(1), 190)).toBe(100)

    // Touching, 0 to 100 and 100 to 200, are not one run: 70 is nearer 100, the edge they
    // share, which is clear.
    const touching = [band(0, 100), band(100, 100)]
    expect(clearLine(touching, 70)).toBe(100)
    expect(clearLine(touching, 130)).toBe(100)
    expect(clearLine(touching, 100)).toBe(100)
  })

  it("sizes the sheet to its viewport at the zoom, or wider to hold its components and taller to hold its sections", () => {
    expect(sheetWidth(1000, 1, [])).toBe(1000)
    expect(sheetWidth(1000, 0.5, [])).toBe(2000)
    expect(sheetWidth(1000, 1, [{ x: 1200, y: 0, w: 300, h: 10 }])).toBe(1500 + SHEET_EDGE)
    // Room below the bottom of its sections, so it lengthens once they reach past the least sheet less that room.
    expect(sheetHeight(0)).toBe(MIN_SHEET_HEIGHT)
    expect(sheetHeight(561)).toBe(MIN_SHEET_HEIGHT + 1)
    expect(sheetHeight(1000)).toBe(1240)
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
