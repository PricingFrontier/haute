/**
 * The sheet (specs/workbench): components drawn where the form places them, selected by
 * a press, moved and resized by drags that snap and undo as one step, a component dragged
 * out of the palette and dropped on the sheet, the sheet fitted to its viewport, and in
 * Preview the components left where they are with their cells taking the quote.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../../stores/useWorkbenchPreviewStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import SheetCanvas from "../SheetCanvas"
import WorkbenchPalette from "../WorkbenchPalette"
import { currentForm, drag, loadForm, releasePointer, sheetForm, stubLayout } from "./fixtures"

const widget = (id: string) => currentForm().pages[0].widgets.find((candidate) => candidate.id === id)
const frame = (id: string) => screen.getByTestId(`sheet-widget-${id}`)

describe("SheetCanvas", () => {
  beforeEach(() => {
    stubLayout()
    loadForm()
    useWorkbenchPreviewStore.setState({ quote: {}, checked: false, price: null })
  })

  afterEach(() => {
    releasePointer()
    cleanup()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it("draws the showing sheet's components where the form places them, with what they show", () => {
    render(<SheetCanvas />)

    expect(frame("w_boxes")).toHaveStyle({ left: "16px", top: "16px", width: "400px", height: "120px" })
    expect(within(frame("w_boxes")).getByText("State")).toBeInTheDocument()
    expect(within(frame("w_grid")).getByText("Equipment")).toBeInTheDocument()
    expect(within(frame("w_grid")).getAllByRole("row")).toHaveLength(4)
    expect(screen.getByRole("group", { name: 'Collection "Policy"' })).toBeInTheDocument()

    act(() => {
      useWorkbenchViewStore.getState().showPage("p2")
    })
    expect(screen.queryByTestId("sheet-widget-w_boxes")).not.toBeInTheDocument()
  })

  it("marks a component that shows nothing or a column that is gone, naming the problem", () => {
    loadForm({ ...sheetForm(), pages: [{ id: "p1", title: "Sheet 1", widgets: [{ id: "w", type: "collection", x: 0, y: 0, w: 200, h: 100, title: "", columns: 3, fields: [] }] }] })
    render(<SheetCanvas />)

    expect(screen.getByTitle("A Collection shows no fields")).toBeInTheDocument()
    expect(within(frame("w")).getByText("Choose its fields from the schema in the panel on the right")).toBeInTheDocument()
  })

  it("selects a component on press and deselects on the empty sheet, recording nothing", () => {
    render(<SheetCanvas />)

    fireEvent.pointerDown(frame("w_grid"), { button: 0 })
    expect(useWorkbenchViewStore.getState().selectedId).toBe("w_grid")
    expect(frame("w_grid")).toHaveAttribute("data-selected", "true")
    expect(frame("w_grid").querySelectorAll("[data-handle]")).toHaveLength(8)

    fireEvent.pointerDown(screen.getByTestId("sheet-viewport"), { button: 0 })
    expect(useWorkbenchViewStore.getState().selectedId).toBeNull()
    expect(useWorkbenchFormStore.getState().undoStack).toEqual([])
  })

  it("moves a component by dragging it, snapped to the grid and divided by the zoom, as one undo step", () => {
    render(<SheetCanvas />)

    drag(frame("w_boxes"), [100, 100], [153, 121])
    expect(widget("w_boxes")).toMatchObject({ x: 72, y: 40 })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
    expect(useWorkbenchFormStore.getState().dirty).toBe(true)

    act(() => {
      useWorkbenchViewStore.getState().setZoom(0.5)
    })
    drag(frame("w_boxes"), [100, 100], [140, 100])
    expect(widget("w_boxes")).toMatchObject({ x: 152, y: 40 })

    act(() => {
      useWorkbenchFormStore.getState().undo()
      useWorkbenchFormStore.getState().undo()
    })
    expect(widget("w_boxes")).toMatchObject({ x: 16, y: 16 })
  })

  it("resizes a selected component by a handle, keeping its minimum size", () => {
    render(<SheetCanvas />)
    fireEvent.pointerDown(frame("w_boxes"), { button: 0 })
    releasePointer()

    drag(frame("w_boxes").querySelector('[data-handle="se"]')!, [416, 136], [456, 170])
    expect(widget("w_boxes")).toMatchObject({ x: 16, y: 16, w: 440, h: 152 })

    drag(frame("w_boxes").querySelector('[data-handle="w"]')!, [16, 80], [900, 80])
    expect(widget("w_boxes")).toMatchObject({ w: 160 })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(2)
  })

  it("adds a component dragged out of the palette where it is dropped, selecting it; a click adds nothing", () => {
    render(
      <>
        <WorkbenchPalette />
        <SheetCanvas />
      </>,
    )
    const item = screen.getByTestId("palette-item-collection")

    fireEvent.pointerDown(item, { button: 0, clientX: 10, clientY: 10 })
    fireEvent.pointerUp(window, { clientX: 10, clientY: 10 })
    expect(currentForm().pages[0].widgets).toHaveLength(2)

    act(() => {
      item.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 0, clientX: 10, clientY: 10 }))
      window.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, clientX: 2000, clientY: 500 }))
    })
    expect(screen.queryByTestId("drop-ghost")).not.toBeInTheDocument()
    act(() => {
      window.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, clientX: 300, clientY: 500 }))
    })
    expect(screen.getByTestId("drop-ghost")).toHaveTextContent("Collection")
    act(() => {
      window.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, clientX: 300, clientY: 500 }))
    })

    const added = currentForm().pages[0].widgets[2]
    expect(added).toMatchObject({ type: "collection", x: 288, y: 488, w: 720, h: 120, fields: [], columns: 3 })
    expect(useWorkbenchViewStore.getState().selectedId).toBe(added.id)
    expect(useWorkbenchViewStore.getState().creating).toBeNull()
  })

  it("fits what is on the sheet to the viewport when it first shows, and sizes the sheet to hold its components", () => {
    loadForm({ ...sheetForm(), pages: [{ id: "p1", title: "Sheet 1", widgets: [{ id: "w", type: "tableInput", x: 0, y: 0, w: 1968, h: 100, title: "", rows: 3, fields: [] }] }] })
    render(<SheetCanvas />)

    // 1000 wide less the padding, over the component's reach plus the edge, in steps of 5%.
    expect(useWorkbenchViewStore.getState().zoom).toBe(0.45)
    expect(screen.getByTestId("sheet")).toHaveStyle({ width: "2080px", transform: "scale(0.45)" })
  })

  it("fits a very wide sheet at the zoom's minimum, never at nothing", () => {
    loadForm({ ...sheetForm(), pages: [{ id: "p1", title: "Sheet 1", widgets: [{ id: "w", type: "tableInput", x: 0, y: 0, w: 100_000, h: 100, title: "", rows: 3, fields: [] }] }] })
    render(<SheetCanvas />)

    expect(useWorkbenchViewStore.getState().zoom).toBe(0.25)
    expect(screen.getByTestId("sheet")).toHaveStyle({ transform: "scale(0.25)" })
  })

  it("in Preview, neither selects nor moves a component, offers no drop, and types into the quote", () => {
    useWorkbenchViewStore.setState({ section: "preview" })
    render(
      <>
        <WorkbenchPalette />
        <SheetCanvas />
      </>,
    )

    fireEvent.pointerDown(frame("w_grid"), { button: 0 })
    expect(useWorkbenchViewStore.getState().selectedId).toBeNull()
    expect(frame("w_grid")).not.toHaveAttribute("data-selected")
    expect(frame("w_grid").querySelectorAll("[data-handle]")).toHaveLength(0)
    drag(frame("w_boxes"), [100, 100], [153, 121])
    expect(widget("w_boxes")).toMatchObject({ x: 16, y: 16 })
    expect(useWorkbenchFormStore.getState().undoStack).toEqual([])

    const item = screen.getByTestId("palette-item-collection")
    expect(item).toHaveAttribute("aria-disabled", "true")
    act(() => {
      item.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 0, clientX: 10, clientY: 10 }))
      window.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, clientX: 300, clientY: 500 }))
    })
    expect(screen.queryByTestId("drop-ghost")).not.toBeInTheDocument()
    expect(useWorkbenchViewStore.getState().creating).toBeNull()

    fireEvent.change(within(frame("w_boxes")).getByRole("combobox", { name: "State" }), { target: { value: "NY" } })
    expect(useWorkbenchPreviewStore.getState().quote).toEqual({ policy: [{ "policy.state": "NY" }] })
    expect(currentForm().sample).toEqual({})
  })
})
