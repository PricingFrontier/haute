/**
 * The workbench view's own state (specs/workbench): which section and sheet show, the
 * zoom within its bounds and fitted to the viewport, and the properties panel's width.
 */
import { beforeEach, describe, expect, it } from "vitest"
import { SHEET_PADDING, fitZoom } from "../../utils/sheetGeometry"
import { loadForm, sheetForm } from "../../workbench/__tests__/fixtures"
import useWorkbenchFormStore from "../useWorkbenchFormStore"
import useWorkbenchViewStore, { MAX_ZOOM, MIN_ZOOM, activePage } from "../useWorkbenchViewStore"

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

  it("keeps the properties panel's width between openings", () => {
    useWorkbenchViewStore.getState().setPanelWidth(420)

    expect(useWorkbenchViewStore.getState().panelWidth).toBe(420)
  })
})
