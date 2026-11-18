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
 *   - History-aware: `setNodes`, `setEdges`, `setNodesAndEdges`,
 *     `setNodesAndEdgesAndSubmodels`, `setPreamble`, `setGlobalConstants`, and
 *     manual `pushSnapshot`. Each commits through `commitGraphChange`, which
 *     captures the pre-mutation graph onto `undoStack` and clears `redoStack`
 *     when the authored fingerprint changed; a change of no authored state
 *     (a selection, a generated step-code refresh) preserves both stacks.
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
 * An open submodel (`enterSubmodelView` / `exitSubmodelView`) is a view of the
 * one document: `focus` names the occurrence and keeps the pipeline's own nodes
 * and edges while the canvas shows the submodel. Every write the canvas takes
 * there changes the document in the same update (`liftSubmodelCanvas`), or is
 * refused whole; a drag's frames move the canvas only and reach the document
 * with the write that ends the drag. History entries are documents recording
 * the view their step was made in, and Undo or Redo shows that view again
 * (`projectSubmodelFocus`).
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
  holdsCanvasState,
  serializeSnapshot,
  selectIsDirty,
  cloneGraphSnapshot,
} from "../utils/graphSnapshot"
import { inputOrderedEdges } from "../utils/inputOrderedEdges"
import { shallowNodeDataHash } from "../utils/shallowNodeHash"
import { isSubmodelDefinition, isSubmodelInstanceConfig, nodeData } from "../types/node"
import { isPlainObject } from "../types/guards"
import type { PipelineEdge } from "../types/node"
import type { GlobalConstantDraft } from "../utils/globalConstants"
import {
  READ_ONLY_REASON,
  childAnnotations,
  liftSubmodelCanvas,
  projectSubmodelFocus,
  type SubmodelFocusIdentity,
} from "../utils/submodelDocument"
import useToastStore from "./useToastStore"
import { answer, watch, type Part } from "../utils/answers"

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
  /**
   * A history entry's view: the occurrence its step was made in, or null for
   * the main pipeline. `nodes` and `edges` are then the pipeline's own.
   */
  focus?: SubmodelFocusIdentity | null
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
 * The open submodel: which occurrence the canvas shows, and the pipeline's own
 * nodes and edges while it does. The document is these with `submodels`, the
 * preamble and the global constants; the canvas is drawn from it.
 */
export interface SubmodelFocus extends SubmodelFocusIdentity {
  /** A read-only copy (`instance_of`): the document refuses its edits. */
  readOnly: boolean
  rootNodes: Node[]
  rootEdges: PipelineEdge[]
}

/** The result of a boundary edit: the open view, and the document it leaves. */
export interface SubmodelBoundaryEdit {
  viewNodes: Node[]
  viewEdges: PipelineEdge[]
  parentNodes: Node[]
  parentEdges: PipelineEdge[]
  submodels: Record<string, unknown>
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
  /** Set while a submodel is open: the canvas shows that occurrence of its definition. */
  focus: SubmodelFocus | null
  /**
   * The execution annotations (a preview's columns, statuses) each open
   * occurrence's view last held for its children, by occurrence and child id:
   * they belong to the view, never to the shared definition.
   */
  submodelAnnotations: Record<string, Record<string, Record<string, unknown>>>
  /** Advances whenever the open view changes (opening, leaving, Undo or Redo moving it, a load). */
  navigationSerial: number
  /**
   * Advances with every change to the document other than moving nodes, and with
   * every Undo, Redo, view change and load, even one back to identical content: an
   * edit prepared against one generation commits only while it is still current.
   */
  semanticGeneration: number

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
   * Show an occurrence of a submodel, drawn from the document. Navigation,
   * not an edit: history and the dirty state stay as they are. Throws while a
   * submodel is already open, and when the occurrence cannot be drawn.
   */
  enterSubmodelView: (occurrence: SubmodelFocusIdentity) => void
  /** Show the main pipeline of the same document. Throws when no submodel is open. */
  exitSubmodelView: () => void
  /** Apply a boundary edit as one undo step: the view, the pipeline's nodes and edges, the definitions. */
  commitSubmodelBoundaryEdit: (edit: SubmodelBoundaryEdit) => void
  /** Replace the pipeline's own nodes (behind an open submodel, or on the canvas), without an undo entry (preview columns). */
  setRootNodesRaw: (nodes: Node[] | ((nds: Node[]) => Node[])) => void
  /** Replace the pipeline's own edges (behind an open submodel, or on the canvas), without an undo entry. */
  setRootEdgesRaw: (edges: PipelineEdge[] | ((eds: PipelineEdge[]) => PipelineEdge[])) => void

