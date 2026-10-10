/**
 * The view's shortcuts on the sheets (specs/workbench): Ctrl+1 fits the sheet, and with a
 * component selected Escape deselects, Delete removes, Ctrl+D duplicates and the arrows
 * nudge as one undo step per burst; none of it from a field or a select, nor on the
 * schema section, nor in Preview, where only Ctrl+1 applies.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, renderHook } from "@testing-library/react"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import useWorkbenchShortcuts from "../useWorkbenchShortcuts"
import { currentForm, loadForm } from "./fixtures"

const key = (init: KeyboardEventInit, target: EventTarget = window) =>
  target.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init }))
const widgets = () => currentForm().pages[0].widgets

describe("useWorkbenchShortcuts on the sheets", () => {
  beforeEach(() => {
    loadForm()
    useWorkbenchViewStore.setState({ selectedId: "w_boxes" })
    renderHook(() => useWorkbenchShortcuts())
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it("deselects on Escape and removes the selected component on Delete or Backspace", () => {
    key({ key: "Escape" })
    expect(useWorkbenchViewStore.getState().selectedId).toBeNull()

    useWorkbenchViewStore.setState({ selectedId: "w_boxes" })
    key({ key: "Delete" })
    expect(widgets().map((widget) => widget.id)).toEqual(["w_grid"])
    expect(useWorkbenchViewStore.getState().selectedId).toBeNull()
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
  })

  it("duplicates the selected component on Ctrl+D and selects the copy", () => {
    key({ key: "d", ctrlKey: true })

    expect(widgets()).toHaveLength(3)
    const copy = widgets()[1]
    expect(copy).toMatchObject({ type: "collection", title: "Policy", x: 32, y: 32 })
    expect(useWorkbenchViewStore.getState().selectedId).toBe(copy.id)
  })

  it("nudges by a grid step, or a pixel with Shift, a burst of presses being one undo step", () => {
    vi.useFakeTimers()
    key({ key: "ArrowRight" })
    key({ key: "ArrowDown" })
    key({ key: "ArrowLeft", shiftKey: true })
    expect(widgets()[0]).toMatchObject({ x: 23, y: 24 })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)

    vi.advanceTimersByTime(1000)
    key({ key: "ArrowUp" })
    expect(widgets()[0]).toMatchObject({ x: 23, y: 16 })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(2)

    // An undo inside the burst ends it: the next nudge is a step of its own, and the
    // undone step is not left to redo over it.
    key({ key: "z", ctrlKey: true })
    expect(widgets()[0]).toMatchObject({ x: 23, y: 24 })
    expect(useWorkbenchFormStore.getState().redoStack).toHaveLength(1)
    key({ key: "ArrowRight" })
    expect(widgets()[0]).toMatchObject({ x: 31, y: 24 })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(2)
    expect(useWorkbenchFormStore.getState().redoStack).toHaveLength(0)
    key({ key: "z", ctrlKey: true })
    expect(widgets()[0]).toMatchObject({ x: 23, y: 24 })
  })

  it("fits the sheet on Ctrl+1", () => {
    const fitZoom = vi.fn()
    useWorkbenchViewStore.setState({ fitZoom })

    key({ key: "1", ctrlKey: true })

    expect(fitZoom).toHaveBeenCalledTimes(1)
  })

  it("leaves keys in a field or a select alone, and does nothing on the schema section", () => {
    const select = document.createElement("select")
    document.body.appendChild(select)
    key({ key: "Delete" }, select)
    key({ key: "Backspace" }, select)
    expect(widgets()).toHaveLength(2)
    select.remove()

    useWorkbenchViewStore.setState({ section: "schema" })
    key({ key: "Delete" })
    key({ key: "d", ctrlKey: true })
    expect(widgets()).toHaveLength(2)
  })

  it("in Preview, fits the sheet on Ctrl+1 but neither edits the sheet nor undoes", () => {
    const fitZoom = vi.fn()
    const undo = vi.fn()
    useWorkbenchViewStore.setState({ section: "preview", fitZoom })
    useWorkbenchFormStore.setState({ undo })

    key({ key: "1", ctrlKey: true })
    key({ key: "Delete" })
    key({ key: "d", ctrlKey: true })
    key({ key: "ArrowRight" })
    key({ key: "z", ctrlKey: true })

    expect(fitZoom).toHaveBeenCalledTimes(1)
    expect(widgets()).toHaveLength(2)
    expect(widgets()[0]).toMatchObject({ x: 16, y: 16 })
    expect(undo).not.toHaveBeenCalled()
  })
})
