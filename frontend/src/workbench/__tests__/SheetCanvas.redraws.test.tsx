/**
 * What a drag on the sheet redraws (specs/workbench): on a form of fifty components, a Table
 * of fifty rows among them, each step of a drag redraws the component it moves alone.
 */
import { act, cleanup, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { FormSpec } from "../../api/types"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { findWidget } from "../../utils/workbenchForm"
import SheetCanvas from "../SheetCanvas"
import { addTo, currentForm, fieldsWidget, loadForm, releasePointer, sheetForm, stubLayout, widgetsOf } from "./fixtures"

// Each component showing fields reads the sheet's values as it is drawn: the reads count the redraws.
const reads = vi.hoisted(() => ({ count: 0 }))
vi.mock("../useSheetValues", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../useSheetValues")>()
  return {
    ...actual,
    useSheetValues: () => {
      reads.count += 1
      return actual.useSheetValues()
    },
  }
})

/** `sheetForm` with its Table starting with fifty rows and copies of its Collection up to fifty components. */
function busyForm(): FormSpec {
  const form = sheetForm()
  const grid = fieldsWidget(form, "w_grid")
  if (grid.type !== "tableInput") throw new Error("w_grid is a Table")
  grid.rows = 50
  const boxes = widgetsOf(form)[0]
  for (let index = 0; widgetsOf(form).length < 50; index += 1) {
    addTo(form, { ...structuredClone(boxes), id: `w_boxes_${index}`, y: 400 + 136 * index })
  }
  return form
}

/** A pointer pressed, moved or released at (x, y). */
const pointer = (type: "pointerdown" | "pointermove" | "pointerup", x: number, y: number, target: EventTarget = window) =>
  act(() => {
    target.dispatchEvent(new MouseEvent(type, { bubbles: true, button: 0, buttons: type === "pointerup" ? 0 : 1, clientX: x, clientY: y }))
  })

describe("SheetCanvas redraws", () => {
  beforeEach(() => {
    stubLayout()
    loadForm(busyForm())
    useWorkbenchViewStore.setState({ fitted: true })
  })

  afterEach(() => {
    releasePointer()
    cleanup()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it("redraws only the component each step of a drag moves", () => {
    render(<SheetCanvas />)
    // Drawn once each: the counting counts.
    expect(reads.count).toBeGreaterThanOrEqual(50)

    pointer("pointerdown", 100, 100, screen.getByTestId("sheet-widget-w_boxes"))
    reads.count = 0
    pointer("pointermove", 300, 100)
    expect(findWidget(currentForm(), "w_boxes")?.widget.x).toBe(216)
    expect(reads.count).toBe(1)

    reads.count = 0
    pointer("pointermove", 340, 140)
    expect(reads.count).toBe(1)

    // The release records the drag for undo and redraws nothing.
    reads.count = 0
    pointer("pointerup", 340, 140)
    expect(reads.count).toBe(0)
  })
})
