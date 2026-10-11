/**
 * The workbench view's own state (specs/workbench): which of the view's sections and which
 * sheet show, a component, a section or the sheet's own panel selected, a section moved by
 * its header, the sections and components Preview draws, the zoom within its bounds and
 * fitted to the viewport, and the properties panel's width.
 */
import { beforeEach, describe, expect, it } from "vitest"
import { SHEET_PADDING, fitZoom } from "../../utils/sheetGeometry"
import { sheetVisibility } from "../../utils/sheetVisibility"
import { pageWidgets } from "../../utils/workbenchForm"
import { coverForm, loadForm, sectionedForm, sheetForm, widgetsOf } from "../../workbench/__tests__/fixtures"
import useWorkbenchFormStore from "../useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../useWorkbenchPreviewStore"
import useWorkbenchViewStore, { MAX_ZOOM, MIN_ZOOM, activePage, previewSections, shownPage } from "../useWorkbenchViewStore"

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
    expect(store().zoom).toBe(fitZoom(400 - SHEET_PADDING * 2, pageWidgets(sheetForm().pages[0])))
    expect(store().zoom).toBeLessThan(1)
    expect(store().zoom).toBeGreaterThanOrEqual(MIN_ZOOM)
  })

  it("opens the sheet's own panel or selects a component, never both, and closes the panel as another sheet or another of the view's sections shows", () => {
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

  it("selects a section, opening its panel, never with a component selected or the sheet's panel open", () => {
    const store = useWorkbenchViewStore.getState
    store().select("w_boxes")
    store().selectSection("s_p1")
    expect(store()).toMatchObject({ sectionId: "s_p1", selectedId: null, sheetSelected: false })
    store().selectSheet()
    store().selectSection("s_p1")
    expect(store()).toMatchObject({ sectionId: "s_p1", selectedId: null, sheetSelected: false })

    // A component selected, or nothing, or the sheet's own panel opened: the section's closes.
    store().select("w_grid")
    expect(store()).toMatchObject({ sectionId: null, selectedId: "w_grid", sheetSelected: false })
    store().selectSection("s_p1")
    store().select(null)
    expect(store()).toMatchObject({ sectionId: null, selectedId: null })
    store().selectSection("s_p1")
    store().selectSheet()
    expect(store()).toMatchObject({ sectionId: null, sheetSelected: true })
  })

  it("closes the section's panel as another sheet or another of the view's sections shows", () => {
    const store = useWorkbenchViewStore.getState
    store().selectSection("s_p1")
    store().showPage("p2")
    expect(store()).toMatchObject({ pageId: "p2", sectionId: null })

    store().selectSection("s_p2")
    store().showSection("schema")
    expect(store()).toMatchObject({ section: "schema", sectionId: null })
    store().showSection("sheets")
    store().selectSection("s_p2")
    store().showSection("preview")
    expect(store()).toMatchObject({ section: "preview", sectionId: null })
  })

  it("follows a section dragged by its header, with the place it would move to, until it is let go", () => {
    loadForm(sectionedForm())
    const store = useWorkbenchViewStore.getState
    expect(store().movingSection).toBeNull()

    store().setMovingSection({ id: "s_policy", to: 1 })
    store().setMovingSection({ id: "s_policy", to: 2 })
    expect(store().movingSection).toEqual({ id: "s_policy", to: 2 })
    store().setMovingSection(null)
    expect(store().movingSection).toBeNull()
  })

  it("in Preview reads the sheet chosen while it shows, else the first that shows, else none", () => {
    const form = sheetForm()
    expect(shownPage(form, "p2", new Set(["p1", "p2"]))?.id).toBe("p2")
    expect(shownPage(form, "p2", new Set(["p1"]))?.id).toBe("p1")
    expect(shownPage(form, null, new Set(["p2"]))?.id).toBe("p2")
    expect(shownPage(form, "p1", new Set())).toBeNull()
  })

  it("in Preview keeps a sheet's sections that show, in order, each with only its components that show", () => {
    const form = sectionedForm()
    // The note shows only while the policy's state is NY.
    widgetsOf(form, 0, 2)[0].show_when = { match: "all", conditions: [{ table: "policy", column: "policy.state", op: "equals", values: ["NY"] }] }
    const [policy, flood, notes] = form.pages[0].sections

    // Nothing typed: Flood hidden with cover unticked, and Notes shown without its note,
    // its place in the section left empty.
    expect(previewSections(form.pages[0], sheetVisibility(form, {}))).toEqual([policy, { ...notes, widgets: [] }])
    // Cover ticked and the state NY: every section in the sheet's order, each whole.
    const quote = { policy: [{ "policy.cover": true, "policy.state": "NY" }] }
    expect(previewSections(form.pages[0], sheetVisibility(form, quote))).toEqual([policy, flood, notes])
    // A hidden sheet's sections, none: the Cover sheet's while cover is unticked.
    expect(previewSections(form.pages[1], sheetVisibility(form, {}))).toEqual([])
    // The sheet keeps its components.
    expect(notes.widgets.map((widget) => widget.id)).toEqual(["w_note"])
  })

  it("fits Preview's sheet to the components the quote shows on it", () => {
    const form = coverForm()
    widgetsOf(form)[1].show_when = { match: "all", conditions: [{ table: "policy", column: "policy.state", op: "equals", values: ["NY"] }] }
    loadForm(form)
    useWorkbenchPreviewStore.getState().clear()
    const store = useWorkbenchViewStore.getState
    store().setViewport(viewport(400))
    store().showSection("preview")

    store().fitZoom()
    expect(store().zoom).toBe(fitZoom(400 - SHEET_PADDING * 2, [widgetsOf(form)[0]]))
    useWorkbenchPreviewStore.getState().setCell("policy", 0, "policy.state", "NY")
    store().fitZoom()
    expect(store().zoom).toBe(fitZoom(400 - SHEET_PADDING * 2, widgetsOf(form)))
  })

  it("fits Preview's sheet to the components of the sections the quote shows, and every section's while building", () => {
    const form = sectionedForm()
    loadForm(form)
    useWorkbenchPreviewStore.getState().clear()
    const store = useWorkbenchViewStore.getState
    store().setViewport(viewport(400))
    const width = 400 - SHEET_PADDING * 2
    const [boxes, grid, note] = pageWidgets(form.pages[0])

    // While building, every section's components, the hidden Flood's wide Table included.
    store().fitZoom()
    expect(store().zoom).toBe(fitZoom(width, [boxes, grid, note]))

    // In Preview, with Flood hidden while cover is unticked, its Table reaches nothing,
    // though it has no conditions of its own.
    store().showSection("preview")
    store().fitZoom()
    expect(store().zoom).toBe(fitZoom(width, [boxes, note]))
    expect(store().zoom).toBeGreaterThan(fitZoom(width, [boxes, grid, note]))

    useWorkbenchPreviewStore.getState().setCell("policy", 0, "policy.cover", true)
    store().fitZoom()
    expect(store().zoom).toBe(fitZoom(width, [boxes, grid, note]))
  })

  it("keeps the properties panel's width between openings", () => {
    useWorkbenchViewStore.getState().setPanelWidth(420)

    expect(useWorkbenchViewStore.getState().panelWidth).toBe(420)
  })
})
