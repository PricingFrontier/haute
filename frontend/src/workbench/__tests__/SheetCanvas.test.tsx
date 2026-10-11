/**
 * The sheet (specs/workbench): its sections stacked one under the next, each under the bar
 * naming it while building, each holding its components where the form places them,
 * selected by a press, moved (into the section their top edge is over, kept inside it) and
 * resized by drags that snap, only a resize past a section's bottom or a component taller
 * than its section growing one, keep what the press commits and undo as one step, a
 * component dragged out of the palette and dropped in a section, kept inside it, a section
 * dragged out of it splitting one, its line clear of every component, or put in before one
 * or below them all, the sheet fitted to its
 * viewport, the components read as laid out
 * and stacked as added, and in Preview only the sections and components the quote shows,
 * the sections under a hidden one closing up its space and a hidden component's place in
 * its section left empty, with their cells taking the quote, what shows held while a
 * pointer is pressed on the sheet and the section holding the focus kept in place on the
 * screen.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../../stores/useWorkbenchPreviewStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { CHART_OPTIONS, findWidget, HEADING_OPTIONS, TABLE_OPTIONS, updateWidget } from "../../utils/workbenchForm"
import PropertiesPanel from "../PropertiesPanel"
import SheetCanvas from "../SheetCanvas"
import WorkbenchPalette from "../WorkbenchPalette"
import { addTo, coverForm, currentForm, drag, headingWidget, loadForm, releasePointer, sectionedForm, sheet, sheetForm, stubLayout, widgetsOf } from "./fixtures"

const widget = (id: string) => findWidget(currentForm(), id)?.widget
/** The section a component is in. */
const sectionOf = (id: string) => findWidget(currentForm(), id)?.section.id
/** The first sheet's sections' heights, in order. */
const heights = () => currentForm().pages[0].sections.map((section) => section.height)
const frame = (id: string) => screen.getByTestId(`sheet-widget-${id}`)
/** A section as the sheet draws it. */
const band = (id: string) => screen.getByTestId(`sheet-section-${id}`)
/** The components on the sheet, by id, in the page's order: the order they are read in. */
const order = () => screen.getAllByTestId(/^sheet-widget-/).map((element) => element.dataset.testid?.replace("sheet-widget-", ""))
/** A component's place in the stack. */
const layer = (id: string) => Number(frame(id).style.zIndex)
/** A pointer pressed on `target` at (x, y), or moved or released there over the window. */
const pointer = (type: "pointerdown" | "pointermove" | "pointerup", x: number, y: number, target: EventTarget = window) =>
  act(() => {
    target.dispatchEvent(new MouseEvent(type, { bubbles: true, button: 0, clientX: x, clientY: y }))
  })
/** A turn passes, under fake timers. */
const turn = () =>
  act(() => {
    vi.advanceTimersByTime(0)
  })
