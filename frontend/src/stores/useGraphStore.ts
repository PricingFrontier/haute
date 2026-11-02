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
 *     and clears `redoStack`. An atomic graph commit whose authored fingerprint
 *     is unchanged (such as generated step-code refresh) preserves both stacks.
 *
 *   - Raw: `setNodesRaw`, `setEdgesRaw`, `setSubmodelsRaw`, `setPreambleRaw`. No
 *     history push — used for mid-drag position updates (React Flow's
 *     `onNodesChange` replays many `position` events per frame; snapshotting
 *     each would fill undo with ~60 entries per drag), for WebSocket sync,
 *     for guarded external synchronisation, and for removing the workbench
 *     nodes' connections to tables the form no longer has, which follow the
 *     form, not the graph's history (an undo that puts one back sees it
 *     removed again at once).
 *
 * The 100-entry MAX_HISTORY cap prevents unbounded growth on long editing
 * sessions.
 *
 * A drilled submodel view (`enterSubmodelView` / `exitSubmodelView`) gets its
 * own stacks and saved baseline; the root's wait in `submodelView` until exit.
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
import { isPlainObject } from "../types/guards"
import type { PipelineEdge } from "../types/node"
import type { GlobalConstantDraft } from "../utils/globalConstants"

// ─── Types ───────────────────────────────────────────────────────────────

export interface GraphSnapshot {
  nodes: Node[]
  edges: PipelineEdge[]
  preamble: string
  submodels: Record<string, unknown>
  /** The global constants as the Constants pane edits them (absent: none). */
  globalConstants?: GlobalConstantDraft[]
  /** Why the constants file failed to load; only a document load sets it. */
  globalConstantsError?: string | null
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

/**
 * The root document's state while a submodel definition is on the canvas.
 *
 * Drilling in swaps the canvas for the child view, so the live `nodes`,
 * `edges`, history stacks and saved baseline all describe that view. The
 * root's own history and saved baseline wait here until the view is left.
 */
export interface SubmodelViewContext {
  /** The root document on entry; leaving after a change records it as one undo step. */
  entrySnapshot: GraphSnapshot
  entryPersistedFingerprint: string
  /** The view on entry: the clean baseline while the root it came from is saved. */
  entryViewSnapshot: GraphSnapshot
  entryViewPersistedFingerprint: string
  parentUndoStack: HistoryEntry[]
  parentRedoStack: HistoryEntry[]
  parentSavedSnapshot: GraphSnapshot | null
  parentSavedPersistedFingerprint: string | null
  /**
   * The whole document was marked saved while drilled, with the view at this
   * fingerprint: leaving from that view makes the root on exit the baseline.
   */
  savedViewPersistedFingerprint: string | null
}

export interface GraphStore {
  // State
  nodes: Node[]
  edges: PipelineEdge[]
  preamble: string
  /** Persisted submodel graphs; included in history and dirty fingerprints. */
  submodels: Record<string, unknown>
  /**
   * The global constants, in the dirty and structural fingerprints and in
   * every history entry: undo and redo restore them with the graph.
   */
  globalConstants: GlobalConstantDraft[]
  /** Why the constants file failed to load; the pane is read-only while set. */
  globalConstantsError: string | null
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
  /** Set while a submodel definition is on the canvas instead of the root. */
  submodelView: SubmodelViewContext | null

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
    preamble?: string,
  ) => void
  setPreamble: (value: string) => void

  // Raw actions (skip history push — for mid-drag and guarded WS sync)
  setNodesRaw: (nodes: Node[] | ((nds: Node[]) => Node[])) => void
  setEdgesRaw: (edges: PipelineEdge[] | ((eds: PipelineEdge[]) => PipelineEdge[])) => void
  setSubmodelsRaw: (submodels: Record<string, unknown>) => void
  /**
   * Advance `structuralVersion` for a change the graph does not hold but every structural
   * reader must see: the tables the workbench nodes are given from the form, which are their
   * ports and their frames (specs/workbench). The graph, its history and its dirty state are
   * left as they are.
   */
  noteStructureOutsideGraph: () => void
  setPreambleRaw: (value: string) => void
  /** Replace the global constants without an undo entry. */
  setGlobalConstantsRaw: (constants: GlobalConstantDraft[]) => void
  /** Replace the global constants as one undo step (none when nothing changes). */
  setGlobalConstants: (constants: GlobalConstantDraft[]) => void

