import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react"
import type { Node, Edge } from "@xyflow/react"
import { MarkerType, useStore } from "@xyflow/react"
import type { TraceResult } from "../types/trace"
import type { PreviewSeedPlanEntry } from "../api/types"
import type { RuntimeNodeKey } from "../generated/api-contracts.generated"
import { NODE_TYPES } from "../utils/nodeTypes"
import { nodeData, type NodeStatus } from "../types/node"
import { traceCell } from "../api/client"
import { documentRequestScope, frameTargetPayload, preparePipelineRequest } from "../utils/requestGateway"
import { frameKey, type FrameTarget } from "../utils/nodeAddress"
import { keyText, placeAddress, type BoundaryCard } from "../utils/answerAddresses"
import type { DrilledOccurrenceIdentity } from "../utils/submodelRuntimeTarget"
import {
  GRAPH_EFFECTS_LITE_GRAPH_SIZE_LIMIT,
  shouldUseLiteGraphEffects,
} from "../utils/graphPerformance"
import useSettingsStore from "../stores/useSettingsStore"
import useDocumentStatusStore from "../stores/useDocumentStatusStore"
import useGraphStore from "../stores/useGraphStore"
import useNodeDataStore from "../stores/useNodeDataStore"
import { apiErrorCode, apiErrorMessage } from "../api/errors"
import { canonicalJson } from "../utils/canonicalJson"
import { ask, newest, type Part } from "../utils/answers"
import { useRequestKind } from "./useRequestKind"

export const TRACE_MOTION_GRAPH_SIZE_LIMIT = GRAPH_EFFECTS_LITE_GRAPH_SIZE_LIMIT
const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)"
const TRACE_MOTION_LITE_CLASS = "trace-motion-lite"
/** Delay before exceptional trace latency replaces the current node panel. */
export const TRACE_PROGRESS_DELAY_MS = 500

interface TracingParams {
  nodes: Node[]
  edges: Edge[]
  selectedNode: Node | null
  activeSubmodelIdentity: DrilledOccurrenceIdentity | null
  nodeStatuses: Record<string, NodeStatus>
  hoveredNodeId: string | null
  /** The node a trace card or derivation row points at, ringed while a trace shows. */
  traceFocusNodeId?: string | null
  /** The nodes the latest assistant change or undo touched, ringed while set. */
  changeFocusNodeIds?: readonly string[] | null
  refreshPreview?: (node: Node) => void
  /** The `seed_plan` of the preview shown for the selected node. */
  previewSeedPlan?: PreviewSeedPlanEntry[]
  /** The frame the preview shown for the selected node read its rows from, by address. */
  previewTarget?: FrameTarget
}

export type TraceRequestState =
  | { status: "idle" }
  | { status: "loading"; progressVisible: boolean }
  | { status: "ready" }
  | { status: "error"; message: string; detail: string; retryable: boolean }

export interface TracingReturn {
  traceResult: TraceResult | null
  tracedCell: { rowIndex: number; column: string } | null
  traceState: TraceRequestState
  handleCellClick: (rowIndex: number, column: string, rowValues?: Record<string, unknown>) => void
  clearTrace: () => void
  cancelTrace: () => void
  retryTrace: () => void
  nodesWithStatus: Node[]
  edgesWithTrace: Edge[]
  /**
   * The canvas node showing the node a trace names under `key`: the node itself, a
   * submodel's card, or a boundary card; null when the canvas shown does not hold it.
   */
  resolveTraceNodeId: (key: string) => string | null
}

export interface EdgeAdjacency {
  nodesByNodeId: Map<string, Set<string>>
  edgeIdsByNodeId: Map<string, Set<string>>
  endpointsByEdgeId: Map<string, { source: string; target: string }>
}

function addToSetMap(map: Map<string, Set<string>>, key: string, value: string) {
  const values = map.get(key)
  if (values) {
    values.add(value)
    return
  }
  map.set(key, new Set([value]))
}

