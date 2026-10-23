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
 * and stacked as added, while building what a new quote shows, and in Preview only the
 * sections and components the sample shows, the sections under a hidden one closing up its
 * space and a hidden component's place in its section left empty, with their cells taking
 * the sample, what shows held while a pointer is pressed on the sheet and the section
 * holding the focus kept in place on the screen.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../../stores/useWorkbenchPreviewStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import {
  CHART_OPTIONS,
  COLLECTION_OPTIONS,
  findSection,
  findWidget,
  HEADING_OPTIONS,
  setColumnLook,
  setPageWidth,
  setSectionShowWhen,
  TABLE_OPTIONS,
  updateWidget,
  withSampleCell,
} from "../../utils/workbenchForm"
import PropertiesPanel from "../PropertiesPanel"
import SheetCanvas from "../SheetCanvas"
import WorkbenchPalette from "../WorkbenchPalette"
import { addTo, coverForm, currentForm, drag, headingWidget, loadForm, releasePointer, sectionedForm, sheet, sheetForm, stubLayout, topOnSheet, widgetsOf } from "./fixtures"

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
    target.dispatchEvent(new MouseEvent(type, { bubbles: true, button: 0, buttons: type === "pointerup" ? 0 : 1, clientX: x, clientY: y }))
  })
/** A turn passes, under fake timers. */
const turn = () =>
  act(() => {
    vi.advanceTimersByTime(0)
  })
/** The sample's cover ticked or not, as a value a press commits, or one typed elsewhere, sets it. */
const sampleCover = (ticked: boolean) =>
  act(() => useWorkbenchFormStore.getState().change((spec) => withSampleCell(spec, "policy", 0, "policy.cover", ticked)))

