/**
 * Zustand store for graph-shaped state (nodes, edges, preamble) with
 * integrated undo/redo history.
 *
 * Consolidates state previously scattered across `useGraphCanvasState`
 * (React Flow state + past/future refs), `App.tsx` (preamble + dirty refs),
 * `useUIStore.dirty`, and the implicit graph version in
 * `useNodeResultsStore`.
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
 * Dirty is maintained as a primitive boolean plus persisted-state
 * fingerprints. `markSaved()` captures the current state as the new
 * baseline; graph mutations update the boolean without App needing to
 * serialize the whole graph in a render-time selector.
 *
 * This replaces the imperative `setDirty(true)` pattern, which had a
 * class-of-bug: undoing back to the saved state left `dirty=true` because
 * the boolean and the saved reference were not kept in sync.  Derivation
 * eliminates that entirely.
 */
import { create } from "zustand"
import type { Node, Edge } from "@xyflow/react"
import { EMPTY_SNAPSHOT, serializeSnapshot, selectIsDirty } from "../utils/graphSnapshot"
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
  panelContextVersion: number
  panelContextFingerprint: string
  persistedFingerprint: string
  savedPersistedFingerprint: string | null
  dirty: boolean

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
type PanelContextNode = StructuralNode & { type?: string }
type StructuralEdge = {
  id?: string
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

function hasSameNodeStructureByReference(current: StructuralNode[], next: StructuralNode[]): boolean {
  if (current.length !== next.length) return false

  const dataById = new Map<string, Record<string, unknown>>()
  for (const node of current) {
    dataById.set(node.id, node.data)
  }

  for (const node of next) {
    if (dataById.get(node.id) !== node.data) return false
  }
  return true
}

const REACT_FLOW_NODE_UI_FIELDS = new Set([
  "selected",
  "dragging",
  "positionAbsolute",
  "measured",
  "resizing",
  "computed",
])

const REACT_FLOW_EDGE_UI_FIELDS = new Set(["selected"])

function positionsEqual(
  a: { x: number; y: number } | undefined,
  b: { x: number; y: number } | undefined,
): boolean {
  return a?.x === b?.x && a?.y === b?.y
}

function hasSamePersistedNodeState(current: Node[], next: Node[]): boolean {
  if (current.length !== next.length) return false

  for (let index = 0; index < next.length; index += 1) {
    const previous = current[index]
    const node = next[index]
    if (previous.id !== node.id) return false
    if (previous.data !== node.data) return false
    if (!positionsEqual(previous.position, node.position)) return false

    const keys = new Set([...Object.keys(previous), ...Object.keys(node)])
    for (const key of keys) {
      if (key === "data" || key === "position") continue
      if (REACT_FLOW_NODE_UI_FIELDS.has(key)) continue
      const previousValue = (previous as unknown as Record<string, unknown>)[key]
      const nextValue = (node as unknown as Record<string, unknown>)[key]
      if (!Object.is(previousValue, nextValue)) return false
    }
  }
  return true
}

function hasOnlyNodePositionChanges(current: Node[], next: Node[]): boolean {
  if (current.length !== next.length) return false

  let positionChanged = false
  for (let index = 0; index < next.length; index += 1) {
    const previous = current[index]
    const node = next[index]
    if (previous.id !== node.id) return false
    if (previous.data !== node.data) return false

    const keys = new Set([...Object.keys(previous), ...Object.keys(node)])
    for (const key of keys) {
      if (key === "position") continue
      if (REACT_FLOW_NODE_UI_FIELDS.has(key)) continue
      const previousValue = (previous as unknown as Record<string, unknown>)[key]
      const nextValue = (node as unknown as Record<string, unknown>)[key]
      if (!Object.is(previousValue, nextValue)) return false
    }

    if (!positionsEqual(previous.position, node.position)) {
      positionChanged = true
    }
  }
  return positionChanged
}

function edgeStructuralKey(edge: StructuralEdge): string {
  return `${edge.source}:${edge.sourceHandle ?? ""}->${edge.target}:${edge.targetHandle ?? ""}`
}

function hasSameEdgeStructure(current: StructuralEdge[], next: StructuralEdge[]): boolean {
  if (current.length !== next.length) return false

  const currentParts = current.map(edgeStructuralKey).sort()
  const nextParts = next.map(edgeStructuralKey).sort()
  return currentParts.every((part, index) => part === nextParts[index])
}

function hasSamePersistedEdgeState(current: Edge[], next: Edge[]): boolean {
  if (current.length !== next.length) return false

  for (let index = 0; index < next.length; index += 1) {
    const previous = current[index]
    const edge = next[index]
    if (previous.id !== edge.id) return false
    const keys = new Set([...Object.keys(previous), ...Object.keys(edge)])
    for (const key of keys) {
      if (REACT_FLOW_EDGE_UI_FIELDS.has(key)) continue
      const previousValue = (previous as unknown as Record<string, unknown>)[key]
      const nextValue = (edge as unknown as Record<string, unknown>)[key]
      if (!Object.is(previousValue, nextValue)) return false
    }
  }
  return true
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

const PANEL_CONTEXT_NODE_DATA_KEYS = [
  "label",
  "description",
  "nodeType",
  "config",
  "code",
  "func_name",
  "_columns",
  "_availableColumns",
  "_schemaWarnings",
] as const

function panelContextNodeDataHash(data: Record<string, unknown>): string {
  const parts: string[] = []
  for (const key of PANEL_CONTEXT_NODE_DATA_KEYS) {
    const value = data[key]
    parts.push(value === undefined ? "" : JSON.stringify(value) ?? String(value))
  }
  return parts.join("\u0001")
}

export function computePanelContextFingerprint(
  nodes: PanelContextNode[],
  edges: StructuralEdge[],
): string {
  const nodeParts = nodes
    .map((node) => {
      const dataHash = panelContextNodeDataHash(
        nodeData(node) as unknown as Record<string, unknown>,
      )
      return `${node.id}:${node.type ?? ""}:${dataHash}`
    })
    .sort()
  const edgeParts = edges
    .map((edge) =>
      `${edge.id ?? ""}:${edge.source}:${edge.sourceHandle ?? ""}->${edge.target}:${edge.targetHandle ?? ""}`,
    )
    .sort()
  return `nodes:${nodeParts.join("|")}||edges:${edgeParts.join("|")}`
}

function computePersistedFingerprint(nodes: Node[], edges: Edge[], preamble: string): string {
  return serializeSnapshot({ nodes, edges, preamble })
}

function computeDirty(
  lastSavedSnapshot: GraphSnapshot | null,
  savedPersistedFingerprint: string | null,
  persistedFingerprint: string,
): boolean {
  if (lastSavedSnapshot === null) {
    return selectIsDirty({ lastSavedSnapshot: null }, persistedFingerprint)
  }
  return persistedFingerprint !== savedPersistedFingerprint
}

function hasSameSavedNodePositions(saved: GraphSnapshot, nodes: Node[]): boolean {
  if (saved.nodes.length !== nodes.length) return false

  for (let index = 0; index < nodes.length; index += 1) {
    const savedNode = saved.nodes[index]
    const node = nodes[index]
    if (savedNode.id !== node.id) return false
    if (!positionsEqual(savedNode.position, node.position)) return false
  }
  return true
}

function computeDirtyForPositionOnlyNodes(
  state: Pick<
    GraphStore,
    "lastSavedSnapshot" | "savedPersistedFingerprint" | "persistedFingerprint"
  >,
  nodes: Node[],
): boolean {
  if (state.lastSavedSnapshot === null) {
    return nodes.length > 0
  }
  if (state.persistedFingerprint !== state.savedPersistedFingerprint) {
    return true
  }
  return !hasSameSavedNodePositions(state.lastSavedSnapshot, nodes)
}

function computePanelContextPatch(
  state: Pick<GraphStore, "panelContextFingerprint" | "panelContextVersion">,
  nodes: Node[],
  edges: Edge[],
): Partial<Pick<GraphStore, "panelContextFingerprint" | "panelContextVersion">> {
  const nextFingerprint = computePanelContextFingerprint(nodes, edges)
  return nextFingerprint === state.panelContextFingerprint
    ? {}
    : {
        panelContextFingerprint: nextFingerprint,
        panelContextVersion: state.panelContextVersion + 1,
      }
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
    panelContextVersion: 0,
    panelContextFingerprint: computePanelContextFingerprint([], []),
    persistedFingerprint: computePersistedFingerprint([], [], ""),
    savedPersistedFingerprint: null,
    dirty: false,

    // ── History-aware actions ────────────────────────────────────────────

    setNodes: (updater) => {
      set((state) => ({
        undoStack: pushSnapshotInternal(),
        redoStack: [],
        ...(() => {
          const nodes = applyUpdater(state.nodes, updater)
          const nextPersistedFingerprint = computePersistedFingerprint(nodes, state.edges, state.preamble)
          const nextFingerprint = computeStructuralFingerprint(nodes, state.edges, state.preamble)
          return {
            nodes,
            ...computePanelContextPatch(state, nodes, state.edges),
            persistedFingerprint: nextPersistedFingerprint,
            dirty: computeDirty(
              state.lastSavedSnapshot,
              state.savedPersistedFingerprint,
              nextPersistedFingerprint,
            ),
            ...(nextFingerprint === state.structuralFingerprint
              ? {}
              : {
                  structuralFingerprint: nextFingerprint,
                  structuralVersion: state.structuralVersion + 1,
                }),
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
          const nextPersistedFingerprint = computePersistedFingerprint(state.nodes, edges, state.preamble)
          const nextFingerprint = computeStructuralFingerprint(state.nodes, edges, state.preamble)
          return {
            edges,
            ...computePanelContextPatch(state, state.nodes, edges),
            persistedFingerprint: nextPersistedFingerprint,
            dirty: computeDirty(
              state.lastSavedSnapshot,
              state.savedPersistedFingerprint,
              nextPersistedFingerprint,
            ),
            ...(nextFingerprint === state.structuralFingerprint
              ? {}
              : {
                  structuralFingerprint: nextFingerprint,
                  structuralVersion: state.structuralVersion + 1,
                }),
          }
        })(),
      }))
    },

    setPreamble: (value) => {
      set((state) => {
        const nextPersistedFingerprint = computePersistedFingerprint(state.nodes, state.edges, value)
        const nextFingerprint = computeStructuralFingerprint(state.nodes, state.edges, value)
        return {
          undoStack: pushSnapshotInternal(),
          redoStack: [],
          preamble: value,
          persistedFingerprint: nextPersistedFingerprint,
          dirty: computeDirty(
            state.lastSavedSnapshot,
            state.savedPersistedFingerprint,
            nextPersistedFingerprint,
          ),
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
        const samePersisted = hasSamePersistedNodeState(state.nodes, nodes)
        const sameStructural = hasSameNodeStructureByReference(state.nodes, nodes)
        if (samePersisted && sameStructural) {
          return { nodes }
        }

        if (sameStructural && hasOnlyNodePositionChanges(state.nodes, nodes)) {
          return {
            nodes,
            dirty: computeDirtyForPositionOnlyNodes(state, nodes),
          }
        }

        const persistedPatch = samePersisted
          ? {}
          : (() => {
              const nextPersistedFingerprint = computePersistedFingerprint(
                nodes,
                state.edges,
                state.preamble,
              )
              return {
                persistedFingerprint: nextPersistedFingerprint,
                dirty: computeDirty(
                  state.lastSavedSnapshot,
                  state.savedPersistedFingerprint,
                  nextPersistedFingerprint,
                ),
              }
            })()
        const panelContextPatch = computePanelContextPatch(state, nodes, state.edges)
        if (sameStructural) {
          return { nodes, ...persistedPatch, ...panelContextPatch }
        }

        const nextFingerprint = computeStructuralFingerprint(nodes, state.edges, state.preamble)
        return {
          nodes,
          ...persistedPatch,
          ...panelContextPatch,
          ...(nextFingerprint === state.structuralFingerprint
            ? {}
            : {
                structuralFingerprint: nextFingerprint,
                structuralVersion: state.structuralVersion + 1,
              }),
        }
      })
    },

    setEdgesRaw: (updater) => {
      set((state) => {
        const edges = applyUpdater(state.edges, updater)
        const samePersisted = hasSamePersistedEdgeState(state.edges, edges)
        const sameStructural = hasSameEdgeStructure(state.edges, edges)
        if (samePersisted && sameStructural) {
          return { edges }
        }

        const panelContextPatch = computePanelContextPatch(state, state.nodes, edges)
        const persistedPatch = samePersisted
          ? {}
          : (() => {
              const nextPersistedFingerprint = computePersistedFingerprint(
                state.nodes,
                edges,
                state.preamble,
              )
              return {
                persistedFingerprint: nextPersistedFingerprint,
                dirty: computeDirty(
                  state.lastSavedSnapshot,
                  state.savedPersistedFingerprint,
                  nextPersistedFingerprint,
                ),
              }
            })()
        if (sameStructural) {
          return { edges, ...persistedPatch, ...panelContextPatch }
        }

        const nextFingerprint = computeStructuralFingerprint(state.nodes, edges, state.preamble)
        return {
          edges,
          ...persistedPatch,
          ...panelContextPatch,
          ...(nextFingerprint === state.structuralFingerprint
            ? {}
            : {
                structuralFingerprint: nextFingerprint,
                structuralVersion: state.structuralVersion + 1,
              }),
        }
      })
    },

    setPreambleRaw: (value) => {
      set((state) => {
        const nextPersistedFingerprint = computePersistedFingerprint(state.nodes, state.edges, value)
        const nextFingerprint = computeStructuralFingerprint(state.nodes, state.edges, value)
        return {
          preamble: value,
          persistedFingerprint: nextPersistedFingerprint,
          dirty: computeDirty(
            state.lastSavedSnapshot,
            state.savedPersistedFingerprint,
            nextPersistedFingerprint,
          ),
          ...(nextFingerprint === state.structuralFingerprint
            ? {}
            : {
                structuralFingerprint: nextFingerprint,
                structuralVersion: state.structuralVersion + 1,
              }),
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
      const nextPanelContextFingerprint = computePanelContextFingerprint(prev.nodes, prev.edges)
      const nextPersistedFingerprint = computePersistedFingerprint(prev.nodes, prev.edges, prev.preamble)
      set((state) => ({
        undoStack: newUndo,
        redoStack: [...state.redoStack, captureSnapshot(state)],
        nodes: prev.nodes,
        edges: prev.edges,
        preamble: prev.preamble,
        persistedFingerprint: nextPersistedFingerprint,
        dirty: computeDirty(
          state.lastSavedSnapshot,
          state.savedPersistedFingerprint,
          nextPersistedFingerprint,
        ),
        structuralFingerprint: nextFingerprint,
        structuralVersion: nextFingerprint === state.structuralFingerprint
          ? state.structuralVersion
          : state.structuralVersion + 1,
        panelContextFingerprint: nextPanelContextFingerprint,
        panelContextVersion: nextPanelContextFingerprint === state.panelContextFingerprint
          ? state.panelContextVersion
          : state.panelContextVersion + 1,
      }))
    },

    redo: () => {
      const { redoStack } = get()
      if (redoStack.length === 0) return
      const next = redoStack[redoStack.length - 1]
      const newRedo = redoStack.slice(0, -1)
      const nextFingerprint = computeStructuralFingerprint(next.nodes, next.edges, next.preamble)
      const nextPanelContextFingerprint = computePanelContextFingerprint(next.nodes, next.edges)
      const nextPersistedFingerprint = computePersistedFingerprint(next.nodes, next.edges, next.preamble)
      set((state) => ({
        redoStack: newRedo,
        undoStack: [...state.undoStack, captureSnapshot(state)],
        nodes: next.nodes,
        edges: next.edges,
        preamble: next.preamble,
        persistedFingerprint: nextPersistedFingerprint,
        dirty: computeDirty(
          state.lastSavedSnapshot,
          state.savedPersistedFingerprint,
          nextPersistedFingerprint,
        ),
        structuralFingerprint: nextFingerprint,
        structuralVersion: nextFingerprint === state.structuralFingerprint
          ? state.structuralVersion
          : state.structuralVersion + 1,
        panelContextFingerprint: nextPanelContextFingerprint,
        panelContextVersion: nextPanelContextFingerprint === state.panelContextFingerprint
          ? state.panelContextVersion
          : state.panelContextVersion + 1,
      }))
    },

    // ── Dirty tracking ──────────────────────────────────────────────────

    markSaved: () => {
      set((state) => {
        const persistedFingerprint = computePersistedFingerprint(
          state.nodes,
          state.edges,
          state.preamble,
        )
        return {
          lastSavedSnapshot: captureSnapshot(state),
          persistedFingerprint,
          savedPersistedFingerprint: persistedFingerprint,
          dirty: false,
        }
      })
    },

    // ── Pure selectors ──────────────────────────────────────────────────

    isDirty: () => {
      const { lastSavedSnapshot, nodes, edges, preamble } = get()
      const current = serializeSnapshot({ nodes, edges, preamble })
      if (lastSavedSnapshot === null) {
        return current !== EMPTY_SNAPSHOT
      }
      return current !== serializeSnapshot(lastSavedSnapshot)
    },

    canUndo: () => get().undoStack.length > 0,

    canRedo: () => get().redoStack.length > 0,
  }
})

export default useGraphStore
