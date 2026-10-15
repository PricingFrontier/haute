/**
 * The sections' bars while building (specs/workbench): each 24 tall just above its section,
 * numbered and named, a press selecting its section and Enter or Space opening its panel, a
 * bar dragged past another section's middle moving its section there, a line marking where
 * it would go, as one undo step, a click moving nothing, Alt+Up and Alt+Down moving the
 * focused bar's section a place, keeping the focus, Delete and Backspace deleting it, asking
 * first while components are in it and never a sheet's last, each of a bar's own keys kept
 * from the sheet's shortcuts; the selected section's handle alone, dragged to set its height
 * and double-clicked to fit it, at the zoom and wherever the sheet sits on the screen; and a
 * section's eye, struck through and its bar faded while a new quote hides it, its problem
 * marked, its conditions and its problem told to a screen reader.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, renderHook, screen, within } from "@testing-library/react"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { findSection, findWidget, renameSection, withSampleCell } from "../../utils/workbenchForm"
import { answerQuestion, asked } from "../../test-utils/question"
import SheetCanvas from "../SheetCanvas"
import useWorkbenchShortcuts from "../useWorkbenchShortcuts"
import { currentForm, drag, loadForm, releasePointer, sectionedForm, stubLayout, topOnSheet } from "./fixtures"

const bar = (id: string) => screen.getByTestId(`section-header-${id}`)
const band = (id: string) => screen.getByTestId(`sheet-section-${id}`)
const edge = (id: string) => screen.getByTestId(`section-edge-${id}`)
const movingLine = () => screen.queryByTestId("section-moving-line")
/** The first sheet's sections, by id, in order. */
const order = () => currentForm().pages[0].sections.map((section) => section.id)
const heightOf = (id: string) => findSection(currentForm(), id)?.section.height
const undoSteps = () => useWorkbenchFormStore.getState().undoStack.length
const undo = () => act(() => useWorkbenchFormStore.getState().undo())
const view = () => useWorkbenchViewStore.getState()
/** A pointer pressed on `target` at (x, y), or moved or released there over the window. */
const pointer = (type: "pointerdown" | "pointermove" | "pointerup", x: number, y: number, target: EventTarget = window) =>
  act(() => {
    target.dispatchEvent(new MouseEvent(type, { bubbles: true, button: 0, buttons: type === "pointerup" ? 0 : 1, clientX: x, clientY: y }))
  })
/** `sectionedForm` with its Notes section emptied, 72 tall. */
const emptyNotes = () => {
  const form = sectionedForm()
  form.pages[0].sections[2].widgets = []
  return form
}

// While building each section is under a 24 bar: Policy's bar at 0 and its band 24 to 176,
// Flood's bar at 176 and its band 200 to 432, Notes' bar at 432 and its band 456 to 528.
// Their middles, which a dragged bar passes to move its section, are 100, 316 and 492.
const SECTIONS = ["s_policy", "s_flood", "s_notes"]

