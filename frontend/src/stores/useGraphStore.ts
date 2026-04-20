/**
 * Zustand store for graph-shaped state (nodes, edges, preamble) with
 * integrated undo/redo history.
 *
 * Phase 3 Wave 7 package 7E — consolidation of state previously scattered
 * across `useUndoRedo` (React Flow state + past/future refs), `App.tsx`
 * (preamble + dirty refs), `useUIStore.dirty`, and the implicit graph
 * version in `useNodeResultsStore`.
 *
 * ── Selector contract ─────────────────────────────────────────────────
 *
 * Consumers MUST subscribe via selectors so unrelated state changes do
 * not trigger re-renders.  The selector-isolation test block in
 * `useGraphStore.consolidation.test.ts` asserts this contract:
 *
 *   const nodes = useGraphStore((s) => s.nodes)   // OK
 *   const store = useGraphStore()                 // BAD — re-renders on any change
 *
 * ── History model ─────────────────────────────────────────────────────
 *
 * Actions split into two tiers:
 *
 *   - History-aware: `setNodes`, `setEdges`, `setPreamble`, and manual
 *     `pushSnapshot`.  Each captures the pre-mutation `{nodes, edges,
 *     preamble}` onto `undoStack` and clears `redoStack`.
 *
 *   - Raw: `setNodesRaw`, `setEdgesRaw`, `setPreambleRaw`.  These skip the
 *     history push — used for mid-drag position updates (React Flow's
 *     `onNodesChange` replays many `position` events per frame; snapshotting
 *     each would fill undo with ~60 entries per drag), for WebSocket sync,
 *     and for pipeline load.
 *
 * The 100-entry MAX_HISTORY cap prevents unbounded growth on long editing
 * sessions.
 *
 * ── Dirty derivation ──────────────────────────────────────────────────
 *
 * `isDirty()` is a pure selector: compare the current `(nodes, edges,
 * preamble)` against `lastSavedSnapshot`.  `null` last-saved means "never
 * saved in this session", which is treated as clean — the fresh-load baseline
 * matches disk and shouldn't light up the unsaved indicator.  `markSaved()`
 * captures the current state as the new baseline.
 *
 * This replaces the imperative `setDirty(true)` pattern, which had a
 * class-of-bug: undoing back to the saved state left `dirty=true` because
 * the boolean and the saved reference were not kept in sync.  Derivation
 * eliminates that entirely.
 */
import { create } from "zustand"
import type { Node, Edge } from "@xyflow/react"

// ─── Types ───────────────────────────────────────────────────────────────

export interface GraphSnapshot {
  nodes: Node[]
  edges: Edge[]
  preamble: string
}

export interface GraphStore {
  // State
  nodes: Node[]
  edges: Edge[]
  preamble: string
  lastSavedSnapshot: GraphSnapshot | null
  undoStack: GraphSnapshot[]
  redoStack: GraphSnapshot[]

  // History-aware actions
  setNodes: (updater: Node[] | ((nds: Node[]) => Node[])) => void
  setEdges: (updater: Edge[] | ((eds: Edge[]) => Edge[])) => void
  setPreamble: (value: string) => void

  // Raw actions (skip history push — for mid-drag, WS sync, load)
  setNodesRaw: (nodes: Node[] | ((nds: Node[]) => Node[])) => void
  setEdgesRaw: (edges: Edge[] | ((eds: Edge[]) => Edge[])) => void
  setPreambleRaw: (value: string) => void

  // Explicit history operations
  pushSnapshot: () => void
  undo: () => void
  redo: () => void

  // Dirty tracking
  markSaved: () => void

  // Pure selectors — callable from getState()
  isDirty: () => boolean
  canUndo: () => boolean
  canRedo: () => boolean
}

// ─── Constants ───────────────────────────────────────────────────────────

export const MAX_HISTORY = 100

// ─── Helpers ─────────────────────────────────────────────────────────────

/**
 * Snapshot of the current graph-shaped state.  Shallow-clones nodes and
 * edges so in-place mutations by React Flow (e.g. position updates during
 * drag) don't retroactively corrupt historical entries.
 */
function captureSnapshot(state: Pick<GraphStore, "nodes" | "edges" | "preamble">): GraphSnapshot {
  return {
    nodes: state.nodes.map((n) => ({ ...n, data: { ...n.data } })),
    edges: state.edges.map((e) => ({ ...e })),
    preamble: state.preamble,
  }
}

