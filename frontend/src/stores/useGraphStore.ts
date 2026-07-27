/**
 * Zustand store for graph-shaped state (nodes, edges, preamble, submodels) with
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
 *   - History-aware: `setNodes`, `setEdges`, `setPreamble`,
 *     `setNodesAndEdgesAndSubmodels`, and manual `pushSnapshot`. Each captures
 *     the pre-mutation `{nodes, edges, preamble, submodels}` onto `undoStack`
 *     and clears `redoStack`.
 *
 *   - Raw: `setNodesRaw`, `setEdgesRaw`, `setSubmodelsRaw`, `setPreambleRaw`.
 *     history push — used for mid-drag position updates (React Flow's
 *     `onNodesChange` replays many `position` events per frame; snapshotting
 *     each would fill undo with ~60 entries per drag), for WebSocket sync,
 *     and for guarded external synchronisation.
 *
 * The 100-entry MAX_HISTORY cap prevents unbounded growth on long editing
 * sessions.
 *
 * ── Dirty derivation ──────────────────────────────────────────────────
 *
 * Dirty is maintained as a primitive boolean plus persisted-state
 * fingerprints. `markSaved()` captures a saved graph snapshot as the new
 * baseline; graph mutations update the boolean without App needing to
 * serialize the whole graph in a render-time selector.
 *
 * This replaces the imperative `setDirty(true)` pattern, which had a
 * class-of-bug: undoing back to the saved state left `dirty=true` because
 * the boolean and the saved reference were not kept in sync.  Derivation
 * eliminates that entirely.
 */
import { create } from "zustand"
import type { Node } from "@xyflow/react"
import {
  EMPTY_SNAPSHOT,
  serializeSnapshot,
  selectIsDirty,
  cloneGraphSnapshot,
} from "../utils/graphSnapshot"
import { shallowNodeDataHash } from "../utils/shallowNodeHash"
import { nodeData } from "../types/node"
import type { PipelineEdge } from "../types/node"

// ─── Types ───────────────────────────────────────────────────────────────

export interface GraphSnapshot {
  nodes: Node[]
  edges: PipelineEdge[]
  preamble: string
  submodels: Record<string, unknown>
}

/** A version-control operation (branch switch / archive / delete) riding the
 *  same history stacks as graph snapshots, so toolbar Undo/Redo reverses it
 *  in order. Entries carry their own inverse as async closures (the closures
 *  own API calls, toasts and git-state refresh); the store only sequences
 *  them and blocks further history motion while one is in flight. Ordering
 *  makes the graph snapshots below a switch valid again once the switch
 *  itself has been undone. */
export interface VcHistoryEntry {
  kind: "vc"
  label: string
  undo: () => Promise<void>
  redo: () => Promise<void>
}

export type HistoryEntry = GraphSnapshot | VcHistoryEntry

export const isVcEntry = (e: HistoryEntry): e is VcHistoryEntry =>
  "kind" in e && e.kind === "vc"

export interface GraphStore {
  // State
  nodes: Node[]
  edges: PipelineEdge[]
  preamble: string
  /** Persisted submodel graphs; included in history and dirty fingerprints. */
  submodels: Record<string, unknown>
  lastSavedSnapshot: GraphSnapshot | null
  undoStack: HistoryEntry[]
  redoStack: HistoryEntry[]
  /** True while a VC entry's async undo/redo runs — history is locked. */
  vcBusy: boolean
  structuralVersion: number
  structuralFingerprint: string
  panelContextVersion: number
  panelContextFingerprint: string
  persistedFingerprint: string
  savedPersistedFingerprint: string | null
  dirty: boolean

