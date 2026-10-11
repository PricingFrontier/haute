/**
 * The workbench view's own state (specs/workbench): which section and sheet show, a
 * component or the sheet's own panel selected, the zoom within its bounds and fitted to
 * the viewport, and the properties panel's width.
 */
import { beforeEach, describe, expect, it } from "vitest"
import { SHEET_PADDING, fitZoom } from "../../utils/sheetGeometry"
import { coverForm, loadForm, sheetForm } from "../../workbench/__tests__/fixtures"
import useWorkbenchFormStore from "../useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../useWorkbenchPreviewStore"
import useWorkbenchViewStore, { MAX_ZOOM, MIN_ZOOM, activePage, shownPage } from "../useWorkbenchViewStore"

/** A viewport `width` across, as the sheet's scrolling element reports it. */
function viewport(width: number): HTMLElement {
  const element = document.createElement("div")
  Object.defineProperty(element, "clientWidth", { value: width })
  return element
}

describe("useWorkbenchViewStore", () => {
  beforeEach(() => loadForm())

  it("shows a sheet with nothing selected: on the sheets section while building, in Preview as before", () => {
    const store = useWorkbenchViewStore.getState
    store().showSection("schema")
    store().select("w_boxes")

    store().showPage("p2")
    expect(store()).toMatchObject({ section: "sheets", pageId: "p2", selectedId: null })

    store().showSection("preview")
    store().showPage("p1")
    expect(store()).toMatchObject({ section: "preview", pageId: "p1" })

    // The sheet named, or the first when none is or it was removed.
    expect(activePage(sheetForm(), "p2").id).toBe("p2")
    expect(activePage(sheetForm(), null).id).toBe("p1")
    expect(activePage(sheetForm(), "gone").id).toBe("p1")
  })

  it("keeps the zoom within its bounds, whether set, stepped or fitted", () => {
    const store = useWorkbenchViewStore.getState
    store().setZoom(5)
    expect(store().zoom).toBe(MAX_ZOOM)
    store().zoomBy(-5)
    expect(store().zoom).toBe(MIN_ZOOM)

    // Nothing to fit to: no viewport yet, or no form.
    store().setZoom(1)
    store().fitZoom()
    expect(store().zoom).toBe(1)
    store().setViewport(viewport(400))
    useWorkbenchFormStore.setState({ form: null })
    store().fitZoom()
    expect(store().zoom).toBe(1)

    loadForm()
    store().setViewport(viewport(400))
    store().fitZoom()
    expect(store().zoom).toBe(fitZoom(400 - SHEET_PADDING * 2, sheetForm().pages[0].widgets))
    expect(store().zoom).toBeLessThan(1)
    expect(store().zoom).toBeGreaterThanOrEqual(MIN_ZOOM)
  })

  it("opens the sheet's own panel or selects a component, never both, and closes the panel with the section or the sheet", () => {
    const store = useWorkbenchViewStore.getState
    store().select("w_boxes")
    store().selectSheet()
    expect(store()).toMatchObject({ selectedId: null, sheetSelected: true })
    store().select("w_grid")
    expect(store()).toMatchObject({ selectedId: "w_grid", sheetSelected: false })

    store().selectSheet()
    store().showPage("p2")
    expect(store().sheetSelected).toBe(false)
    store().selectSheet()
    store().showSection("preview")
    expect(store().sheetSelected).toBe(false)
  })

  it("in Preview reads the sheet chosen while it shows, else the first that shows, else none", () => {
    const form = sheetForm()
    expect(shownPage(form, "p2", new Set(["p1", "p2"]))?.id).toBe("p2")
    expect(shownPage(form, "p2", new Set(["p1"]))?.id).toBe("p1")
    expect(shownPage(form, null, new Set(["p2"]))?.id).toBe("p2")
    expect(shownPage(form, "p1", new Set())).toBeNull()
  })

  it("fits Preview's sheet to the components the quote shows on it", () => {
    const form = coverForm()
    form.pages[0].widgets[1].show_when = { match: "all", conditions: [{ table: "policy", column: "policy.state", op: "equals", values: ["NY"] }] }
    loadForm(form)
    useWorkbenchPreviewStore.getState().clear()
    const store = useWorkbenchViewStore.getState
    store().setViewport(viewport(400))
    store().showSection("preview")

    store().fitZoom()
    expect(store().zoom).toBe(fitZoom(400 - SHEET_PADDING * 2, [form.pages[0].widgets[0]]))
    useWorkbenchPreviewStore.getState().setCell("policy", 0, "policy.state", "NY")
    store().fitZoom()
    expect(store().zoom).toBe(fitZoom(400 - SHEET_PADDING * 2, form.pages[0].widgets))
  })

  it("keeps the properties panel's width between openings", () => {
    useWorkbenchViewStore.getState().setPanelWidth(420)

    expect(useWorkbenchViewStore.getState().panelWidth).toBe(420)
  })
})