/**
 * Apply an updater that may be a direct replacement value or a functional
 * updater (matches React's useState / React Flow's useNodesState signature).
 */
function applyUpdater<T>(current: T, updater: T | ((prev: T) => T)): T {
  return typeof updater === "function" ? (updater as (prev: T) => T)(current) : updater
}

/**
 * Canonical string form of a snapshot used for dirty comparison.
 *
 * Node/edge objects are shallow-cloned and JSON-stringified.  Keys end up in
 * insertion order; since both `captureSnapshot` and the live state objects
 * originate from the same shape, equal graphs produce equal strings.
 */
function serializeSnapshot(s: Pick<GraphSnapshot, "nodes" | "edges" | "preamble">): string {
  return JSON.stringify({ nodes: s.nodes, edges: s.edges, preamble: s.preamble })
}

// ─── Store ───────────────────────────────────────────────────────────────

const useGraphStore = create<GraphStore>()((set, get) => {
  /**
   * Push the current pre-mutation state onto undoStack, clear redoStack,
   * and respect MAX_HISTORY by dropping the oldest entry when overflowing.
   */
  function pushSnapshotInternal(): GraphSnapshot[] {
    const { undoStack } = get()
    const snap = captureSnapshot(get())
    const next =
      undoStack.length >= MAX_HISTORY
        ? [...undoStack.slice(undoStack.length - MAX_HISTORY + 1), snap]
        : [...undoStack, snap]
    return next
  }

  return {
    nodes: [],
    edges: [],
    preamble: "",
    lastSavedSnapshot: null,
    undoStack: [],
    redoStack: [],

    // ── History-aware actions ────────────────────────────────────────────

    setNodes: (updater) => {
      set((state) => ({
        undoStack: pushSnapshotInternal(),
        redoStack: [],
        nodes: applyUpdater(state.nodes, updater),
      }))
    },

    setEdges: (updater) => {
      set((state) => ({
        undoStack: pushSnapshotInternal(),
        redoStack: [],
        edges: applyUpdater(state.edges, updater),
      }))
    },

    setPreamble: (value) => {
      set(() => ({
        undoStack: pushSnapshotInternal(),
        redoStack: [],
        preamble: value,
      }))
    },

    // ── Raw actions ─────────────────────────────────────────────────────

    setNodesRaw: (updater) => {
      set((state) => ({ nodes: applyUpdater(state.nodes, updater) }))
    },

    setEdgesRaw: (updater) => {
      set((state) => ({ edges: applyUpdater(state.edges, updater) }))
    },

    setPreambleRaw: (value) => {
      set(() => ({ preamble: value }))
    },

    // ── Explicit history operations ─────────────────────────────────────

    pushSnapshot: () => {
      set(() => ({ undoStack: pushSnapshotInternal(), redoStack: [] }))
    },

    undo: () => {
      const { undoStack } = get()
      if (undoStack.length === 0) return
      const prev = undoStack[undoStack.length - 1]
      const newUndo = undoStack.slice(0, -1)
      set((state) => ({
        undoStack: newUndo,
        redoStack: [...state.redoStack, captureSnapshot(state)],
        nodes: prev.nodes,
        edges: prev.edges,
        preamble: prev.preamble,
      }))
    },

    redo: () => {
      const { redoStack } = get()
      if (redoStack.length === 0) return
      const next = redoStack[redoStack.length - 1]
      const newRedo = redoStack.slice(0, -1)
      set((state) => ({
        redoStack: newRedo,
        undoStack: [...state.undoStack, captureSnapshot(state)],
        nodes: next.nodes,
        edges: next.edges,
        preamble: next.preamble,
      }))
    },

    // ── Dirty tracking ──────────────────────────────────────────────────

    markSaved: () => {
      set((state) => ({ lastSavedSnapshot: captureSnapshot(state) }))
    },

    // ── Pure selectors ──────────────────────────────────────────────────

    isDirty: () => {
      const { lastSavedSnapshot, nodes, edges, preamble } = get()
      if (lastSavedSnapshot === null) return false
      return serializeSnapshot({ nodes, edges, preamble }) !== serializeSnapshot(lastSavedSnapshot)
    },

    canUndo: () => get().undoStack.length > 0,

    canRedo: () => get().redoStack.length > 0,
  }
})

// ─── Public helpers ──────────────────────────────────────────────────────

export { serializeSnapshot }

export default useGraphStore