export function buildEdgeAdjacency(edges: Edge[]): EdgeAdjacency {
  const nodesByNodeId = new Map<string, Set<string>>()
  const edgeIdsByNodeId = new Map<string, Set<string>>()
  const endpointsByEdgeId = new Map<string, { source: string; target: string }>()

  for (const edge of edges) {
    const { id, source, target } = edge
    endpointsByEdgeId.set(id, { source, target })
    addToSetMap(nodesByNodeId, source, source)
    addToSetMap(nodesByNodeId, source, target)
    addToSetMap(nodesByNodeId, target, target)
    addToSetMap(nodesByNodeId, target, source)
    addToSetMap(edgeIdsByNodeId, source, id)
    addToSetMap(edgeIdsByNodeId, target, id)
  }

  return { nodesByNodeId, edgeIdsByNodeId, endpointsByEdgeId }
}

function mergeClassName(existing: string | undefined, added: string | undefined): string | undefined {
  if (!added) return existing
  if (!existing) return added
  return existing.split(/\s+/).includes(added) ? existing : `${existing} ${added}`
}

function edgeWithVisuals(
  edge: Edge,
  endpoints: { source: string; target: string },
  visuals: Partial<Pick<Edge, "style" | "markerEnd" | "animated" | "className">>,
): Edge {
  const descriptors: Record<string, PropertyDescriptor> = Object.getOwnPropertyDescriptors(edge)
  delete descriptors.source
  delete descriptors.target
  delete descriptors.style
  delete descriptors.markerEnd
  delete descriptors.className
  if ("animated" in visuals) {
    delete descriptors.animated
  }
  return Object.defineProperties(
    {
      source: endpoints.source,
      target: endpoints.target,
      ...visuals,
      className: mergeClassName(edge.className, visuals.className),
    },
    descriptors,
  ) as Edge
}

type EdgeVisualState =
  | "trace-active"
  | "trace-active-lite"
  | "trace-dimmed"
  | "trace-dimmed-lite"
  | "hover-connected"
  | "hover-connected-no-marker"
  | "hover-dimmed"
  | "hover-dimmed-no-marker"
  | "zoomed-out"

interface CachedEdgeProjection {
  source: Edge
  visualState: EdgeVisualState
  projected: Edge
}

function projectEdgeWithCache(
  cache: Map<string, CachedEdgeProjection>,
  edge: Edge,
  endpoints: { source: string; target: string },
  visualState: EdgeVisualState,
  visuals: Partial<Pick<Edge, "style" | "markerEnd" | "animated" | "className">>,
): Edge {
  const cached = cache.get(edge.id)
  if (
    cached !== undefined &&
    cached.source === edge &&
    cached.visualState === visualState
  ) {
    return cached.projected
  }

  const projected = edgeWithVisuals(edge, endpoints, visuals)
  cache.set(edge.id, { source: edge, visualState, projected })
  return projected
}

function pruneEdgeProjectionCache(cache: Map<string, CachedEdgeProjection>, seenIds: Set<string>) {
  if (cache.size <= seenIds.size) return
  for (const id of cache.keys()) {
    if (!seenIds.has(id)) cache.delete(id)
  }
}

function subscribeToReducedMotion(onStoreChange: () => void): () => void {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return () => {}
  }
  const mediaQuery = window.matchMedia(REDUCED_MOTION_QUERY)
  mediaQuery.addEventListener("change", onStoreChange)
  return () => mediaQuery.removeEventListener("change", onStoreChange)
}

function getReducedMotionSnapshot(): boolean {
  return typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia(REDUCED_MOTION_QUERY).matches
}

function getServerReducedMotionSnapshot(): boolean {
  return false
}

function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(
    subscribeToReducedMotion,
    getReducedMotionSnapshot,
    getServerReducedMotionSnapshot,
  )
}

/** The server's raw error detail for the trace panel's "Technical details"
 *  disclosure: structured details are kept whole (keys sorted), not reduced to
 *  the message `apiErrorMessage` would show beside them. */
