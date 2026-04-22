/**
 * Zustand store for graph-shaped state (nodes, edges, preamble) with
 * integrated undo/redo history.
 *
 * Phase 3 Wave 7 package 7E — consolidation of state previously scattered
 * across `useGraphCanvasState` (React Flow state + past/future refs), `App.tsx`
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
import { serializeSnapshot, EMPTY_SNAPSHOT } from "../utils/graphSnapshot"
import { shallowNodeDataHash } from "../utils/shallowNodeHash"
import { nodeData } from "../types/node"

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
  structuralVersion: number
  structuralFingerprint: string

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

type StructuralNode = { id: string; data: Record<string, unknown> }
type StructuralEdge = {
  source: string
  target: string
  sourceHandle?: string | null
  targetHandle?: string | null
}

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

export function computeStructuralFingerprint(
  nodes: StructuralNode[],
  edges: StructuralEdge[],
  preamble = "",
): string {
  const nodeParts = nodes
    .map((n) => `${n.id}:${shallowNodeDataHash(nodeData(n) as unknown as Record<string, unknown>)}`)
    .sort()
  const edgeParts = edges
    .map((e) => `${e.source}:${e.sourceHandle ?? ""}->${e.target}:${e.targetHandle ?? ""}`)
    .sort()
  return `nodes:${nodeParts.join("|")}||edges:${edgeParts.join("|")}||preamble:${JSON.stringify(preamble)}`
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
    structuralVersion: 0,
    structuralFingerprint: computeStructuralFingerprint([], [], ""),

    // ── History-aware actions ────────────────────────────────────────────

    setNodes: (updater) => {
      set((state) => ({
        undoStack: pushSnapshotInternal(),
        redoStack: [],
        ...(() => {
          const nodes = applyUpdater(state.nodes, updater)
          const nextFingerprint = computeStructuralFingerprint(nodes, state.edges, state.preamble)
          return nextFingerprint === state.structuralFingerprint
            ? { nodes }
            : {
                nodes,
                structuralFingerprint: nextFingerprint,
                structuralVersion: state.structuralVersion + 1,
              }
        })(),
      }))
    },

    setEdges: (updater) => {
      set((state) => ({
        undoStack: pushSnapshotInternal(),
        redoStack: [],
        ...(() => {
          const edges = applyUpdater(state.edges, updater)
          const nextFingerprint = computeStructuralFingerprint(state.nodes, edges, state.preamble)
          return nextFingerprint === state.structuralFingerprint
            ? { edges }
            : {
                edges,
                structuralFingerprint: nextFingerprint,
                structuralVersion: state.structuralVersion + 1,
              }
        })(),
      }))
    },

    setPreamble: (value) => {
      set((state) => {
        const nextFingerprint = computeStructuralFingerprint(state.nodes, state.edges, value)
        return {
          undoStack: pushSnapshotInternal(),
          redoStack: [],
          preamble: value,
          ...(nextFingerprint === state.structuralFingerprint
            ? {}
            : {
                structuralFingerprint: nextFingerprint,
                structuralVersion: state.structuralVersion + 1,
              }),
        }
      })
    },

    // ── Raw actions ─────────────────────────────────────────────────────

    setNodesRaw: (updater) => {
      set((state) => {
        const nodes = applyUpdater(state.nodes, updater)
        const nextFingerprint = computeStructuralFingerprint(nodes, state.edges, state.preamble)
        return nextFingerprint === state.structuralFingerprint
          ? { nodes }
          : {
              nodes,
              structuralFingerprint: nextFingerprint,
              structuralVersion: state.structuralVersion + 1,
            }
      })
    },

    setEdgesRaw: (updater) => {
      set((state) => {
        const edges = applyUpdater(state.edges, updater)
        const nextFingerprint = computeStructuralFingerprint(state.nodes, edges, state.preamble)
        return nextFingerprint === state.structuralFingerprint
          ? { edges }
          : {
              edges,
              structuralFingerprint: nextFingerprint,
              structuralVersion: state.structuralVersion + 1,
            }
      })
    },

    setPreambleRaw: (value) => {
      set((state) => {
        const nextFingerprint = computeStructuralFingerprint(state.nodes, state.edges, value)
        return nextFingerprint === state.structuralFingerprint
          ? { preamble: value }
          : {
              preamble: value,
              structuralFingerprint: nextFingerprint,
              structuralVersion: state.structuralVersion + 1,
            }
      })
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
      const nextFingerprint = computeStructuralFingerprint(prev.nodes, prev.edges, prev.preamble)
      set((state) => ({
        undoStack: newUndo,
        redoStack: [...state.redoStack, captureSnapshot(state)],
        nodes: prev.nodes,
        edges: prev.edges,
        preamble: prev.preamble,
        structuralFingerprint: nextFingerprint,
        structuralVersion: nextFingerprint === state.structuralFingerprint
          ? state.structuralVersion
          : state.structuralVersion + 1,
      }))
    },

    redo: () => {
      const { redoStack } = get()
      if (redoStack.length === 0) return
      const next = redoStack[redoStack.length - 1]
      const newRedo = redoStack.slice(0, -1)
      const nextFingerprint = computeStructuralFingerprint(next.nodes, next.edges, next.preamble)
      set((state) => ({
        redoStack: newRedo,
        undoStack: [...state.undoStack, captureSnapshot(state)],
        nodes: next.nodes,
        edges: next.edges,
        preamble: next.preamble,
        structuralFingerprint: nextFingerprint,
        structuralVersion: nextFingerprint === state.structuralFingerprint
          ? state.structuralVersion
          : state.structuralVersion + 1,
      }))
    },

    // ── Dirty tracking ──────────────────────────────────────────────────

    markSaved: () => {
      set((state) => ({ lastSavedSnapshot: captureSnapshot(state) }))
    },

    // ── Pure selectors ──────────────────────────────────────────────────

    isDirty: () => {
      const { lastSavedSnapshot, nodes, edges, preamble } = get()
      const current = serializeSnapshot({ nodes, edges, preamble })
      if (lastSavedSnapshot === null) {
        // Fresh workspace (never saved) — match selectIsDirty's sentinel:
        // empty current => clean; non-empty => dirty (user built something
        // without saving).
        return current !== EMPTY_SNAPSHOT
      }
      return current !== serializeSnapshot(lastSavedSnapshot)
    },

    canUndo: () => get().undoStack.length > 0,

    canRedo: () => get().redoStack.length > 0,
  }
})

export default useGraphStore