  // Submodel navigation
  /**
   * Put a submodel's view on the canvas with history of its own: Undo and
   * Redo cannot reach the root's entries, and the view is clean exactly when
   * it is unchanged and the root it came from was clean. Throws while a view
   * is already open or a version-control step is running.
   */
  enterSubmodelView: (view: { nodes: Node[]; edges: PipelineEdge[] }) => void
  /**
   * Return to the root document, reconciled with the view's edits. The root's
   * history comes back; a changed document gains one undo step to the root as
   * it was on entry.
   */
  exitSubmodelView: (root: {
    nodes: Node[]
    edges: PipelineEdge[]
    submodels: Record<string, unknown>
  }) => void

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

  /**
   * Restore the complete initial state between tests — never call in app
   * code. Deliberately un-gated, matching the established ForTests
   * precedent (useOutputWriteStore et al.). Throws while a VC undo/redo is
   * in flight (`vcBusy`): those entries' catch/finally handlers `set()`
   * unconditionally, so a reset under them would be silently overwritten
   * with stale VC state — settle the promise first.
   */
  resetForTests: () => void
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
  state: Pick<GraphStore, "nodes" | "edges" | "preamble" | "submodels">
    & { globalConstants?: GlobalConstantDraft[] },
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

function structuralGraphParts(nodes: StructuralNode[], edges: StructuralEdge[]): string {
  const nodeParts = nodes
    .map((n) => `${n.id}:${shallowNodeDataHash(nodeData(n) as unknown as Record<string, unknown>)}`)
    .sort()
  const edgeParts = edges
    .map((e) => `${e.source}:${e.sourceHandle ?? ""}->${e.target}:${e.targetHandle ?? ""}`)
    .sort()
  return `nodes:${nodeParts.join("|")}||edges:${edgeParts.join("|")}`
}

// Submodels change by replacement, so one hash per object keeps drag frames cheap.
const submodelsStructuralCache = new WeakMap<Record<string, unknown>, string>()

/**
 * The submodel definitions as structure: each definition's ports and file, and
 * its child graph hashed like the canvas (input keys only, so preview results
 * written into a definition cannot change it).
 */
function submodelsStructuralHash(submodels: Record<string, unknown>): string {
  const cached = submodelsStructuralCache.get(submodels)
  if (cached !== undefined) return cached
  const parts = Object.keys(submodels).sort().map((id) => {
    const definition = submodels[id]
    if (!isPlainObject(definition) || !isPlainObject(definition.graph)) {
      return `${id}=${JSON.stringify(definition)}`
    }
    const { graph, ...rest } = definition
    const nodes = Array.isArray(graph.nodes) ? graph.nodes : []
    const edges = Array.isArray(graph.edges) ? graph.edges as StructuralEdge[] : []
    // A child with node data hashes by its input keys; anything else hashes whole.
    const hashable = nodes.filter((node): node is StructuralNode => isPlainObject(node) && isPlainObject(node.data))
    const other = nodes.filter((node) => !hashable.includes(node as StructuralNode)).map((node) => JSON.stringify(node)).sort()
    return `${id}=${JSON.stringify(rest)}{${structuralGraphParts(hashable, edges)}||${other.join("|")}}`
  })
  const hash = parts.join("|")
  submodelsStructuralCache.set(submodels, hash)
  return hash
}

export function computeStructuralFingerprint(
  nodes: StructuralNode[],
  edges: StructuralEdge[],
  preamble = "",
  globalConstants: readonly GlobalConstantDraft[] = [],
  submodels: Record<string, unknown> = {},
): string {
  return `${structuralGraphParts(nodes, edges)}||preamble:${JSON.stringify(preamble)}`
    + `||constants:${JSON.stringify(globalConstants)}`
    + `||submodels:${submodelsStructuralHash(submodels)}`
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
  "_frameColumns",
  "_schemaWarnings",
  "_notes",
  "ports",
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
  globalConstants: readonly GlobalConstantDraft[],
): string {
  return serializeSnapshot({ nodes, edges, preamble, submodels, globalConstants })
}

/** The constants a history entry restores; every entry is captured from the full store state. */
function historyConstants(entry: GraphSnapshot): GlobalConstantDraft[] {
  if (entry.globalConstants === undefined) {
    throw new Error("A graph history entry has no global constants to restore")
  }
  return entry.globalConstants
}

/**
 * `stack` with `entry` appended, the oldest entry dropped once `MAX_HISTORY` is reached:
 * the one history rule, which the workbench's form store (`useWorkbenchFormStore`) shares.
 */
export function appendHistoryEntry<T>(stack: T[], entry: T): T[] {
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

/**
 * The non-function (data) keys of `GraphStore`, derived rather than
 * enumerated: a data field added anywhere in the interface joins this type
 * automatically, so `createInitialGraphState` fails typecheck until it
 * initialises the new field. (A function-typed STATE field would evade the
 * derivation by classifying as an action — keep state non-callable.)
 */
type GraphStoreData = Pick<
  GraphStore,
  {
    [K in keyof GraphStore]: GraphStore[K] extends (...args: never[]) => unknown ? never : K
  }[keyof GraphStore]
>

/**
 * Complete initial state slice — the single source of truth for both store
 * creation and `resetForTests`. The derived `GraphStoreData` return type
 * means a data field cannot be initialised inline in the store literal
 * without also being added (and therefore reset) here.
 */
function createInitialGraphState(): GraphStoreData {
  return {
    nodes: [],
    edges: [],
    preamble: "",
    submodels: {},
    globalConstants: [],
    globalConstantsError: null,
    lastSavedSnapshot: null,
    undoStack: [],
    redoStack: [],
    vcBusy: false,
    structuralVersion: 0,
    structuralFingerprint: computeStructuralFingerprint([], [], "", [], {}),
    panelContextVersion: 0,
    panelContextFingerprint: computePanelContextFingerprint([], []),
    persistedFingerprint: computePersistedFingerprint([], [], "", {}, []),
    savedPersistedFingerprint: null,
    dirty: false,
    submodelView: null,
  }
}

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

  /**
   * One atomic graph commit of nodes, edges and submodels (and the preamble when given),
   * as an undo step when `history` holds and the authored graph changed. A render can
   * refresh generated step code without changing authored graph state: that cache update
   * consumes neither Undo nor Redo.
   */
  function commitGraphState(
    state: GraphStore,
    nodesUpdater: Node[] | ((nds: Node[]) => Node[]),
    edgesUpdater: PipelineEdge[] | ((eds: PipelineEdge[]) => PipelineEdge[]),
    submodels: Record<string, unknown>,
    preamble: string | undefined,
    history: boolean,
  ): Partial<GraphStore> {
    const nodes = applyUpdater(state.nodes, nodesUpdater)
    const edges = applyUpdater(state.edges, edgesUpdater)
    const nextPreamble = preamble === undefined ? state.preamble : preamble
    const nextPersistedFingerprint = computePersistedFingerprint(
      nodes,
      edges,
      nextPreamble,
      submodels,
      state.globalConstants,
    )
    const nextFingerprint = computeStructuralFingerprint(nodes, edges, nextPreamble, state.globalConstants, submodels)
    const recorded = history && nextPersistedFingerprint !== state.persistedFingerprint
    return {
      undoStack: recorded ? pushSnapshotInternal() : state.undoStack,
      redoStack: recorded ? [] : state.redoStack,
      nodes,
      edges,
      preamble: nextPreamble,
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
  }

  /** The constants replaced, with the fingerprints and dirty state they move. */
  function globalConstantsPatch(state: GraphStore, constants: GlobalConstantDraft[]) {
    const persistedFingerprint = computePersistedFingerprint(
      state.nodes,
      state.edges,
      state.preamble,
      state.submodels,
      constants,
    )
    const nextFingerprint = computeStructuralFingerprint(
      state.nodes,
      state.edges,
      state.preamble,
      constants,
      state.submodels,
    )
    return {
      globalConstants: constants,
      persistedFingerprint,
      dirty: computeDirty(state.lastSavedSnapshot, state.savedPersistedFingerprint, persistedFingerprint),
      ...(nextFingerprint === state.structuralFingerprint
        ? {}
        : {
            structuralFingerprint: nextFingerprint,
            structuralVersion: state.structuralVersion + 1,
          }),
    }
  }

  /**
   * `markSaved` while a submodel view is open. A snapshot is a root document
   * (saving is refused while drilled, so it is a save begun before entry):
   * it becomes the root's baseline, and the view is clean against it only if
   * the root on entry was that document. Without a snapshot the whole current
   * document counts as saved, so leaving from this same view makes the root on
   * exit the baseline.
   */
  function markSavedInSubmodelView(context: SubmodelViewContext, snapshot: GraphSnapshot | undefined) {
    set((state) => {
      if (snapshot === undefined) {
        return {
          lastSavedSnapshot: captureGraphSnapshot(state),
          savedPersistedFingerprint: state.persistedFingerprint,
          dirty: false,
          submodelView: { ...context, savedViewPersistedFingerprint: state.persistedFingerprint },
        }
      }
      const parentSavedSnapshot = captureGraphSnapshot({
        ...snapshot,
        globalConstants: snapshot.globalConstants ?? state.globalConstants,
      })
      const parentSavedPersistedFingerprint = computePersistedFingerprint(
        parentSavedSnapshot.nodes,
        parentSavedSnapshot.edges,
        parentSavedSnapshot.preamble,
        parentSavedSnapshot.submodels,
        parentSavedSnapshot.globalConstants ?? [],
      )
      const entryWasSaved = parentSavedPersistedFingerprint === context.entryPersistedFingerprint
      const lastSavedSnapshot = entryWasSaved ? context.entryViewSnapshot : parentSavedSnapshot
      const savedPersistedFingerprint = entryWasSaved
        ? context.entryViewPersistedFingerprint
        : parentSavedPersistedFingerprint
      return {
        lastSavedSnapshot,
        savedPersistedFingerprint,
        dirty: computeDirty(lastSavedSnapshot, savedPersistedFingerprint, state.persistedFingerprint),
        submodelView: {
          ...context,
          parentSavedSnapshot,
          parentSavedPersistedFingerprint,
          savedViewPersistedFingerprint: null,
        },
      }
    })
  }

  return {
    ...createInitialGraphState(),

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
            state.globalConstants,
          )
          const nextFingerprint = computeStructuralFingerprint(nodes, state.edges, state.preamble, state.globalConstants, state.submodels)
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
            state.globalConstants,
          )
          const nextFingerprint = computeStructuralFingerprint(state.nodes, edges, state.preamble, state.globalConstants, state.submodels)
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
          state.globalConstants,
        )
        const nextFingerprint = computeStructuralFingerprint(nodes, edges, state.preamble, state.globalConstants, state.submodels)
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