describe("SectionHeaders", () => {
  beforeEach(() => {
    stubLayout()
    loadForm(sectionedForm())
  })

  afterEach(() => {
    releasePointer()
    cleanup()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it("draws a bar just above each section while building, numbered and named, and none in Preview", () => {
    render(<SheetCanvas />)

    expect(SECTIONS.map((id) => topOnSheet(bar(id)))).toEqual([0, 176, 432])
    expect(SECTIONS.map((id) => bar(id).style.height)).toEqual(["24px", "24px", "24px"])
    expect(band("s_policy")).toHaveStyle({ top: "24px", height: "152px" })
    expect(band("s_flood")).toHaveStyle({ top: "200px" })
    expect(band("s_notes")).toHaveStyle({ top: "456px" })
    // Each drawn first in its section, before its components.
    expect(band("s_flood").firstElementChild).toBe(bar("s_flood"))
    expect(screen.getAllByRole("button", { name: /^Section \d/ }).map((button) => button.getAttribute("aria-label"))).toEqual([
      "Section 1, Policy",
      "Section 2, Flood",
      "Section 3, Notes",
    ])
    expect(bar("s_flood")).toHaveTextContent("Flood")
    expect(bar("s_flood")).not.toHaveAttribute("data-selected")

    act(() => useWorkbenchViewStore.setState({ section: "preview" }))
    expect(screen.queryAllByTestId(/^section-header-/)).toHaveLength(0)
  })

  it("selects a section by a press on its bar, kept from the sheet's own, or by Enter or Space on it", () => {
    render(<SheetCanvas />)
    act(() => view().select("w_grid"))

    // The press is the bar's: the sheet's would deselect the component before the click.
    pointer("pointerdown", 500, 180, bar("s_flood"))
    expect(view().selectedId).toBe("w_grid")
    pointer("pointerup", 500, 180)
    fireEvent.click(bar("s_flood"))
    expect(view()).toMatchObject({ sectionId: "s_flood", selectedId: null })
    expect(bar("s_flood")).toHaveAttribute("data-selected", "true")
    expect(bar("s_policy")).not.toHaveAttribute("data-selected")

    // Enter and Space are left to the button, which clicks itself.
    expect(fireEvent.keyDown(bar("s_notes"), { key: "Enter" })).toBe(true)
    fireEvent.click(bar("s_notes"))
    expect(view().sectionId).toBe("s_notes")
    expect(fireEvent.keyDown(bar("s_policy"), { key: " " })).toBe(true)
    fireEvent.click(bar("s_policy"))
    expect(view().sectionId).toBe("s_policy")
    expect(bar("s_policy")).toHaveAttribute("data-selected", "true")
    expect(bar("s_notes")).not.toHaveAttribute("data-selected")
    expect(undoSteps()).toBe(0)
  })

  it("moves a section by dragging its bar to another place, a line marking where it would go, as one undo step; a click moves nothing", () => {
    render(<SheetCanvas />)

    // A press that barely moves is a click.
    pointer("pointerdown", 500, 10, bar("s_policy"))
    pointer("pointermove", 501, 12)
    expect(movingLine()).toBeNull()
    pointer("pointerup", 501, 12)
    fireEvent.click(bar("s_policy"))
    expect(order()).toEqual(SECTIONS)
    expect(undoSteps()).toBe(0)
    expect(view().sectionId).toBe("s_policy")

    // Past Flood's middle the line is drawn over Notes' bar, a screen pixel either side of its top.
    pointer("pointerdown", 500, 10, bar("s_policy"))
    pointer("pointermove", 500, 320)
    expect(view().movingSection).toEqual({ id: "s_policy", to: 1 })
    expect(movingLine()).toHaveStyle({ top: "431px", height: "2px" })
    // Past every section's middle, it is drawn under the last.
    pointer("pointermove", 500, 600)
    expect(view().movingSection).toEqual({ id: "s_policy", to: 2 })
    expect(movingLine()).toHaveStyle({ top: "527px" })
    pointer("pointermove", 500, 320)
    // Nothing moves until the release.
    expect(order()).toEqual(SECTIONS)
    pointer("pointerup", 500, 320)

    expect(movingLine()).toBeNull()
    expect(view().movingSection).toBeNull()
    expect(order()).toEqual(["s_flood", "s_policy", "s_notes"])
    expect(undoSteps()).toBe(1)
    // Drawn and numbered in its new place: under Flood, 24 + 232 down.
    expect(bar("s_policy")).toHaveAttribute("aria-label", "Section 2, Policy")
    expect(topOnSheet(bar("s_policy"))).toBe(256)
    expect(band("s_policy")).toHaveStyle({ top: "280px" })

    undo()
    expect(order()).toEqual(SECTIONS)
  })

  it("moves a section only once the pointer passes another section's middle, up or down", () => {
    render(<SheetCanvas />)

    // Down, past Flood's middle at 316.
    act(() => drag(bar("s_policy"), [500, 10], [500, 315]))
    expect(order()).toEqual(SECTIONS)
    act(() => drag(bar("s_policy"), [500, 10], [500, 317]))
    expect(order()).toEqual(["s_flood", "s_policy", "s_notes"])
    expect(undoSteps()).toBe(1)
    undo()

    // Up, past Flood's middle, then Policy's at 100, the line over the bar it would go above.
    act(() => drag(bar("s_notes"), [500, 440], [500, 317]))
    expect(order()).toEqual(SECTIONS)
    act(() => drag(bar("s_notes"), [500, 440], [500, 315]))
    expect(order()).toEqual(["s_policy", "s_notes", "s_flood"])
    undo()
    pointer("pointerdown", 500, 440, bar("s_notes"))
    pointer("pointermove", 500, 101)
    expect(view().movingSection).toEqual({ id: "s_notes", to: 1 })
    expect(movingLine()).toHaveStyle({ top: "175px" })
    pointer("pointermove", 500, 99)
    expect(view().movingSection).toEqual({ id: "s_notes", to: 0 })
    expect(movingLine()).toHaveStyle({ top: "-1px" })
    pointer("pointerup", 500, 99)
    expect(order()).toEqual(["s_notes", "s_policy", "s_flood"])
    expect(undoSteps()).toBe(1)
  })

  it("moves the focused bar's section a place with Alt+Up and Alt+Down, keeping the focus, and nothing past either end", () => {
    render(<SheetCanvas />)
    act(() => bar("s_flood").focus())

    fireEvent.keyDown(bar("s_flood"), { key: "ArrowDown", altKey: true })
    expect(order()).toEqual(["s_policy", "s_notes", "s_flood"])
    expect(document.activeElement).toBe(bar("s_flood"))
    expect(bar("s_flood")).toHaveAccessibleName("Section 3, Flood")
    fireEvent.keyDown(bar("s_flood"), { key: "ArrowDown", altKey: true })
    expect(order()).toEqual(["s_policy", "s_notes", "s_flood"])
    expect(undoSteps()).toBe(1)

    fireEvent.keyDown(bar("s_flood"), { key: "ArrowUp", altKey: true })
    fireEvent.keyDown(bar("s_flood"), { key: "ArrowUp", altKey: true })
    expect(order()).toEqual(["s_flood", "s_policy", "s_notes"])
    expect(document.activeElement).toBe(bar("s_flood"))
    expect(bar("s_flood")).toHaveAccessibleName("Section 1, Flood")
    fireEvent.keyDown(bar("s_flood"), { key: "ArrowUp", altKey: true })
    expect(order()).toEqual(["s_flood", "s_policy", "s_notes"])
    expect(undoSteps()).toBe(3)

    // Without Alt an arrow is not the bar's.
    expect(fireEvent.keyDown(bar("s_flood"), { key: "ArrowDown" })).toBe(true)
    expect(order()).toEqual(["s_flood", "s_policy", "s_notes"])
  })

  it("deletes nothing once the pipeline's document is adopted anew while it asks", async () => {
    render(<SheetCanvas />)

    fireEvent.keyDown(bar("s_flood"), { key: "Delete" })
    expect(asked()).not.toBeNull()
    // A branch switched meanwhile: the form read anew may hold another section of that id.
    act(() => useDocumentStatusStore.setState((state) => ({ executionGeneration: state.executionGeneration + 1 })))
    await act(async () => undefined)

    expect(asked()).toBeNull()
    expect(order()).toEqual(SECTIONS)
    expect(undoSteps()).toBe(0)
  })

  it("deletes the focused bar's section with Delete or Backspace, asking first while components are in it, and never a sheet's last", async () => {
    loadForm(emptyNotes())
    render(<SheetCanvas />)
    fireEvent.click(bar("s_flood"))

    // Cancelled, it stays.
    fireEvent.keyDown(bar("s_flood"), { key: "Delete" })
    expect(asked()).toMatchObject({ title: 'Delete "Flood" and the 1 component in it?', text: "The schema and the sample stay.", confirm: "Delete", danger: true })
    await answerQuestion(null)
    expect(order()).toEqual(SECTIONS)
    expect(undoSteps()).toBe(0)

    fireEvent.keyDown(bar("s_flood"), { key: "Backspace" })
    await answerQuestion("confirm")
    expect(order()).toEqual(["s_policy", "s_notes"])
    expect(findWidget(currentForm(), "w_grid")).toBeNull()
    expect(screen.queryByTestId("section-header-s_flood")).not.toBeInTheDocument()
    // Its panel closed with it.
    expect(view().sectionId).toBeNull()
    expect(undoSteps()).toBe(1)

    // An empty one goes without asking.
    fireEvent.keyDown(bar("s_notes"), { key: "Delete" })
    expect(asked()).toBeNull()
    expect(order()).toEqual(["s_policy"])
    expect(bar("s_policy")).toHaveAccessibleName("Section 1, Policy")

    // The sheet's last stays, its components or not, without asking.
    fireEvent.keyDown(bar("s_policy"), { key: "Delete" })
    fireEvent.keyDown(bar("s_policy"), { key: "Backspace" })
    expect(asked()).toBeNull()
    expect(order()).toEqual(["s_policy"])
    expect(undoSteps()).toBe(2)
  })

  it("draws the handle under the selected section alone, its drag setting the height on the grid, never below what its components reach or 48, each one undo step, the sheet ending with the last section", () => {
    loadForm(emptyNotes())
    render(<SheetCanvas />)
    expect(screen.queryAllByTestId(/^section-edge-/)).toHaveLength(0)
    expect(screen.getByTestId("sheet")).toHaveStyle({ height: "528px" })

    fireEvent.click(bar("s_flood"))
    expect(screen.getAllByTestId(/^section-edge-/)).toEqual([edge("s_flood")])
    // Under the middle of its bottom, 200 + 232 down, less half the handle's 8.
    expect(topOnSheet(edge("s_flood"))).toBe(428)
    expect(edge("s_flood")).toHaveStyle({ width: "40px", height: "8px" })

    // 43 down, 275, snaps to 272; the press is kept from the sheet, so the section stays selected.
    act(() => drag(edge("s_flood"), [500, 432], [500, 475]))
    expect([heightOf("s_policy"), heightOf("s_flood"), heightOf("s_notes")]).toEqual([152, 272, 72])
    expect(undoSteps()).toBe(1)
    expect(view().sectionId).toBe("s_flood")
    expect(band("s_notes")).toHaveStyle({ top: "496px" })
    expect(topOnSheet(edge("s_flood"))).toBe(468)
    // The sheet lengthened as far.
    expect(screen.getByTestId("sheet")).toHaveStyle({ height: "568px" })

    // Up past its Table's bottom, 16 + 200: kept there.
    act(() => drag(edge("s_flood"), [500, 472], [500, 300]))
    expect(heightOf("s_flood")).toBe(216)
    expect(undoSteps()).toBe(2)

    // An empty section is never under 48.
    fireEvent.click(bar("s_notes"))
    expect(screen.getAllByTestId(/^section-edge-/)).toEqual([edge("s_notes")])
    act(() => drag(edge("s_notes"), [500, 500], [500, -100]))
    expect(heightOf("s_notes")).toBe(48)
    expect(undoSteps()).toBe(3)

    // A press that does not move sets nothing.
    act(() => drag(edge("s_notes"), [500, 500], [500, 501]))
    expect(undoSteps()).toBe(3)

    undo()
    undo()
    undo()
    expect([heightOf("s_policy"), heightOf("s_flood"), heightOf("s_notes")]).toEqual([152, 232, 72])
  })

  it("records nothing for a drag of the handle held at the least its section can be, or brought back where it began, keeping redo", () => {
    const form = emptyNotes()
    form.pages[0].sections[2].height = 48
    loadForm(form)
    render(<SheetCanvas />)
    const redoSteps = () => useWorkbenchFormStore.getState().redoStack.length

    // An edit undone, to redo.
    fireEvent.click(bar("s_flood"))
    act(() => drag(edge("s_flood"), [500, 432], [500, 475]))
    undo()
    expect([undoSteps(), redoSteps()]).toEqual([0, 1])

    // Notes, empty and 48 tall, its handle under 456 + 48: dragged up, it stays at 48.
    fireEvent.click(bar("s_notes"))
    act(() => drag(edge("s_notes"), [500, 504], [500, 400]))
    expect(heightOf("s_notes")).toBe(48)
    expect([undoSteps(), redoSteps()]).toEqual([0, 1])

    // Dragged down, then back before it is released.
    pointer("pointerdown", 500, 504, edge("s_notes"))
    pointer("pointermove", 500, 560)
    expect(heightOf("s_notes")).toBe(104)
    pointer("pointermove", 500, 504)
    pointer("pointerup", 500, 504)
    expect(heightOf("s_notes")).toBe(48)
    expect([undoSteps(), redoSteps()]).toEqual([0, 1])

    act(() => useWorkbenchFormStore.getState().redo())
    expect(heightOf("s_flood")).toBe(272)
  })

  it("fits the selected section on a double-click on its handle: 16 below its lowest component, or 48 with none", () => {
    const form = emptyNotes()
    form.pages[0].sections[0].height = 400
    loadForm(form)
    render(<SheetCanvas />)

    fireEvent.click(bar("s_policy"))
    fireEvent.doubleClick(edge("s_policy"))
    // The boxes reach 16 + 120.
    expect(heightOf("s_policy")).toBe(152)
    expect(undoSteps()).toBe(1)
    // Fitted already, nothing is recorded.
    fireEvent.doubleClick(edge("s_policy"))
    expect(undoSteps()).toBe(1)

    fireEvent.click(bar("s_notes"))
    fireEvent.doubleClick(edge("s_notes"))
    expect(heightOf("s_notes")).toBe(48)
    expect(undoSteps()).toBe(2)
  })

  it("finishes a handle's drag as Ctrl+Z is pressed during it, recording it and undoing it, so undo and redo keep their direction", () => {
    renderHook(() => useWorkbenchShortcuts(() => {}))
    render(<SheetCanvas />)
    const titleOf = (id: string) => findSection(currentForm(), id)?.section.title
    const ctrl = (key: string, shiftKey = false) => fireEvent.keyDown(window, { key, ctrlKey: true, shiftKey })
    act(() => useWorkbenchFormStore.getState().change((spec) => renameSection(spec, "s_notes", "Cover")))
    fireEvent.click(bar("s_notes"))

    // Notes, 456 to 528, its handle under its bottom: pressed and dragged 16 down, then Ctrl+Z.
    pointer("pointerdown", 500, 528, edge("s_notes"))
    pointer("pointermove", 500, 544)
    expect(heightOf("s_notes")).toBe(88)
    ctrl("z")
    // The drag is undone, the title kept, and the pointer no longer followed.
    expect([heightOf("s_notes"), titleOf("s_notes")]).toEqual([72, "Cover"])
    pointer("pointermove", 500, 600)
    pointer("pointerup", 500, 600)
    expect(heightOf("s_notes")).toBe(72)

    // Undo goes on back, to the title, and redo forward, to the drag.
    ctrl("z")
    expect([heightOf("s_notes"), titleOf("s_notes")]).toEqual([72, "Notes"])
    ctrl("z", true)
    ctrl("z", true)
    expect([heightOf("s_notes"), titleOf("s_notes")]).toEqual([88, "Cover"])
  })

  it("ends a bar's drag as Ctrl+Z is pressed during it, moving nothing on its release", () => {
    renderHook(() => useWorkbenchShortcuts(() => {}))
    render(<SheetCanvas />)
    act(() => useWorkbenchFormStore.getState().change((spec) => renameSection(spec, "s_notes", "Cover")))

    // Policy's bar dragged past Flood's middle, then Ctrl+Z: the line goes and nothing moves.
    pointer("pointerdown", 500, 12, bar("s_policy"))
    pointer("pointermove", 500, 330)
    expect(view().movingSection).toEqual({ id: "s_policy", to: 1 })
    fireEvent.keyDown(window, { key: "z", ctrlKey: true })
    expect(view().movingSection).toBeNull()
    expect(movingLine()).not.toBeInTheDocument()
    pointer("pointerup", 500, 330)
    expect(order()).toEqual(SECTIONS)
    expect(findSection(currentForm(), "s_notes")?.section.title).toBe("Notes")
  })

  it("drags a bar and a handle at the zoom, wherever the sheet sits on the screen", () => {
    stubLayout({ left: 40, top: 100, width: 1000, height: 800 })
    render(<SheetCanvas />)
    act(() => view().setZoom(0.5))

    // 100 down the screen is the sheet's top, and each screen pixel two of the sheet's:
    // Flood's middle, 316 down the sheet, is 258 down the screen.
    pointer("pointerdown", 500, 105, bar("s_policy"))
    pointer("pointermove", 500, 257)
    expect(view().movingSection).toEqual({ id: "s_policy", to: 0 })
    // The line is a screen pixel either side, two of the sheet's: over Flood's bar at 176.
    expect(movingLine()).toHaveStyle({ top: "174px", height: "4px" })
    pointer("pointermove", 500, 259)
    expect(view().movingSection).toEqual({ id: "s_policy", to: 1 })
    expect(movingLine()).toHaveStyle({ top: "430px" })
    pointer("pointerup", 500, 259)
    expect(order()).toEqual(["s_flood", "s_policy", "s_notes"])

    // 20 screen pixels down is 40 on the sheet: 232 + 40.
    fireEvent.click(bar("s_flood"))
    act(() => drag(edge("s_flood"), [500, 230], [500, 250]))
    expect(heightOf("s_flood")).toBe(272)
    // And 50 up is 100 up, 172, snapped to 176 and kept at what its Table reaches.
    act(() => drag(edge("s_flood"), [500, 250], [500, 200]))
    expect(heightOf("s_flood")).toBe(216)
    expect(undoSteps()).toBe(3)
  })

  it("keeps a bar's own keys from the sheet's shortcuts, which never also act on the selected component", async () => {
    render(<SheetCanvas />)
    renderHook(() => useWorkbenchShortcuts(vi.fn()))
    act(() => view().select("w_boxes"))
    const boxes = () => findWidget(currentForm(), "w_boxes")?.widget

    act(() => bar("s_notes").focus())
    fireEvent.keyDown(bar("s_notes"), { key: "ArrowUp", altKey: true })
    expect(order()).toEqual(["s_policy", "s_notes", "s_flood"])
    // Past the top, nothing moves, the boxes neither.
    fireEvent.keyDown(bar("s_policy"), { key: "ArrowUp", altKey: true })
    fireEvent.keyDown(bar("s_flood"), { key: "ArrowDown", altKey: true })
    expect(order()).toEqual(["s_policy", "s_notes", "s_flood"])
    expect(boxes()).toMatchObject({ x: 16, y: 16 })

    fireEvent.keyDown(bar("s_notes"), { key: "Delete" })
    await answerQuestion("confirm")
    fireEvent.keyDown(bar("s_flood"), { key: "Backspace" })
    await answerQuestion("confirm")
    expect(order()).toEqual(["s_policy"])
    // The last stays, and its Delete is still its own.
    fireEvent.keyDown(bar("s_policy"), { key: "Delete" })
    expect(order()).toEqual(["s_policy"])
    expect(boxes()).toMatchObject({ x: 16, y: 16 })
    expect(view().selectedId).toBe("w_boxes")
    expect(undoSteps()).toBe(3)

    // Pressed anywhere else, Delete is the sheet's: it removes the selected component.
    fireEvent.keyDown(document.body, { key: "Delete" })
    expect(boxes()).toBeUndefined()
  })

  it("marks a section with conditions with an eye, struck through and its bar faded while a new quote hides it, its conditions told as the bar's description", () => {
    render(<SheetCanvas />)

    // Cover is not ticked on a new quote: Flood hides, whatever the sample holds.
    act(() => useWorkbenchFormStore.getState().change((spec) => withSampleCell(spec, "policy", 0, "policy.cover", true)))
    expect(bar("s_flood")).toHaveAttribute("data-hidden", "true")
    expect(bar("s_flood")).toHaveStyle({ opacity: "0.5" })
    const hiddenEye = within(bar("s_flood")).getByRole("img", { name: "Hidden for a new quote. Shows when Cover is ticked" })
    expect(hiddenEye.querySelector(".lucide-eye-off")).not.toBeNull()
    expect(bar("s_flood")).toHaveAttribute("aria-describedby", "s_flood-conditions")
    expect(bar("s_flood")).toHaveAccessibleDescription("Hidden for a new quote. Shows when Cover is ticked")
    // A section always shown has no eye and nothing to tell.
    expect(bar("s_policy")).not.toHaveAttribute("data-hidden")
    expect(within(bar("s_policy")).queryByRole("img")).not.toBeInTheDocument()
    expect(bar("s_policy")).not.toHaveAttribute("aria-describedby")

    // Shown while Cover is not ticked, as on a new quote.
    act(() =>
      useWorkbenchFormStore.getState().change((spec) => {
        const next = structuredClone(spec)
        next.pages[0].sections[1].show_when.conditions = [{ table: "policy", column: "policy.cover", op: "not_ticked", values: [] }]
        return next
      }),
    )
    expect(bar("s_flood")).not.toHaveAttribute("data-hidden")
    expect(bar("s_flood").style.opacity).toBe("")
    const eye = within(bar("s_flood")).getByRole("img", { name: "Shows when Cover is not ticked" })
    expect(eye.querySelector(".lucide-eye")).not.toBeNull()
    expect(bar("s_flood")).toHaveAccessibleDescription("Shows when Cover is not ticked")
  })

  it("marks a section whose conditions have a problem, naming it, its conditions and its problem told as the bar's description", () => {
    const form = sectionedForm()
    form.pages[0].sections[2].show_when = { match: "all", conditions: [{ table: "policy", column: "policy.state", op: "equals", values: ["TX"] }] }
    loadForm(form)
    render(<SheetCanvas />)

    const problem = `Section "Notes" tests state against "TX", which isn't one of its allowed values`
    expect(within(bar("s_notes")).getByRole("img", { name: problem })).toHaveAttribute("title", problem)
    expect(bar("s_notes")).toHaveAttribute("aria-describedby", "s_notes-conditions s_notes-problem")
    expect(bar("s_notes")).toHaveAccessibleDescription(`Hidden for a new quote. Shows when State equals TX ${problem}`)
    // Flood's conditions have no problem.
    expect(within(bar("s_flood")).getAllByRole("img")).toHaveLength(1)
    expect(bar("s_flood")).toHaveAttribute("aria-describedby", "s_flood-conditions")
  })
})
