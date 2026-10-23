/**
 * Zustand store for the workbench view's own state while it shows (specs/workbench):
 * which of the view's sections shows (the sheets or the schema while building, or
 * Preview), whose values Preview shows (the form's sample, or the underwriter app's
 * quote), which sheet, the selected component, the selected section or the sheet's own
 * panel, the sheet's zoom, a component being dragged out of the palette, a section being
 * moved by its header, the sheet's size as pressed while an edge is dragged, the properties
 * panel's width and the pane it last showed for each component, the component copied, and
 * the sheet and viewport elements that drops are placed on and the zoom fits to. None of it is part of the form, so none is saved or undone.
 */
import { create } from "zustand"
import type { FormSpec, Page, Section } from "../api/types"
import { SHEET_PADDING, fitZoom as zoomToFit, type Size } from "../utils/sheetGeometry"
import { sheetVisibility, type SheetVisibility } from "../utils/sheetVisibility"
import { findWidget, type PaletteType, type Widget } from "../utils/workbenchForm"
import useWorkbenchFormStore from "./useWorkbenchFormStore"
import useWorkbenchPreviewStore from "./useWorkbenchPreviewStore"

/** The sheets and the schema are Build's; Preview shows the sheets as an underwriter sees them. */
export type WorkbenchSection = "sheets" | "schema" | "preview"

/** A component or a section being dragged out of the palette, and where the pointer is. */
export interface Creating {
  type: PaletteType
  clientX: number
  clientY: number
}

/** A section being moved by its header, and the place on its sheet it would move to. */
export interface MovingSection {
  id: string
  to: number
}

/** A Table's or a Collection's properties pane: the data it shows, or how it shows it. */
export type WidgetPane = "config" | "display"

/** A component copied, as it was then, and the section it was in. */
export interface Copied {
  widget: Widget
  sectionId: string
}

/** How far one Zoom In or Zoom Out changes the zoom. */
export const ZOOM_STEP = 0.1
export const MIN_ZOOM = 0.25
export const MAX_ZOOM = 2
const DEFAULT_PANEL_WIDTH = 360

/** The sheet `pageId` names, or the first when it names none: none chosen yet, or one removed. */
export function activePage(form: FormSpec, pageId: string | null): Page {
  return form.pages.find((page) => page.id === pageId) ?? form.pages[0]
}

/**
 * The sheet Preview shows of those that show (`shown`): the one `pageId` names while it
 * shows, else the first that does, else none.
 */
export function shownPage(form: FormSpec, pageId: string | null, shown: ReadonlySet<string>): Page | null {
  const chosen = activePage(form, pageId)
  return shown.has(chosen.id) ? chosen : (form.pages.find((page) => shown.has(page.id)) ?? null)
}

/** A sheet's sections that show, in order, each with only its components that show. */
export const previewSections = (page: Page, visibility: SheetVisibility): Section[] =>
  page.sections
    .filter((section) => visibility.sections.has(section.id))
    .map((section) => ({ ...section, widgets: section.widgets.filter((widget) => visibility.widgets.has(widget.id)) }))

interface WorkbenchViewState {
  section: WorkbenchSection
  /**
   * The sheets are the underwriter app's: Preview shows its quote, the Preview store's,
   * rather than the form's sample. The app sets it as it opens its release.
   */
  underwriterApp: boolean
  /** The sheet showing; null until one is chosen, which `activePage` reads as the first. */
  pageId: string | null
  /** The selected component, whose properties panel is open. */
  selectedId: string | null
  /** The showing sheet's own panel is open; never with a component or a section selected. */
  sheetSelected: boolean
  /** The selected section, whose panel is open; never with a component selected or the sheet's panel open. */
  sectionId: string | null
  zoom: number
  /** The sheets have been fitted to the viewport since the form was read: a zoom chosen since is kept. */
  fitted: boolean
  creating: Creating | null
  movingSection: MovingSection | null
  /**
   * The sheet's size as pressed, its height as drawn while building, while one of its
   * handles or a section's is dragged: the viewport scrolls over no less, so a sheet growing
   * shorter or narrower never scrolls the edge from the pointer.
   */
  heldSize: Size | null
  /** The properties panel's width, kept between openings. */
  panelWidth: number
  /** The pane each component's properties panel last showed, by component id; Config until another is chosen. */
  widgetPanes: Record<string, WidgetPane>
  /** The component Ctrl/Cmd+C copied, which Ctrl/Cmd+V pastes, or null. */
  copied: Copied | null
  /** The sheet element and its scrolling viewport, while the sheets section shows. */
  sheet: HTMLElement | null
  viewport: HTMLElement | null
  showSection: (section: WorkbenchSection) => void
  /** Show a sheet, with nothing selected: in Preview as before, else on the sheets section. */
  showPage: (pageId: string) => void
  /** Select a component, or nothing; either closes the sheet's panel and the section's. */
  select: (id: string | null) => void
  /** Open the showing sheet's own panel, no component or section selected. */
  selectSheet: () => void
  /** Select a section, opening its panel, no component selected. */
  selectSection: (id: string) => void
  setZoom: (zoom: number) => void
  zoomBy: (step: number) => void
  /**
   * Zoom so the sheet showing, in Preview the one the values show, fits the viewport
   * across, never past 100%, and count the sheets fitted.
   */
  fitZoom: () => void
  setCreating: (creating: Creating | null) => void
  setMovingSection: (moving: MovingSection | null) => void
  setHeldSize: (size: Size | null) => void
  setPanelWidth: (width: number) => void
  setWidgetPane: (id: string, pane: WidgetPane) => void
  setCopied: (copied: Copied | null) => void
  setSheet: (element: HTMLElement | null) => void
  setViewport: (element: HTMLElement | null) => void
}