    setNodesAndEdgesAndSubmodels: (nodesUpdater, edgesUpdater, submodels, preamble) => {
      set((state) => commitGraphState(state, nodesUpdater, edgesUpdater, submodels, preamble, true))
    },

    setPreamble: (value) => {
      set((state) => {
        const nextPersistedFingerprint = computePersistedFingerprint(
          state.nodes,
          state.edges,
          value,
          state.submodels,
          state.globalConstants,
        )
        const nextFingerprint = computeStructuralFingerprint(state.nodes, state.edges, value, state.globalConstants, state.submodels)
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
          state.globalConstants,
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

        const nextFingerprint = computeStructuralFingerprint(nodes, state.edges, state.preamble, state.globalConstants, state.submodels)
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
          state.globalConstants,
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

        const nextFingerprint = computeStructuralFingerprint(state.nodes, edges, state.preamble, state.globalConstants, state.submodels)
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

    noteStructureOutsideGraph: () => {
      set((state) => ({ structuralVersion: state.structuralVersion + 1 }))
    },

    setSubmodelsRaw: (submodels) => {
      set((state) => {
        const nextPersistedFingerprint = computePersistedFingerprint(
          state.nodes,
          state.edges,
          state.preamble,
          submodels,
          state.globalConstants,
        )
        const nextFingerprint = computeStructuralFingerprint(
          state.nodes,
          state.edges,
          state.preamble,
          state.globalConstants,
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
        const nextPersistedFingerprint = computePersistedFingerprint(
          state.nodes,
          state.edges,
          value,
          state.submodels,
          state.globalConstants,
        )
        const nextFingerprint = computeStructuralFingerprint(state.nodes, state.edges, value, state.globalConstants, state.submodels)
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

    setGlobalConstantsRaw: (constants) => {
      set((state) => globalConstantsPatch(state, constants))
    },

    setGlobalConstants: (constants) => {
      set((state) => {
        const patch = globalConstantsPatch(state, constants)
        return patch.persistedFingerprint === state.persistedFingerprint
          ? patch
          : { ...patch, undoStack: pushSnapshotInternal(), redoStack: [] }
      })
    },

    // ── Submodel navigation ─────────────────────────────────────────────

    enterSubmodelView: (view) => {
      const state = get()
      if (state.submodelView !== null) {
        throw new Error("A submodel is already open. Return to the main pipeline first.")
      }
      if (state.vcBusy) {
        throw new Error("A version-control undo or redo is still running.")
      }
      const entrySnapshot = captureGraphSnapshot(state)
      const entryViewSnapshot = captureGraphSnapshot({ ...state, nodes: view.nodes, edges: view.edges })
      const entryViewPersistedFingerprint = computePersistedFingerprint(
        view.nodes,
        view.edges,
        state.preamble,
        state.submodels,
        state.globalConstants,
      )
      // A view entered from a clean root is clean until it changes. From a
      // dirty root the root's own baseline stays, which no view matches.
      const lastSavedSnapshot = state.dirty ? state.lastSavedSnapshot : entryViewSnapshot
      const savedPersistedFingerprint = state.dirty
        ? state.savedPersistedFingerprint
        : entryViewPersistedFingerprint
      const nextFingerprint = computeStructuralFingerprint(
        view.nodes,
        view.edges,
        state.preamble,
        state.globalConstants,
        state.submodels,
      )
      set({
        nodes: view.nodes,
        edges: view.edges,
        undoStack: [],
        redoStack: [],
        lastSavedSnapshot,
        savedPersistedFingerprint,
        persistedFingerprint: entryViewPersistedFingerprint,
        dirty: computeDirty(
          lastSavedSnapshot,
          savedPersistedFingerprint,
          entryViewPersistedFingerprint,
        ),
        ...computePanelContextPatch(state, view.nodes, view.edges),
        ...(nextFingerprint === state.structuralFingerprint
          ? {}
          : {
              structuralFingerprint: nextFingerprint,
              structuralVersion: state.structuralVersion + 1,
            }),
        submodelView: {
          entrySnapshot,
          entryPersistedFingerprint: state.persistedFingerprint,
          entryViewSnapshot,
          entryViewPersistedFingerprint,
          parentUndoStack: state.undoStack,
          parentRedoStack: state.redoStack,
          parentSavedSnapshot: state.lastSavedSnapshot,
          parentSavedPersistedFingerprint: state.savedPersistedFingerprint,
          savedViewPersistedFingerprint: null,
        },
      })
    },

    exitSubmodelView: (root) => {
      const state = get()
      const context = state.submodelView
      if (context === null) {
        throw new Error("No submodel is open.")
      }
      const persistedFingerprint = computePersistedFingerprint(
        root.nodes,
        root.edges,
        state.preamble,
        root.submodels,
        state.globalConstants,
      )
      const changed = persistedFingerprint !== context.entryPersistedFingerprint
      const leavingSavedView = context.savedViewPersistedFingerprint === state.persistedFingerprint
      const lastSavedSnapshot = leavingSavedView
        ? captureGraphSnapshot({ ...state, ...root })
        : context.parentSavedSnapshot
      const savedPersistedFingerprint = leavingSavedView
        ? persistedFingerprint
        : context.parentSavedPersistedFingerprint
      const nextFingerprint = computeStructuralFingerprint(
        root.nodes,
        root.edges,
        state.preamble,
        state.globalConstants,
        root.submodels,
      )
      set({
        nodes: root.nodes,
        edges: root.edges,
        submodels: root.submodels,
        // The view's own steps end with it: one step undoes the whole visit.
        undoStack: changed
          ? appendHistoryEntry(context.parentUndoStack, context.entrySnapshot)
          : context.parentUndoStack,
        redoStack: changed ? [] : context.parentRedoStack,
        lastSavedSnapshot,
        savedPersistedFingerprint,
        persistedFingerprint,
        dirty: computeDirty(lastSavedSnapshot, savedPersistedFingerprint, persistedFingerprint),
        ...computePanelContextPatch(state, root.nodes, root.edges),
        ...(nextFingerprint === state.structuralFingerprint
          ? {}
          : {
              structuralFingerprint: nextFingerprint,
              structuralVersion: state.structuralVersion + 1,
            }),
        submodelView: null,
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
          loaded.globalConstants ?? [],
          loaded.submodels,
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
          loaded.globalConstants ?? [],
        )
        return {
          nodes: loaded.nodes,
          edges: loaded.edges,
          preamble: loaded.preamble,
          submodels: loaded.submodels,
          globalConstants: loaded.globalConstants ?? [],
          globalConstantsError: loaded.globalConstantsError ?? null,
          lastSavedSnapshot,
          // Graph snapshots from before the load would bring back the old
          // document; a version-control entry is an API inverse, still valid
          // (a switch's own document arrives through this load). A load is
          // always a root document, so an open submodel view ends here.
          undoStack: [...(state.submodelView?.parentUndoStack ?? []), ...state.undoStack]
            .filter(isVcEntry),
          redoStack: [...(state.submodelView?.parentRedoStack ?? []), ...state.redoStack]
            .filter(isVcEntry),
          submodelView: null,
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
      const prevConstants = historyConstants(prev)
      const nextFingerprint = computeStructuralFingerprint(prev.nodes, prev.edges, prev.preamble, prevConstants, prev.submodels)
      const nextPanelContextFingerprint = computePanelContextFingerprint(prev.nodes, prev.edges)
      const nextPersistedFingerprint = computePersistedFingerprint(
        prev.nodes,
        prev.edges,
        prev.preamble,
        prev.submodels,
        prevConstants,
      )
      set((state) => ({
        undoStack: newUndo,
        redoStack: appendHistoryEntry(state.redoStack, captureGraphSnapshot(state)),
        nodes: prev.nodes,
        edges: prev.edges,
        preamble: prev.preamble,
        submodels: prev.submodels,
        globalConstants: prevConstants,
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
      const nextConstants = historyConstants(next)
      const nextFingerprint = computeStructuralFingerprint(next.nodes, next.edges, next.preamble, nextConstants, next.submodels)
      const nextPanelContextFingerprint = computePanelContextFingerprint(next.nodes, next.edges)
      const nextPersistedFingerprint = computePersistedFingerprint(
        next.nodes,
        next.edges,
        next.preamble,
        next.submodels,
        nextConstants,
      )
      set((state) => ({
        redoStack: newRedo,
        undoStack: appendHistoryEntry(state.undoStack, captureGraphSnapshot(state)),
        nodes: next.nodes,
        edges: next.edges,
        preamble: next.preamble,
        submodels: next.submodels,
        globalConstants: nextConstants,
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
      const context = get().submodelView
      if (context !== null) {
        markSavedInSubmodelView(context, snapshot)
        return
      }
      set((state) => {
        const lastSavedSnapshot = snapshot === undefined
          ? captureGraphSnapshot(state)
          : captureGraphSnapshot({
              ...snapshot,
              globalConstants: snapshot.globalConstants ?? state.globalConstants,
            })
        const currentPersistedFingerprint = computePersistedFingerprint(
          state.nodes,
          state.edges,
          state.preamble,
          state.submodels,
          state.globalConstants,
        )
        const savedPersistedFingerprint = computePersistedFingerprint(
          lastSavedSnapshot.nodes,
          lastSavedSnapshot.edges,
          lastSavedSnapshot.preamble,
          lastSavedSnapshot.submodels,
          lastSavedSnapshot.globalConstants ?? [],
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
      const { lastSavedSnapshot, nodes, edges, preamble, submodels, globalConstants } = get()
      const current = serializeSnapshot({ nodes, edges, preamble, submodels, globalConstants })
      if (lastSavedSnapshot === null) {
        return current !== EMPTY_SNAPSHOT
      }
      return current !== serializeSnapshot(lastSavedSnapshot)
    },

    canUndo: () => get().undoStack.length > 0 && !get().vcBusy,

    canRedo: () => get().redoStack.length > 0 && !get().vcBusy,

    // ── Test-only ───────────────────────────────────────────────────────

    resetForTests: () => {
      if (get().vcBusy) {
        throw new Error(
          "resetForTests called while a VC undo/redo is in flight (vcBusy). " +
            "Settle that promise first - its completion handlers set() unconditionally " +
            "and would overwrite the fresh reset with stale VC state.",
        )
      }
      set(createInitialGraphState())
    },
  }
})

// Convenience wrapper for suites that import the store statically (parity
// with resetOutputWriteStoreForTests); consumed by the undoAtomicity and
// fieldEditUndo suites. The consolidation test cannot use it — it reaches
// the store through a guarded dynamic import for importError diagnostics —
// and calls the store action directly instead.
export const resetGraphStoreForTests = () =>
  useGraphStore.getState().resetForTests()

export default useGraphStore
