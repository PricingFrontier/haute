/**
 * The sheets' tabs (specs/workbench): one per sheet, the showing one marked; a sheet
 * added and renamed by double-click, and no delete on a tab, the sheet's panel deleting it;
 * a sheet's eye, a new quote's while building, its settings and its problems; and, read
 * only in Preview, only switching between the sheets the sample shows, or the underwriter
 * app's quote.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../../stores/useWorkbenchPreviewStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { updateSchemaColumn, withSampleCell } from "../../utils/workbenchForm"
import PageTabs from "../PageTabs"
import { coverForm, currentForm, loadForm } from "./fixtures"

const tabs = () => screen.getAllByRole("tab").map((tab) => [tab.textContent, tab.getAttribute("aria-selected")])

describe("PageTabs", () => {
  beforeEach(() => {
    loadForm()
    useWorkbenchPreviewStore.setState({ quote: {}, price: null, error: null, pricing: false, readOnly: false })
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it("lists the sheets, marking the one showing, and shows another on click", () => {
    render(<PageTabs />)

    expect(tabs()).toEqual([["Sheet 1", "true"], ["Sheet 2", "false"]])
    fireEvent.click(screen.getByRole("tab", { name: "Sheet 2" }))
    expect(useWorkbenchViewStore.getState().pageId).toBe("p2")
    expect(tabs()).toEqual([["Sheet 1", "false"], ["Sheet 2", "true"]])
  })

  it("adds a sheet, named in turn, and shows it", () => {
    render(<PageTabs />)

    fireEvent.click(screen.getByRole("button", { name: "Add a sheet" }))

    expect(currentForm().pages.map((page) => page.title)).toEqual(["Sheet 1", "Sheet 2", "Sheet 3"])
    expect(useWorkbenchViewStore.getState().pageId).toBe(currentForm().pages[2].id)
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
  })

  it("renames a sheet on double-click, committing on Enter and keeping a blank name out", () => {
    render(<PageTabs />)

    fireEvent.doubleClick(screen.getByRole("tab", { name: "Sheet 1" }))
    const field = screen.getByRole("textbox", { name: "Sheet name" })
    expect(field).toBe(document.activeElement)
    fireEvent.change(field, { target: { value: "  Policy  " } })
    fireEvent.keyDown(field, { key: "Enter" })
    expect(currentForm().pages[0].title).toBe("Policy")
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument()
    // The tab takes the focus back.
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Policy" }))

    fireEvent.doubleClick(screen.getByRole("tab", { name: "Policy" }))
    fireEvent.change(screen.getByRole("textbox", { name: "Sheet name" }), { target: { value: "   " } })
    fireEvent.blur(screen.getByRole("textbox", { name: "Sheet name" }))
    expect(currentForm().pages[0].title).toBe("Policy")

    // By the keyboard: F2 on the tab.
    fireEvent.keyDown(screen.getByRole("tab", { name: "Policy" }), { key: "F2" })
    fireEvent.change(screen.getByRole("textbox", { name: "Sheet name" }), { target: { value: "Cover" } })
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Sheet name" }), { key: "Enter" })
    expect(currentForm().pages[0].title).toBe("Cover")
  })

  it("goes from tab to tab with Left, Right, Home and End, round the ends, showing each sheet, Tab stopping at the showing one alone", () => {
    render(<PageTabs />)
    const tab = (name: string) => screen.getByRole("tab", { name })
    const stops = () => screen.getAllByRole("tab").map((element) => element.tabIndex)
    expect(stops()).toEqual([0, -1])
    act(() => tab("Sheet 1").focus())

    fireEvent.keyDown(tab("Sheet 1"), { key: "ArrowRight" })
    expect(document.activeElement).toBe(tab("Sheet 2"))
    expect(useWorkbenchViewStore.getState().pageId).toBe("p2")
    expect(stops()).toEqual([-1, 0])
    // Round the end, and back.
    fireEvent.keyDown(tab("Sheet 2"), { key: "ArrowRight" })
    expect(document.activeElement).toBe(tab("Sheet 1"))
    fireEvent.keyDown(tab("Sheet 1"), { key: "ArrowLeft" })
    expect(document.activeElement).toBe(tab("Sheet 2"))
    fireEvent.keyDown(tab("Sheet 2"), { key: "Home" })
    expect(useWorkbenchViewStore.getState().pageId).toBe("p1")
    fireEvent.keyDown(tab("Sheet 1"), { key: "End" })
    expect(document.activeElement).toBe(tab("Sheet 2"))
    // Nothing moved.
    expect(currentForm().pages.map((page) => page.id)).toEqual(["p1", "p2"])
    expect(useWorkbenchFormStore.getState().undoStack).toEqual([])
  })

  it("puts no delete on a tab: the sheet's own panel deletes it", () => {
    render(<PageTabs />)

    // The showing tab's one button beside it is its settings mark.
    expect(within(screen.getByTestId("sheet-tab-p1")).getAllByRole("button").map((button) => button.getAttribute("aria-label"))).toEqual(["Settings of sheet Sheet 1"])
    expect(screen.queryByRole("button", { name: /Delete/ })).not.toBeInTheDocument()
  })

  it("read only, in Preview, only switches sheets, staying in Preview", () => {
    useWorkbenchViewStore.setState({ mode: "preview" })
    render(<PageTabs />)

    expect(screen.queryByRole("button", { name: "Add a sheet" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Delete sheet/ })).not.toBeInTheDocument()
    fireEvent.doubleClick(screen.getByRole("tab", { name: "Sheet 1" }))
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole("tab", { name: "Sheet 2" }))
    expect(useWorkbenchViewStore.getState()).toMatchObject({ pageId: "p2", mode: "preview" })
    // The arrow keys go from tab to tab there too.
    fireEvent.keyDown(screen.getByRole("tab", { name: "Sheet 2" }), { key: "ArrowLeft" })
    expect(useWorkbenchViewStore.getState()).toMatchObject({ pageId: "p1", mode: "preview" })
    fireEvent.keyDown(screen.getByRole("tab", { name: "Sheet 1" }), { key: "F2" })
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument()

    // Nor are the sheets moved there.
    expect(screen.getByTestId("sheet-tab-p1")).toHaveAttribute("draggable", "false")
    fireEvent.keyDown(screen.getByRole("tab", { name: "Sheet 1" }), { key: "ArrowRight", altKey: true })
    expect(currentForm().pages.map((page) => page.id)).toEqual(["p1", "p2"])
  })

  it("moves a sheet by dragging its tab onto another, as one undo step", () => {
    render(<PageTabs />)

    fireEvent.dragStart(screen.getByTestId("sheet-tab-p1"))
    fireEvent.dragOver(screen.getByTestId("sheet-tab-p2"))
    expect(screen.getByTestId("sheet-tab-p2")).toHaveAttribute("data-drop-target", "true")
    fireEvent.drop(screen.getByTestId("sheet-tab-p2"))

    expect(currentForm().pages.map((page) => page.id)).toEqual(["p2", "p1"])
    // The sheet that showed, the first while none was chosen, still shows.
    expect(tabs()).toEqual([["Sheet 2", "false"], ["Sheet 1", "true"]])
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
  })

  it("keeps the selection when a tab moves before any sheet was chosen, the sheet showing pinned", () => {
    useWorkbenchViewStore.setState({ pageId: null, selectedId: "w_boxes" })
    render(<PageTabs />)

    fireEvent.keyDown(screen.getByRole("tab", { name: "Sheet 1" }), { key: "ArrowRight", altKey: true })

    expect(useWorkbenchViewStore.getState()).toMatchObject({ pageId: "p1", selectedId: "w_boxes" })
  })

  it("moves the focused tab's sheet with Alt+Left and Alt+Right, keeping the focus and never past either end", () => {
    render(<PageTabs />)
    const first = () => screen.getByRole("tab", { name: "Sheet 1" })
    act(() => first().focus())

    fireEvent.keyDown(first(), { key: "ArrowRight", altKey: true })
    expect(currentForm().pages.map((page) => page.id)).toEqual(["p2", "p1"])
    expect(document.activeElement).toBe(first())

    fireEvent.keyDown(first(), { key: "ArrowRight", altKey: true })
    fireEvent.keyDown(first(), { key: "ArrowLeft" })
    expect(currentForm().pages.map((page) => page.id)).toEqual(["p2", "p1"])
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)

    fireEvent.keyDown(first(), { key: "ArrowLeft", altKey: true })
    expect(currentForm().pages.map((page) => page.id)).toEqual(["p1", "p2"])
    expect(document.activeElement).toBe(first())
  })

  it("marks a sheet with conditions with an eye, struck through and its tab faded while a new quote hides it, whatever the sample holds", () => {
    loadForm(coverForm())
    render(<PageTabs />)

    const cover = () => screen.getByRole("tab", { name: /Cover$/ })
    expect(cover()).toHaveAttribute("data-hidden", "true")
    expect(within(cover()).getByRole("img")).toHaveAccessibleName("Hidden for a new quote. Shows when Cover is ticked")
    expect(within(screen.getByRole("tab", { name: "Sheet 1" })).queryByRole("img")).not.toBeInTheDocument()

    // The sample is Preview's: while building the sheets are a new quote's.
    act(() => useWorkbenchFormStore.getState().change((spec) => withSampleCell(spec, "policy", 0, "policy.cover", true)))
    expect(cover()).toHaveAttribute("data-hidden", "true")

    // A new quote holds its defaults: cover starting ticked shows the sheet.
    act(() => useWorkbenchFormStore.getState().change((spec) => updateSchemaColumn(spec, "policy", "policy.cover", { default_value: true })))
    expect(cover()).not.toHaveAttribute("data-hidden")
  })

  it("opens the showing sheet's own panel from its settings mark", () => {
    useWorkbenchViewStore.setState({ selectedId: "w_grid" })
    render(<PageTabs />)

    expect(screen.queryByRole("button", { name: "Settings of sheet Sheet 2" })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Settings of sheet Sheet 1" }))
    expect(useWorkbenchViewStore.getState()).toMatchObject({ sheetSelected: true, selectedId: null })
  })

  it("marks a sheet whose conditions have a problem, naming it", () => {
    const form = coverForm()
    // Tested on the sheet it shows alone: once hidden, nothing could show the sheet again.
    form.pages[1].show_when.conditions = [{ table: "policy", column: "policy.year", op: "above", values: ["2020"] }]
    loadForm(form)
    render(<PageTabs />)

    expect(screen.getByRole("img", { name: /^Sheet "Cover" waits on year, which is only on components that hide with it/ })).toBeInTheDocument()
  })

  it("in Preview offers only the sheets the sample shows, the first that does marked in place of a hidden one chosen", () => {
    loadForm(coverForm())
    useWorkbenchViewStore.setState({ mode: "preview", pageId: "p2" })
    render(<PageTabs />)

    expect(tabs()).toEqual([["Sheet 1", "true"]])
    act(() => useWorkbenchFormStore.getState().change((spec) => withSampleCell(spec, "policy", 0, "policy.cover", true)))
    expect(tabs()).toEqual([["Sheet 1", "false"], ["Cover", "true"]])
    expect(screen.queryByRole("img")).not.toBeInTheDocument()
  })

  it("in the underwriter app offers the sheets its quote shows, not the sample's", () => {
    loadForm(withSampleCell(coverForm(), "policy", 0, "policy.cover", true))
    useWorkbenchViewStore.setState({ mode: "preview", underwriterApp: true, pageId: "p2" })
    render(<PageTabs />)

    expect(tabs()).toEqual([["Sheet 1", "true"]])
    act(() => useWorkbenchPreviewStore.setState({ quote: { policy: [{ "policy.cover": true }] } }))
    expect(tabs()).toEqual([["Sheet 1", "false"], ["Cover", "true"]])
  })
})