  /**
   * Replace the complete persisted document and make it the clean saved
   * baseline. Whole-document loads are cache-identity boundaries, so both
   * derived versions advance monotonically even when fingerprints match.
   * A load shows the main pipeline; `keepOpenSubmodel` (live sync's reload) keeps an
   * open submodel the new document still holds, and otherwise shows the main pipeline
   * with a notice.
   */
  loadGraphSnapshot: (snapshot: GraphSnapshot, options?: { keepOpenSubmodel?: boolean }) => void

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

/** The document's own nodes: the pipeline's, also while a submodel is open. */
function documentNodes(state: { nodes: Node[]; focus: SubmodelFocus | null }): Node[] {
  return state.focus ? state.focus.rootNodes : state.nodes
}

/** The document's own edges: the pipeline's, also while a submodel is open. */
function documentEdges(state: { edges: PipelineEdge[]; focus: SubmodelFocus | null }): PipelineEdge[] {
  return state.focus ? state.focus.rootEdges : state.edges
}

function focusIdentity(focus: SubmodelFocus | null): SubmodelFocusIdentity | null {
  return focus ? { instanceId: focus.instanceId, definitionId: focus.definitionId } : null
}

function focusKey(focus: SubmodelFocusIdentity | null | undefined): string {
  return focus ? JSON.stringify([focus.instanceId, focus.definitionId]) : ""
}

/**
 * The open submodel `wanted` shown in `document`: its focus, `readOnly` read from the
 * occurrence, and the canvas drawn from the document, or null when the document no longer
 * holds that occurrence of that definition.
 */
function submodelViewIn(
  document: { nodes: Node[]; edges: PipelineEdge[]; submodels: Record<string, unknown> },
  wanted: SubmodelFocusIdentity,
  annotations: Record<string, Record<string, unknown>> | undefined,
  previous: { nodes: Node[]; edges: PipelineEdge[] } | undefined,
): { focus: SubmodelFocus; nodes: Node[]; edges: PipelineEdge[] } | null {
  const occurrence = document.nodes.find((node) => node.id === wanted.instanceId)
  const config = occurrence?.data.config
  if (
    !occurrence
    || !isSubmodelInstanceConfig(config)
    || config.definition_id !== wanted.definitionId
    || !isSubmodelDefinition(document.submodels[wanted.definitionId], wanted.definitionId)
  ) {
    return null
  }
  const canvas = projectSubmodelFocus({
    rootNodes: document.nodes,
    rootEdges: document.edges,
    submodels: document.submodels,
    focus: wanted,
    annotations,
    previous,
  })
  return {
    focus: {
      instanceId: wanted.instanceId,
      definitionId: wanted.definitionId,
      readOnly: config.instance_of !== undefined,
      rootNodes: document.nodes,
      rootEdges: document.edges,
    },
    nodes: canvas.nodes,
    edges: canvas.edges,
  }
}

/**
 * `nodes` with every position on whole pixels (READ-11): a node placed or dragged lands on
 * one, so a save writes the position the canvas compares, and a nudge smaller than a pixel
 * moves nothing. Nodes already whole keep their identity, and so does the array.
 */
export function wholePositions<N extends { position: { x: number; y: number } }>(nodes: N[]): N[] {
  let changed = false
  const next = nodes.map((node) => {
    const { x, y } = node.position
    if (Number.isInteger(x) && Number.isInteger(y)) return node
    changed = true
    return { ...node, position: { ...node.position, x: Math.round(x), y: Math.round(y) } }
  })
  return changed ? next : nodes
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

/**
 * Whether `next` only moves nodes of `current`: the same nodes in the same order,
 * each the same object or differing only in its position and presentation fields.
 */
function onlyNodesMoved(current: Node[], next: Node[]): boolean {
  if (current.length !== next.length) return false
  return next.every((node, index) => {
    const previous = current[index]
    if (previous === node) return true
    if (previous.id !== node.id || previous.data !== node.data) return false
    const keys = new Set([...Object.keys(previous), ...Object.keys(node)])
    return [...keys].every((key) => key === "position" || REACT_FLOW_NODE_UI_FIELDS.has(key)
      || Object.is((previous as unknown as Record<string, unknown>)[key], (node as unknown as Record<string, unknown>)[key]))
  })
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

/** Each edge's key, in input order (`inputOrderedEdges`). */
function inputOrderedEdgeParts(edges: readonly StructuralEdge[]): string[] {
  return inputOrderedEdges(edges).map(edgeStructuralKey)
}

function hasSameEdgeStructure(current: StructuralEdge[], next: StructuralEdge[]): boolean {
  if (current.length !== next.length) return false

  const currentParts = inputOrderedEdgeParts(current)
  const nextParts = inputOrderedEdgeParts(next)
  return currentParts.every((part, index) => part === nextParts[index])
}

function structuralGraphParts(
  nodes: readonly StructuralNode[],
  edges: readonly StructuralEdge[],
): string {
  const nodeParts = nodes
    .map((n) => `${n.id}:${shallowNodeDataHash(nodeData(n) as unknown as Record<string, unknown>)}`)
    .sort()
  return `nodes:${nodeParts.join("|")}||edges:${inputOrderedEdgeParts(edges).join("|")}`
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
  nodes: readonly StructuralNode[],
  edges: readonly StructuralEdge[],
  preamble = "",
  globalConstants: readonly GlobalConstantDraft[] = [],
  submodels: Record<string, unknown> = {},
): string {
  return `${structuralGraphParts(nodes, edges)}||preamble:${JSON.stringify(preamble)}`
    + `||constants:${JSON.stringify(globalConstants)}`
    + `||submodels:${submodelsStructuralHash(submodels)}`
}

/** The document a derived request is computed from, as {@link derivedRequestKey} reads it. */
export type DerivedRequestDocument = {
  nodes: readonly StructuralNode[]
  edges: readonly StructuralEdge[]
  submodels?: Record<string, unknown>
  preamble?: string
  globalConstants: readonly GlobalConstantDraft[]
}

/**
 * The one key of a request derived from the document as it stands: every node's inputs
 * but `exceptNodeId`'s (the node whose own settings the caller keys beside it), the edges
 * in input order, the submodel definitions, the preamble and the constants, read as the
 * structural fingerprint reads them. Every estimate and preview kept in a panel keys what
 * it was computed from by this, never by a list of its own, so an Undo that moves any of
 * them, a constant included, makes the kept answer old.
 */
export function derivedRequestKey(document: DerivedRequestDocument, exceptNodeId?: string): string {
  const nodes = exceptNodeId === undefined
    ? document.nodes
    : document.nodes.filter((node) => node.id !== exceptNodeId)
  return computeStructuralFingerprint(
    nodes,
    document.edges,
    document.preamble,
    document.globalConstants,
    document.submodels,
  )
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

type GraphFields = Pick<GraphStore, "nodes" | "edges" | "preamble" | "submodels" | "globalConstants" | "focus">

/**
 * The structural fingerprint of the document: the pipeline's own nodes and edges (also
 * while a submodel is open), every definition, the preamble and the constants. It is the
 * clock results are checked against, so opening or leaving a submodel leaves it as it
 * is, and an edit inside one moves it through the definition.
 */
function structuralFingerprintOf(fields: GraphFields): string {
  return computeStructuralFingerprint(
    documentNodes(fields),
    documentEdges(fields),
    fields.preamble,
    fields.globalConstants,
    fields.submodels,
  )
}

/** The persisted fingerprint of the document the fields hold. */
function persistedFingerprintOf(fields: GraphFields): string {
  return computePersistedFingerprint(
    documentNodes(fields),
    documentEdges(fields),
    fields.preamble,
    fields.submodels,
    fields.globalConstants,
  )
}

/**
 * What a change to the persisted fields moves, the one derivation every action shares: the
 * persisted fingerprint and `dirty` against `state`'s saved baseline, and the panel-context
 * and structural fingerprints, each version advancing only when its fingerprint changed.
 * Panel context reads nodes and edges only, so unchanged references skip it; `structural`
 * false skips the structural hash for a change known to keep it.
 */
function derivedPatch(
  state: Pick<
    GraphStore,
    | "nodes" | "edges" | "lastSavedSnapshot" | "savedPersistedFingerprint"
    | "panelContextFingerprint" | "panelContextVersion" | "structuralFingerprint" | "structuralVersion"
  >,
  next: GraphFields,
  structural = true,
): Partial<GraphStore> {
  const persistedFingerprint = persistedFingerprintOf(next)
  const patch: Partial<GraphStore> = {
    persistedFingerprint,
    dirty: computeDirty(state.lastSavedSnapshot, state.savedPersistedFingerprint, persistedFingerprint),
    ...(next.nodes === state.nodes && next.edges === state.edges
      ? {}
      : computePanelContextPatch(state, next.nodes, next.edges)),
  }
  if (!structural) return patch
  const structuralFingerprint = structuralFingerprintOf(next)
  return structuralFingerprint === state.structuralFingerprint
    ? patch
    : { ...patch, structuralFingerprint, structuralVersion: state.structuralVersion + 1 }
}

/** The document a graph history entry holds, which undo and redo restore together. */
function restoredDocument(entry: GraphSnapshot): Omit<GraphFields, "focus"> {
  return {
    nodes: entry.nodes,
    edges: entry.edges,
    preamble: entry.preamble,
    submodels: entry.submodels,
    globalConstants: historyConstants(entry),
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
    structuralFingerprint: structuralFingerprintOf({
      nodes: [], edges: [], preamble: "", globalConstants: [], submodels: {}, focus: null,
    }),
    panelContextVersion: 0,
    panelContextFingerprint: computePanelContextFingerprint([], []),
    persistedFingerprint: computePersistedFingerprint([], [], "", {}, []),
    savedPersistedFingerprint: null,
    dirty: false,
    focus: null,
    submodelAnnotations: {},
    navigationSerial: 0,
    semanticGeneration: 0,
  }
}

const useGraphStore = create<GraphStore>()((set, get) => {
  /** History is locked while a VC entry's undo or redo runs, and unlocked once it has settled. */
  const unlockHistory = (running: boolean) => {
    if (!running) set({ vcBusy: false })
  }
  /**
   * The document as one history entry: the pipeline's own nodes and edges with
   * the definitions, preamble and constants, recording `focus` as the view the
   * step is made in (the open one unless given).
   */
  function captureDocument(
    state: GraphStore,
    focus: SubmodelFocusIdentity | null = focusIdentity(state.focus),
  ): GraphSnapshot {
    return {
      ...captureGraphSnapshot({
        nodes: documentNodes(state),
        edges: documentEdges(state),
        preamble: state.preamble,
        submodels: state.submodels,
        globalConstants: state.globalConstants,
      }),
      focus,
    }
  }

  /**
   * The step a gesture inside an open submodel began (`pushSnapshot` before
   * separate raw writes, such as a drag): recorded when the first of its writes
   * changes the document, and dropped when one is refused, so a gesture is one
   * step or none.
   */
  let pendingGesture: GraphSnapshot | null = null

  /**
   * The canvas the document last agreed with, kept while drag frames move the
   * canvas ahead of it: the write that ends the drag is lifted against it, so
   * the moves the frames made reach the definition.
   */
  let liftBase: { nodes: Node[]; edges: PipelineEdge[] } | null = null

  /** A view or document change ends any gesture in progress. */
  function endGesture() {
    pendingGesture = null
    liftBase = null
  }

  function refuse(reason: string) {
    pendingGesture = null
    useToastStore.getState().addToast("error", reason)
  }

  /**
   * The one commit of a change to the graph's fields: the changed fields with the
   * fingerprints, versions and dirty state they move, and, when `history` holds and
   * the document changed, an undo step of the document before it (clearing redo). A
   * change of no authored state — a selection, or a render refreshing generated step
   * code — consumes neither Undo nor Redo. While a submodel is open, a change to the
   * canvas changes the document through `liftSubmodelCanvas`, or is refused whole.
   */
  function commitGraphChange(
    state: GraphStore,
    change: Partial<Omit<GraphFields, "focus">>,
    history: boolean,
  ): { patch: Partial<GraphStore> } | { refused: string } {
    const focus = state.focus
    const nodes = change.nodes ?? state.nodes
    const edges = change.edges ?? state.edges
    let submodels = change.submodels ?? state.submodels
    let annotations: Partial<GraphStore> = {}
    // A canvas write inside an open submodel ends the gesture it belongs to,
    // whether or not it changed the document.
    let endsGesture = false
    const onlyCanvas = (change.submodels === undefined || change.submodels === state.submodels)
      && (change.preamble === undefined || change.preamble === state.preamble)
      && (change.globalConstants === undefined || change.globalConstants === state.globalConstants)
    // Whether the change only moves nodes, which leaves the semantic generation as it is.
    let layoutOnly = !focus
      && onlyCanvas
      && edges === state.edges
      && nodes !== state.nodes
      && onlyNodesMoved(state.nodes, nodes)
    if (focus && (nodes !== state.nodes || edges !== state.edges)) {
      // A drag frame moves the canvas only; the move reaches the definition when it ends.
      if (
        nodes.some((node) => node.dragging)
        && change.submodels === undefined
        && change.preamble === undefined
        && change.globalConstants === undefined
      ) {
        liftBase ??= { nodes: state.nodes, edges: state.edges }
        return { patch: { nodes, edges } }
      }
      const definition = submodels[focus.definitionId]
      if (!isSubmodelDefinition(definition, focus.definitionId)) {
        throw new Error(`The open submodel's definition ${focus.definitionId} is not in the document`)
      }
      const lifted = liftSubmodelCanvas({
        definition,
        readOnly: focus.readOnly,
        previous: liftBase ?? { nodes: state.nodes, edges: state.edges },
        next: { nodes, edges },
      })
      if (lifted.kind === "refused") return { refused: lifted.reason }
      liftBase = null
      endsGesture = true
      layoutOnly = onlyCanvas && (lifted.kind === "canvas" || lifted.layoutOnly)
      if (lifted.kind === "definition") {
        submodels = { ...submodels, [focus.definitionId]: lifted.definition }
      }
      annotations = {
        submodelAnnotations: { ...state.submodelAnnotations, [focus.instanceId]: childAnnotations(nodes) },
      }
    }
    const fields: GraphFields = { ...state, ...change, nodes, edges, submodels, focus }
    const patch = derivedPatch(state, fields)
    // A drag's position-only raw updates leave the cached fingerprint behind, so
    // the state before the change is fingerprinted as it is.
    const changed = patch.persistedFingerprint !== persistedFingerprintOf(state)
    let historyPatch: Partial<GraphStore> = {}
    const gesture = pendingGesture
    if (changed && (history || gesture)) {
      historyPatch = {
        undoStack: appendHistoryEntry(state.undoStack, history || !gesture ? captureDocument(state) : gesture),
        redoStack: [],
      }
    }
    if (changed || history || endsGesture) pendingGesture = null
    return {
      patch: {
        ...historyPatch,
        ...(changed && !layoutOnly ? { semanticGeneration: state.semanticGeneration + 1 } : {}),
        ...change,
        nodes,
        edges,
        submodels,
        ...annotations,
        ...patch,
      },
    }
  }

  function commit(change: (state: GraphStore) => Partial<Omit<GraphFields, "focus">>, history: boolean) {
    const state = get()
    const planned = commitGraphChange(state, change(state), history)
    if ("refused" in planned) {
      // A refused drag end puts back the canvas the document agrees with.
      if (liftBase) {
        const base = liftBase
        liftBase = null
        set({ nodes: base.nodes, edges: base.edges })
      }
      refuse(planned.refused)
      return
    }
    set(planned.patch)
  }

  /**
   * The state an undo or redo of `entry` leaves: its document, shown in the view
   * its step was made in, or the main pipeline (with a notice) when that
   * occurrence is gone. The entry for the other stack is the document as it is,
   * recording the same view, so the step can be crossed again from anywhere.
   */
  function restore(state: GraphStore, entry: GraphSnapshot): { patch: Partial<GraphStore>; notice: string | null } {
    const document = restoredDocument(entry)
    const wanted = entry.focus ?? null
    let focus: SubmodelFocus | null = null
    let canvas = { nodes: document.nodes, edges: document.edges }
    let notice: string | null = null
    if (wanted) {
      const sameView = focusKey(focusIdentity(state.focus)) === focusKey(wanted)
      const view = submodelViewIn(
        document,
        wanted,
        state.submodelAnnotations[wanted.instanceId],
        sameView ? { nodes: state.nodes, edges: state.edges } : undefined,
      )
      if (view) {
        canvas = { nodes: view.nodes, edges: view.edges }
        focus = view.focus
      } else {
        notice = "The submodel that step was made in is gone, so the main pipeline is shown."
      }
    }
    // A step undone or redone from inside a submodel that takes the submodel away
    // (its occurrence created or deleted by the step) shows the main pipeline too.
    const open = state.focus
    if (notice === null && open !== null && focus === null) {
      const occurrence = document.nodes.find((node) => node.id === open.instanceId)
      const config = occurrence?.data.config
      if (!occurrence || !isSubmodelInstanceConfig(config) || config.definition_id !== open.definitionId) {
        notice = "The submodel that was open is gone, so the main pipeline is shown."
      }
    }
    const fields: GraphFields = { ...document, nodes: canvas.nodes, edges: canvas.edges, focus }
    const viewChanged = focusKey(focusIdentity(state.focus)) !== focusKey(focusIdentity(focus))
    return {
      patch: {
        ...fields,
        ...derivedPatch(state, fields),
        ...(viewChanged ? { navigationSerial: state.navigationSerial + 1 } : {}),
        semanticGeneration: state.semanticGeneration + 1,
      },
      notice,
    }
  }

  return {
    ...createInitialGraphState(),

    // ── History-aware actions ────────────────────────────────────────────

    setNodes: (updater) => {
      commit((state) => ({ nodes: wholePositions(applyUpdater(state.nodes, updater)) }), true)
    },

    setEdges: (updater) => {
      commit((state) => ({ edges: applyUpdater(state.edges, updater) }), true)
    },

    setNodesAndEdges: (nodesUpdater, edgesUpdater) => {
      // Both updaters in this single commit, so nodes and edges never render in
      // an inconsistent in-between state, and a single undo restores both.
      commit((state) => ({
        nodes: wholePositions(applyUpdater(state.nodes, nodesUpdater)),
        edges: applyUpdater(state.edges, edgesUpdater),
      }), true)
    },

    setNodesAndEdgesAndSubmodels: (nodesUpdater, edgesUpdater, submodels, preamble) => {
      commit((state) => ({
        nodes: wholePositions(applyUpdater(state.nodes, nodesUpdater)),
        edges: applyUpdater(state.edges, edgesUpdater),
        submodels,
        ...(preamble === undefined ? {} : { preamble }),
      }), true)
    },

    setPreamble: (value) => {
      commit(() => ({ preamble: value }), true)
    },

    // ── Raw actions ─────────────────────────────────────────────────────

    setNodesRaw: (updater) => {
      if (get().focus) {
        commit((state) => ({ nodes: wholePositions(applyUpdater(state.nodes, updater)) }), false)
        return
      }
      set((state) => {
        const nodes = wholePositions(applyUpdater(state.nodes, updater))
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
        const patch = derivedPatch(state, { ...state, nodes }, !sameStructural)
        return {
          nodes,
          ...patch,
          ...(patch.persistedFingerprint !== state.persistedFingerprint
            ? { semanticGeneration: state.semanticGeneration + 1 }
            : {}),
        }
      })
    },

    setEdgesRaw: (updater) => {
      if (get().focus) {
        commit((state) => ({ edges: applyUpdater(state.edges, updater) }), false)
        return
      }
      set((state) => {
        const edges = applyUpdater(state.edges, updater)
        const sameStructural = hasSameEdgeStructure(state.edges, edges)
        if (hasOnlyEdgeUiFieldChanges(state.edges, edges)) {
          return { edges }
        }
        const patch = derivedPatch(state, { ...state, edges }, !sameStructural)
        return {
          edges,
          ...patch,
          ...(patch.persistedFingerprint !== state.persistedFingerprint
            ? { semanticGeneration: state.semanticGeneration + 1 }
            : {}),
        }
      })
    },

    noteStructureOutsideGraph: () => {
      set((state) => ({ structuralVersion: state.structuralVersion + 1 }))
    },

    setSubmodelsRaw: (submodels) => {
      commit(() => ({ submodels }), false)
    },

    setPreambleRaw: (value) => {
      commit(() => ({ preamble: value }), false)
    },

    setGlobalConstantsRaw: (constants) => {
      commit(() => ({ globalConstants: constants }), false)
    },

    setGlobalConstants: (constants) => {
      commit(() => ({ globalConstants: constants }), true)
    },

    // ── Submodel navigation ─────────────────────────────────────────────

    enterSubmodelView: (occurrence) => {
      const state = get()
      if (state.focus !== null) {
        throw new Error("A submodel is already open. Return to the main pipeline first.")
      }
      if (state.vcBusy) {
        throw new Error("A version-control undo or redo is still running.")
      }
      const node = state.nodes.find((candidate) => candidate.id === occurrence.instanceId)
      const config = node?.data.config
      if (!node || !isSubmodelInstanceConfig(config) || config.definition_id !== occurrence.definitionId) {
        throw new Error(`Submodel instance ${occurrence.instanceId} is not on the canvas`)
      }
      const canvas = projectSubmodelFocus({
        rootNodes: state.nodes,
        rootEdges: state.edges,
        submodels: state.submodels,
        focus: occurrence,
        annotations: state.submodelAnnotations[occurrence.instanceId],
      })
      const focus: SubmodelFocus = {
        instanceId: occurrence.instanceId,
        definitionId: occurrence.definitionId,
        readOnly: config.instance_of !== undefined,
        rootNodes: state.nodes,
        rootEdges: state.edges,
      }
      endGesture()
      const fields: GraphFields = { ...state, nodes: canvas.nodes, edges: canvas.edges, focus }
      set({
        nodes: canvas.nodes,
        edges: canvas.edges,
        focus,
        navigationSerial: state.navigationSerial + 1,
        semanticGeneration: state.semanticGeneration + 1,
        ...derivedPatch(state, fields),
      })
    },

    exitSubmodelView: () => {
      const state = get()
      const focus = state.focus
      if (focus === null) {
        throw new Error("No submodel is open.")
      }
      endGesture()
      const fields: GraphFields = { ...state, nodes: focus.rootNodes, edges: focus.rootEdges, focus: null }
      set({
        nodes: focus.rootNodes,
        edges: focus.rootEdges,
        focus: null,
        navigationSerial: state.navigationSerial + 1,
        semanticGeneration: state.semanticGeneration + 1,
        ...derivedPatch(state, fields),
      })
    },

    commitSubmodelBoundaryEdit: (edit) => {
      const state = get()
      const focus = state.focus
      if (focus === null) {
        throw new Error("A boundary edit needs an open submodel.")
      }
      // A boundary command edits the document, so its definition holds its children as
      // the document does: one holding the canvas's state (a selection, a size, a
      // preview's columns) is a defect of the command, refused rather than cleaned.
      const open = edit.submodels[focus.definitionId]
      const withCanvasState = isSubmodelDefinition(open, focus.definitionId)
        ? open.graph.nodes.find(holdsCanvasState)
        : undefined
      if (withCanvasState) {
        throw new Error(`A boundary edit's definition holds the canvas's state of ${withCanvasState.id}`)
      }
      const submodels = edit.submodels
      const nextFocus: SubmodelFocus = { ...focus, rootNodes: edit.parentNodes, rootEdges: edit.parentEdges }
      const fields: GraphFields = {
        ...state,
        nodes: edit.viewNodes,
        edges: edit.viewEdges,
        submodels,
        focus: nextFocus,
      }
      const patch = derivedPatch(state, fields)
      const changed = patch.persistedFingerprint !== persistedFingerprintOf(state)
      const dragBase = liftBase
      const dragGesture = pendingGesture
      endGesture()
      if (changed && focus.readOnly) {
        refuse(READ_ONLY_REASON)
        return
      }
      set({
        ...(changed
          ? {
              undoStack: appendHistoryEntry(state.undoStack, captureDocument(state)),
              redoStack: [],
              semanticGeneration: state.semanticGeneration + 1,
            }
          : {}),
        nodes: edit.viewNodes,
        edges: edit.viewEdges,
        submodels,
        focus: nextFocus,
        submodelAnnotations: { ...state.submodelAnnotations, [focus.instanceId]: childAnnotations(edit.viewNodes) },
        ...patch,
      })
      // A drag still under way (an edit waiting on server names can land
      // mid-drag) goes on over the edited view: its end is lifted against that
      // view as the document has it, with the dragged children where the drag
      // found them, and it records its own step after this one.
      if (dragBase) {
        const found = new Map(dragBase.nodes.map((node) => [node.id, node.position]))
        liftBase = {
          nodes: edit.viewNodes.map((node) => {
            const position = found.get(node.id)
            return position ? { ...node, position } : node
          }),
          edges: edit.viewEdges,
        }
        if (dragGesture) pendingGesture = captureDocument(get())
      }
    },

    setRootNodesRaw: (updater) => {
      const state = get()
      const focus = state.focus
      // With no submodel open the pipeline's own nodes are the canvas.
      if (focus === null) {
        get().setNodesRaw(updater)
        return
      }
      const nextFocus: SubmodelFocus = { ...focus, rootNodes: applyUpdater(focus.rootNodes, updater) }
      set({ focus: nextFocus, ...derivedPatch(state, { ...state, focus: nextFocus }, false) })
    },

    setRootEdgesRaw: (updater) => {
      const state = get()
      const focus = state.focus
      // With no submodel open the pipeline's own edges are the canvas.
      if (focus === null) {
        get().setEdgesRaw(updater)
        return
      }
      // The open view's cards carry the pipeline's bindings into it, so the view is drawn
      // again from the new edges, keeping its canvas state, as Undo and Redo draw it.
      const view = submodelViewIn(
        { nodes: focus.rootNodes, edges: applyUpdater(focus.rootEdges, updater), submodels: state.submodels },
        focusIdentity(focus)!,
        state.submodelAnnotations[focus.instanceId],
        { nodes: state.nodes, edges: state.edges },
      )
      if (view === null) {
        throw new Error(`Submodel occurrence ${focus.instanceId} is not in the document`)
      }
      // The view is drawn from the authored forms, which hold no selection: what was selected
      // on the canvas stays selected.
      const selectedNodes = new Set(state.nodes.filter((node) => node.selected).map((node) => node.id))
      const selectedEdges = new Set(state.edges.filter((each) => each.selected).map((each) => each.id))
      const nodes = view.nodes.map((node) => (selectedNodes.has(node.id) ? { ...node, selected: true } : node))
      const edges = view.edges.map((each) => (selectedEdges.has(each.id) ? { ...each, selected: true } : each))
      const fields: GraphFields = { ...state, nodes, edges, focus: view.focus }
      const patch = derivedPatch(state, fields)
      set({
        nodes,
        edges,
        focus: view.focus,
        ...patch,
        ...(patch.persistedFingerprint !== state.persistedFingerprint
          ? { semanticGeneration: state.semanticGeneration + 1 }
          : {}),
      })
    },

    // ── Explicit history operations ─────────────────────────────────────

    loadGraphSnapshot: (snapshot, options) => {
      endGesture()
      let notice: string | null = null
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
        // A reload keeps the open submodel while the new document holds it; its view's
        // annotations belong to the old document and go with it.
        const open = options?.keepOpenSubmodel ? focusIdentity(state.focus) : null
        const view = open ? submodelViewIn(loaded, open, undefined, undefined) : null
        if (open && view === null) {
          notice = "The submodel that was open is gone, so the main pipeline is shown."
        }
        const fields: GraphFields = {
          nodes: view ? view.nodes : loaded.nodes,
          edges: view ? view.edges : loaded.edges,
          preamble: loaded.preamble,
          submodels: loaded.submodels,
          globalConstants: loaded.globalConstants ?? [],
          focus: view ? view.focus : null,
        }
        const viewChanged = focusKey(focusIdentity(state.focus)) !== focusKey(focusIdentity(fields.focus))
        const persistedFingerprint = persistedFingerprintOf(fields)
        return {
          ...fields,
          globalConstantsError: loaded.globalConstantsError ?? null,
          lastSavedSnapshot,
          // Graph snapshots from before the load would bring back the old
          // document; a version-control entry is an API inverse, still valid
          // (a switch's own document arrives through this load).
          undoStack: state.undoStack.filter(isVcEntry),
          redoStack: state.redoStack.filter(isVcEntry),
          submodelAnnotations: {},
          navigationSerial: state.navigationSerial + (viewChanged ? 1 : 0),
          semanticGeneration: state.semanticGeneration + 1,
          structuralFingerprint: structuralFingerprintOf(fields),
          structuralVersion: state.structuralVersion + 1,
          panelContextFingerprint: computePanelContextFingerprint(fields.nodes, fields.edges),
          panelContextVersion: state.panelContextVersion + 1,
          persistedFingerprint,
          savedPersistedFingerprint: persistedFingerprint,
          dirty: false,
        }
      })
      if (notice) useToastStore.getState().addToast("info", notice)
    },

    pushSnapshot: () => {
      const state = get()
      if (state.focus) {
        // Recorded when the gesture's first write changes the document.
        pendingGesture = captureDocument(state)
        return
      }
      set({ undoStack: appendHistoryEntry(state.undoStack, captureDocument(state)), redoStack: [] })
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
        // The entry returns only to the history that still holds it: one a load replaced is gone.
        void answer([watch(useGraphStore, (state) => state.redoStack.includes(prev))], prev.undo(), {
          failed: () => set((state) => ({
            undoStack: appendHistoryEntry(state.undoStack, prev),
            redoStack: state.redoStack.filter((e) => e !== prev),
          })),
        }, { inFlight: unlockHistory })
        return
      }
      endGesture()
      const state = get()
      const { patch, notice } = restore(state, prev)
      set({
        ...patch,
        undoStack: newUndo,
        redoStack: appendHistoryEntry(state.redoStack, captureDocument(state, prev.focus ?? null)),
      })
      if (notice) useToastStore.getState().addToast("info", notice)
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
        void answer([watch(useGraphStore, (state) => state.undoStack.includes(next))], next.redo(), {
          failed: () => set((state) => ({
            redoStack: appendHistoryEntry(state.redoStack, next),
            undoStack: state.undoStack.filter((e) => e !== next),
          })),
        }, { inFlight: unlockHistory })
        return
      }
      endGesture()
      const state = get()
      const { patch, notice } = restore(state, next)
      set({
        ...patch,
        redoStack: newRedo,
        undoStack: appendHistoryEntry(state.undoStack, captureDocument(state, next.focus ?? null)),
      })
      if (notice) useToastStore.getState().addToast("info", notice)
    },

    // ── Dirty tracking ──────────────────────────────────────────────────

    markSaved: (snapshot?: GraphSnapshot) => {
      set((state) => {
        const lastSavedSnapshot = snapshot === undefined
          ? captureDocument(state, null)
          : captureGraphSnapshot({
              ...snapshot,
              globalConstants: snapshot.globalConstants ?? state.globalConstants,
            })
        const currentPersistedFingerprint = persistedFingerprintOf(state)
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
      const state = get()
      const current = serializeSnapshot({
        nodes: documentNodes(state),
        edges: documentEdges(state),
        preamble: state.preamble,
        submodels: state.submodels,
        globalConstants: state.globalConstants,
      })
      if (state.lastSavedSnapshot === null) {
        return current !== EMPTY_SNAPSHOT
      }
      return current !== serializeSnapshot(state.lastSavedSnapshot)
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

/** The document's own nodes and edges: the pipeline's, also while a submodel is open. */
export function documentGraph(): { nodes: Node[]; edges: PipelineEdge[] } {
  const state = useGraphStore.getState()
  return { nodes: documentNodes(state), edges: documentEdges(state) }
}

let canvasSeen: { nodes: Node[]; edges: PipelineEdge[] } | null = null

/**
 * The canvas as one object, the same object while its nodes and edges are the
 * same: for readers that capture the graph and later ask whether it changed.
 */
export function canvasGraph(): { nodes: Node[]; edges: PipelineEdge[] } {
  const { nodes, edges } = useGraphStore.getState()
  if (canvasSeen === null || canvasSeen.nodes !== nodes || canvasSeen.edges !== edges) {
    canvasSeen = { nodes, edges }
  }
  return canvasSeen
}

// Convenience wrapper for suites that import the store statically (parity
// with resetOutputWriteStoreForTests); consumed by the undoAtomicity and
// fieldEditUndo suites. The consolidation test cannot use it — it reaches
// the store through a guarded dynamic import for importError diagnostics —
// and calls the store action directly instead.
export const resetGraphStoreForTests = () =>
  useGraphStore.getState().resetForTests()

/**
 * The graph as it stood, as an answer's part (`utils/answers.ts`): an edit, an Undo, a Redo, a
 * view change or a load moves it (`semanticGeneration`).
 */
export function editPart(): Part {
  return watch(useGraphStore, (state) => state.semanticGeneration)
}

/**
 * No edit made on the canvas since it was asked, as an answer's part: it holds while the canvas
 * is as saved, or while nothing has been done to it since (no change to the document, no Undo
 * or Redo, no history entry, as a move's first step records). A document the canvas takes
 * while it is as saved (the answer's own change arriving by live sync) is no edit made here;
 * a dirty canvas takes none, the sync banner holding it back.
 */
export function unsavedEditsPart(): Part {
  return watch(
    useGraphStore,
    (state) => (state.dirty
      ? { generation: state.semanticGeneration, undone: state.undoStack.at(-1), redone: state.redoStack.at(-1), nodes: state.nodes }
      : null),
    (asked, now) => now === null || (
      asked !== null && now.generation === asked.generation && now.undone === asked.undone
      && now.redone === asked.redone && sameNodePositions(asked.nodes, now.nodes)
    ),
  )
}

/** Whether the canvas holds the same nodes at the same places: a drag's steps, in a submodel too, move them. */
function sameNodePositions(asked: readonly Node[], now: readonly Node[]): boolean {
  return asked === now || (
    asked.length === now.length
    && now.every((node, index) => node.id === asked[index].id && positionsEqual(node.position, asked[index].position))
  )
}

export default useGraphStore