/** The quote's cover ticked or not, as a value a press commits, or one typed elsewhere, sets it. */
const quoteCover = (ticked: boolean) => act(() => useWorkbenchPreviewStore.getState().setCell("policy", 0, "policy.cover", ticked))

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
    vi.useRealTimers()
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

  it("stacks the sheet's sections one under the next, each a group under the bar naming it, its components placed from its top-left corner", () => {
    const form = sectionedForm()
    // The note sits higher in its section than the boxes do in theirs.
    widgetsOf(form, 0, 2)[0].y = 0
    loadForm(form)
    render(<SheetCanvas />)

    // While building each sits under its bar, 24 tall.
    expect(band("s_policy")).toHaveStyle({ top: "24px", height: "152px" })
    expect(band("s_flood")).toHaveStyle({ top: "200px", height: "232px" })
    expect(band("s_notes")).toHaveStyle({ top: "456px", height: "72px" })
    expect(within(band("s_policy")).getByTestId("sheet-widget-w_boxes")).toHaveStyle({ left: "16px", top: "16px" })
    expect(within(band("s_flood")).getByTestId("sheet-widget-w_grid")).toHaveStyle({ left: "16px", top: "16px", width: "720px", height: "200px" })
    expect(within(band("s_notes")).getByTestId("sheet-widget-w_note")).toHaveStyle({ top: "0px" })
    // While building each is a group named by its title, its bar just above it.
    expect(screen.getByRole("group", { name: "Flood" })).toBe(band("s_flood"))
    expect(screen.getByTestId("section-header-s_flood")).toHaveStyle({ top: "176px" })
    // Read section by section, whatever their places in them.
    expect(order()).toEqual(["w_boxes", "w_grid", "w_note"])
  })

  it("while building draws a bar naming each section just above it, the selected one's handle under its bottom, and in Preview no bars, the sections stacked tight", () => {
    loadForm(sectionedForm())
    // Cover ticked in the quote, so Preview shows Flood too.
    useWorkbenchPreviewStore.setState({ quote: { policy: [{ "policy.cover": true }] } })
    render(<SheetCanvas />)
    const bar = (id: string) => screen.getByTestId(`section-header-${id}`)

    // Each bar 24 tall, its bottom its section's top.
    expect(bar("s_policy")).toHaveStyle({ top: "0px", height: "24px" })
    expect(bar("s_flood")).toHaveStyle({ top: "176px", height: "24px" })
    expect(bar("s_notes")).toHaveStyle({ top: "432px", height: "24px" })
    expect(screen.getByRole("button", { name: "Section 2, Flood" })).toBe(bar("s_flood"))
    // The handle setting a section's height only while it is selected, over the middle of its bottom.
    expect(screen.queryAllByTestId(/^section-edge-/)).toHaveLength(0)
    fireEvent.click(bar("s_flood"))
    expect(useWorkbenchViewStore.getState().sectionId).toBe("s_flood")
    expect(screen.getAllByTestId(/^section-edge-/)).toEqual([screen.getByTestId("section-edge-s_flood")])
    expect(screen.getByTestId("section-edge-s_flood")).toHaveStyle({ top: "428px", height: "8px" })

    act(() => useWorkbenchViewStore.getState().showSection("preview"))
    expect(screen.queryAllByTestId(/^section-(header|edge)-/)).toHaveLength(0)
    expect(band("s_policy")).toHaveStyle({ top: "0px" })
    expect(band("s_flood")).toHaveStyle({ top: "152px" })
    expect(band("s_notes")).toHaveStyle({ top: "384px" })
  })

  it("marks a component that shows nothing or a column that is gone, naming the problem", () => {
    loadForm({ ...sheetForm(), pages: [sheet("p1", "Sheet 1", [{ id: "w", type: "collection", x: 0, y: 0, w: 200, h: 100, title: "", columns: 3, fields: [], show_when: { match: "all" as const, conditions: [] } }])] })
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

  it("drags a component into the section its top edge is over and back up, snapped, kept inside the section it is over, which never grows, as one undo step", () => {
    loadForm(sectionedForm())
    render(<SheetCanvas />)

    // Pressed near its bottom: the pointer passes into Flood while the top edge, snapped,
    // is still over the policy's section, so it stops at that section's bottom, 152 less its
    // 120, and the section keeps its height.
    pointer("pointerdown", 100, 130, frame("w_boxes"))
    pointer("pointermove", 100, 229)
    expect(sectionOf("w_boxes")).toBe("s_policy")
    expect(widget("w_boxes")).toMatchObject({ x: 16, y: 32 })
    expect(heights()).toEqual([152, 232, 72])

    // Its top edge over Flood: placed in it, last in its order, and no section grows; the
    // policy's section, left empty, keeps its height.
    pointer("pointermove", 121, 380)
    expect(sectionOf("w_boxes")).toBe("s_flood")
    expect(widget("w_boxes")).toMatchObject({ x: 40, y: 88 })
    expect(widgetsOf(currentForm(), 0, 1).map((placed) => placed.id)).toEqual(["w_grid", "w_boxes"])
    expect(heights()).toEqual([152, 232, 72])
    // Under the pointer: Flood's top and its place in it, 200 and 88, add up to where it
    // started on the sheet, 40 under the policy's bar, and the 250 travelled, snapped.
    expect(band("s_flood")).toHaveStyle({ top: "200px" })
    expect(within(band("s_flood")).getByTestId("sheet-widget-w_boxes")).toHaveStyle({ left: "40px", top: "88px" })
    expect(band("s_notes")).toHaveStyle({ top: "456px" })

    // Back up, the pointer on Flood's bar and the top edge over the section above: in it, at
    // its bottom again, and Flood is as it was.
    pointer("pointermove", 100, 178)
    expect(sectionOf("w_boxes")).toBe("s_policy")
    expect(widget("w_boxes")).toMatchObject({ x: 16, y: 32 })
    expect(heights()).toEqual([152, 232, 72])
    expect(within(band("s_policy")).getByTestId("sheet-widget-w_boxes")).toHaveStyle({ top: "32px" })

    pointer("pointerup", 100, 178)
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
    act(() => useWorkbenchFormStore.getState().undo())
    expect(sectionOf("w_boxes")).toBe("s_policy")
    expect(widget("w_boxes")).toMatchObject({ x: 16, y: 16 })
    expect(heights()).toEqual([152, 232, 72])
  })

  it("drags a component that starts in a lower section from where it sits on the sheet, its section's top added, staying in that section", () => {
    loadForm(sectionedForm())
    render(<SheetCanvas />)

    // The Table sits 16 down Flood, whose top is 200: 100 further down, its top edge 320 down
    // the sheet, is still over Flood, 120 down it, so it stops at Flood's bottom, 232 less its
    // 200, and Flood keeps its height.
    pointer("pointerdown", 100, 200, frame("w_grid"))
    pointer("pointermove", 100, 300)
    expect(sectionOf("w_grid")).toBe("s_flood")
    expect(widget("w_grid")).toMatchObject({ x: 16, y: 32 })
    expect(heights()).toEqual([152, 232, 72])
    expect(within(band("s_flood")).getByTestId("sheet-widget-w_grid")).toHaveStyle({ top: "32px" })

    // 124 above where it started is 92 down the sheet: in the policy's section, under its
    // bar. Taller than that section, it sits at its top and the section grows to hold it,
    // pushing Flood down.
    pointer("pointermove", 100, 76)
    expect(sectionOf("w_grid")).toBe("s_policy")
    expect(widget("w_grid")).toMatchObject({ x: 16, y: 0 })
    expect(heights()).toEqual([200, 232, 72])
    expect(band("s_flood")).toHaveStyle({ top: "248px" })

    pointer("pointerup", 100, 76)
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
  })

  it("stops a component dragged down at its section's bottom, never growing it, and moves it into the next section once its top edge is over that section's bar", () => {
    loadForm(sectionedForm())
    render(<SheetCanvas />)
    const placed = () => ({ section: sectionOf("w_boxes"), y: widget("w_boxes")?.y })

    // The boxes start 40 down the sheet, 16 down the policy's section, 152 tall.
    pointer("pointerdown", 100, 100, frame("w_boxes"))
    pointer("pointermove", 100, 108)
    expect(placed()).toEqual({ section: "s_policy", y: 24 })
    // Further down, it stops at the section's bottom, 152 less its 120, however far the
    // pointer goes while its top edge is over the section: here 168 down the sheet.
    pointer("pointermove", 100, 140)
    expect(placed()).toEqual({ section: "s_policy", y: 32 })
    pointer("pointermove", 100, 228)
    expect(placed()).toEqual({ section: "s_policy", y: 32 })
    expect(heights()).toEqual([152, 232, 72])
    expect(within(band("s_policy")).getByTestId("sheet-widget-w_boxes")).toHaveStyle({ top: "32px" })
    expect(band("s_flood")).toHaveStyle({ top: "200px" })

    // Its top edge 176 down the sheet, over Flood's bar: into Flood, at its top.
    pointer("pointermove", 100, 236)
    expect(placed()).toEqual({ section: "s_flood", y: 0 })
    expect(within(band("s_flood")).getByTestId("sheet-widget-w_boxes")).toHaveStyle({ top: "0px" })
    // Over Flood itself, 240 down the sheet: 40 down it.
    pointer("pointermove", 100, 300)
    expect(placed()).toEqual({ section: "s_flood", y: 40 })
    expect(heights()).toEqual([152, 232, 72])

    pointer("pointerup", 100, 300)
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
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

  it("grows a section to hold a component resized past its bottom, the sections under it moving down and the sheet lengthening", () => {
    loadForm(sectionedForm())
    render(<SheetCanvas />)
    fireEvent.pointerDown(frame("w_boxes"), { button: 0 })
    releasePointer()

    act(() => drag(frame("w_boxes").querySelector('[data-handle="s"]')!, [216, 136], [216, 537]))
    expect(widget("w_boxes")).toMatchObject({ y: 16, h: 520 })
    expect(heights()).toEqual([536, 232, 72])
    expect(band("s_flood")).toHaveStyle({ top: "584px" })
    expect(band("s_notes")).toHaveStyle({ top: "840px" })
    // The sections' 840 and their bars' 72, and room below them.
    expect(screen.getByTestId("sheet")).toHaveStyle({ height: "1152px" })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
  })

  it("grows a section for a component resized past its bottom, pushing the next one down, where one moved past it stops at it", () => {
    loadForm(sectionedForm())
    render(<SheetCanvas />)

    // Moved down past the policy's section's bottom: it stops there, 152 less its 120.
    act(() => drag(frame("w_boxes"), [100, 100], [100, 180]))
    expect(widget("w_boxes")).toMatchObject({ y: 32, h: 120 })
    expect(heights()).toEqual([152, 232, 72])

    // Its bottom handle dragged 40 past the section's bottom: the section grows to its 192,
    // and Flood and Notes move down as far.
    act(() => drag(frame("w_boxes").querySelector('[data-handle="s"]')!, [216, 176], [216, 216]))
    expect(widget("w_boxes")).toMatchObject({ y: 32, h: 160 })
    expect(heights()).toEqual([192, 232, 72])
    expect(band("s_flood")).toHaveStyle({ top: "240px" })
    expect(band("s_notes")).toHaveStyle({ top: "496px" })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(2)
  })

  it("keeps a value typed in a cell, not yet committed, as another component is dragged or its own resized, an undo taking back the drag alone", () => {
    render(<SheetCanvas />)
    const cell = (row: number) => within(frame("w_grid")).getByRole("textbox", { name: `Item row ${row}` })
    /** Press a cell, as the builder does to type there, and type `text` into it, leaving it focused and uncommitted. */
    const typeInto = (row: number, text: string) => {
      fireEvent.pointerDown(cell(row), { button: 0 })
      act(() => cell(row).focus())
      fireEvent.change(cell(row), { target: { value: text } })
    }

    typeInto(1, "Pump")
    expect(currentForm().sample).toEqual({})
    // Pressing another component leaves the cell, which commits it, and the drag keeps it.
    pointer("pointerdown", 100, 100, frame("w_boxes"))
    pointer("pointermove", 153, 121)
    pointer("pointerup", 153, 121)
    expect(widget("w_boxes")).toMatchObject({ x: 72, y: 40 })
    expect(currentForm().sample).toEqual({ equipment: [{ "equipment.item": "Pump" }] })
    // The commit and the drag are a step each: one undo takes back the drag, the value staying.
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(2)
    act(() => useWorkbenchFormStore.getState().undo())
    expect(widget("w_boxes")).toMatchObject({ x: 16, y: 16 })
    expect(currentForm().sample).toEqual({ equipment: [{ "equipment.item": "Pump" }] })

    // So with a handle of the Table the cell is in, selected by the press in the cell.
    typeInto(2, "Hose")
    pointer("pointerdown", 736, 360, frame("w_grid").querySelector('[data-handle="se"]')!)
    pointer("pointermove", 776, 394)
    pointer("pointerup", 776, 394)
    expect(widget("w_grid")).toMatchObject({ x: 16, y: 160, w: 760, h: 232 })
    expect(currentForm().sample).toEqual({ equipment: [{ "equipment.item": "Pump" }, { "equipment.item": "Hose" }] })
  })

  it("keeps a title typed in the panel, not yet committed, as another component is dragged or the one it names resized", () => {
    render(
      <>
        <SheetCanvas />
        <PropertiesPanel />
      </>,
    )
    /** Type `text` into the panel's title, leaving it focused and uncommitted. */
    const retitle = (text: string) => {
      const title = screen.getByRole("textbox", { name: "Title" })
      act(() => title.focus())
      fireEvent.change(title, { target: { value: text } })
    }

    fireEvent.pointerDown(frame("w_grid"), { button: 0 })
    releasePointer()
    retitle("Contents")
    pointer("pointerdown", 100, 100, frame("w_boxes"))
    pointer("pointermove", 153, 121)
    pointer("pointerup", 153, 121)
    expect(widget("w_grid")).toMatchObject({ title: "Contents" })
    expect(widget("w_boxes")).toMatchObject({ title: "Policy", x: 72, y: 40 })

    // The dragged component's panel open now: its title typed, then a handle dragged.
    retitle("Policy details")
    pointer("pointerdown", 472, 160, frame("w_boxes").querySelector('[data-handle="se"]')!)
    pointer("pointermove", 512, 194)
    pointer("pointerup", 512, 194)
    expect(widget("w_boxes")).toMatchObject({ title: "Policy details", x: 72, y: 40, w: 440, h: 152 })
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
    expect(widgetsOf(currentForm())).toHaveLength(2)

    act(() => {
      item.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 0, clientX: 10, clientY: 10 }))
      window.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, clientX: 2000, clientY: 500 }))
    })
    expect(screen.queryByTestId("drop-ghost")).not.toBeInTheDocument()
    act(() => {
      window.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, clientX: 300, clientY: 412 }))
    })
    expect(screen.getByTestId("drop-ghost")).toHaveTextContent("Collection")
    // Over every component on the sheet, the selected one's place included.
    expect(screen.getByTestId("drop-ghost")).toHaveStyle({ zIndex: "4" })
    act(() => {
      window.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, clientX: 300, clientY: 412 }))
    })

    // 400 down the sheet is 376 down the section, under its bar.
    const added = widgetsOf(currentForm())[2]
    expect(added).toMatchObject({ type: "collection", x: 288, y: 376, w: 720, h: 120, fields: [], columns: 3 })
    // Inside the section, which keeps its height.
    expect(heights()).toEqual([560])
    expect(useWorkbenchViewStore.getState().selectedId).toBe(added.id)
    expect(useWorkbenchViewStore.getState().creating).toBeNull()
  })

  it("adds a component dropped out of the palette to the section the ghost's top edge is over, kept inside it, the ghost drawn where it lands", () => {
    loadForm(sectionedForm())
    render(
      <>
        <WorkbenchPalette />
        <SheetCanvas />
      </>,
    )

    act(() => {
      screen.getByTestId("palette-item-collection").dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 0, clientX: 10, clientY: 10 }))
      window.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, clientX: 300, clientY: 180 }))
    })
    // The pointer is on Flood's bar, but the ghost's top edge, a little above it and snapped,
    // 168 down the sheet, is over the policy's section: kept inside it, its bottom at the
    // section's, 152 less its 120 down it, and drawn there.
    expect(screen.getByTestId("drop-ghost")).toHaveStyle({ left: "288px", top: "56px", width: "720px", height: "120px" })
    act(() => {
      window.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, clientX: 300, clientY: 180 }))
    })

    const added = widgetsOf(currentForm())[1]
    expect(added).toMatchObject({ type: "collection", x: 288, y: 32, w: 720, h: 120 })
    expect(heights()).toEqual([152, 232, 72])
    expect(within(band("s_policy")).getByTestId(`sheet-widget-${added.id}`)).toHaveStyle({ top: "32px" })
    expect(band("s_flood")).toHaveStyle({ top: "200px" })
    expect(useWorkbenchViewStore.getState().selectedId).toBe(added.id)
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
  })

  it("draws the ghost of a component dragged over a lower section where it lands on the sheet, and adds it to that section", () => {
    loadForm(sectionedForm())
    render(
      <>
        <WorkbenchPalette />
        <SheetCanvas />
      </>,
    )

    act(() => {
      screen.getByTestId("palette-item-collection").dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 0, clientX: 10, clientY: 10 }))
      window.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, clientX: 300, clientY: 300 }))
    })
    // Its top edge, snapped, 288 down the sheet: over Flood, whose top is 200, 88 down it.
    expect(screen.getByTestId("drop-ghost")).toHaveStyle({ left: "288px", top: "288px" })
    act(() => {
      window.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, clientX: 300, clientY: 300 }))
    })

    const added = widgetsOf(currentForm(), 0, 1)[1]
    expect(added).toMatchObject({ type: "collection", x: 288, y: 88 })
    expect(heights()).toEqual([152, 232, 72])
    expect(within(band("s_flood")).getByTestId(`sheet-widget-${added.id}`)).toHaveStyle({ top: "88px" })
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
      window.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, clientX: 300, clientY: 236 }))
    })
    expect(screen.getByTestId("drop-ghost")).toHaveTextContent("Chart")
    act(() => {
      window.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, clientX: 300, clientY: 236 }))
    })

    // 224 down the sheet is 200 down the section, under its bar, where its 280 fit.
    const added = widgetsOf(currentForm())[2]
    expect(added).toMatchObject({ type: "chart", x: 288, y: 200, w: 480, h: 280, fields: [], ...CHART_OPTIONS })
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
      window.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, clientX: 300, clientY: 524 }))
    })
    expect(screen.getByTestId("drop-ghost")).toHaveTextContent("Heading")
    act(() => {
      window.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, clientX: 300, clientY: 524 }))
    })

    const added = widgetsOf(currentForm())[2]
    expect(added).toMatchObject({ type: "heading", x: 288, y: 488, w: 480, h: 48, text: "", description: "", ...HEADING_OPTIONS })
    expect(useWorkbenchViewStore.getState().selectedId).toBe(added.id)
    expect(frame(added.id)).toHaveAttribute("title", "A Heading has no text")
    expect(within(frame(added.id)).getByText("Type its text in the panel on the right")).toBeInTheDocument()

    act(() => useWorkbenchFormStore.getState().change((spec) => updateWidget(spec, added.id, { text: "Equipment schedule" })))
    expect(screen.getByRole("group", { name: 'Heading "Equipment schedule"' })).not.toHaveAttribute("title")
    expect(within(frame(added.id)).getByRole("heading", { level: 3, name: "Equipment schedule" })).toBeInTheDocument()
  })

  /** The sheet with the palette beside it. */
  const withPalette = () =>
    render(
      <>
        <WorkbenchPalette />
        <SheetCanvas />
      </>,
    )
  /** The palette's Section pressed and dragged to (x, y), not yet released. */
  const dragSection = (x: number, y: number) => {
    pointer("pointerdown", 10, 10, screen.getByTestId("palette-item-section"))
    pointer("pointermove", x, y)
  }
  const ghost = () => screen.getByTestId("drop-ghost")
  const sections = () => currentForm().pages[0].sections
  const selectedSection = () => useWorkbenchViewStore.getState().sectionId

  it("keeps a component dropped out of the palette near a section's bottom inside it, the ghost drawn there, the section keeping its height and those under it their places", () => {
    loadForm(sectionedForm())
    withPalette()

    // The ghost's top edge, snapped, 408 down the sheet: over Flood, 208 down it, where a
    // Collection's 120 would reach past its bottom. Kept inside it, at 232 less 120, and
    // drawn there.
    pointer("pointerdown", 10, 10, screen.getByTestId("palette-item-collection"))
    pointer("pointermove", 300, 420)
    expect(ghost()).toHaveStyle({ left: "288px", top: "312px", width: "720px", height: "120px" })
    pointer("pointerup", 300, 420)

    const added = widgetsOf(currentForm(), 0, 1)[1]
    expect(added).toMatchObject({ type: "collection", x: 288, y: 112 })
    expect(heights()).toEqual([152, 232, 72])
    expect(within(band("s_flood")).getByTestId(`sheet-widget-${added.id}`)).toHaveStyle({ top: "112px" })
    expect(band("s_notes")).toHaveStyle({ top: "456px" })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
  })

  it("grows a section for a component dropped out of the palette taller than it, at its top, pushing the sections under it down", () => {
    loadForm(sectionedForm())
    withPalette()

    // Over Flood, 232 tall, a Chart 280 tall: the ghost at Flood's top, at its size.
    pointer("pointerdown", 10, 10, screen.getByTestId("palette-item-chart"))
    pointer("pointermove", 300, 260)
    expect(ghost()).toHaveStyle({ left: "288px", top: "200px", width: "480px", height: "280px" })
    pointer("pointerup", 300, 260)

    const added = widgetsOf(currentForm(), 0, 1)[1]
    expect(added).toMatchObject({ type: "chart", x: 288, y: 0, h: 280 })
    expect(heights()).toEqual([152, 280, 72])
    expect(within(band("s_flood")).getByTestId(`sheet-widget-${added.id}`)).toHaveStyle({ top: "0px" })
    // Notes, under it, moves down by the 48 Flood grew.
    expect(band("s_notes")).toHaveStyle({ top: "504px" })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
  })

  it("draws a line across the sheet where a Section dragged out of the palette would start, saying whether it splits one, and none off the sheet", () => {
    loadForm(sectionedForm())
    withPalette()

    // Inside Flood, whose top is 200, below its Table, which reaches 216 down it: the grid
    // line nearest the pointer, 224 down it.
    dragSection(300, 422)
    expect(ghost()).toHaveTextContent("New section from here")
    // Two thick, centred on the line.
    expect(ghost()).toHaveStyle({ top: "423px", height: "2px" })
    // On Flood's bar: in before it, along the bar's top.
    pointer("pointermove", 300, 188)
    expect(ghost()).toHaveTextContent(/^New section$/)
    expect(ghost()).toHaveStyle({ top: "175px" })
    // At its very bottom: in after it.
    pointer("pointermove", 300, 430)
    expect(ghost()).toHaveTextContent(/^New section$/)
    expect(ghost()).toHaveStyle({ top: "431px" })
    // Below the last section, which ends 528 down the sheet: at the end.
    pointer("pointermove", 300, 600)
    expect(ghost()).toHaveTextContent(/^New section$/)
    expect(ghost()).toHaveStyle({ top: "527px" })
    pointer("pointermove", 2000, 300)
    expect(screen.queryByTestId("drop-ghost")).not.toBeInTheDocument()
    pointer("pointerup", 2000, 300)
    expect(heights()).toEqual([152, 232, 72])
  })

  it("splits a section where a Section dragged out of the palette is dropped in it, the components under the line moving into the new one, which is named and selected, as one undo step", () => {
    withPalette()

    // 170 down the sheet is 146 down the one section, under its bar: the grid line nearest is
    // 144, between the boxes, which reach 136, and the Table, 160 down it.
    dragSection(300, 170)
    expect(ghost()).toHaveTextContent("New section from here")
    expect(ghost()).toHaveStyle({ top: "167px" })
    pointer("pointerup", 300, 170)

    const [kept, added] = sections()
    expect(sections()).toHaveLength(2)
    expect(kept.id).toBe("s_p1")
    expect(kept.widgets.map((placed) => placed.id)).toEqual(["w_boxes"])
    // The Table 16 down the new section, which takes the rest of the height.
    expect(added).toMatchObject({ title: "Section 2", show_when: { match: "all", conditions: [] } })
    expect(added.widgets).toEqual([expect.objectContaining({ id: "w_grid", x: 16, y: 16 })])
    expect(heights()).toEqual([144, 416])
    expect(selectedSection()).toBe(added.id)
    expect(useWorkbenchViewStore.getState().selectedId).toBeNull()
    expect(useWorkbenchViewStore.getState().creating).toBeNull()
    // Drawn under a bar of its own, selected, with its handle.
    expect(band(added.id)).toHaveStyle({ top: "192px", height: "416px" })
    expect(within(band(added.id)).getByTestId("sheet-widget-w_grid")).toHaveStyle({ top: "16px" })
    expect(screen.getByRole("button", { name: "Section 2, Section 2" })).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByTestId(`section-edge-${added.id}`)).toBeInTheDocument()
    // In Preview, with no bars, the Table is where it was on the sheet: 144 and 16 down, 160.
    act(() => useWorkbenchViewStore.getState().showSection("preview"))
    expect(band(added.id)).toHaveStyle({ top: "144px" })

    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
    act(() => useWorkbenchFormStore.getState().undo())
    expect(heights()).toEqual([560])
    expect(widgetsOf(currentForm()).map(({ id, y }) => ({ id, y }))).toEqual([
      { id: "w_boxes", y: 16 },
      { id: "w_grid", y: 160 },
    ])
  })

  it("starts a Section dropped where its line would cross a component at that component's nearer edge instead, its bottom or its top, the line drawn there", () => {
    withPalette()

    // 96 down the one section, through the boxes, 16 to 136 down it: nearer their bottom,
    // so the line is there, 136 down the section, 160 down the sheet.
    dragSection(300, 120)
    expect(ghost()).toHaveTextContent("New section from here")
    expect(ghost()).toHaveStyle({ top: "159px" })
    pointer("pointerup", 300, 120)

    const [kept, added] = sections()
    expect(kept.widgets.map((placed) => placed.id)).toEqual(["w_boxes"])
    // The section above ends at the boxes' bottom; the Table, 24 under the line, is 24 down
    // the new section, where it was on the sheet.
    expect(heights()).toEqual([136, 424])
    expect(added.widgets).toEqual([expect.objectContaining({ id: "w_grid", y: 24 })])
    expect(selectedSection()).toBe(added.id)
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)

    // Undone, then 200 down the section, through the Table, 160 to 360 down it: nearer its
    // top, so the line is there, 184 down the sheet, and the Table starts the new section.
    act(() => useWorkbenchFormStore.getState().undo())
    dragSection(300, 224)
    expect(ghost()).toHaveTextContent("New section from here")
    expect(ghost()).toHaveStyle({ top: "183px" })
    pointer("pointerup", 300, 224)

    const [above, below] = sections()
    expect(above.widgets.map((placed) => placed.id)).toEqual(["w_boxes"])
    expect(below.widgets).toEqual([expect.objectContaining({ id: "w_grid", y: 0 })])
    expect(heights()).toEqual([160, 400])
    expect(selectedSection()).toBe(below.id)
  })

  it("puts a Section dropped on a section's bar, or at its very top, in before it, empty, a new section's height, named and selected, as one undo step", () => {
    loadForm(sectionedForm())
    withPalette()

    // At Flood's very top, then on its bar.
    dragSection(300, 202)
    expect(ghost()).toHaveTextContent(/^New section$/)
    expect(ghost()).toHaveStyle({ top: "175px" })
    pointer("pointermove", 300, 188)
    pointer("pointerup", 300, 188)

    const added = sections()[1]
    expect(sections().map((section) => section.id)).toEqual(["s_policy", added.id, "s_flood", "s_notes"])
    expect(added).toMatchObject({ title: "Section 4", height: 160, widgets: [] })
    expect(heights()).toEqual([152, 160, 232, 72])
    expect(selectedSection()).toBe(added.id)
    // Under its bar where Flood's was, Flood under it.
    expect(band(added.id)).toHaveStyle({ top: "200px" })
    expect(band("s_flood")).toHaveStyle({ top: "384px" })

    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
    act(() => useWorkbenchFormStore.getState().undo())
    expect(sections().map((section) => section.id)).toEqual(["s_policy", "s_flood", "s_notes"])
  })

  it("adds a Section dropped below the last section at the end, empty and a new section's height, named and selected", () => {
    loadForm(sectionedForm())
    withPalette()

    dragSection(300, 600)
    pointer("pointerup", 300, 600)

    const added = sections()[3]
    expect(sections().map((section) => section.id)).toEqual(["s_policy", "s_flood", "s_notes", added.id])
    expect(added).toMatchObject({ title: "Section 4", height: 160, widgets: [] })
    expect(selectedSection()).toBe(added.id)
    expect(band(added.id)).toHaveStyle({ top: "552px", height: "160px" })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
  })

  it("adds no section for a click on the palette's Section, nor for one dropped off the sheet", () => {
    withPalette()
    const item = screen.getByTestId("palette-item-section")

    fireEvent.pointerDown(item, { button: 0, clientX: 10, clientY: 10 })
    fireEvent.pointerUp(window, { clientX: 10, clientY: 10 })
    expect(heights()).toEqual([560])
    expect(selectedSection()).toBeNull()

    dragSection(2000, 300)
    pointer("pointerup", 2000, 300)
    expect(heights()).toEqual([560])
    expect(selectedSection()).toBeNull()
    expect(useWorkbenchViewStore.getState().creating).toBeNull()
    expect(useWorkbenchFormStore.getState().undoStack).toEqual([])
  })

  /** `sheetForm` with Headings below its components, each always shown. */
  const headed = (...headings: { id: string; text: string; size: "large" | "medium" | "small"; y: number }[]) => {
    const form = sheetForm()
    for (const { id, text, size, y } of headings) addTo(form, headingWidget(id, text, { size, y }))
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
    addTo(form, headingWidget("w_title", "Motor quote", { y: 0 }))
    addTo(form, headingWidget("w_section", "Equipment", { y: 136 }))
    addTo(form, headingWidget("w_note", "Over the grid", { y: 160 }))
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
    addTo(form, headingWidget("w_title", "Motor quote", { y: 0 }))
    addTo(form, headingWidget("w_note", "Over the grid", { y: 160 }))
    addTo(form, headingWidget("w_ca", "California", { y: 136, show_when: californian }))
    loadForm(form)
    useWorkbenchViewStore.setState({ section: "preview" })
    render(<SheetCanvas />)

    expect(order()).toEqual(["w_title", "w_boxes", "w_grid", "w_note"])
    expect(["w_boxes", "w_grid", "w_title", "w_note"].map(layer)).toEqual([1, 2, 3, 4])
  })

  it("keeps the keyboard's focus, and the selection, on a component moved past another", () => {
    const form = sheetForm()
    addTo(form, headingWidget("w_title", "Motor quote", { y: 0 }))
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
    addTo(form, headingWidget("w_left", "New York", { y: 384, show_when: ny }))
    addTo(form, headingWidget("w_right", "New York", { y: 448, align: "right", show_when: ny }))
    loadForm(form)
    render(<SheetCanvas />)
    const eye = (id: string) => within(frame(id)).getByRole("img", { name: /Shows when State equals NY/ })

    expect(eye("w_left")).toHaveClass("right-1")
    expect(eye("w_right")).toHaveClass("left-1")
    expect(eye("w_right")).not.toHaveClass("right-1")
  })

  it("fits what is on the sheet to the viewport when it first shows, and sizes the sheet to hold its components", () => {
    loadForm({ ...sheetForm(), pages: [sheet("p1", "Sheet 1", [{ id: "w", type: "tableInput", x: 0, y: 0, w: 1968, h: 100, title: "", rows: 3, ...TABLE_OPTIONS, fields: [], show_when: { match: "all" as const, conditions: [] } }])] })
    render(<SheetCanvas />)

    // 1000 wide less the padding, over the component's reach plus the edge, in steps of 5%.
    expect(useWorkbenchViewStore.getState().zoom).toBe(0.45)
    expect(screen.getByTestId("sheet")).toHaveStyle({ width: "2080px", transform: "scale(0.45)" })
  })

  it("fits a very wide sheet at the zoom's minimum, never at nothing", () => {
    loadForm({ ...sheetForm(), pages: [sheet("p1", "Sheet 1", [{ id: "w", type: "tableInput", x: 0, y: 0, w: 100_000, h: 100, title: "", rows: 3, ...TABLE_OPTIONS, fields: [], show_when: { match: "all" as const, conditions: [] } }])] })
    render(<SheetCanvas />)

    expect(useWorkbenchViewStore.getState().zoom).toBe(0.25)
    expect(screen.getByTestId("sheet")).toHaveStyle({ transform: "scale(0.25)" })
  })

  it("keeps a zoom chosen since the sheets were fitted when they show again, until the form is read anew", () => {
    const wide = { ...sheetForm(), pages: [sheet("p1", "Sheet 1", [{ id: "w", type: "tableInput", x: 0, y: 0, w: 1968, h: 100, title: "", rows: 3, ...TABLE_OPTIONS, fields: [], show_when: { match: "all" as const, conditions: [] } }])] }
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

  it("in Preview, neither selects nor moves a component, offers no drop, draws no section bars, and types into the quote", () => {
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
    expect(screen.getByTestId("palette-item-section")).toHaveAttribute("aria-disabled", "true")

    // The section is drawn, but not as the builder's: no bar, no name and no handle, at the sheet's top.
    expect(screen.queryByTestId("section-header-s_p1")).not.toBeInTheDocument()
    expect(screen.queryByTestId("section-edge-s_p1")).not.toBeInTheDocument()
    expect(band("s_p1")).not.toHaveAttribute("role")
    expect(band("s_p1")).toHaveStyle({ top: "0px" })

    fireEvent.change(within(frame("w_boxes")).getByRole("combobox", { name: "State" }), { target: { value: "NY" } })
    expect(useWorkbenchPreviewStore.getState().quote).toEqual({ policy: [{ "policy.state": "NY" }] })
    expect(currentForm().sample).toEqual({})
  })

  it("while building draws a component the sample hides faded with its eye struck through, and one that shows with its eye", () => {
    const form = coverForm()
    widgetsOf(form)[1].show_when = { match: "all", conditions: [{ table: "policy", column: "policy.state", op: "equals", values: ["NY"] }] }
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

  it("while building draws a section the sample hides in its place, its components faded and still selected", () => {
    loadForm(sectionedForm())
    render(<SheetCanvas />)
    const body = (id: string) => frame(id).querySelector("[data-hidden]")

    // Cover is not ticked in the sample: Flood hides, and is drawn where it sits, its Table faded.
    expect(band("s_flood")).toHaveStyle({ top: "200px", height: "232px" })
    expect(body("w_grid")).not.toBeNull()
    expect(body("w_boxes")).toBeNull()
    expect(body("w_note")).toBeNull()
    expect(band("s_notes")).toHaveStyle({ top: "456px" })
    fireEvent.pointerDown(frame("w_grid"), { button: 0 })
    releasePointer()
    expect(useWorkbenchViewStore.getState().selectedId).toBe("w_grid")

    // Ticked, it shows, and nothing moves.
    fireEvent.click(within(frame("w_boxes")).getByRole("checkbox", { name: "Cover" }))
    expect(body("w_grid")).toBeNull()
    expect(band("s_flood")).toHaveStyle({ top: "200px" })
    expect(band("s_notes")).toHaveStyle({ top: "456px" })
  })

  it("in Preview draws only what the quote shows, on the first sheet that shows in place of a hidden one chosen", () => {
    const form = coverForm()
    widgetsOf(form)[1].show_when = { match: "all", conditions: [{ table: "policy", column: "policy.state", op: "equals", values: ["NY"] }] }
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

  it("in Preview draws no section the quote hides, the sections under it moved up into its place, and down again once it shows", () => {
    loadForm(sectionedForm())
    useWorkbenchViewStore.setState({ section: "preview" })
    render(<SheetCanvas />)
    const cover = () => within(frame("w_boxes")).getByRole("checkbox", { name: "Cover" })

    // Cover is not ticked: Flood and its Table are not drawn, and Notes closes up under the policy's section.
    expect(screen.queryByTestId("sheet-section-s_flood")).not.toBeInTheDocument()
    expect(screen.queryByTestId("sheet-widget-w_grid")).not.toBeInTheDocument()
    expect(band("s_notes")).toHaveStyle({ top: "152px", height: "72px" })

    fireEvent.click(cover())
    expect(band("s_flood")).toHaveStyle({ top: "152px", height: "232px" })
    expect(within(band("s_flood")).getByTestId("sheet-widget-w_grid")).toBeInTheDocument()
    expect(band("s_notes")).toHaveStyle({ top: "384px" })

    fireEvent.click(cover())
    expect(screen.queryByTestId("sheet-section-s_flood")).not.toBeInTheDocument()
    expect(band("s_notes")).toHaveStyle({ top: "152px" })
  })

  it("in Preview holds the sections drawn, and their places, while a pointer is pressed on the sheet, never faded, until a turn after the release", () => {
    vi.useFakeTimers()
    loadForm(sectionedForm())
    useWorkbenchViewStore.setState({ section: "preview" })
    render(<SheetCanvas />)
    const cover = () => within(frame("w_boxes")).getByRole("checkbox", { name: "Cover" })

    // Pressed, a value the press commits opens Flood: nothing moves under the pointer.
    fireEvent.pointerDown(cover(), { button: 0 })
    quoteCover(true)
    expect(screen.queryByTestId("sheet-section-s_flood")).not.toBeInTheDocument()
    expect(band("s_notes")).toHaveStyle({ top: "152px" })
    // Released, still held for the release's click; a turn later Flood opens.
    fireEvent.pointerUp(cover())
    expect(band("s_notes")).toHaveStyle({ top: "152px" })
    turn()
    expect(band("s_flood")).toHaveStyle({ top: "152px", height: "232px" })
    expect(band("s_notes")).toHaveStyle({ top: "384px" })

    // Pressed again, a value closing Flood leaves its Table drawn as it was, not faded.
    fireEvent.pointerDown(cover(), { button: 0 })
    quoteCover(false)
    expect(frame("w_grid").querySelector("[data-hidden]")).toBeNull()
    expect(band("s_notes")).toHaveStyle({ top: "384px" })
    fireEvent.pointerUp(cover())
    turn()
    expect(screen.queryByTestId("sheet-section-s_flood")).not.toBeInTheDocument()
    expect(band("s_notes")).toHaveStyle({ top: "152px" })
  })

  it("in Preview keeps the section holding the focus in place on the screen as a section opens above it", () => {
    const form = sectionedForm()
    // The policy's year in Notes, under its Heading.
    addTo(form, { id: "w_year", type: "collection", x: 16, y: 72, w: 400, h: 120, title: "Year", columns: 2, fields: [{ table: "policy", column: "policy.year" }], show_when: { match: "all", conditions: [] } }, 0, 2)
    loadForm(form)
    useWorkbenchViewStore.setState({ section: "preview" })
    render(<SheetCanvas />)
    // jsdom has no scrollTo: one moving the viewport's scroll position, as a browser's does.
    const viewport = screen.getByTestId("sheet-viewport")
    const scrollTo = vi.fn((options: ScrollToOptions) => {
      viewport.scrollTop = options.top ?? viewport.scrollTop
    })
    Object.defineProperty(viewport, "scrollTo", { configurable: true, value: scrollTo })

    // The year focused, with the policy's section first in view: Flood opens above Notes,
    // moving it 232 down, and the view scrolls as far, so the year stays where it was.
    act(() => within(frame("w_year")).getByRole("textbox", { name: "Year" }).focus())
    quoteCover(true)
    expect(band("s_notes")).toHaveStyle({ top: "384px" })
    expect(scrollTo.mock.calls.map(([options]) => options.top)).toEqual([232])
  })

  it("in Preview leaves a hidden component's place in its section empty, the one under it staying where it sits", () => {
    const form = coverForm()
    widgetsOf(form)[1].show_when = { match: "all", conditions: [{ table: "policy", column: "policy.state", op: "equals", values: ["NY"] }] }
    addTo(form, headingWidget("w_after", "Under the equipment", { y: 384 }))
    loadForm(form)
    useWorkbenchViewStore.setState({ section: "preview" })
    render(<SheetCanvas />)

    expect(screen.queryByTestId("sheet-widget-w_grid")).not.toBeInTheDocument()
    expect(frame("w_after")).toHaveStyle({ top: "384px" })
    expect(band("s_p1")).toHaveStyle({ top: "0px", height: "560px" })

    fireEvent.change(within(frame("w_boxes")).getByRole("combobox", { name: "State" }), { target: { value: "NY" } })
    expect(frame("w_grid")).toHaveStyle({ top: "160px" })
    expect(frame("w_after")).toHaveStyle({ top: "384px" })
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

  it("in Preview holds the sheet drawn while a pointer is pressed on it, though a value the press commits hides every sheet, until a turn after the release", () => {
    vi.useFakeTimers()
    const form = coverForm()
    // Each sheet shows only while cover is ticked, its tick box on the first.
    form.pages[0].show_when = { match: "all", conditions: [{ table: "policy", column: "policy.cover", op: "ticked", values: [] }] }
    loadForm(form)
    useWorkbenchPreviewStore.setState({ quote: { policy: [{ "policy.cover": true }] } })
    useWorkbenchViewStore.setState({ section: "preview" })
    render(<SheetCanvas />)
    const cover = () => within(frame("w_boxes")).getByRole("checkbox", { name: "Cover" })

    // Pressed, the press's commit unticks it: the sheet and the tick box stay, the value live.
    fireEvent.pointerDown(cover(), { button: 0 })
    quoteCover(false)
    expect(cover()).not.toBeChecked()
    expect(screen.queryByText("No sheet shows for this quote.")).not.toBeInTheDocument()
    fireEvent.pointerUp(cover())
    expect(screen.getByTestId("sheet")).toBeInTheDocument()

    turn()
    expect(screen.getByText("No sheet shows for this quote.")).toBeInTheDocument()
    expect(screen.queryByTestId("sheet")).not.toBeInTheDocument()
  })
})
