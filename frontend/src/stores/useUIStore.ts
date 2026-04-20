/**
 * Zustand store for chrome / layout UI state — panel toggles, modals,
 * sync banner, last-saved snapshot reference, and node panel width.
 *
 * Toast notifications live in useToastStore.
 * Application settings (MLflow, sources, caches) live in useSettingsStore.
 *
 * ── Derived dirty flag (item #99) ──────────────────────────────────────
 *
 * The store holds a single `lastSavedSnapshot: string | null`, not a
 * boolean `dirty` field. The GUI's "unsaved changes" indicator is
 * derived at the call-site via {@link selectIsDirty}, which compares a
 * serialized snapshot of the current graph to the stored last-saved
 * string.
 *
 * Rationale: maintaining both a `dirty` boolean AND a lastSavedRef is a
 * class-of-bugs (keep-them-in-sync issues where undo-to-saved-state
 * still shows the amber dot). Deriving `dirty` from a pure comparison
 * eliminates the category entirely.
 */
import { create } from "zustand"
import type { Node, Edge } from "@xyflow/react"

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

  // Last-saved snapshot (derived-dirty source of truth)
  //
  // `null` => never saved this session (fresh workspace).
  // A string => canonical JSON of {nodes, edges, preamble} captured at
  // the moment of save or load-from-disk.
  lastSavedSnapshot: string | null
  markSaved: (snapshot: string) => void

  // Node panel width (persisted across selection changes)
  nodePanelWidth: number
  setNodePanelWidth: (width: number) => void

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

  // Last-saved snapshot
  lastSavedSnapshot: null,
  markSaved: (snapshot) => set({ lastSavedSnapshot: snapshot }),

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
}))

// ---------------------------------------------------------------------------
// Serialization + derived-dirty selector (module-level exports)
//
// The `serializeSnapshot` and `selectIsDirty` helpers below are plain
// pure functions — NOT store actions. They operate on the raw inputs
// supplied by the caller, which keeps graph state (nodes, edges,
// preamble) outside the store (it lives in ReactFlow + local useState)
// while still giving every consumer a single canonical way to compare.
// ---------------------------------------------------------------------------

/**
 * Deterministic stringifier — same object shape and contents always
 * produce the same string regardless of JSON.stringify key-order
 * quirks.
 *
 * Walks the value recursively and rewrites every encountered object so
 * its keys appear in sorted order. Arrays retain their original order
 * (intentional: the order of nodes/edges in the graph is user-
 * meaningful). Non-plain values (null, numbers, strings, booleans) are
 * returned as-is.
 *
 * The recursion is safe for the graph shapes we serialize — they are
 * JSON-like tree structures with no cycles. If a consumer ever passes a
 * cyclic object here, JSON.stringify itself would throw, which is the
 * correct loud-failure behavior.
 */
function canonicalize(value: unknown): unknown {
  if (value === null || typeof value !== "object") return value
  if (Array.isArray(value)) return value.map(canonicalize)
  const entries = Object.entries(value as Record<string, unknown>)
  entries.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
  const out: Record<string, unknown> = {}
  for (const [k, v] of entries) {
    out[k] = canonicalize(v)
  }
  return out
}

/**
 * Canonical serialization of a graph snapshot used for dirty-derivation.
 *
 * Scope: {nodes, edges, preamble} — the user-editable surface of a
 * pipeline. Extra fields carried by the backend (preserved_blocks,
 * submodels, etc.) are out of scope because they round-trip verbatim
 * and are not user-editable via the GUI; changing them should NOT flag
 * the graph as dirty.
 *
 * Presentation-only fields injected by React Flow (`selected`,
 * `dragging`, `positionAbsolute`, `measured`) are stripped so that UI
 * state like "which node is clicked" does not fake a dirty graph.
 * These fields exist because React Flow manages selection/drag state in
 * the same `nodes` array that holds the graph, but they are NOT part of
 * what the backend writes to disk.
 *
 * Determinism: equal inputs produce equal strings even if the caller
 * constructed the object with keys in a different order.
 */

// Fields on a Node that React Flow manages for presentation only — not
// part of the on-disk pipeline. Stripped before serialization so that
// selecting a node doesn't flip the unsaved-changes indicator.
const REACT_FLOW_NODE_UI_FIELDS = [
  "selected",
  "dragging",
  "positionAbsolute",
  "measured",
  "resizing",
  "computed",
] as const

// Same principle for edges (though edges have fewer such fields).
const REACT_FLOW_EDGE_UI_FIELDS = ["selected"] as const

function stripNodeUiFields(n: Node): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(n as unknown as Record<string, unknown>)) {
    if ((REACT_FLOW_NODE_UI_FIELDS as readonly string[]).includes(k)) continue
    out[k] = v
  }
  return out
}

function stripEdgeUiFields(e: Edge): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(e as unknown as Record<string, unknown>)) {
    if ((REACT_FLOW_EDGE_UI_FIELDS as readonly string[]).includes(k)) continue
    out[k] = v
  }
  return out
}

export function serializeSnapshot(input: {
  nodes: readonly Node[]
  edges: readonly Edge[]
  preamble: string
}): string {
  return JSON.stringify(
    canonicalize({
      nodes: input.nodes.map(stripNodeUiFields),
      edges: input.edges.map(stripEdgeUiFields),
      preamble: input.preamble,
    }),
  )
}

/**
 * Pure selector: returns whether the current graph differs from the
 * last-saved snapshot. Intended to be called from render code with a
 * memoized `currentSnapshot` (see App.tsx — `useMemo` on
 * [nodes, edges, preamble]).
 *
 * Semantics:
 *   - `lastSavedSnapshot === null` (never saved) + empty current graph
 *     => NOT dirty. Matches the old effect's behaviour: a fresh
 *     workspace is clean until the user either edits or saves.
 *   - `lastSavedSnapshot === null` + non-empty current graph => DIRTY.
 *     The user built something without saving.
 *   - `lastSavedSnapshot !== null`: string-compare against current.
 */
export function selectIsDirty(
  state: Pick<UIState, "lastSavedSnapshot">,
  currentSnapshot: string,
): boolean {
  if (state.lastSavedSnapshot === null) {
    // Empty-initial-workspace sentinel: equal-to-empty-snapshot means clean.
    return currentSnapshot !== EMPTY_SNAPSHOT
  }
  return currentSnapshot !== state.lastSavedSnapshot
}

/**
 * Precomputed "empty workspace" string so selectIsDirty's
 * never-saved-yet branch is a single reference compare on the common
 * path (app launch before the first save arrives).
 */
const EMPTY_SNAPSHOT = serializeSnapshot({ nodes: [], edges: [], preamble: "" })

export default useUIStore