  // History-aware actions
  setNodes: (updater: Node[] | ((nds: Node[]) => Node[])) => void
  setEdges: (updater: PipelineEdge[] | ((eds: PipelineEdge[]) => PipelineEdge[])) => void
  /**
   * Mutate nodes AND edges in a single history-aware step: one snapshot, one
   * `set()`. Use this for any gesture that touches both (delete, paste, cut)
   * so it collapses to exactly one undo entry — calling `setNodes` then
   * `setEdges` pushes TWO snapshots, so one delete would take two undos to
   * reverse (the undo-atomicity bug class).
   */
  setNodesAndEdges: (
    nodes: Node[] | ((nds: Node[]) => Node[]),
    edges: PipelineEdge[] | ((eds: PipelineEdge[]) => PipelineEdge[]),
  ) => void
  setNodesAndEdgesAndSubmodels: (
    nodes: Node[] | ((nds: Node[]) => Node[]),
    edges: PipelineEdge[] | ((eds: PipelineEdge[]) => PipelineEdge[]),
    submodels: Record<string, unknown>,
  ) => void
  setPreamble: (value: string) => void

  // Raw actions (skip history push — for mid-drag and guarded WS sync)
  setNodesRaw: (nodes: Node[] | ((nds: Node[]) => Node[])) => void
  setEdgesRaw: (edges: PipelineEdge[] | ((eds: PipelineEdge[]) => PipelineEdge[])) => void
  setSubmodelsRaw: (submodels: Record<string, unknown>) => void
  setPreambleRaw: (value: string) => void

  /**
   * Replace the complete persisted document and make it the clean saved
   * baseline. Whole-document loads are cache-identity boundaries, so both
   * derived versions advance monotonically even when fingerprints match.
   */
  loadGraphSnapshot: (snapshot: GraphSnapshot) => void

  // Explicit history operations
  pushSnapshot: () => void
  /** Record a completed VC operation so Undo/Redo replays its inverse. */
  pushVcEntry: (entry: Omit<VcHistoryEntry, "kind">) => void
  undo: () => void
  redo: () => void