describe("SheetCanvas", () => {
  beforeEach(() => {
    stubLayout()
    loadForm()
    useWorkbenchPreviewStore.setState({ quote: {}, price: null })
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
    expect(topOnSheet(screen.getByTestId("section-header-s_flood"))).toBe(176)
    // Read section by section, whatever their places in them, each section's bar before its components.
    expect(order()).toEqual(["w_boxes", "w_grid", "w_note"])
    const read = screen.getAllByTestId(/^(section-header|sheet-widget)-/).map((element) => element.dataset.testid)
    expect(read).toEqual([
      "section-header-s_policy",
      "sheet-widget-w_boxes",
      "section-header-s_flood",
      "sheet-widget-w_grid",
      "section-header-s_notes",
      "sheet-widget-w_note",
    ])
  })

  it("while building draws a bar naming each section just above it, the selected one's handle under its bottom, and in Preview no bars, the sections stacked tight", () => {
    // Cover ticked in the sample, so Preview shows Flood too.
    loadForm(withSampleCell(sectionedForm(), "policy", 0, "policy.cover", true))
    render(<SheetCanvas />)
    const bar = (id: string) => screen.getByTestId(`section-header-${id}`)

    // Each bar 24 tall, its bottom its section's top.
    expect(["s_policy", "s_flood", "s_notes"].map((id) => topOnSheet(bar(id)))).toEqual([0, 176, 432])
    expect(["s_policy", "s_flood", "s_notes"].map((id) => bar(id).style.height)).toEqual(["24px", "24px", "24px"])
    expect(screen.getByRole("button", { name: "Section 2, Flood" })).toBe(bar("s_flood"))
    // The handle setting a section's height only while it is selected, over the middle of its bottom.
    expect(screen.queryAllByTestId(/^section-edge-/)).toHaveLength(0)
    fireEvent.click(bar("s_flood"))
    expect(useWorkbenchViewStore.getState().sectionId).toBe("s_flood")
    expect(screen.getAllByTestId(/^section-edge-/)).toEqual([screen.getByTestId("section-edge-s_flood")])
    expect(topOnSheet(screen.getByTestId("section-edge-s_flood"))).toBe(428)
    expect(screen.getByTestId("section-edge-s_flood")).toHaveStyle({ height: "8px" })

    act(() => useWorkbenchViewStore.getState().showMode("preview"))
    expect(screen.queryAllByTestId(/^section-(header|edge)-/)).toHaveLength(0)
    expect(band("s_policy")).toHaveStyle({ top: "0px" })
    expect(band("s_flood")).toHaveStyle({ top: "152px" })
    expect(band("s_notes")).toHaveStyle({ top: "384px" })
  })

  it("draws the sheet's handles while its own panel is open, the right edge's drag setting its width and the bottom's its height through its last section's, on the grid, never under what its components reach, each one undo step", () => {
    loadForm(sectionedForm())
    render(<SheetCanvas />)
    const right = () => screen.getByTestId("sheet-edge-width")
    const bottom = () => screen.getByTestId("sheet-edge-height")
    const undoSteps = () => useWorkbenchFormStore.getState().undoStack.length
    expect(screen.queryAllByTestId(/^sheet-edge-/)).toHaveLength(0)
    act(() => useWorkbenchViewStore.getState().selectSection("s_notes"))
    expect(screen.queryAllByTestId(/^sheet-edge-/)).toHaveLength(0)

    act(() => useWorkbenchViewStore.getState().selectSheet())
    // On its right edge, 936 across, and under its bottom, 152 + 232 + 72 and three bars of 24 down, less half the handle's 8.
    expect(right()).toHaveStyle({ left: "932px", width: "8px", height: "40px" })
    expect(bottom()).toHaveStyle({ top: "524px", width: "40px", height: "8px" })

    // 43 right, 979, snaps to 976; the press is kept from the sheet, so its panel stays open.
    act(() => drag(right(), [936, 264], [979, 264]))
    expect(currentForm().pages[0].width).toBe(976)
    expect(useWorkbenchViewStore.getState().sheetSelected).toBe(true)
    expect(screen.getByTestId("sheet")).toHaveStyle({ width: "976px" })
    expect(right()).toHaveStyle({ left: "972px" })
    // Left past its Table's right edge, 16 + 720: kept there.
    act(() => drag(right(), [976, 264], [300, 264]))
    expect(currentForm().pages[0].width).toBe(736)

    // 43 down, 499, snaps to 496: Notes 112, the others kept, the sheet as much longer.
    act(() => drag(bottom(), [468, 528], [468, 571]))
    expect(heights()).toEqual([152, 232, 112])
    expect(screen.getByTestId("sheet")).toHaveStyle({ height: "568px" })
    expect(bottom()).toHaveStyle({ top: "564px" })
    // Up past Notes' Heading, 16 + 40: kept there.
    act(() => drag(bottom(), [468, 568], [468, 100]))
    expect(heights()).toEqual([152, 232, 56])
    expect(undoSteps()).toBe(4)

    act(() => useWorkbenchViewStore.getState().showMode("preview"))
    expect(screen.queryAllByTestId(/^sheet-edge-/)).toHaveLength(0)
    for (let step = 0; step < 4; step += 1) act(() => useWorkbenchFormStore.getState().undo())
    expect(currentForm().pages[0].width).toBe(936)
    expect(heights()).toEqual([152, 232, 72])
  })

  it("scrolls over the sheet as pressed while its edge or a section's bottom is dragged, so the scroll never carries the edge from the pointer, and over the sheet itself once the drag ends", () => {
    loadForm(sectionedForm())
    render(<SheetCanvas />)
    // What the viewport scrolls over: the sheet and the padding either side, 64.
    const area = () => screen.getByTestId("sheet").parentElement
    act(() => useWorkbenchViewStore.getState().selectSheet())

    // Its right edge, 936 across, dragged 200 in: the sheet narrower, the area as wide as it was.
    pointer("pointerdown", 936, 264, screen.getByTestId("sheet-edge-width"))
    pointer("pointermove", 736, 264)
    expect(screen.getByTestId("sheet")).toHaveStyle({ width: "736px" })
    expect(area()).toHaveStyle({ width: "1000px" })
    pointer("pointerup", 736, 264)
    expect(area()).toHaveStyle({ width: "800px" })

    // Notes' bottom, 528 down the sheet, dragged 16 up: the area as long as the sheet was.
    act(() => useWorkbenchViewStore.getState().selectSection("s_notes"))
    pointer("pointerdown", 468, 528, screen.getByTestId("section-edge-s_notes"))
    pointer("pointermove", 468, 512)
    expect(heights()).toEqual([152, 232, 56])
    expect(screen.getByTestId("sheet")).toHaveStyle({ height: "512px" })
    expect(area()).toHaveStyle({ height: "592px" })
    pointer("pointerup", 468, 512)
    expect(area()).toHaveStyle({ height: "576px" })

    // An undo during the drag ends it, and the area is the sheet's again.
    pointer("pointerdown", 400, 120, screen.getByTestId("section-edge-s_notes"))
    pointer("pointermove", 400, 300)
    expect(area()).toHaveStyle({ height: "760px" })
    act(() => useWorkbenchFormStore.getState().undo())
    expect(useWorkbenchViewStore.getState().heldSize).toBeNull()
    expect(area()).toHaveStyle({ height: "576px" })
  })

  it("marks a component that shows nothing or a column that is gone, naming the problem", () => {
    loadForm({ ...sheetForm(), pages: [sheet("p1", "Sheet 1", [{ id: "w", type: "collection", x: 0, y: 0, w: 200, h: 100, title: "", columns: 3, ...COLLECTION_OPTIONS, fields: [], show_when: { match: "all" as const, conditions: [] } }])] })
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
    // Its frame takes the focus, though the press's default is prevented to drag it.
    expect(document.activeElement).toBe(frame("w_grid"))

    // A press on the viewport's scrollbar, past its client box, scrolls and keeps the selection.
    fireEvent.pointerDown(screen.getByTestId("sheet-viewport"), { button: 0, clientX: 1005, clientY: 100 })
    expect(useWorkbenchViewStore.getState().selectedId).toBe("w_grid")

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

  it("lands a component dragged sideways where the pointer puts it, under a section whose height is off the grid", () => {
    // The policy's section 300 tall puts Flood's top 348 down the sheet, off the 8px grid.
    const form = sectionedForm()
    form.pages[0].sections[0].height = 300
    loadForm(form)
    render(<SheetCanvas />)

    pointer("pointerdown", 100, 400, frame("w_grid"))
    pointer("pointermove", 140, 400)

    // Snapped once, in Flood's own coordinates: 40 across, nothing down.
    expect(widget("w_grid")).toMatchObject({ x: 56, y: 16 })
    pointer("pointerup", 140, 400)
    expect(widget("w_grid")).toMatchObject({ x: 56, y: 16 })
  })

  it("ends a drag released outside the window at the next move with no button down, and one when the window loses the focus", () => {
    loadForm(sectionedForm())
    render(<SheetCanvas />)
    pointer("pointerdown", 100, 200, frame("w_grid"))
    pointer("pointermove", 100, 240)
    const moved = widget("w_grid")

    // Released outside the window: its release was never seen.
    act(() => {
      window.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, buttons: 0, clientX: 600, clientY: 600 }))
    })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
    pointer("pointermove", 600, 600)
    expect(widget("w_grid")).toEqual(moved)

    pointer("pointerdown", 100, 240, frame("w_grid"))
    pointer("pointermove", 140, 240)
    act(() => {
      window.dispatchEvent(new Event("blur"))
    })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(2)
    const ended = widget("w_grid")
    pointer("pointermove", 400, 240)
    expect(widget("w_grid")).toEqual(ended)
  })

  it("keeps a dragged component under the pointer as the sheet scrolls under it", () => {
    loadForm(sectionedForm())
    render(<SheetCanvas />)
    pointer("pointerdown", 100, 200, frame("w_grid"))
    pointer("pointermove", 140, 200)
    expect(widget("w_grid")).toMatchObject({ x: 56 })

    // The sheet scrolls 40 left under the still pointer: the component goes 40 further across it.
    stubLayout({ left: -40, top: 0, width: 1000, height: 800 })
    act(() => {
      window.dispatchEvent(new Event("scroll"))
    })

    expect(widget("w_grid")).toMatchObject({ x: 96 })
    pointer("pointerup", 140, 200)
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

  it("stops a component dragged right at the sheet's right edge, and widens the sheet for one resized past it, each one undo step", () => {
    render(<SheetCanvas />)

    // The Table, 720 wide from 16 across the sheet's 936, dragged 400 right: stopped with its
    // right edge at the sheet's, at 216.
    act(() => drag(frame("w_grid"), [100, 300], [500, 300]))
    expect(widget("w_grid")).toMatchObject({ x: 216, w: 720 })
    expect(currentForm().pages[0].width).toBe(936)

    // Its right handle dragged 200 past that edge: the sheet widens to hold it, drawn as wide.
    act(() => drag(frame("w_grid").querySelector('[data-handle="e"]')!, [936, 300], [1136, 300]))
    expect(widget("w_grid")).toMatchObject({ x: 216, w: 920 })
    expect(currentForm().pages[0].width).toBe(1136)
    expect(screen.getByTestId("sheet")).toHaveStyle({ width: "1136px" })
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
    // The sections' 840 and their bars' 72, nothing under them.
    expect(screen.getByTestId("sheet")).toHaveStyle({ height: "912px" })
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

  it("keeps a title typed in the panel, not yet committed, as another component is dragged or the one it names resized", () => {
    render(
      <>
        <SheetCanvas />
        <PropertiesPanel />
      </>,
    )
    /** Type `text` into the panel's title, on its Display pane, leaving it focused and uncommitted. */
    const retitle = (text: string) => {
      fireEvent.click(screen.getByRole("tab", { name: "Display" }))
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

  it("keeps a value typed in the panel, not yet committed, as the empty sheet is pressed and the panel closes", () => {
    render(
      <>
        <SheetCanvas />
        <PropertiesPanel />
      </>,
    )
    fireEvent.pointerDown(frame("w_grid"), { button: 0 })
    releasePointer()
    fireEvent.click(screen.getByRole("tab", { name: "Display" }))
    const rows = screen.getByTestId("widget-rows")
    act(() => rows.focus())
    fireEvent.change(rows, { target: { value: "5" } })

    fireEvent.pointerDown(screen.getByTestId("sheet-viewport"), { button: 0 })
    expect(useWorkbenchViewStore.getState().selectedId).toBeNull()
    expect(screen.queryByTestId("widget-rows")).not.toBeInTheDocument()
    expect(widget("w_grid")).toMatchObject({ rows: 5 })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
  })

  it("keeps a section's title or height typed in its panel, not yet committed, as a Section is dragged out of the palette or its handle dragged, each its own undo step", () => {
    render(
      <>
        <WorkbenchPalette />
        <SheetCanvas />
        <PropertiesPanel />
      </>,
    )
    /** Type `text` into `field`, leaving it focused and uncommitted. */
    const typeInto = (field: HTMLElement, text: string) => {
      act(() => field.focus())
      fireEvent.change(field, { target: { value: text } })
    }
    const undoSteps = () => useWorkbenchFormStore.getState().undoStack.length

    act(() => useWorkbenchViewStore.getState().selectSection("s_p1"))
    typeInto(screen.getByRole("textbox", { name: "Section title" }), "Cover")
    // A Section dropped below the last: the new one selected, its panel in place of the first's.
    pointer("pointerdown", 10, 10, screen.getByTestId("palette-item-section"))
    pointer("pointermove", 300, 700)
    pointer("pointerup", 300, 700)
    const [first, added] = currentForm().pages[0].sections
    expect(first.title).toBe("Cover")
    expect(useWorkbenchViewStore.getState().sectionId).toBe(added.id)
    expect(undoSteps()).toBe(2)

    // Its height typed, then its handle, under its bottom (608 + 160), dragged 16 down: from what was typed.
    typeInto(screen.getByTestId("section-height"), "304")
    pointer("pointerdown", 500, 766, screen.getByTestId(`section-edge-${added.id}`))
    pointer("pointermove", 500, 782)
    pointer("pointerup", 500, 782)
    const heightOf = () => findSection(currentForm(), added.id)?.section.height
    expect(heightOf()).toBe(320)
    expect(undoSteps()).toBe(4)
    act(() => useWorkbenchFormStore.getState().undo())
    expect(heightOf()).toBe(304)
    act(() => useWorkbenchFormStore.getState().undo())
    act(() => useWorkbenchFormStore.getState().undo())
    expect(currentForm().pages[0].sections.map((section) => section.title)).toEqual(["Cover"])
  })

  it("records nothing for a component dragged away and back where it began, keeping redo", () => {
    render(<SheetCanvas />)
    const history = () => {
      const { undoStack, redoStack } = useWorkbenchFormStore.getState()
      return { undo: undoStack.length, redo: redoStack.length }
    }
    act(() => useWorkbenchFormStore.getState().change((spec) => updateWidget(spec, "w_boxes", { title: "Cover" })))
    act(() => useWorkbenchFormStore.getState().undo())
    expect(history()).toEqual({ undo: 0, redo: 1 })

    pointer("pointerdown", 100, 100, frame("w_boxes"))
    pointer("pointermove", 180, 180)
    expect(widget("w_boxes")).toMatchObject({ x: 96, y: 96 })
    pointer("pointermove", 100, 100)
    pointer("pointerup", 100, 100)
    expect(widget("w_boxes")).toMatchObject({ x: 16, y: 16 })
    expect(history()).toEqual({ undo: 0, redo: 1 })
    act(() => useWorkbenchFormStore.getState().redo())
    expect(widget("w_boxes")).toMatchObject({ title: "Cover" })
  })

  it("finishes a component's drag when an undo comes during it, recording and undoing it, the pointer no longer followed", () => {
    render(<SheetCanvas />)
    const forms = useWorkbenchFormStore.getState
    act(() => forms().change((spec) => updateWidget(spec, "w_boxes", { title: "Cover" })))

    pointer("pointerdown", 100, 100, frame("w_boxes"))
    pointer("pointermove", 180, 180)
    expect(widget("w_boxes")).toMatchObject({ x: 96, y: 96 })
    act(() => forms().undo())
    expect(widget("w_boxes")).toMatchObject({ title: "Cover", x: 16, y: 16 })
    pointer("pointermove", 260, 260)
    pointer("pointerup", 260, 260)
    expect(widget("w_boxes")).toMatchObject({ x: 16, y: 16 })

    act(() => forms().undo())
    expect(widget("w_boxes")).toMatchObject({ title: "Policy", x: 16, y: 16 })
    act(() => forms().redo())
    act(() => forms().redo())
    expect(widget("w_boxes")).toMatchObject({ title: "Cover", x: 96, y: 96 })
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
      window.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, buttons: 1, clientX: 2000, clientY: 500 }))
    })
    expect(screen.queryByTestId("drop-ghost")).not.toBeInTheDocument()
    act(() => {
      window.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, buttons: 1, clientX: 200, clientY: 412 }))
    })
    expect(screen.getByTestId("drop-ghost")).toHaveTextContent("Collection")
    // Over every component on the sheet, the selected one's place included.
    expect(screen.getByTestId("drop-ghost")).toHaveStyle({ zIndex: "4" })
    act(() => {
      window.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, clientX: 200, clientY: 412 }))
    })

    // 400 down the sheet is 376 down the section, under its bar.
    const added = widgetsOf(currentForm())[2]
    expect(added).toMatchObject({ type: "collection", x: 184, y: 376, w: 720, h: 120, fields: [], columns: 3, ...COLLECTION_OPTIONS })
    // Inside the section, which keeps its height.
    expect(heights()).toEqual([560])
    expect(useWorkbenchViewStore.getState().selectedId).toBe(added.id)
    expect(useWorkbenchViewStore.getState().creating).toBeNull()
  })

  it("adds a component on Enter on its palette item, at the first place free in the selected section, selected and focused; a Section after it", async () => {
    render(
      <>
        <WorkbenchPalette />
        <SheetCanvas />
      </>,
    )

    // Nothing selected: the sheet's first section, beside the Collection, above the Table.
    fireEvent.keyDown(screen.getByTestId("palette-item-heading"), { key: "Enter" })
    const heading = widgetsOf(currentForm())[2]
    expect(heading).toMatchObject({ type: "heading", x: 432, y: 8, w: 480, h: 48 })
    expect(useWorkbenchViewStore.getState().selectedId).toBe(heading.id)
    await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId(`sheet-widget-${heading.id}`)))
    // The undo step it is: one.
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)

    // Space too, as for a button: a Chart, too tall to fit above the Table, goes below it all, the section growing.
    fireEvent.keyDown(screen.getByTestId("palette-item-chart"), { key: " " })
    const chart = widgetsOf(currentForm())[3]
    expect(chart).toMatchObject({ type: "chart", x: 8, y: 376 })
    expect(heights()).toEqual([656])

    // A Section goes in after the selected component's section, selected.
    fireEvent.keyDown(screen.getByTestId("palette-item-section"), { key: "Enter" })
    const sections = currentForm().pages[0].sections
    expect(sections).toHaveLength(2)
    expect(useWorkbenchViewStore.getState().sectionId).toBe(sections[1].id)
    // With that section selected, a component goes into it.
    fireEvent.keyDown(screen.getByTestId("palette-item-collection"), { key: "Enter" })
    expect(widgetsOf(currentForm(), 0, 1)).toMatchObject([{ type: "collection", x: 8, y: 8 }])
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
      window.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, buttons: 1, clientX: 200, clientY: 180 }))
    })
    // The pointer is on Flood's bar, but the ghost's top edge, a little above it and snapped,
    // 168 down the sheet, is over the policy's section: kept inside it, its bottom at the
    // section's, 152 less its 120 down it, and drawn there.
    expect(screen.getByTestId("drop-ghost")).toHaveStyle({ left: "184px", top: "56px", width: "720px", height: "120px" })
    act(() => {
      window.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, clientX: 200, clientY: 180 }))
    })

    const added = widgetsOf(currentForm())[1]
    expect(added).toMatchObject({ type: "collection", x: 184, y: 32, w: 720, h: 120 })
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
      window.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, buttons: 1, clientX: 200, clientY: 300 }))
    })
    // Its top edge, snapped, 288 down the sheet: over Flood, whose top is 200, 88 down it.
    expect(screen.getByTestId("drop-ghost")).toHaveStyle({ left: "184px", top: "288px" })
    act(() => {
      window.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, clientX: 200, clientY: 300 }))
    })

    const added = widgetsOf(currentForm(), 0, 1)[1]
    expect(added).toMatchObject({ type: "collection", x: 184, y: 88 })
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
      window.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, buttons: 1, clientX: 300, clientY: 236 }))
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
      window.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, buttons: 1, clientX: 300, clientY: 524 }))
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
    pointer("pointermove", 200, 420)
    expect(ghost()).toHaveStyle({ left: "184px", top: "312px", width: "720px", height: "120px" })
    pointer("pointerup", 200, 420)

    const added = widgetsOf(currentForm(), 0, 1)[1]
    expect(added).toMatchObject({ type: "collection", x: 184, y: 112 })
    expect(heights()).toEqual([152, 232, 72])
    expect(within(band("s_flood")).getByTestId(`sheet-widget-${added.id}`)).toHaveStyle({ top: "112px" })
    expect(band("s_notes")).toHaveStyle({ top: "456px" })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
  })

  it("keeps a component dropped out of the palette past the sheet's right edge, or beside it on its left, inside its width, the ghost drawn there, and widens a sheet narrower than it", () => {
    withPalette()

    // Its left edge, snapped, 888 across: a Collection's 720 would reach past the sheet's
    // 936, so it is kept inside it, at 216, and drawn there.
    pointer("pointerdown", 10, 10, screen.getByTestId("palette-item-collection"))
    pointer("pointermove", 904, 412)
    expect(ghost()).toHaveStyle({ left: "216px", width: "720px" })
    pointer("pointerup", 904, 412)
    expect(widgetsOf(currentForm())[2]).toMatchObject({ type: "collection", x: 216, w: 720 })
    expect(currentForm().pages[0].width).toBe(936)

    // Beside the sheet on its left, in the padding between it and the palette: at its left edge.
    const box = (left: number, width: number) => ({ left, top: 0, right: left + width, bottom: 800, width, height: 800, x: left, y: 0, toJSON: () => ({}) }) as DOMRect
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      return this.dataset.testid === "sheet" ? box(32, 936) : box(0, 1000)
    })
    pointer("pointerdown", 10, 10, screen.getByTestId("palette-item-collection"))
    pointer("pointermove", 16, 412)
    expect(ghost()).toHaveStyle({ left: "0px", width: "720px" })
    pointer("pointerup", 16, 412)
    expect(widgetsOf(currentForm())[3]).toMatchObject({ type: "collection", x: 0, w: 720 })
    stubLayout()

    // On an empty sheet 600 across, at its left edge, the sheet widening to hold it.
    act(() => {
      useWorkbenchFormStore.getState().change((spec) => setPageWidth(spec, "p2", 600))
      useWorkbenchViewStore.getState().showPage("p2")
    })
    pointer("pointerdown", 10, 10, screen.getByTestId("palette-item-collection"))
    pointer("pointermove", 300, 100)
    expect(ghost()).toHaveStyle({ left: "0px", width: "720px" })
    pointer("pointerup", 300, 100)
    expect(widgetsOf(currentForm(), 1)[0]).toMatchObject({ type: "collection", x: 0, w: 720 })
    expect(currentForm().pages[1].width).toBe(720)
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
    expect(screen.getByRole("button", { name: "Section 2, Section 2" })).toHaveAttribute("data-selected", "true")
    expect(screen.getByTestId(`section-edge-${added.id}`)).toBeInTheDocument()
    // In Preview, with no bars, the Table is where it was on the sheet: 144 and 16 down, 160.
    act(() => useWorkbenchViewStore.getState().showMode("preview"))
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

  it("never starts a Section less than the least a section can be down one: nearer its top, in before it or at the first clear line that far down, whichever is nearer, the line drawn where it starts", () => {
    withPalette()

    // 8 down the one section, above its boxes, 16 to 136 down it: a section above would be
    // under 48, and the first clear line from 48 down is the boxes' bottom, 136, further
    // than the top, so it goes in before the section, the line along its bar's top.
    dragSection(300, 32)
    expect(ghost()).toHaveTextContent(/^New section$/)
    expect(ghost()).toHaveStyle({ top: "-1px" })
    pointer("pointerup", 300, 32)
    const [added, kept] = sections()
    expect(added.widgets).toEqual([])
    expect(kept.id).toBe("s_p1")
    expect(kept.widgets.map(({ id, y }) => ({ id, y }))).toEqual([
      { id: "w_boxes", y: 16 },
      { id: "w_grid", y: 160 },
    ])
    expect(heights()).toEqual([160, 560])
    act(() => useWorkbenchFormStore.getState().undo())

    // With the boxes 56 down the section, 32 down it is nearer 48, clear of them, than the
    // top: the section splits at 48, the line 72 down the sheet, and the boxes are 8 down
    // the new section, where they were on the sheet.
    act(() => useWorkbenchFormStore.getState().setFormRaw(updateWidget(currentForm(), "w_boxes", { y: 56 })))
    dragSection(300, 56)
    expect(ghost()).toHaveTextContent("New section from here")
    expect(ghost()).toHaveStyle({ top: "71px" })
    pointer("pointerup", 300, 56)
    const [above, below] = sections()
    expect(above.widgets).toEqual([])
    expect(below.widgets.map(({ id, y }) => ({ id, y }))).toEqual([
      { id: "w_boxes", y: 8 },
      { id: "w_grid", y: 112 },
    ])
    expect(heights()).toEqual([48, 512])
    // Under its bar, which starts where the line was drawn.
    expect(band(below.id)).toHaveStyle({ top: "96px" })

    // 16 down it, nearer the top: in before it.
    act(() => useWorkbenchFormStore.getState().undo())
    dragSection(300, 40)
    expect(ghost()).toHaveTextContent(/^New section$/)
    expect(ghost()).toHaveStyle({ top: "-1px" })
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
    useWorkbenchViewStore.setState({ mode: "preview" })
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
    useWorkbenchViewStore.setState({ mode: "preview" })
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

  it("fits the sheet's width to the viewport when it first shows, and draws the sheet at its width whatever the zoom from the viewport's top-left corner, the viewport scrolling across one wider than it", () => {
    loadForm({ ...sheetForm(), pages: [sheet("p1", "Sheet 1", [{ id: "w", type: "tableInput", x: 0, y: 0, w: 1968, h: 100, title: "", rows: 3, ...TABLE_OPTIONS, fields: [], show_when: { match: "all" as const, conditions: [] } }])] })
    render(<SheetCanvas />)
    const sheetElement = screen.getByTestId("sheet")

    // 1000 wide less the padding, over the sheet's 1968, in steps of 5%.
    expect(useWorkbenchViewStore.getState().zoom).toBe(0.45)
    expect(sheetElement).toHaveStyle({ width: "1968px", transform: "scale(0.45)" })
    // Zoomed out, the sheet keeps its width, drawn smaller from its top-left corner, which
    // stays the padding in from the viewport's; zoomed in, it is drawn wider than the
    // viewport, which scrolls across it.
    act(() => useWorkbenchViewStore.getState().setZoom(0.25))
    expect(sheetElement).toHaveStyle({ width: "1968px", transform: "scale(0.25)", transformOrigin: "0 0", left: "32px", top: "32px" })
    expect(sheetElement.parentElement).toHaveStyle({ width: `${1968 * 0.25 + 64}px` })
    act(() => useWorkbenchViewStore.getState().setZoom(2))
    expect(sheetElement).toHaveStyle({ width: "1968px", left: "32px", top: "32px" })
    expect(sheetElement.parentElement).toHaveStyle({ width: `${1968 * 2 + 64}px` })
    expect(screen.getByTestId("sheet-viewport")).toHaveClass("overflow-auto")
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

  it("in Preview, neither selects nor moves a component, offers no drop, draws no section bars, and types into the sample", () => {
    useWorkbenchViewStore.setState({ mode: "preview" })
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
      window.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, buttons: 1, clientX: 300, clientY: 500 }))
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
    expect(currentForm().sample).toEqual({ policy: [{ "policy.state": "NY" }] })
    expect(useWorkbenchPreviewStore.getState().quote).toEqual({})
  })

  it("while building draws a component a new quote hides faded with its eye struck through, whatever the sample holds, and one that shows with its eye", () => {
    const form = coverForm()
    widgetsOf(form)[1].show_when = { match: "all", conditions: [{ table: "policy", column: "policy.state", op: "equals", values: ["NY"] }] }
    loadForm(withSampleCell(form, "policy", 0, "policy.state", "NY"))
    render(<SheetCanvas />)

    const body = (id: string) => frame(id).querySelector("[data-hidden]")
    expect(body("w_grid")).not.toBeNull()
    expect(within(frame("w_grid")).getByRole("img", { name: "Hidden for a new quote. Shows when State equals NY" })).toBeInTheDocument()
    expect(within(frame("w_boxes")).queryByRole("img")).not.toBeInTheDocument()

    // Shown while Cover is not ticked, as on a new quote.
    act(() =>
      useWorkbenchFormStore.getState().change((spec) =>
        updateWidget(spec, "w_grid", { show_when: { match: "all", conditions: [{ table: "policy", column: "policy.cover", op: "not_ticked", values: [] }] } }),
      ),
    )
    expect(body("w_grid")).toBeNull()
    expect(within(frame("w_grid")).getByRole("img", { name: "Shows when Cover is not ticked" })).toBeInTheDocument()

    // A component on a sheet a new quote hides is hidden with it, and still drawn to edit.
    act(() => useWorkbenchViewStore.getState().showPage("p2"))
    expect(body("w_year")).not.toBeNull()
  })

  it("while building draws a section a new quote hides in its place, its components faded and still selected", () => {
    loadForm(sectionedForm())
    render(<SheetCanvas />)
    const body = (id: string) => frame(id).querySelector("[data-hidden]")

    // Cover is not ticked on a new quote: Flood hides, and is drawn where it sits, its Table faded.
    expect(band("s_flood")).toHaveStyle({ top: "200px", height: "232px" })
    expect(body("w_grid")).not.toBeNull()
    expect(body("w_boxes")).toBeNull()
    expect(body("w_note")).toBeNull()
    expect(band("s_notes")).toHaveStyle({ top: "456px" })
    fireEvent.pointerDown(frame("w_grid"), { button: 0 })
    releasePointer()
    expect(useWorkbenchViewStore.getState().selectedId).toBe("w_grid")

    // Shown while Cover is not ticked, as on a new quote, and nothing moves.
    act(() =>
      useWorkbenchFormStore.getState().change((spec) =>
        setSectionShowWhen(spec, "s_flood", { match: "all", conditions: [{ table: "policy", column: "policy.cover", op: "not_ticked", values: [] }] }),
      ),
    )
    expect(body("w_grid")).toBeNull()
    expect(band("s_flood")).toHaveStyle({ top: "200px" })
    expect(band("s_notes")).toHaveStyle({ top: "456px" })
  })

  it("in Preview draws only what the sample shows, on the first sheet that shows in place of a hidden one chosen", () => {
    const form = coverForm()
    widgetsOf(form)[1].show_when = { match: "all", conditions: [{ table: "policy", column: "policy.state", op: "equals", values: ["NY"] }] }
    loadForm(form)
    useWorkbenchViewStore.setState({ mode: "preview", pageId: "p2" })
    render(<SheetCanvas />)

    // The sample leaves Cover unticked: Sheet 1 shows in its place, without the hidden Table.
    expect(frame("w_boxes")).toBeInTheDocument()
    expect(screen.queryByTestId("sheet-widget-w_grid")).not.toBeInTheDocument()
    expect(within(frame("w_boxes")).queryByRole("img")).not.toBeInTheDocument()

    fireEvent.change(within(frame("w_boxes")).getByRole("combobox", { name: "State" }), { target: { value: "NY" } })
    expect(frame("w_grid")).toBeInTheDocument()
    fireEvent.click(within(frame("w_boxes")).getByRole("checkbox", { name: "Cover" }))
    expect(frame("w_year")).toBeInTheDocument()
    expect(screen.queryByTestId("sheet-widget-w_boxes")).not.toBeInTheDocument()
  })

  it("in Preview draws no section the sample hides, the sections under it moved up into its place, and down again once it shows", () => {
    loadForm(sectionedForm())
    useWorkbenchViewStore.setState({ mode: "preview" })
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
    useWorkbenchViewStore.setState({ mode: "preview" })
    render(<SheetCanvas />)
    const cover = () => within(frame("w_boxes")).getByRole("checkbox", { name: "Cover" })

    // Pressed, a value the press commits opens Flood: nothing moves under the pointer.
    fireEvent.pointerDown(cover(), { button: 0 })
    sampleCover(true)
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
    sampleCover(false)
    expect(frame("w_grid").querySelector("[data-hidden]")).toBeNull()
    expect(band("s_notes")).toHaveStyle({ top: "384px" })
    fireEvent.pointerUp(cover())
    turn()
    expect(screen.queryByTestId("sheet-section-s_flood")).not.toBeInTheDocument()
    expect(band("s_notes")).toHaveStyle({ top: "152px" })
  })

  it("in Preview holds a component's fields drawn while a pointer is pressed on the sheet, though a value the press commits shows one, until a turn after the release", () => {
    vi.useFakeTimers()
    const year = { table: "policy", column: "policy.year" }
    const form = coverForm()
    const fields = [{ table: "policy", column: "policy.state" }, { table: "policy", column: "policy.cover" }, year]
    loadForm(
      setColumnLook(updateWidget(form, "w_boxes", { fields }), "w_boxes", year, {
        show_when: { match: "all", conditions: [{ table: "policy", column: "policy.cover", op: "ticked", values: [] }] },
      }),
    )
    useWorkbenchViewStore.setState({ mode: "preview" })
    render(<SheetCanvas />)
    const cover = () => within(frame("w_boxes")).getByRole("checkbox", { name: "Cover" })
    const yearBox = () => within(frame("w_boxes")).queryByRole("textbox", { name: "Year" })

    fireEvent.pointerDown(cover(), { button: 0 })
    sampleCover(true)
    expect(yearBox()).not.toBeInTheDocument()
    fireEvent.pointerUp(cover())
    expect(yearBox()).not.toBeInTheDocument()
    turn()
    expect(yearBox()).toBeInTheDocument()
  })

  it("in Preview keeps the section holding the focus in place on the screen as a section opens above it", () => {
    const form = sectionedForm()
    // The policy's year in Notes, under its Heading.
    addTo(form, { id: "w_year", type: "collection", x: 16, y: 72, w: 400, h: 120, title: "Year", columns: 2, ...COLLECTION_OPTIONS, fields: [{ table: "policy", column: "policy.year" }], show_when: { match: "all", conditions: [] } }, 0, 2)
    loadForm(form)
    useWorkbenchViewStore.setState({ mode: "preview" })
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
    sampleCover(true)
    expect(band("s_notes")).toHaveStyle({ top: "384px" })
    expect(scrollTo.mock.calls.map(([options]) => options.top)).toEqual([232])
  })

  it("in Preview leaves a hidden component's place in its section empty, the one under it staying where it sits", () => {
    const form = coverForm()
    widgetsOf(form)[1].show_when = { match: "all", conditions: [{ table: "policy", column: "policy.state", op: "equals", values: ["NY"] }] }
    addTo(form, headingWidget("w_after", "Under the equipment", { y: 384 }))
    loadForm(form)
    useWorkbenchViewStore.setState({ mode: "preview" })
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
    useWorkbenchViewStore.setState({ mode: "preview" })
    render(<SheetCanvas />)

    expect(screen.getByText("No sheet shows for this quote.")).toBeInTheDocument()
    expect(screen.queryByTestId("sheet")).not.toBeInTheDocument()
  })

  it("in Preview holds the sheet drawn while a pointer is pressed on it, though a value the press commits hides every sheet, until a turn after the release", () => {
    vi.useFakeTimers()
    const form = coverForm()
    // Each sheet shows only while cover is ticked, its tick box on the first.
    form.pages[0].show_when = { match: "all", conditions: [{ table: "policy", column: "policy.cover", op: "ticked", values: [] }] }
    loadForm(withSampleCell(form, "policy", 0, "policy.cover", true))
    useWorkbenchViewStore.setState({ mode: "preview" })
    render(<SheetCanvas />)
    const cover = () => within(frame("w_boxes")).getByRole("checkbox", { name: "Cover" })

    // Pressed, the press's commit unticks it: the sheet and the tick box stay, the value live.
    fireEvent.pointerDown(cover(), { button: 0 })
    sampleCover(false)
    expect(cover()).not.toBeChecked()
    expect(screen.queryByText("No sheet shows for this quote.")).not.toBeInTheDocument()
    fireEvent.pointerUp(cover())
    expect(screen.getByTestId("sheet")).toBeInTheDocument()

    turn()
    expect(screen.getByText("No sheet shows for this quote.")).toBeInTheDocument()
    expect(screen.queryByTestId("sheet")).not.toBeInTheDocument()
  })
})
