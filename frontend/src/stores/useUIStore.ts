/**
 * Zustand store for chrome / layout UI state — panel toggles, modals,
 * sync banner, and node panel width.
 *
 * Toast notifications live in useToastStore.
 * Application settings (MLflow, sources, caches) live in useSettingsStore.
 * Graph-shaped state (nodes, edges, preamble) and the dirty-tracking
 * `lastSavedSnapshot` live in useGraphStore.
 */
import { create } from "zustand"

export type RatingStepEditorSection = "tables" | "combined"
export type ExplorePane = "code" | "overview" | "relationships" | "charts" | "export"
export type ExplorePreviewPane = "preview" | "overview" | "relationships" | "charts"

function setNodeIdEntry<T>(map: Record<string, T>, nodeId: string, value: T): Record<string, T> {
  return { ...map, [nodeId]: value }
}

interface UIState {
  // Modals / panels
  paletteOpen: boolean
  setPaletteOpen: (open: boolean) => void
  utilityOpen: boolean
  setUtilityOpen: (open: boolean) => void
  importsOpen: boolean
  setImportsOpen: (open: boolean) => void
  assistantOpen: boolean
  setAssistantOpen: (open: boolean) => void
  gitOpen: boolean
  setGitOpen: (open: boolean) => void
  shortcutsOpen: boolean
  setShortcutsOpen: (open: boolean | ((prev: boolean) => boolean)) => void
  submodelDialog: { nodeIds: string[] } | null
  setSubmodelDialog: (dialog: { nodeIds: string[] } | null) => void
  renameDialog: { nodeId: string; currentLabel: string } | null
  setRenameDialog: (dialog: { nodeId: string; currentLabel: string } | null) => void

  // Sync banner
  syncBanner: string | null
  setSyncBanner: (banner: string | null) => void

  // Node panel width (persisted across selection changes)
  nodePanelWidth: number
  setNodePanelWidth: (width: number) => void

  // Rating step editor section (remembered across node panel remounts)
  ratingStepEditorSections: Record<string, RatingStepEditorSection>
  setRatingStepEditorSection: (nodeId: string, section: RatingStepEditorSection) => void
  explorePanes: Record<string, ExplorePane>
  setExplorePane: (nodeId: string, pane: ExplorePane) => void
  explorePreviewPanes: Record<string, ExplorePreviewPane>
  setExplorePreviewPane: (nodeId: string, pane: ExplorePreviewPane) => void

  // Hover highlight — when set, connected edges glow and unconnected nodes/edges dim
  hoveredNodeId: string | null
  setHoveredNodeId: (id: string | null) => void

  // Node search (Ctrl+K)
  nodeSearchOpen: boolean
  setNodeSearchOpen: (open: boolean | ((prev: boolean) => boolean)) => void
}

const useUIStore = create<UIState>()((set) => ({
  // Modals / panels
  paletteOpen: true,
  setPaletteOpen: (open) => set({ paletteOpen: open }),
  utilityOpen: false,
  setUtilityOpen: (open) => set({ utilityOpen: open, importsOpen: false, assistantOpen: false, gitOpen: false }),
  importsOpen: false,
  setImportsOpen: (open) => set({ importsOpen: open, utilityOpen: false, assistantOpen: false, gitOpen: false }),
  assistantOpen: false,
  setAssistantOpen: (open) => set({ assistantOpen: open, utilityOpen: false, importsOpen: false, gitOpen: false }),
  gitOpen: false,
  setGitOpen: (open) => set({ gitOpen: open, utilityOpen: false, importsOpen: false, assistantOpen: false }),
  shortcutsOpen: false,
  setShortcutsOpen: (open) => {
    if (typeof open === "function") {
      set((s) => ({ shortcutsOpen: open(s.shortcutsOpen) }))
    } else {
      set({ shortcutsOpen: open })
    }
  },
  submodelDialog: null,
  setSubmodelDialog: (dialog) => set({ submodelDialog: dialog }),
  renameDialog: null,
  setRenameDialog: (dialog) => set({ renameDialog: dialog }),

  // Sync banner
  syncBanner: null,
  setSyncBanner: (banner) => set({ syncBanner: banner }),

  // Node panel width (0 = use dynamic default: 50% of available space)
  nodePanelWidth: 0,
  setNodePanelWidth: (width) => set({ nodePanelWidth: width }),

  // Per-node UI selections (remembered across node panel remounts)
  ratingStepEditorSections: {},
  setRatingStepEditorSection: (nodeId, section) => set((state) => ({
    ratingStepEditorSections: setNodeIdEntry(state.ratingStepEditorSections, nodeId, section),
  })),
  explorePanes: {},
  setExplorePane: (nodeId, pane) => set((state) => ({
    explorePanes: setNodeIdEntry(state.explorePanes, nodeId, pane),
  })),
  explorePreviewPanes: {},
  setExplorePreviewPane: (nodeId, pane) => set((state) => ({
    explorePreviewPanes: setNodeIdEntry(state.explorePreviewPanes, nodeId, pane),
  })),

  // Hover highlight
  hoveredNodeId: null,
  setHoveredNodeId: (id) => set({ hoveredNodeId: id }),

  // Node search
  nodeSearchOpen: false,
  setNodeSearchOpen: (open) => {
    if (typeof open === "function") {
      set((s) => ({ nodeSearchOpen: open(s.nodeSearchOpen) }))
    } else {
      set({ nodeSearchOpen: open })
    }
  },
}))

export default useUIStore