function technicalDetail(err: unknown): string {
  const detail = (err as { detail?: unknown; rawDetail?: unknown })?.rawDetail ?? (err as { detail?: unknown })?.detail
  if (typeof detail === "string") return detail
  if (detail !== undefined) return canonicalJson(detail)
  return apiErrorMessage(err)
}

const IDLE_TRACE_STATE: TraceRequestState = { status: "idle" }

/** The open occurrence's boundary cards, with the pipeline's nodes each reaches. */
function boundaryCards(nodes: readonly Node[]): BoundaryCard[] {
  const cards: BoundaryCard[] = []
  for (const node of nodes) {
    const data = nodeData(node)
    if (data.nodeType !== NODE_TYPES.SUBMODEL_PORT) continue
    const direction = data.portDirection
    if (direction !== "input" && direction !== "output") continue
    const externalNodeIds = Array.isArray(data.externalNodeIds)
      ? data.externalNodeIds.filter((id): id is string => typeof id === "string" && id.length > 0)
      : []
    cards.push({ id: node.id, direction, externalNodeIds })
  }
  return cards
}

export default function useTracing({
  nodes, edges, selectedNode,
  activeSubmodelIdentity,
  nodeStatuses,
  hoveredNodeId,
  traceFocusNodeId = null,
  changeFocusNodeIds = null,
  refreshPreview,
  previewSeedPlan,
  previewTarget,
}: TracingParams): TracingReturn {
  const rowLimit = useSettingsStore((s) => s.rowLimit)
  const activeSource = useSettingsStore((s) => s.activeSource)
  const structuralVersion = useGraphStore((s) => s.structuralVersion)
  const nodeDataEpoch = useNodeDataStore((s) => s.epoch)
  // Boost edge contrast at low zoom — only re-renders on threshold change
  const zoomedOut = useStore((s) => s.transform[2] < 0.45)
  const prefersReducedMotion = usePrefersReducedMotion()
  const traceMotionLite = prefersReducedMotion || shouldUseLiteGraphEffects(nodes.length, edges.length)
  const [storedTraceResult, setStoredTraceResult] = useState<TraceResult | null>(null)
  const [storedTracedCell, setStoredTracedCell] = useState<{ rowIndex: number; column: string } | null>(null)
  const [storedTraceState, setStoredTraceState] = useState<TraceRequestState>(IDLE_TRACE_STATE)
  const [storedSemanticContextToken, setStoredSemanticContextToken] = useState<object | null>(null)
  const nodesRef = useRef(nodes)
  useEffect(() => {
    nodesRef.current = nodes
  }, [nodes])
  // The newest trace wins: clearing, a new semantic context or another trace ends the one
  // asked before, and aborts it; so does unmounting.
  const traces = useRequestKind()
  const progressTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const retryRequest = useRef<{
    rowIndex: number
    column: string
    rowValues?: Record<string, unknown>
    semanticContextToken: object
  } | null>(null)
  const activeSemanticContextToken = useRef<object | null>(null)
  const activeRequestContext = useRef<string | null>(null)
  const edgeAdjacency = useMemo(() => buildEdgeAdjacency(edges), [edges])
  const [edgeProjectionCache] = useState<Map<string, CachedEdgeProjection>>(() => new Map())

  // clearTrace fully resets both traceResult and tracedCell, so trace
  // decorations (node highlights, edge styling) that depend on traceResult
  // are automatically cleaned up. This is called on node delete (via edge
  // handlers) ensuring deleted node IDs are never referenced by trace state.
  const clearTrace = useCallback(() => {
    traces.bump()
    if (progressTimer.current) clearTimeout(progressTimer.current)
    progressTimer.current = null
    activeSemanticContextToken.current = null
    activeRequestContext.current = null
    retryRequest.current = null
    setStoredSemanticContextToken(null)
    setStoredTraceResult(null)
    setStoredTracedCell(null)
    setStoredTraceState(IDLE_TRACE_STATE)
  }, [traces])

  // What a 409's recovery notice belongs to. The preview refresh it starts
  // replaces the seed plan and may raise the node-data epoch; the notice
  // asking for the row again must outlive exactly that refresh.
  const recoveryContext = canonicalJson({
    structuralVersion,
    activeSource,
    rowLimit,
    targetNodeId: selectedNode?.id ?? null,
  })
  const recoveryContextToken = useMemo<object>(
    () => ({ recoveryContext }),
    [recoveryContext],
  )
  const semanticContext = canonicalJson({
    recoveryContext,
    // The frame the explained preview read; a refresh replaces it, as it does the seed plan.
    frame: previewTarget ? frameKey(previewTarget) : null,
    // The generations the explained preview read, and the node-data epoch:
    // a snapshot published, refreshed, or cleared invalidates the evidence.
    seedPlan: (previewSeedPlan ?? []).map(
      (entry) => [entry.node_id, entry.identity_digest, entry.generation_id],
    ),
    nodeDataEpoch,
  })
  // The token is renewed on every context transition, including A → B → A,
  // so evidence invalidated by an intermediate change can never reappear.
  const semanticContextToken = useMemo<object>(
    () => ({ semanticContext }),
    [semanticContext],
  )

  // Visibility is derived during render, so a semantic change cannot paint
  // stale evidence while this effect performs the transport-side cleanup.
  const traceContextIsCurrent = (
    storedTraceState.status === "idle"
    || storedSemanticContextToken === semanticContextToken
    || storedSemanticContextToken === recoveryContextToken
  )
  const traceResult = traceContextIsCurrent ? storedTraceResult : null
  const tracedCell = traceContextIsCurrent ? storedTracedCell : null
  const traceState: TraceRequestState = traceContextIsCurrent
    ? storedTraceState
    : IDLE_TRACE_STATE

  useEffect(() => {
    if (
      activeSemanticContextToken.current !== null &&
      activeSemanticContextToken.current !== semanticContextToken
    ) {
      traces.bump()
      if (progressTimer.current) clearTimeout(progressTimer.current)
      progressTimer.current = null
      activeSemanticContextToken.current = null
      activeRequestContext.current = null
      retryRequest.current = null
    }
  }, [semanticContextToken, traces])

  useEffect(() => () => {
    if (progressTimer.current) clearTimeout(progressTimer.current)
  }, [])

  const startTrace = useCallback((rowIndex: number, column: string, rowValues?: Record<string, unknown>) => {
    if (!selectedNode) return
    const documentSnapshot = useDocumentStatusStore.getState()
    if (
      documentSnapshot.capabilities?.can_execute !== true ||
      !documentSnapshot.graphSynchronized
    ) return
    const documentStillCurrent = () => {
      const current = useDocumentStatusStore.getState()
      return current.sourceFile === documentSnapshot.sourceFile &&
        current.sourceRevision === documentSnapshot.sourceRevision &&
        current.loadStatus === documentSnapshot.loadStatus &&
        current.graphSynchronized &&
        current.capabilities?.can_execute === true
    }
    // The row is traced from the frame its preview read, so the trace reads what the
    // seed plan describes.
    if (!previewTarget) return
    const graph = preparePipelineRequest(documentRequestScope).graph
    const newestTrace = newest(traces)
    // The trace belongs to the document it was asked of; once that moves, it is cleared.
    const documentAsTraced: Part = {
      holds: documentStillCurrent,
      subscribe: (listener) => useDocumentStatusStore.subscribe(() => listener()),
    }
    const asked = ask([newestTrace, documentAsTraced])
    if (progressTimer.current) clearTimeout(progressTimer.current)
    const requestContext = canonicalJson({
      semanticContext,
      rowIndex,
      column,
      rowValues,
    })
    activeSemanticContextToken.current = semanticContextToken
    activeRequestContext.current = requestContext
    retryRequest.current = { rowIndex, column, rowValues, semanticContextToken }
    setStoredSemanticContextToken(semanticContextToken)
    setStoredTraceResult(null)
    setStoredTracedCell({ rowIndex, column })
    setStoredTraceState({ status: "loading", progressVisible: false })
    progressTimer.current = setTimeout(() => {
      asked.apply(() => setStoredTraceState({ status: "loading", progressVisible: true }))
    }, TRACE_PROGRESS_DELAY_MS)
    const stopProgress = () => {
      if (progressTimer.current) clearTimeout(progressTimer.current)
      progressTimer.current = null
    }
    void asked.answer((signal) => traceCell({
      graph,
      row_index: rowIndex,
      target: frameTargetPayload(previewTarget),
      column,
      row_limit: rowLimit,
      source: activeSource,
      row_values: rowValues,
      seed_plan: (previewSeedPlan ?? []).map((entry) => ({
        node_id: entry.node_id,
        port_label: null,
        identity_digest: entry.identity_digest,
        generation_id: entry.generation_id,
      })),
      signal,
    }), {
      ok: (data) => {
        stopProgress()
        if (data.status === "ok") {
          setStoredTraceResult(data.trace)
          setStoredTraceState({ status: "ready" })
        } else {
          setStoredTraceState({ status: "error", message: "Unable to trace this value.", detail: "The trace service returned an unsuccessful response.", retryable: true })
        }
      },
      failed: (err) => {
        stopProgress()
        if ((err as { status?: unknown })?.status === 409) {
          refreshPreview?.(selectedNode)
          retryRequest.current = null
          activeRequestContext.current = null
          setStoredTraceResult(null)
          setStoredTracedCell(null)
          const message = apiErrorCode(err) === "preview_seed_plan_expired"
            ? "The cached data this preview read has changed. The preview is being refreshed - select the row again when it is ready."
            : "This row changed before it could be traced. The preview is being refreshed - select the intended row again when it is ready."
          setStoredSemanticContextToken(recoveryContextToken)
          setStoredTraceState({ status: "error", message, detail: technicalDetail(err), retryable: false })
          return
        }
        setStoredTraceResult(null)
        setStoredTraceState({ status: "error", message: "Unable to trace this value. Check the details and try again.", detail: technicalDetail(err), retryable: true })
      },
      // A trace a newer one overtook says nothing; one whose document moved is cleared.
      stale: () => {
        if (!newestTrace.holds()) return
        stopProgress()
        retryRequest.current = null
        activeRequestContext.current = null
        setStoredTraceResult(null)
        setStoredTracedCell(null)
        setStoredTraceState(IDLE_TRACE_STATE)
      },
    })
  }, [selectedNode, rowLimit, activeSource, semanticContext, semanticContextToken, recoveryContextToken, refreshPreview, previewSeedPlan, previewTarget, traces])

  const handleCellClick = startTrace
  const cancelTrace = clearTrace
  const retryTrace = useCallback(() => {
    const request = retryRequest.current
    if (request && request.semanticContextToken === semanticContextToken) {
      startTrace(request.rowIndex, request.column, request.rowValues)
    }
  }, [semanticContextToken, startTrace])

  // A trace names its nodes by key and gives each its address: each is placed on the
  // canvas shown by address, and inside a submodel a node outside it on the boundary card
  // that reaches it.
  const openOccurrence = activeSubmodelIdentity?.instanceId ?? null
  const boundaries = useMemo(() => boundaryCards(nodes), [nodes])
  const resolveTraceId = useCallback(
    (key: string): string | null => {
      const address = traceResult?.node_addresses[key]
      return address ? placeAddress(address, openOccurrence, boundaries)?.id ?? null : null
    },
    [traceResult, openOccurrence, boundaries],
  )

  const allTraceNodeIds = useMemo(() => {
    if (!traceResult) return new Set<string>()
    const ids = new Set<string>()
    const add = (key: RuntimeNodeKey) => {
      const id = resolveTraceId(keyText(key))
      if (id !== null) ids.add(id)
    }
    for (const s of traceResult.steps) add(s.node_id)
    // A node the trace could not follow above a shared snapshot (its
    // diagnostic names the seeds) is still on the value's path — its data
    // reached the target through that snapshot — so it is not dimmed as
    // unrelated, though it carries no traced value.
    for (const omission of traceResult.omissions) {
      const diagnostic = traceResult.correlation_diagnostics[omission.diagnostic_index]
      if (diagnostic?.seed_node_ids.length) add(omission.node_id)
    }
    return ids
  }, [traceResult, resolveTraceId])

  const { traceValueMap, relevantNodeIds } = useMemo(() => {
    if (!traceResult) return { traceValueMap: new Map<string, unknown>(), relevantNodeIds: new Set<string>() }
    const valMap = new Map<string, unknown>()
    const relIds = new Set<string>()
    for (const s of traceResult.steps) {
      if (!s.column_relevant) continue
      const visibleId = resolveTraceId(keyText(s.node_id))
      if (visibleId === null) continue
      relIds.add(visibleId)
      // A column trace shows the traced value, else what the step computed
      // for it; a step that only carries the value's inputs shows none.
      const k = traceResult.column
        ? (s.output_values[traceResult.column] !== undefined ? traceResult.column : s.contributed_columns[0])
        : s.schema_diff.columns_added[0] || s.schema_diff.columns_modified[0]
      if (k) valMap.set(visibleId, s.output_values[k])
    }
    return { traceValueMap: valMap, relevantNodeIds: relIds }
  }, [traceResult, resolveTraceId])

  // Hover highlight: set of node IDs connected to the hovered node (including itself)
  const hoverConnectedIds = useMemo(() => {
    if (!hoveredNodeId) return null
    return edgeAdjacency.nodesByNodeId.get(hoveredNodeId) ?? new Set<string>([hoveredNodeId])
  }, [hoveredNodeId, edgeAdjacency])

  const hoverConnectedEdgeIds = useMemo(() => {
    if (!hoveredNodeId) return null
    return edgeAdjacency.edgeIdsByNodeId.get(hoveredNodeId) ?? new Set<string>()
  }, [hoveredNodeId, edgeAdjacency])

  const traceConnectedEdgeIds = useMemo(() => {
    if (!traceResult) return new Set<string>()
    const edgeIds = new Set<string>()
    for (const [edgeId, endpoints] of edgeAdjacency.endpointsByEdgeId) {
      if (allTraceNodeIds.has(endpoints.source) && allTraceNodeIds.has(endpoints.target)) {
        edgeIds.add(edgeId)
      }
    }
    return edgeIds
  }, [traceResult, allTraceNodeIds, edgeAdjacency])

  // Per-node projection cache keyed by the source `Node` reference.
  // The cache is held via `useState` lazy-init (never reassigned) so it
  // has a stable identity across renders of this hook instance but does
  // NOT leak across instances — each useTracing call gets its own Map.
  // We validate each entry against the current computed flags; if the
  // projection would be identical we return the cached Node, giving
  // React Flow's diff a reference-equal object to skip.
  //
  // Invalidation shape: an entry is valid iff
  //   (a) the source node reference is unchanged AND
  //   (b) every computed flag (_status, _traceActive, _traceDimmed,
  //       _hoverDimmed, _traceValue, motion mode) matches what the
  //       current render would produce.
  // Nodes no longer in the input list are pruned on each pass so the
  // Map can't grow without bound.
  interface CachedProjection {
    source: Node
    status: NodeStatus | undefined
    traceActive: boolean
    traceDimmed: boolean
    hoverDimmed: boolean
    traceFocused: boolean
    changeFocused: boolean
    traceValue: unknown
    traceMotionLite: boolean
    projected: Node
  }
  const [projectionCache] = useState<Map<string, CachedProjection>>(() => new Map())

  const nodesWithStatus = useMemo(() => {
    const hasTrace = traceResult !== null
    const focusedId = traceFocusNodeId === null ? null : resolveTraceId(traceFocusNodeId)
    const changeFocusedIds = new Set(changeFocusNodeIds ?? [])
    const seenIds = new Set<string>()
    const next: Node[] = new Array(nodes.length)

    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i]
      seenIds.add(n.id)
      const status = nodeStatuses[n.id]
      const traceActive = hasTrace && relevantNodeIds.has(n.id)
      const inTrace = allTraceNodeIds.has(n.id)
      const traceDimmed = hasTrace && !inTrace
      // Hover dim: when hovering a node and no trace is active, dim unconnected nodes
      const hoverDimmed = !hasTrace && hoverConnectedIds !== null && !hoverConnectedIds.has(n.id)
      const traceFocused = hasTrace && n.id === focusedId
      const changeFocused = changeFocusedIds.has(n.id)
      const traceValue = traceValueMap.get(n.id)

      const cached = projectionCache.get(n.id)
      if (
        cached !== undefined &&
        cached.source === n &&
        cached.status === status &&
        cached.traceActive === traceActive &&
        cached.traceDimmed === traceDimmed &&
        cached.hoverDimmed === hoverDimmed &&
        cached.traceFocused === traceFocused &&
        cached.changeFocused === changeFocused &&
        cached.traceValue === traceValue &&
        cached.traceMotionLite === traceMotionLite
      ) {
        next[i] = cached.projected
        continue
      }

      const projected: Node = {
        ...n,
        data: {
          ...n.data,
          _status: status,
          _traceActive: traceActive,
          _traceDimmed: traceDimmed,
          _hoverDimmed: hoverDimmed,
          _traceFocused: traceFocused,
          _changeFocused: changeFocused,
          _traceValue: traceValue,
          _traceMotionDisabled: traceMotionLite,
        },
        className: mergeClassName(n.className, traceMotionLite ? TRACE_MOTION_LITE_CLASS : undefined),
        style: {
          ...(n.style || {}),
          transition: traceMotionLite ? "none" : 'opacity 0.2s ease',
        },
      }
      projectionCache.set(n.id, {
        source: n,
        status,
        traceActive,
        traceDimmed,
        hoverDimmed,
        traceFocused,
        changeFocused,
        traceValue,
        traceMotionLite,
        projected,
      })
      next[i] = projected
    }

    // Prune cache entries for removed nodes so the Map can't grow without
    // bound over long sessions.
    if (projectionCache.size > seenIds.size) {
      for (const id of projectionCache.keys()) {
        if (!seenIds.has(id)) projectionCache.delete(id)
      }
    }

    return next
  }, [nodes, nodeStatuses, traceResult, allTraceNodeIds, relevantNodeIds, traceValueMap, hoverConnectedIds, traceFocusNodeId, changeFocusNodeIds, resolveTraceId, projectionCache, traceMotionLite])

  const edgesWithTrace = useMemo(() => {
    // Trace styling takes priority over hover styling
    if (traceResult) {
      const seenIds = new Set<string>()
      const next = edges.map((e) => {
        seenIds.add(e.id)
        const endpoints = edgeAdjacency.endpointsByEdgeId.get(e.id)!
        if (traceConnectedEdgeIds.has(e.id)) {
          return projectEdgeWithCache(
            edgeProjectionCache,
            e,
            endpoints,
            traceMotionLite ? "trace-active-lite" : "trace-active",
            {
              style: traceMotionLite
                ? { stroke: 'var(--accent)', strokeWidth: 2.5, filter: 'none' }
                : { stroke: 'var(--accent)', strokeWidth: 2.5, filter: 'drop-shadow(0 0 4px var(--accent))' },
              markerEnd: { type: MarkerType.ArrowClosed as const, width: 14, height: 14, color: 'var(--accent)' },
              animated: !traceMotionLite,
              className: traceMotionLite ? TRACE_MOTION_LITE_CLASS : undefined,
            },
          )
        }
        return projectEdgeWithCache(
          edgeProjectionCache,
          e,
          endpoints,
          traceMotionLite ? "trace-dimmed-lite" : "trace-dimmed",
          {
            style: {
              stroke: 'rgba(255,255,255,.05)',
              strokeWidth: 1,
              ...(traceMotionLite ? { filter: 'none' } : {}),
            },
            markerEnd: { type: MarkerType.ArrowClosed as const, width: 14, height: 14, color: 'rgba(255,255,255,.05)' },
            ...(traceMotionLite ? { animated: false } : {}),
            className: traceMotionLite ? TRACE_MOTION_LITE_CLASS : undefined,
          },
        )
      })
      pruneEdgeProjectionCache(edgeProjectionCache, seenIds)
      return next
    }

    // Hover highlighting: when hovering a node, brighten connected edges, dim others
    if (hoveredNodeId) {
      const hoveredNode = nodes.find((node) => node.id === hoveredNodeId)
      const suppressHoverMarkers = hoveredNode
        ? nodeData(hoveredNode).nodeType === NODE_TYPES.EDGE_JOIN
        : false
      const seenIds = new Set<string>()
      const next = edges.map((e) => {
        seenIds.add(e.id)
        const endpoints = edgeAdjacency.endpointsByEdgeId.get(e.id)!
        if (hoverConnectedEdgeIds?.has(e.id)) {
          return projectEdgeWithCache(
            edgeProjectionCache,
            e,
            endpoints,
            suppressHoverMarkers ? "hover-connected-no-marker" : "hover-connected",
            {
              style: { stroke: 'rgba(255,255,255,.55)', strokeWidth: 2 },
              ...(suppressHoverMarkers ? {} : {
                markerEnd: { type: MarkerType.ArrowClosed as const, width: 14, height: 14, color: 'rgba(255,255,255,.55)' },
              }),
            },
          )
        }
        return projectEdgeWithCache(
          edgeProjectionCache,
          e,
          endpoints,
          suppressHoverMarkers ? "hover-dimmed-no-marker" : "hover-dimmed",
          {
            style: { stroke: 'rgba(255,255,255,.06)', strokeWidth: 1 },
            ...(suppressHoverMarkers ? {} : {
              markerEnd: { type: MarkerType.ArrowClosed as const, width: 14, height: 14, color: 'rgba(255,255,255,.06)' },
            }),
          },
        )
      })
      pruneEdgeProjectionCache(edgeProjectionCache, seenIds)
      return next
    }

    // At low zoom, boost edge contrast so connections remain visible
    if (zoomedOut) {
      const seenIds = new Set<string>()
      const next = edges.map((e) => {
        seenIds.add(e.id)
        return projectEdgeWithCache(
          edgeProjectionCache,
          e,
          edgeAdjacency.endpointsByEdgeId.get(e.id)!,
          "zoomed-out",
          {
            style: { stroke: 'rgba(255,255,255,.38)', strokeWidth: 2 },
            markerEnd: { type: MarkerType.ArrowClosed as const, width: 16, height: 16, color: 'rgba(255,255,255,.38)' },
          },
        )
      })
      pruneEdgeProjectionCache(edgeProjectionCache, seenIds)
      return next
    }

    if (edgeProjectionCache.size > 0) edgeProjectionCache.clear()
    return edges
  }, [edges, nodes, edgeAdjacency, edgeProjectionCache, traceResult, traceConnectedEdgeIds, hoveredNodeId, hoverConnectedEdgeIds, zoomedOut, traceMotionLite])

  return {
    traceResult, tracedCell, traceState,
    handleCellClick, clearTrace, cancelTrace, retryTrace,
    nodesWithStatus, edgesWithTrace,
    resolveTraceNodeId: resolveTraceId,
  }
}
