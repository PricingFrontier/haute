/**
 * The view's shortcuts on the sheets (specs/workbench): Ctrl+S saves the project through
 * the host, Ctrl+1 fits the sheet, Escape closes the sheet's panel or a section's, and
 * with a component selected Escape deselects, Delete removes, Ctrl+D duplicates, Ctrl+C
 * copies and the arrows nudge as one undo step per burst, kept inside its section, top and
 * bottom, never growing it; Ctrl+V pastes the copy, selected or not; none of the sheet's
 * keys from a field or a select, nor on the schema section, nor in Preview, where Ctrl+1
 * applies, and undo and redo for the sample typed there; the selection's keys only from
 * the sheet or with nothing focused.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, renderHook, waitFor } from "@testing-library/react"
import useToastStore from "../../stores/useToastStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { findWidget, updateWidget } from "../../utils/workbenchForm"
import useWorkbenchShortcuts from "../useWorkbenchShortcuts"
import { currentForm, loadForm, sectionedForm, widgetsOf } from "./fixtures"

const key = (init: KeyboardEventInit, target: EventTarget = window) =>
  target.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init }))
const widgets = () => widgetsOf(currentForm())
const onSave = vi.fn()
// The stores' own actions, put back after each test, since some tests mock them.
const ownUndo = useWorkbenchFormStore.getState().undo
const ownFitZoom = useWorkbenchViewStore.getState().fitZoom

describe("useWorkbenchShortcuts on the sheets", () => {
  beforeEach(() => {
    loadForm()
    useWorkbenchViewStore.setState({ selectedId: "w_boxes" })
    renderHook(() => useWorkbenchShortcuts(onSave))
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.restoreAllMocks()
    onSave.mockClear()
    useWorkbenchFormStore.setState({ undo: ownUndo })
    useWorkbenchViewStore.setState({ fitZoom: ownFitZoom })
  })

  it("saves the project through the host on Ctrl+S or Cmd+S, a focused field's edit committed first", async () => {
    key({ key: "s", ctrlKey: true })
    expect(onSave).toHaveBeenCalledTimes(1)
    key({ key: "s", metaKey: true })
    expect(onSave).toHaveBeenCalledTimes(2)

    // From a field: the field loses focus, which commits its edit, and then the save runs.
    const field = document.createElement("input")
    document.body.appendChild(field)
    try {
      field.focus()
      key({ key: "s", ctrlKey: true }, field)
      expect(document.activeElement).not.toBe(field)
      expect(onSave).toHaveBeenCalledTimes(2)
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(3))
    } finally {
      field.remove()
    }
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

  it("closes the sheet's own panel on Escape, and deletes nothing on Delete while it is open", () => {
    useWorkbenchViewStore.getState().selectSheet()
    key({ key: "Delete" })
    expect(widgets()).toHaveLength(2)
    expect(useWorkbenchViewStore.getState().sheetSelected).toBe(true)

    key({ key: "Escape" })
    expect(useWorkbenchViewStore.getState()).toMatchObject({ sheetSelected: false, selectedId: null })
  })

  it("closes a section's panel on Escape, and deletes nothing on Delete while it is open", () => {
    loadForm(sectionedForm())
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true)
    useWorkbenchViewStore.getState().selectSection("s_flood")
    key({ key: "Delete" })
    key({ key: "Backspace" })
    // A section is deleted from its header or its panel, never by the sheet's keys.
    expect(currentForm().pages[0].sections.map((section) => section.id)).toEqual(["s_policy", "s_flood", "s_notes"])
    expect(widgetsOf(currentForm(), 0, 1).map((widget) => widget.id)).toEqual(["w_grid"])
    expect(confirm).not.toHaveBeenCalled()
    expect(useWorkbenchViewStore.getState().sectionId).toBe("s_flood")

    // Escape in a field, such as the panel's own, is the field's.
    const field = document.createElement("input")
    document.body.appendChild(field)
    key({ key: "Escape" }, field)
    field.remove()
    expect(useWorkbenchViewStore.getState().sectionId).toBe("s_flood")

    key({ key: "Escape" })
    expect(useWorkbenchViewStore.getState()).toMatchObject({ sectionId: null, selectedId: null, sheetSelected: false })
  })

  it("duplicates the selected component on Ctrl+D and selects the copy", () => {
    key({ key: "d", ctrlKey: true })

    expect(widgets()).toHaveLength(3)
    const copy = widgets()[1]
    expect(copy).toMatchObject({ type: "collection", title: "Policy", x: 32, y: 32 })
    expect(useWorkbenchViewStore.getState().selectedId).toBe(copy.id)
  })

  it("copies the selected component on Ctrl+C, as it is then, and pastes it past the original on Ctrl+V, then past that copy, each selected and one undo step", () => {
    const toasts = () => useToastStore.getState().toasts.map((toast) => toast.text)
    useToastStore.setState({ toasts: [] })
    const original = widgets()[0]
    // Copied with the browser's own copy of any text selected left to happen.
    expect(key({ key: "c", ctrlKey: true })).toBe(true)
    expect(toasts()).toContain('Collection "Policy" copied')
    expect(widgets()).toHaveLength(2)

    // A change after the copy is not pasted.
    act(() => useWorkbenchFormStore.getState().change((spec) => updateWidget(spec, "w_boxes", { title: "Changed" })))
    key({ key: "v", metaKey: true })
    expect(widgets()).toHaveLength(3)
    const pasted = widgets()[2]
    expect(pasted).toEqual({ ...original, id: pasted.id, x: 32, y: 32 })
    expect(pasted.id).not.toBe("w_boxes")
    expect(useWorkbenchViewStore.getState().selectedId).toBe(pasted.id)

    key({ key: "v", ctrlKey: true })
    expect(widgets()[3]).toMatchObject({ title: "Policy", x: 48, y: 48 })
    expect(useWorkbenchViewStore.getState().selectedId).toBe(widgets()[3].id)
    // The change, then each paste, one undo step.
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(3)
    key({ key: "z", ctrlKey: true })
    expect(widgets()).toHaveLength(3)
  })

  it("pastes into the section selected, else the selected component's, else its own on its sheet, else the showing sheet's first, selected or not", () => {
    loadForm(sectionedForm())
    const view = useWorkbenchViewStore.getState
    const sectionOf = (id: string) => findWidget(currentForm(), id)?.section.id
    // The Table, in Flood, copied.
    act(() => view().select("w_grid"))
    key({ key: "c", ctrlKey: true })

    // With nothing selected, into its own section.
    act(() => view().select(null))
    key({ key: "v", ctrlKey: true })
    expect(sectionOf(view().selectedId!)).toBe("s_flood")
    // Into the selected component's section: the policy's boxes'.
    act(() => view().select("w_boxes"))
    key({ key: "v", ctrlKey: true })
    expect(sectionOf(view().selectedId!)).toBe("s_policy")
    // Into the section selected: Notes, where it sat, Notes growing to hold it.
    act(() => view().selectSection("s_notes"))
    key({ key: "v", ctrlKey: true })
    const inNotes = findWidget(currentForm(), view().selectedId!)
    expect(inNotes).toMatchObject({ section: { id: "s_notes" }, widget: { x: 16, y: 0 } })
    expect(inNotes?.section.height).toBe(200)

    // On another sheet, its section's: the sheet's first, past the Year boxes where it sat.
    act(() => view().showPage("p2"))
    key({ key: "v", ctrlKey: true })
    expect(findWidget(currentForm(), view().selectedId!)).toMatchObject({ page: { id: "p2" }, section: { id: "s_p2" }, widget: { type: "tableInput", x: 32, y: 32 } })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(4)
  })

  it("pastes nothing before anything is copied, and copies and pastes nothing from a field, on the schema section or in Preview", () => {
    key({ key: "v", ctrlKey: true })
    expect(widgets()).toHaveLength(2)

    // A field's copy and paste are its own.
    const field = document.createElement("input")
    document.body.appendChild(field)
    key({ key: "c", ctrlKey: true }, field)
    expect(useWorkbenchViewStore.getState().copied).toBeNull()
    key({ key: "c", ctrlKey: true })
    key({ key: "v", ctrlKey: true }, field)
    field.remove()
    expect(widgets()).toHaveLength(2)

    useWorkbenchViewStore.setState({ section: "schema" })
    key({ key: "v", ctrlKey: true })
    useWorkbenchViewStore.setState({ section: "preview" })
    key({ key: "v", ctrlKey: true })
    expect(widgets()).toHaveLength(2)
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

  it("finishes a drag under way before a nudge, inside a burst too, so the drag never writes over it", () => {
    vi.useFakeTimers()
    const forms = useWorkbenchFormStore.getState
    key({ key: "ArrowRight" })
    expect(widgets()[0]).toMatchObject({ x: 24 })

    // The component pressed, as a drag begins, then nudged again within the burst.
    const stop = vi.fn()
    forms().beginGesture(forms().form!, stop)
    key({ key: "ArrowRight" })
    expect(stop).toHaveBeenCalledTimes(1)
    expect(widgets()[0]).toMatchObject({ x: 32 })
    // The press moved nothing, so the burst is still one undo step.
    expect(forms().undoStack).toHaveLength(1)
    key({ key: "z", ctrlKey: true })
    expect(widgets()[0]).toMatchObject({ x: 16 })
  })

  it("nudges a component no higher than its section's top and no lower than its bottom, never growing the section", () => {
    vi.useFakeTimers()
    loadForm(sectionedForm())
    useWorkbenchViewStore.setState({ selectedId: "w_grid" })
    const grid = () => findWidget(currentForm(), "w_grid")
    const heights = () => currentForm().pages[0].sections.map((section) => section.height)

    // The Table sits 16 below the top of "Flood", the section under the policy's boxes:
    // it stops at that top, and stays in its section.
    key({ key: "ArrowUp" })
    key({ key: "ArrowUp" })
    key({ key: "ArrowUp" })
    expect(grid()).toMatchObject({ widget: { y: 0 }, section: { id: "s_flood" } })
    expect(heights()).toEqual([152, 232, 72])

    // 200 tall in a section 232 tall, it reaches the bottom at 32 and stops there, by a grid
    // step or a pixel, the section keeping its height and the Table its section.
    for (let press = 0; press < 4; press++) key({ key: "ArrowDown" })
    expect(grid()?.widget.y).toBe(32)
    key({ key: "ArrowDown" })
    key({ key: "ArrowDown", shiftKey: true })
    expect(grid()).toMatchObject({ widget: { y: 32 }, section: { id: "s_flood" } })
    expect(heights()).toEqual([152, 232, 72])

    // Nor past the sheet's left edge: 16 in, it stops there.
    key({ key: "ArrowLeft" })
    key({ key: "ArrowLeft" })
    key({ key: "ArrowLeft" })
    expect(grid()?.widget).toMatchObject({ x: 0, y: 32 })

    // The burst is one undo step, back to where it was.
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
    key({ key: "z", ctrlKey: true })
    expect(grid()?.widget).toMatchObject({ x: 16, y: 16 })
    expect(heights()).toEqual([152, 232, 72])
  })

  it("records nothing for a nudge held at its section's top or the sheet's left, keeping redo", () => {
    const form = sectionedForm()
    const flood = form.pages[0].sections[1]
    flood.widgets = flood.widgets.map((placed) => ({ ...placed, x: 0, y: 0 }))
    loadForm(form)
    useWorkbenchViewStore.setState({ selectedId: "w_grid" })
    const forms = useWorkbenchFormStore.getState
    act(() => forms().change((spec) => updateWidget(spec, "w_grid", { title: "Plant" })))
    act(() => forms().undo())

    key({ key: "ArrowUp" })
    key({ key: "ArrowLeft", shiftKey: true })
    expect(findWidget(currentForm(), "w_grid")?.widget).toMatchObject({ x: 0, y: 0 })
    expect([forms().undoStack.length, forms().redoStack.length]).toEqual([0, 1])

    // A nudge that moves it is recorded, as ever.
    key({ key: "ArrowDown" })
    expect(findWidget(currentForm(), "w_grid")?.widget).toMatchObject({ x: 0, y: 8 })
    expect([forms().undoStack.length, forms().redoStack.length]).toEqual([1, 0])
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

  it("acts on the selection from the sheet or with nothing focused, never from a panel, the toolbar or a tab", () => {
    const viewport = document.createElement("div")
    const onTheSheet = document.createElement("div")
    viewport.appendChild(onTheSheet)
    // A focusable row of the properties panel's Order, and a toolbar button.
    const panelRow = document.createElement("div")
    panelRow.tabIndex = 0
    const button = document.createElement("button")
    document.body.append(viewport, panelRow, button)
    useWorkbenchViewStore.setState({ viewport })
    try {
      for (const target of [panelRow, button]) {
        key({ key: "Delete" }, target)
        key({ key: "Backspace" }, target)
        key({ key: "ArrowRight" }, target)
        key({ key: "d", ctrlKey: true }, target)
        key({ key: "c", ctrlKey: true }, target)
      }
      expect(widgets()).toHaveLength(2)
      expect(widgets()[0]).toMatchObject({ x: 16, y: 16 })
      expect(useWorkbenchViewStore.getState().copied).toBeNull()
      expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(0)

      // Escape closes the panel from inside it.
      key({ key: "Escape" }, panelRow)
      expect(useWorkbenchViewStore.getState().selectedId).toBeNull()

      useWorkbenchViewStore.setState({ selectedId: "w_boxes" })
      key({ key: "Delete" }, onTheSheet)
      expect(widgets().map((widget) => widget.id)).toEqual(["w_grid"])

      useWorkbenchViewStore.setState({ selectedId: "w_grid" })
      key({ key: "Delete" }, document.body)
      expect(widgets()).toHaveLength(0)
    } finally {
      viewport.remove()
      panelRow.remove()
      button.remove()
    }
  })

  it("in Preview, fits the sheet on Ctrl+1 and undoes and redoes, the sample typed there, but never edits the sheet", () => {
    const fitZoom = vi.fn()
    const undo = vi.fn()
    const redo = vi.fn()
    useWorkbenchViewStore.setState({ section: "preview", fitZoom })
    useWorkbenchFormStore.setState({ undo, redo })

    key({ key: "1", ctrlKey: true })
    key({ key: "Delete" })
    key({ key: "d", ctrlKey: true })
    key({ key: "ArrowRight" })
    key({ key: "z", ctrlKey: true })
    key({ key: "z", ctrlKey: true, shiftKey: true })

    expect(fitZoom).toHaveBeenCalledTimes(1)
    expect(widgets()).toHaveLength(2)
    expect(widgets()[0]).toMatchObject({ x: 16, y: 16 })
    expect(undo).toHaveBeenCalledTimes(1)
    expect(redo).toHaveBeenCalledTimes(1)
  })
})