const useWorkbenchViewStore = create<WorkbenchViewState>()((set, get) => ({
  section: "sheets",
  underwriterApp: false,
  pageId: null,
  selectedId: null,
  sheetSelected: false,
  sectionId: null,
  zoom: 1,
  fitted: false,
  creating: null,
  movingSection: null,
  heldSize: null,
  panelWidth: DEFAULT_PANEL_WIDTH,
  widgetPanes: {},
  copied: null,
  sheet: null,
  viewport: null,
  showSection: (section) => set({ section, selectedId: null, sheetSelected: false, sectionId: null }),
  showPage: (pageId) =>
    set((s) => ({ section: s.section === "preview" ? "preview" : "sheets", pageId, selectedId: null, sheetSelected: false, sectionId: null })),
  select: (selectedId) => set({ selectedId, sheetSelected: false, sectionId: null }),
  selectSheet: () => set({ selectedId: null, sheetSelected: true, sectionId: null }),
  selectSection: (sectionId) => set({ selectedId: null, sheetSelected: false, sectionId }),
  // In whole percents, so steps of a tenth never leave 0.30000000000000004.
  setZoom: (zoom) => set({ zoom: Math.round(Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom)) * 100) / 100 }),
  zoomBy: (step) => get().setZoom(get().zoom + step),
  fitZoom: () => {
    const { viewport, pageId, section, underwriterApp } = get()
    const { form } = useWorkbenchFormStore.getState()
    if (viewport === null || form === null) return
    let page: Page | null = activePage(form, pageId)
    if (section === "preview") {
      const visibility = sheetVisibility(form, underwriterApp ? useWorkbenchPreviewStore.getState().quote : form.sample)
      page = shownPage(form, pageId, visibility.pages)
    }
    // Through the same bounds as the other zoom controls: a very wide sheet fits at the
    // minimum, never at nothing.
    get().setZoom(page === null ? 1 : zoomToFit(viewport.clientWidth - SHEET_PADDING * 2, page.width))
    set({ fitted: true })
  },
  setCreating: (creating) => set({ creating }),
  setMovingSection: (movingSection) => set({ movingSection }),
  setHeldSize: (heldSize) => set({ heldSize }),
  setPanelWidth: (panelWidth) => set({ panelWidth }),
  setWidgetPane: (id, pane) => set((s) => ({ widgetPanes: { ...s.widgetPanes, [id]: pane } })),
  setCopied: (copied) => set({ copied }),
  setSheet: (sheet) => set({ sheet }),
  setViewport: (viewport) => set({ viewport }),
}))

// A form read anew, on the view's first showing or after a branch switch, is fitted when its
// sheets next show; the zoom chosen for the form before is nobody's. A save or an undo is no
// read, however the history looks after it. The pane remembered for a component the form no
// longer has, deleted or read away, is forgotten.
useWorkbenchFormStore.subscribe((state, previous) => {
  if (state.reads !== previous.reads) useWorkbenchViewStore.setState({ fitted: false })
  if (state.form === previous.form) return
  const { widgetPanes } = useWorkbenchViewStore.getState()
  const form = state.form
  const gone = Object.keys(widgetPanes).filter((id) => form === null || findWidget(form, id) === null)
  if (gone.length > 0) useWorkbenchViewStore.setState({ widgetPanes: Object.fromEntries(Object.entries(widgetPanes).filter(([id]) => !gone.includes(id))) })
})

export default useWorkbenchViewStore
