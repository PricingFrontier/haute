/**
 * Zustand store for chrome / layout UI state — panel toggles, modals,
 * sync banner, and node panel width.
 *
 * Toast notifications live in useToastStore.
 * Application settings (MLflow, sources, caches) live in useSettingsStore.
 * Graph-shaped state (nodes, edges, preamble) and the dirty-tracking
 * `lastSavedSnapshot` live in useGraphStore (Wave 7E consolidation).
 *
 * `serializeSnapshot` and `selectIsDirty` are re-exported here for
 * existing import paths — the implementations live in
 * `utils/graphSnapshot.ts` and are shared with `useGraphStore`.
 */
import { create } from "zustand"

/**
 * One frame on the submodel-navigation view stack.
 *
 * `kind: "root"` is never pushed — it is the sentinel `currentView()`
 * returns when the stack is empty.  Submodel frames record the submodel
 * `name` (used for breadcrumbs) and an optional `returnTo` hint for
 * consumers that want to surface a back-target to the user.
 */
export interface ViewStackEntry {
  kind: "root" | "submodel"
  name?: string
  returnTo?: string
}

interface UIState {
  // Modals / panels
  paletteOpen: boolean
  setPaletteOpen: (open: boolean) => void
  utilityOpen: boolean
  setUtilityOpen: (open: boolean) => void
  importsOpen: boolean
  setImportsOpen: (open: boolean) => void
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

  // Hover highlight — when set, connected edges glow and unconnected nodes/edges dim
  hoveredNodeId: string | null
  setHoveredNodeId: (id: string | null) => void

  // Node search (Ctrl+K)
  nodeSearchOpen: boolean
  setNodeSearchOpen: (open: boolean | ((prev: boolean) => boolean)) => void

  // ---------------------------------------------------------------------
  // Submodel-navigation view stack (Phase 5 Wave 10C, #128).
  //
  // Previously submodel navigation threaded parent/child refs through prop
  // drilling; components that needed to know "which view am I in?" had to
  // accept a ref prop.  The stack lives on the store so any component can
  // subscribe to `viewStack` or call `currentView()` without wiring refs.
  //
  // Reference stability: `viewStack` is only re-allocated inside the three
  // action methods below, so subscribers to the array reference will only
  // re-render on push/pop/clear (not on unrelated slice updates — zustand
  // default behaviour).
  // ---------------------------------------------------------------------
  viewStack: ViewStackEntry[]
  pushView: (view: ViewStackEntry) => void
  popView: () => void
  clearViews: () => void
  /**
   * Getter-style accessor.  Returns the top frame, or a `{ kind: "root" }`
   * sentinel when the stack is empty so consumers don't need an extra
   * null check to render breadcrumbs.
   */
  currentView: () => ViewStackEntry
}

const useUIStore = create<UIState>()((set, get) => ({
  // Modals / panels
  paletteOpen: true,
  setPaletteOpen: (open) => set({ paletteOpen: open }),
  utilityOpen: false,
  setUtilityOpen: (open) => set({ utilityOpen: open, importsOpen: false, gitOpen: false }),
  importsOpen: false,
  setImportsOpen: (open) => set({ importsOpen: open, utilityOpen: false, gitOpen: false }),
  gitOpen: false,
  setGitOpen: (open) => set({ gitOpen: open, utilityOpen: false, importsOpen: false }),
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

  // Submodel-navigation view stack.  Starts empty; consumers treat an
  // empty stack as "viewing the root pipeline" via `currentView()`.
  viewStack: [],
  pushView: (view) =>
    set((s) => ({ viewStack: [...s.viewStack, view] })),
  popView: () =>
    // Empty-pop is an explicit no-op, not an error.  The UI never needs
    // to "pop below root" — breadcrumbs at root hide the back button —
    // so throwing would only add guard noise at every caller.
    set((s) =>
      s.viewStack.length === 0
        ? s
        : { viewStack: s.viewStack.slice(0, -1) },
    ),
  clearViews: () =>
    set((s) => (s.viewStack.length === 0 ? s : { viewStack: [] })),
  currentView: (): ViewStackEntry => {
    // Read via get() so the selector stays within the store's own
    // closure — using `useUIStore.getState()` here would create a
    // self-referential initializer that TypeScript flags as implicit
    // any.
    const stack = get().viewStack
    return stack.length === 0 ? { kind: "root" } : stack[stack.length - 1]
  },
}))

// ---------------------------------------------------------------------------
// Re-exports — the canonical `serializeSnapshot` / `selectIsDirty`
// helpers live in `utils/graphSnapshot.ts`.  These are kept here so
// existing import paths (`useUIStore, { serializeSnapshot, selectIsDirty }`)
// continue to resolve after the Wave 7E consolidation.
// ---------------------------------------------------------------------------

export { serializeSnapshot, selectIsDirty } from "../utils/graphSnapshot"

export default useUIStore
