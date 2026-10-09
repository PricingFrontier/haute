/**
 * Zustand store for the workbench view's own state while it shows (specs/workbench):
 * which section shows (the sheets or the schema while building, or Preview), which
 * sheet, the selected component, the sheet's zoom, a component being dragged out of the
 * palette, the properties panel's width, and the sheet and viewport elements that drops
 * are placed on and the zoom fits to. None of it is part of the form, so none is saved
 * or undone.
 */
import { create } from "zustand"
import type { FormSpec, Page } from "../api/types"
import { SHEET_PADDING, fitZoom as zoomToFit } from "../utils/sheetGeometry"
import type { WidgetType } from "../utils/workbenchForm"
import useWorkbenchFormStore from "./useWorkbenchFormStore"

/** The sheets and the schema are Build's; Preview shows the sheets as an underwriter sees them. */
export type WorkbenchSection = "sheets" | "schema" | "preview"

/** A component being dragged out of the palette, and where the pointer is. */
export interface Creating {
  type: WidgetType
  clientX: number
  clientY: number
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

interface WorkbenchViewState {
  section: WorkbenchSection
  /** The sheet showing; null until one is chosen, which `activePage` reads as the first. */
  pageId: string | null
  /** The selected component, whose properties panel is open. */
  selectedId: string | null
  zoom: number
  creating: Creating | null
  /** The properties panel's width, kept between openings. */
  panelWidth: number
  /** The sheet element and its scrolling viewport, while the sheets section shows. */
  sheet: HTMLElement | null
  viewport: HTMLElement | null
  showSection: (section: WorkbenchSection) => void
  /** Show a sheet, with nothing selected: in Preview as before, else on the sheets section. */
  showPage: (pageId: string) => void
  select: (id: string | null) => void
  setZoom: (zoom: number) => void
  zoomBy: (step: number) => void
  /** Zoom so everything on the sheet fits the viewport across, never past 100%. */
  fitZoom: () => void
  setCreating: (creating: Creating | null) => void
  setPanelWidth: (width: number) => void
  setSheet: (element: HTMLElement | null) => void
  setViewport: (element: HTMLElement | null) => void
}

const useWorkbenchViewStore = create<WorkbenchViewState>()((set, get) => ({
  section: "sheets",
  pageId: null,
  selectedId: null,
  zoom: 1,
  creating: null,
  panelWidth: DEFAULT_PANEL_WIDTH,
  sheet: null,
  viewport: null,
  showSection: (section) => set({ section, selectedId: null }),
  showPage: (pageId) => set((s) => ({ section: s.section === "preview" ? "preview" : "sheets", pageId, selectedId: null })),
  select: (selectedId) => set({ selectedId }),
  setZoom: (zoom) => set({ zoom: Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom)) }),
  zoomBy: (step) => get().setZoom(get().zoom + step),
  fitZoom: () => {
    const { viewport, pageId } = get()
    const { form } = useWorkbenchFormStore.getState()
    if (viewport === null || form === null) return
    set({ zoom: zoomToFit(viewport.clientWidth - SHEET_PADDING * 2, activePage(form, pageId).widgets) })
  },
  setCreating: (creating) => set({ creating }),
  setPanelWidth: (panelWidth) => set({ panelWidth }),
  setSheet: (sheet) => set({ sheet }),
  setViewport: (viewport) => set({ viewport }),
}))

export default useWorkbenchViewStore
