/**
 * The sheet (specs/workbench): components drawn where the form places them, selected by
 * a press, moved and resized by drags that snap and undo as one step, a component dragged
 * out of the palette and dropped on the sheet, the sheet fitted to its viewport, the
 * components read as laid out and stacked as added, and in Preview the components left
 * where they are with their cells taking the quote.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../../stores/useWorkbenchPreviewStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { CHART_OPTIONS, HEADING_OPTIONS, TABLE_OPTIONS, updateWidget } from "../../utils/workbenchForm"
import SheetCanvas from "../SheetCanvas"
import WorkbenchPalette from "../WorkbenchPalette"
import { coverForm, currentForm, drag, headingWidget, loadForm, releasePointer, sheetForm, stubLayout } from "./fixtures"

const widget = (id: string) => currentForm().pages[0].widgets.find((candidate) => candidate.id === id)
const frame = (id: string) => screen.getByTestId(`sheet-widget-${id}`)
/** The components on the sheet, by id, in the page's order: the order they are read in. */
const order = () => screen.getAllByTestId(/^sheet-widget-/).map((element) => element.dataset.testid?.replace("sheet-widget-", ""))
/** A component's place in the stack. */
const layer = (id: string) => Number(frame(id).style.zIndex)

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
    loadForm({ ...sheetForm(), pages: [{ id: "p1", title: "Sheet 1", show_when: { match: "all" as const, conditions: [] }, widgets: [{ id: "w", type: "collection", x: 0, y: 0, w: 200, h: 100, title: "", columns: 3, fields: [], show_when: { match: "all" as const, conditions: [] } }] }] })
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
    // Over every component on the sheet, the selected one's place included.
    expect(screen.getByTestId("drop-ghost")).toHaveStyle({ zIndex: "4" })
    act(() => {
      window.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, clientX: 300, clientY: 500 }))
    })

    const added = currentForm().pages[0].widgets[2]
    expect(added).toMatchObject({ type: "collection", x: 288, y: 488, w: 720, h: 120, fields: [], columns: 3 })
    expect(useWorkbenchViewStore.getState().selectedId).toBe(added.id)
    expect(useWorkbenchViewStore.getState().creating).toBeNull()
  })

  it("adds a Chart dropped out of the palette at its size, with a new Chart's options and no fields, selecting it", () => {
    render(
      <>
        <WorkbenchPalette />
        <SheetCanvas />
      </>,
    )

    act(() => {
      screen.getByTestId("palette-item-chart").dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 0, clientX: 10, clientY: 10 }))
      window.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, clientX: 300, clientY: 500 }))
    })
    expect(screen.getByTestId("drop-ghost")).toHaveTextContent("Chart")
    act(() => {
      window.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, clientX: 300, clientY: 500 }))
    })

    const added = currentForm().pages[0].widgets[2]
    expect(added).toMatchObject({ type: "chart", x: 288, y: 488, w: 480, h: 280, fields: [], ...CHART_OPTIONS })
    expect(useWorkbenchViewStore.getState().selectedId).toBe(added.id)
    expect(within(frame(added.id)).getByText("Choose its fields from the schema in the panel on the right")).toBeInTheDocument()
  })

  it("adds a Heading dropped out of the palette at its size, medium with no text, marked and asking for some until it has it", () => {
    render(
      <>
        <WorkbenchPalette />
        <SheetCanvas />
      </>,
    )

    act(() => {
      screen.getByTestId("palette-item-heading").dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 0, clientX: 10, clientY: 10 }))
      window.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, clientX: 300, clientY: 500 }))
    })
    expect(screen.getByTestId("drop-ghost")).toHaveTextContent("Heading")
    act(() => {
      window.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, clientX: 300, clientY: 500 }))
    })

    const added = currentForm().pages[0].widgets[2]
    expect(added).toMatchObject({ type: "heading", x: 288, y: 488, w: 480, h: 48, text: "", description: "", ...HEADING_OPTIONS })
    expect(useWorkbenchViewStore.getState().selectedId).toBe(added.id)
    expect(frame(added.id)).toHaveAttribute("title", "A Heading has no text")
    expect(within(frame(added.id)).getByText("Type its text in the panel on the right")).toBeInTheDocument()

    act(() => useWorkbenchFormStore.getState().change((spec) => updateWidget(spec, added.id, { text: "Equipment schedule" })))
    expect(screen.getByRole("group", { name: 'Heading "Equipment schedule"' })).not.toHaveAttribute("title")
    expect(within(frame(added.id)).getByRole("heading", { level: 3, name: "Equipment schedule" })).toBeInTheDocument()
  })

  /** `sheetForm` with Headings below its components, each always shown. */
  const headed = (...headings: { id: string; text: string; size: "large" | "medium" | "small"; y: number }[]) => {
    const form = sheetForm()
    for (const { id, text, size, y } of headings) form.pages[0].widgets.push(headingWidget(id, text, { size, y }))
    return form
  }

  it("resizes a Heading no smaller than 80 by 24", () => {
    loadForm(headed({ id: "w_head", text: "Equipment schedule", size: "medium", y: 384 }))
    render(<SheetCanvas />)
    fireEvent.pointerDown(frame("w_head"), { button: 0 })
    releasePointer()

    drag(frame("w_head").querySelector('[data-handle="s"]')!, [256, 424], [256, 300])
    expect(widget("w_head")).toMatchObject({ y: 384, h: 24 })
    drag(frame("w_head").querySelector('[data-handle="w"]')!, [16, 396], [900, 396])
    expect(widget("w_head")).toMatchObject({ x: 416, w: 80 })
  })

  it("in Preview draws a Heading's text for the underwriter, and one with no text only as the frame its problem draws", () => {
    loadForm(headed({ id: "w_title", text: "Equipment schedule", size: "large", y: 384 }, { id: "w_blank", text: "  ", size: "medium", y: 440 }))
    useWorkbenchViewStore.setState({ section: "preview" })
    render(<SheetCanvas />)

    expect(within(frame("w_title")).getByRole("heading", { level: 2, name: "Equipment schedule" })).toBeInTheDocument()
    expect(frame("w_title")).not.toHaveAttribute("title")
    expect(frame("w_blank")).toHaveAttribute("title", "A Heading has no text")
    expect(frame("w_blank").querySelector(".border-dashed")).not.toBeNull()
    expect(within(frame("w_blank")).queryByRole("heading")).not.toBeInTheDocument()
    expect(within(frame("w_blank")).queryByText("Type its text in the panel on the right")).not.toBeInTheDocument()
  })

  it("draws the components in reading order, row by row from the top, stacked in the order they were added", () => {
    const form = sheetForm()
    // A title and a section's heading added after what they sit above, and a note added on top of the grid.
    form.pages[0].widgets.push(
      headingWidget("w_title", "Motor quote", { y: 0 }),
      headingWidget("w_section", "Equipment", { y: 136 }),
      headingWidget("w_note", "Over the grid", { y: 160 }),
    )
    loadForm(form)
    render(<SheetCanvas />)

    // Read as laid out; a tie, the grid and the note at one place, in the order added.
    expect(order()).toEqual(["w_title", "w_boxes", "w_section", "w_grid", "w_note"])
    // Stacked as added, so the note covers the grid; selected, the grid covers everything.
    expect(["w_boxes", "w_grid", "w_title", "w_section", "w_note"].map(layer)).toEqual([1, 2, 3, 4, 5])
    fireEvent.pointerDown(frame("w_grid"), { button: 0 })
    releasePointer()
    expect(layer("w_grid")).toBe(6)

    // A move changes the reading order, never the stack.
    act(() => useWorkbenchFormStore.getState().change((spec) => updateWidget(spec, "w_title", { y: 400 })))
    expect(order()).toEqual(["w_boxes", "w_section", "w_grid", "w_note", "w_title"])
    expect(layer("w_title")).toBe(3)
  })

  it("in Preview reads the components that show as they sit, stacked in the order they were added", () => {
    const form = sheetForm()
    // Added last but sitting first, over the grid, and hidden while the policy is not in California.
    const californian = { match: "all" as const, conditions: [{ table: "policy", column: "policy.state", op: "equals" as const, values: ["CA"] }] }
    form.pages[0].widgets.push(
      headingWidget("w_title", "Motor quote", { y: 0 }),
      headingWidget("w_note", "Over the grid", { y: 160 }),
      headingWidget("w_ca", "California", { y: 136, show_when: californian }),
    )
    loadForm(form)
    useWorkbenchViewStore.setState({ section: "preview" })
    render(<SheetCanvas />)

    expect(order()).toEqual(["w_title", "w_boxes", "w_grid", "w_note"])
    expect(["w_boxes", "w_grid", "w_title", "w_note"].map(layer)).toEqual([1, 2, 3, 4])
  })

  it("keeps the keyboard's focus, and the selection, on a component moved past another", () => {
    const form = sheetForm()
    form.pages[0].widgets.push(headingWidget("w_title", "Motor quote", { y: 0 }))
    loadForm(form)
    render(<SheetCanvas />)
    act(() => frame("w_title").focus())
    expect(useWorkbenchViewStore.getState().selectedId).toBe("w_title")

    act(() => useWorkbenchFormStore.getState().change((spec) => updateWidget(spec, "w_title", { y: 400 })))
    expect(order()).toEqual(["w_boxes", "w_grid", "w_title"])
    expect(document.activeElement).toBe(frame("w_title"))
    expect(useWorkbenchViewStore.getState().selectedId).toBe("w_title")
  })

  it("puts the eye of a right-aligned Heading with conditions on its left, clear of its text", () => {
    const ny = { match: "all" as const, conditions: [{ table: "policy", column: "policy.state", op: "equals" as const, values: ["NY"] }] }
    const form = sheetForm()
    form.pages[0].widgets.push(
      headingWidget("w_left", "New York", { y: 384, show_when: ny }),
      headingWidget("w_right", "New York", { y: 448, align: "right", show_when: ny }),
    )
    loadForm(form)
    render(<SheetCanvas />)
    const eye = (id: string) => within(frame(id)).getByRole("img", { name: /Shows when State equals NY/ })

    expect(eye("w_left")).toHaveClass("right-1")
    expect(eye("w_right")).toHaveClass("left-1")
    expect(eye("w_right")).not.toHaveClass("right-1")
  })

  it("fits what is on the sheet to the viewport when it first shows, and sizes the sheet to hold its components", () => {
    loadForm({ ...sheetForm(), pages: [{ id: "p1", title: "Sheet 1", show_when: { match: "all" as const, conditions: [] }, widgets: [{ id: "w", type: "tableInput", x: 0, y: 0, w: 1968, h: 100, title: "", rows: 3, ...TABLE_OPTIONS, fields: [], show_when: { match: "all" as const, conditions: [] } }] }] })
    render(<SheetCanvas />)

    // 1000 wide less the padding, over the component's reach plus the edge, in steps of 5%.
    expect(useWorkbenchViewStore.getState().zoom).toBe(0.45)
    expect(screen.getByTestId("sheet")).toHaveStyle({ width: "2080px", transform: "scale(0.45)" })
  })

  it("fits a very wide sheet at the zoom's minimum, never at nothing", () => {
    loadForm({ ...sheetForm(), pages: [{ id: "p1", title: "Sheet 1", show_when: { match: "all" as const, conditions: [] }, widgets: [{ id: "w", type: "tableInput", x: 0, y: 0, w: 100_000, h: 100, title: "", rows: 3, ...TABLE_OPTIONS, fields: [], show_when: { match: "all" as const, conditions: [] } }] }] })
    render(<SheetCanvas />)

    expect(useWorkbenchViewStore.getState().zoom).toBe(0.25)
    expect(screen.getByTestId("sheet")).toHaveStyle({ transform: "scale(0.25)" })
  })

  it("keeps a zoom chosen since the sheets were fitted when they show again, until the form is read anew", () => {
    const wide = { ...sheetForm(), pages: [{ id: "p1", title: "Sheet 1", show_when: { match: "all" as const, conditions: [] }, widgets: [{ id: "w", type: "tableInput" as const, x: 0, y: 0, w: 1968, h: 100, title: "", rows: 3, ...TABLE_OPTIONS, fields: [], show_when: { match: "all" as const, conditions: [] } }] }] }
    loadForm(wide)
    const first = render(<SheetCanvas />)
    expect(useWorkbenchViewStore.getState().zoom).toBe(0.45)
    act(() => useWorkbenchViewStore.getState().setZoom(1))
    first.unmount()

    // Back from the schema, or from the pipeline editor: the chosen zoom stands.
    render(<SheetCanvas />)
    expect(useWorkbenchViewStore.getState().zoom).toBe(1)
    cleanup()

    // Another form read, as a branch switch brings: fitted afresh.
    act(() => useWorkbenchFormStore.setState({ status: "loading" }))
    act(() => loadForm(wide))
    render(<SheetCanvas />)
    expect(useWorkbenchViewStore.getState().zoom).toBe(0.45)
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

  it("while building draws a component the sample hides faded with its eye struck through, and one that shows with its eye", () => {
    const form = coverForm()
    form.pages[0].widgets[1].show_when = { match: "all", conditions: [{ table: "policy", column: "policy.state", op: "equals", values: ["NY"] }] }
    loadForm(form)
    render(<SheetCanvas />)

    const body = (id: string) => frame(id).querySelector("[data-hidden]")
    expect(body("w_grid")).not.toBeNull()
    expect(within(frame("w_grid")).getByRole("img", { name: "Hidden for the sample. Shows when State equals NY" })).toBeInTheDocument()
    expect(within(frame("w_boxes")).queryByRole("img")).not.toBeInTheDocument()

    fireEvent.change(within(frame("w_boxes")).getByRole("combobox", { name: "State" }), { target: { value: "NY" } })
    expect(body("w_grid")).toBeNull()
    expect(within(frame("w_grid")).getByRole("img", { name: "Shows when State equals NY" })).toBeInTheDocument()

    // A component on a sheet the sample hides is hidden with it, and still drawn to edit.
    act(() => useWorkbenchViewStore.getState().showPage("p2"))
    expect(body("w_year")).not.toBeNull()
  })

  it("in Preview draws only what the quote shows, on the first sheet that shows in place of a hidden one chosen", () => {
    const form = coverForm()
    form.pages[0].widgets[1].show_when = { match: "all", conditions: [{ table: "policy", column: "policy.state", op: "equals", values: ["NY"] }] }
    loadForm(form)
    useWorkbenchViewStore.setState({ section: "preview", pageId: "p2" })
    render(<SheetCanvas />)

    // The quote leaves Cover unticked: Sheet 1 shows in its place, without the hidden Table.
    expect(frame("w_boxes")).toBeInTheDocument()
    expect(screen.queryByTestId("sheet-widget-w_grid")).not.toBeInTheDocument()
    expect(within(frame("w_boxes")).queryByRole("img")).not.toBeInTheDocument()

    fireEvent.change(within(frame("w_boxes")).getByRole("combobox", { name: "State" }), { target: { value: "NY" } })
    expect(frame("w_grid")).toBeInTheDocument()
    fireEvent.click(within(frame("w_boxes")).getByRole("checkbox", { name: "Cover" }))
    expect(frame("w_year")).toBeInTheDocument()
    expect(screen.queryByTestId("sheet-widget-w_boxes")).not.toBeInTheDocument()
  })

  it("in Preview says so while no sheet shows", () => {
    const form = coverForm()
    form.pages[0].show_when = { match: "all", conditions: [{ table: "policy", column: "policy.year", op: "filled", values: [] }] }
    loadForm(form)
    useWorkbenchViewStore.setState({ section: "preview" })
    render(<SheetCanvas />)

    expect(screen.getByText("No sheet shows for this quote.")).toBeInTheDocument()
    expect(screen.queryByTestId("sheet")).not.toBeInTheDocument()
  })
})