  // Dirty tracking
  markSaved: (snapshot?: GraphSnapshot) => void

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
 * Snapshot of the current graph-shaped state. Deep-clones nodes and edges
 * so in-place mutations by React Flow or editor code cannot retroactively
 * corrupt history or saved baselines.
 */
export function captureGraphSnapshot(
  state: Pick<GraphStore, "nodes" | "edges" | "preamble" | "submodels">,
): GraphSnapshot {
  return cloneGraphSnapshot(state)
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

function hasOnlyNodeUiFieldChanges(current: Node[], next: Node[]): boolean {
  if (current.length !== next.length) return false

  let changed = false
  for (let index = 0; index < next.length; index += 1) {
    const previous = current[index]
    const node = next[index]
    if (previous === node) return false
    if (previous.id !== node.id) return false
    if (previous.data !== node.data) return false
    if (!positionsEqual(previous.position, node.position)) return false

    const keys = new Set([...Object.keys(previous), ...Object.keys(node)])
    for (const key of keys) {
      if (key === "data" || key === "position") continue
      if (REACT_FLOW_NODE_UI_FIELDS.has(key)) {
        if (!Object.is(
          (previous as unknown as Record<string, unknown>)[key],
          (node as unknown as Record<string, unknown>)[key],
        )) {
          changed = true
        }
        continue
      }
      const previousValue = (previous as unknown as Record<string, unknown>)[key]
      const nextValue = (node as unknown as Record<string, unknown>)[key]
      if (!Object.is(previousValue, nextValue)) return false
    }
  }
  return changed
}

function hasOnlyNodePositionChanges(current: Node[], next: Node[]): boolean {
  if (current.length !== next.length) return false

  let positionChanged = false
  for (let index = 0; index < next.length; index += 1) {
    const previous = current[index]
    const node = next[index]
    if (previous === node) return false
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

function hasOnlyEdgeUiFieldChanges(current: PipelineEdge[], next: PipelineEdge[]): boolean {
  if (current.length !== next.length) return false

  let changed = false
  for (let index = 0; index < next.length; index += 1) {
    const previous = current[index]
    const edge = next[index]
    if (previous === edge) return false
    if (previous.id !== edge.id) return false
    const keys = new Set([...Object.keys(previous), ...Object.keys(edge)])
    for (const key of keys) {
      if (REACT_FLOW_EDGE_UI_FIELDS.has(key)) {
        if (!Object.is(
          (previous as unknown as Record<string, unknown>)[key],
          (edge as unknown as Record<string, unknown>)[key],
        )) {
          changed = true
        }
        continue
      }
      const previousValue = (previous as unknown as Record<string, unknown>)[key]
      const nextValue = (edge as unknown as Record<string, unknown>)[key]
      if (!Object.is(previousValue, nextValue)) return false
    }
  }
  return changed
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

function computePersistedFingerprint(
  nodes: Node[],
  edges: PipelineEdge[],
  preamble: string,
  submodels: Record<string, unknown>,
): string {
  return serializeSnapshot({ nodes, edges, preamble, submodels })
}

function appendHistoryEntry(stack: HistoryEntry[], entry: HistoryEntry): HistoryEntry[] {
  return stack.length >= MAX_HISTORY
    ? [...stack.slice(stack.length - MAX_HISTORY + 1), entry]
    : [...stack, entry]
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
  edges: PipelineEdge[],
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
  function pushSnapshotInternal(): HistoryEntry[] {
    const { undoStack } = get()
    const snap = captureGraphSnapshot(get())
    return appendHistoryEntry(undoStack, snap)
  }

  return {
    nodes: [],
    edges: [],
    preamble: "",
    submodels: {},
    lastSavedSnapshot: null,
    undoStack: [],
    redoStack: [],
    vcBusy: false,
    structuralVersion: 0,
    structuralFingerprint: computeStructuralFingerprint([], [], ""),
    panelContextVersion: 0,
    panelContextFingerprint: computePanelContextFingerprint([], []),
    persistedFingerprint: computePersistedFingerprint([], [], "", {}),
    savedPersistedFingerprint: null,
    dirty: false,

    // ── History-aware actions ────────────────────────────────────────────

    setNodes: (updater) => {
      set((state) => ({
        undoStack: pushSnapshotInternal(),
        redoStack: [],
        ...(() => {
          const nodes = applyUpdater(state.nodes, updater)
          const nextPersistedFingerprint = computePersistedFingerprint(
            nodes,
            state.edges,
            state.preamble,
            state.submodels,
          )
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
          const nextPersistedFingerprint = computePersistedFingerprint(
            state.nodes,
            edges,
            state.preamble,
            state.submodels,
          )
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

    setNodesAndEdges: (nodesUpdater, edgesUpdater) => {
      set((state) => {
        // One snapshot for the whole gesture, captured BEFORE either mutation
        // (pushSnapshotInternal reads current state), then both node and edge
        // updaters applied in this single set() — so nodes and edges never
        // render in an inconsistent in-between state, and a single undo
        // restores both.
        const undoStack = pushSnapshotInternal()
        const nodes = applyUpdater(state.nodes, nodesUpdater)
        const edges = applyUpdater(state.edges, edgesUpdater)
        const nextPersistedFingerprint = computePersistedFingerprint(
          nodes,
          edges,
          state.preamble,
          state.submodels,
        )
        const nextFingerprint = computeStructuralFingerprint(nodes, edges, state.preamble)
        return {
          undoStack,
          redoStack: [],
          nodes,
          edges,
          ...computePanelContextPatch(state, nodes, edges),
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

    setNodesAndEdgesAndSubmodels: (nodesUpdater, edgesUpdater, submodels) => {
      set((state) => {
        const undoStack = pushSnapshotInternal()
        const nodes = applyUpdater(state.nodes, nodesUpdater)
        const edges = applyUpdater(state.edges, edgesUpdater)
        const nextPersistedFingerprint = computePersistedFingerprint(
          nodes,
          edges,
          state.preamble,
          submodels,
        )
        const nextFingerprint = computeStructuralFingerprint(nodes, edges, state.preamble)
        return {
          undoStack,
          redoStack: [],
          nodes,
          edges,
          submodels,
          ...computePanelContextPatch(state, nodes, edges),
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

    setPreamble: (value) => {
      set((state) => {
        const nextPersistedFingerprint = computePersistedFingerprint(
          state.nodes,
          state.edges,
          value,
          state.submodels,
        )
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
        const sameStructural = hasSameNodeStructureByReference(state.nodes, nodes)
        if (hasOnlyNodeUiFieldChanges(state.nodes, nodes)) {
          return { nodes }
        }
        if (hasOnlyNodePositionChanges(state.nodes, nodes)) {
          return {
            nodes,
            dirty: computeDirtyForPositionOnlyNodes(state, nodes),
          }
        }
        const nextPersistedFingerprint = computePersistedFingerprint(
          nodes,
          state.edges,
          state.preamble,
          state.submodels,
        )
        const persistedPatch = {
          persistedFingerprint: nextPersistedFingerprint,
          dirty: computeDirty(
            state.lastSavedSnapshot,
            state.savedPersistedFingerprint,
            nextPersistedFingerprint,
          ),
        }
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
        const sameStructural = hasSameEdgeStructure(state.edges, edges)
        if (hasOnlyEdgeUiFieldChanges(state.edges, edges)) {
          return { edges }
        }
        const panelContextPatch = computePanelContextPatch(state, state.nodes, edges)
        const nextPersistedFingerprint = computePersistedFingerprint(
          state.nodes,
          edges,
          state.preamble,
          state.submodels,
        )
        const persistedPatch = {
          persistedFingerprint: nextPersistedFingerprint,
          dirty: computeDirty(
            state.lastSavedSnapshot,
            state.savedPersistedFingerprint,
            nextPersistedFingerprint,
          ),
        }
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

    setSubmodelsRaw: (submodels) => {
      set((state) => {
        const nextPersistedFingerprint = computePersistedFingerprint(
          state.nodes,
          state.edges,
          state.preamble,
          submodels,
        )
        return {
          submodels,
          persistedFingerprint: nextPersistedFingerprint,
          dirty: computeDirty(
            state.lastSavedSnapshot,
            state.savedPersistedFingerprint,
            nextPersistedFingerprint,
          ),
        }
      })
    },

    setPreambleRaw: (value) => {
      set((state) => {
        const nextPersistedFingerprint = computePersistedFingerprint(
          state.nodes,
          state.edges,
          value,
          state.submodels,
        )
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

    loadGraphSnapshot: (snapshot) => {
      set((state) => {
        // Keep live state and the saved baseline independently cloned. Some
        // React Flow/editor paths mutate node objects in place; sharing
        // references here would let those mutations rewrite the baseline.
        // The live graph may legitimately carry runtime-only column/status
        // metadata supplied by the load response. Preserve it in live state;
        // only the saved/history clone below crosses the persisted boundary
        // that strips transient fields.
        const loaded = structuredClone(snapshot)
        const lastSavedSnapshot = captureGraphSnapshot(loaded)
        const structuralFingerprint = computeStructuralFingerprint(
          loaded.nodes,
          loaded.edges,
          loaded.preamble,
        )
        const panelContextFingerprint = computePanelContextFingerprint(
          loaded.nodes,
          loaded.edges,
        )
        const persistedFingerprint = computePersistedFingerprint(
          loaded.nodes,
          loaded.edges,
          loaded.preamble,
          loaded.submodels,
        )
        return {
          nodes: loaded.nodes,
          edges: loaded.edges,
          preamble: loaded.preamble,
          submodels: loaded.submodels,
          lastSavedSnapshot,
          undoStack: [],
          redoStack: [],
          structuralFingerprint,
          structuralVersion: state.structuralVersion + 1,
          panelContextFingerprint,
          panelContextVersion: state.panelContextVersion + 1,
          persistedFingerprint,
          savedPersistedFingerprint: persistedFingerprint,
          dirty: false,
        }
      })
    },

    pushSnapshot: () => {
      set(() => ({ undoStack: pushSnapshotInternal(), redoStack: [] }))
    },

    pushVcEntry: (entry) => {
      set((state) => {
        const full: VcHistoryEntry = { kind: "vc", ...entry }
        return {
          undoStack: appendHistoryEntry(state.undoStack, full),
          redoStack: [],
        }
      })
    },

    undo: () => {
      const { undoStack, vcBusy } = get()
      if (undoStack.length === 0 || vcBusy) return
      const prev = undoStack[undoStack.length - 1]
      const newUndo = undoStack.slice(0, -1)
      if (isVcEntry(prev)) {
        // A VC entry reverses via its API inverse, not a snapshot restore.
        // History is locked while it runs; on failure the entry returns to
        // the undo stack so the user can retry.
        set((state) => ({
          undoStack: newUndo,
          redoStack: appendHistoryEntry(state.redoStack, prev),
          vcBusy: true,
        }))
        void prev
          .undo()
          .catch(() => {
            set((state) => ({
              undoStack: appendHistoryEntry(state.undoStack, prev),
              redoStack: state.redoStack.filter((e) => e !== prev),
            }))
          })
          .finally(() => set({ vcBusy: false }))
        return
      }
      const nextFingerprint = computeStructuralFingerprint(prev.nodes, prev.edges, prev.preamble)
      const nextPanelContextFingerprint = computePanelContextFingerprint(prev.nodes, prev.edges)
      const nextPersistedFingerprint = computePersistedFingerprint(
        prev.nodes,
        prev.edges,
        prev.preamble,
        prev.submodels,
      )
      set((state) => ({
        undoStack: newUndo,
        redoStack: appendHistoryEntry(state.redoStack, captureGraphSnapshot(state)),
        nodes: prev.nodes,
        edges: prev.edges,
        preamble: prev.preamble,
        submodels: prev.submodels,
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
      const { redoStack, vcBusy } = get()
      if (redoStack.length === 0 || vcBusy) return
      const next = redoStack[redoStack.length - 1]
      const newRedo = redoStack.slice(0, -1)
      if (isVcEntry(next)) {
        set((state) => ({
          redoStack: newRedo,
          undoStack: appendHistoryEntry(state.undoStack, next),
          vcBusy: true,
        }))
        void next
          .redo()
          .catch(() => {
            set((state) => ({
              redoStack: appendHistoryEntry(state.redoStack, next),
              undoStack: state.undoStack.filter((e) => e !== next),
            }))
          })
          .finally(() => set({ vcBusy: false }))
        return
      }
      const nextFingerprint = computeStructuralFingerprint(next.nodes, next.edges, next.preamble)
      const nextPanelContextFingerprint = computePanelContextFingerprint(next.nodes, next.edges)
      const nextPersistedFingerprint = computePersistedFingerprint(
        next.nodes,
        next.edges,
        next.preamble,
        next.submodels,
      )
      set((state) => ({
        redoStack: newRedo,
        undoStack: appendHistoryEntry(state.undoStack, captureGraphSnapshot(state)),
        nodes: next.nodes,
        edges: next.edges,
        preamble: next.preamble,
        submodels: next.submodels,
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

    markSaved: (snapshot?: GraphSnapshot) => {
      set((state) => {
        const lastSavedSnapshot = snapshot === undefined
          ? captureGraphSnapshot(state)
          : captureGraphSnapshot(snapshot)
        const currentPersistedFingerprint = computePersistedFingerprint(
          state.nodes,
          state.edges,
          state.preamble,
          state.submodels,
        )
        const savedPersistedFingerprint = computePersistedFingerprint(
          lastSavedSnapshot.nodes,
          lastSavedSnapshot.edges,
          lastSavedSnapshot.preamble,
          lastSavedSnapshot.submodels,
        )
        return {
          lastSavedSnapshot,
          persistedFingerprint: currentPersistedFingerprint,
          savedPersistedFingerprint,
          dirty: computeDirty(
            lastSavedSnapshot,
            savedPersistedFingerprint,
            currentPersistedFingerprint,
          ),
        }
      })
    },

    // ── Pure selectors ──────────────────────────────────────────────────

    isDirty: () => {
      const { lastSavedSnapshot, nodes, edges, preamble, submodels } = get()
      const current = serializeSnapshot({ nodes, edges, preamble, submodels })
      if (lastSavedSnapshot === null) {
        return current !== EMPTY_SNAPSHOT
      }
      return current !== serializeSnapshot(lastSavedSnapshot)
    },

    canUndo: () => get().undoStack.length > 0 && !get().vcBusy,

    canRedo: () => get().redoStack.length > 0 && !get().vcBusy,
  }
})

export default useGraphStore
