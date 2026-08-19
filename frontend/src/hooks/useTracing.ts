import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react"
import type { Node, Edge } from "@xyflow/react"
import { MarkerType, useStore } from "@xyflow/react"
import type { TraceResult } from "../types/trace"
import { NODE_TYPES } from "../utils/nodeTypes"
import {
  isSubmodelDefinition,
  isSubmodelInstanceConfig,
  nodeData,
} from "../types/node"
import { traceCell } from "../api/client"
import { resolveGraphFromRefs } from "../utils/buildGraph"
import {
  qualifiedRuntimeNodeId,
  runtimeNodeIdForVisibleNode,
  type DrilledOccurrenceIdentity,
} from "../utils/submodelRuntimeTarget"
import {
  GRAPH_EFFECTS_LITE_GRAPH_SIZE_LIMIT,
  shouldUseLiteGraphEffects,
} from "../utils/graphPerformance"
import useSettingsStore from "../stores/useSettingsStore"
import useDocumentStatusStore from "../stores/useDocumentStatusStore"
import useGraphStore from "../stores/useGraphStore"

export const TRACE_MOTION_GRAPH_SIZE_LIMIT = GRAPH_EFFECTS_LITE_GRAPH_SIZE_LIMIT
const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)"
const TRACE_MOTION_LITE_CLASS = "trace-motion-lite"
/** Delay before exceptional trace latency replaces the current node panel. */
export const TRACE_PROGRESS_DELAY_MS = 500

interface TracingParams {
  nodes: Node[]
  edges: Edge[]
  submodels: Record<string, unknown>
  selectedNode: Node | null
  graphRef: React.MutableRefObject<{ nodes: Node[]; edges: Edge[] }>
  parentGraphRef: React.MutableRefObject<{ nodes: Node[]; edges: Edge[]; submodels: Record<string, unknown> } | null>
  activeSubmodelIdentity: DrilledOccurrenceIdentity | null
  submodelsRef: React.MutableRefObject<Record<string, unknown>>
  preambleRef: React.MutableRefObject<string>
  nodeStatuses: Record<string, "ok" | "error" | "running">
  hoveredNodeId: string | null
  refreshPreview?: (node: Node) => void
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

function stableValue(value: unknown): string {
  if (value === undefined) return "undefined"
  if (typeof value === "bigint") return `${value}n`
  if (value === null || typeof value !== "object") return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(stableValue).join(",")}]`
  return `{${Object.entries(value as Record<string, unknown>).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([key, item]) => `${JSON.stringify(key)}:${stableValue(item)}`).join(",")}}`
}

function errorDetail(err: unknown): string {
  const detail = (err as { detail?: unknown; rawDetail?: unknown })?.rawDetail ?? (err as { detail?: unknown })?.detail
  if (typeof detail === "string") return detail
  if (detail !== undefined) return stableValue(detail)
  return err instanceof Error ? err.message : String(err)
}

export default function useTracing({
  nodes, edges, submodels, selectedNode,
  graphRef, parentGraphRef, activeSubmodelIdentity, submodelsRef,
  preambleRef,
  nodeStatuses,
  hoveredNodeId,
  refreshPreview,
}: TracingParams): TracingReturn {
  const rowLimit = useSettingsStore((s) => s.rowLimit)
  const streamingChunkSize = useSettingsStore((s) => s.streamingChunkSize)
  const activeSource = useSettingsStore((s) => s.activeSource)
  const structuralVersion = useGraphStore((s) => s.structuralVersion)
  // Boost edge contrast at low zoom — only re-renders on threshold change
  const zoomedOut = useStore((s) => s.transform[2] < 0.45)
  const prefersReducedMotion = usePrefersReducedMotion()
  const traceMotionLite = prefersReducedMotion || shouldUseLiteGraphEffects(nodes.length, edges.length)
  const [storedTraceResult, setStoredTraceResult] = useState<TraceResult | null>(null)
  const [storedTracedCell, setStoredTracedCell] = useState<{ rowIndex: number; column: string } | null>(null)
  const [storedTraceState, setStoredTraceState] = useState<TraceRequestState>({ status: "idle" })
  const [storedSemanticContextToken, setStoredSemanticContextToken] = useState<object | null>(null)
  const traceRequestSeq = useRef(0)
  const traceAbort = useRef<AbortController | null>(null)
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
    traceRequestSeq.current += 1
    traceAbort.current?.abort()
    traceAbort.current = null
    if (progressTimer.current) clearTimeout(progressTimer.current)
    progressTimer.current = null
    activeSemanticContextToken.current = null
    activeRequestContext.current = null
    retryRequest.current = null
    setStoredSemanticContextToken(null)
    setStoredTraceResult(null)
    setStoredTracedCell(null)
    setStoredTraceState({ status: "idle" })
  }, [])

  const semanticContext = stableValue({
    structuralVersion,
    activeSource,
    rowLimit,
    streamingChunkSize,
    targetNodeId: selectedNode?.id ?? null,
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
  )
  const traceResult = traceContextIsCurrent ? storedTraceResult : null
  const tracedCell = traceContextIsCurrent ? storedTracedCell : null
  const traceState: TraceRequestState = traceContextIsCurrent
    ? storedTraceState
    : { status: "idle" }

  useEffect(() => {
    if (
      activeSemanticContextToken.current !== null &&
      activeSemanticContextToken.current !== semanticContextToken
    ) {
      traceRequestSeq.current += 1
      traceAbort.current?.abort()
      traceAbort.current = null
      if (progressTimer.current) clearTimeout(progressTimer.current)
      progressTimer.current = null
      activeSemanticContextToken.current = null
      activeRequestContext.current = null
      retryRequest.current = null
    }
  }, [semanticContextToken])

  useEffect(() => () => {
    traceAbort.current?.abort()
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
    const graph = resolveGraphFromRefs(graphRef, parentGraphRef, submodelsRef, preambleRef)
    const requestId = traceRequestSeq.current + 1
    traceRequestSeq.current = requestId
    traceAbort.current?.abort()
    if (progressTimer.current) clearTimeout(progressTimer.current)
    const controller = new AbortController()
    traceAbort.current = controller
    const requestContext = stableValue({
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
      if (
        traceRequestSeq.current === requestId &&
        activeRequestContext.current === requestContext &&
        documentStillCurrent()
      ) {
        setStoredTraceState({ status: "loading", progressVisible: true })
      }
    }, TRACE_PROGRESS_DELAY_MS)
    traceCell({
      graph,
      row_index: rowIndex,
      target_node_id: runtimeNodeIdForVisibleNode(
        nodes,
        selectedNode.id,
        activeSubmodelIdentity,
      ),
      column,
      row_limit: rowLimit,
      source: activeSource,
      row_values: rowValues,
      streamingChunkSize,
      signal: controller.signal,
    })
      .then((data) => {
        if (
          traceRequestSeq.current !== requestId ||
          activeRequestContext.current !== requestContext
        ) return
        if (!documentStillCurrent()) {
          if (progressTimer.current) clearTimeout(progressTimer.current)
          progressTimer.current = null
          retryRequest.current = null
          activeRequestContext.current = null
          setStoredTraceResult(null)
          setStoredTracedCell(null)
          setStoredTraceState({ status: "idle" })
          return
        }
        if (progressTimer.current) clearTimeout(progressTimer.current)
        progressTimer.current = null
        if (data.status === "ok") {
          setStoredTraceResult(data.trace)
          setStoredTraceState({ status: "ready" })
        } else {
          setStoredTraceState({ status: "error", message: "Unable to trace this value.", detail: "The trace service returned an unsuccessful response.", retryable: true })
        }
      })
      .catch((err) => {
        if (traceRequestSeq.current !== requestId) return
        if (controller.signal.aborted) return
        if (!documentStillCurrent()) {
          if (progressTimer.current) clearTimeout(progressTimer.current)
          progressTimer.current = null
          retryRequest.current = null
          activeRequestContext.current = null
          setStoredTraceResult(null)
          setStoredTracedCell(null)
          setStoredTraceState({ status: "idle" })
          return
        }
        if (progressTimer.current) clearTimeout(progressTimer.current)
        progressTimer.current = null
        if ((err as { status?: unknown })?.status === 409) {
          refreshPreview?.(selectedNode)
          retryRequest.current = null
          activeRequestContext.current = null
          setStoredTraceResult(null)
          setStoredTracedCell(null)
          setStoredTraceState({ status: "error", message: "This row changed before it could be traced. The preview is being refreshed — select the intended row again when it is ready.", detail: errorDetail(err), retryable: false })
          return
        }
        setStoredTraceResult(null)
        setStoredTraceState({ status: "error", message: "Unable to trace this value. Check the details and try again.", detail: errorDetail(err), retryable: true })
      })
      .finally(() => {
        if (traceRequestSeq.current === requestId) {
          traceAbort.current = null
        }
      })
  }, [selectedNode, nodes, graphRef, parentGraphRef, activeSubmodelIdentity, submodelsRef, preambleRef, rowLimit, streamingChunkSize, activeSource, semanticContext, semanticContextToken, refreshPreview])

  const handleCellClick = startTrace
  const cancelTrace = clearTrace
  const retryTrace = useCallback(() => {
    const request = retryRequest.current
    if (request && request.semanticContextToken === semanticContextToken) {
      startTrace(request.rowIndex, request.column, request.rowValues)
    }
  }, [semanticContextToken, startTrace])

  // Runtime child IDs are qualified by the explicit occurrence identity.
  // Collapse them onto the occurrence in the parent view, or onto the actual
  // child node while drilled into that occurrence.
  const childToSubmodelId = useMemo(() => {
    const map = new Map<string, string>()
    const visibleChildNodes: Node[] = []
    const drilledIdentity = activeSubmodelIdentity

    for (const node of nodes) {
      const data = nodeData(node)
      if (data.nodeType === NODE_TYPES.SUBMODEL_PORT) continue

      visibleChildNodes.push(node)
      if (data.nodeType !== NODE_TYPES.SUBMODEL) continue
      if (!isSubmodelInstanceConfig(data.config)) {
        throw new Error(
          "Submodel instance " + node.id + " has malformed canonical identity config",
        )
      }
      const definition = submodels[data.config.definitionId]
      if (!isSubmodelDefinition(definition, data.config.definitionId)) {
        throw new Error(
          "Submodel instance " + node.id + " references missing or malformed definition "
          + data.config.definitionId,
        )
      }
      for (const child of definition.graph.nodes) {
        map.set(qualifiedRuntimeNodeId(node.id, child.id), node.id)
      }
    }

    if (drilledIdentity) {
      const definition = submodels[drilledIdentity.definitionId]
      if (!isSubmodelDefinition(definition, drilledIdentity.definitionId)) {
        throw new Error(
          "Drilled submodel instance " + drilledIdentity.instanceId
          + " references missing or malformed definition "
          + drilledIdentity.definitionId,
        )
      }
      for (const child of visibleChildNodes) {
        map.set(
          runtimeNodeIdForVisibleNode(nodes, child.id, drilledIdentity),
          child.id,
        )
      }
    }

    return map
  }, [nodes, submodels, activeSubmodelIdentity])
  // Map external parent node IDs to the composite boundary card that represents
  // them, keeping Input and Output separate to avoid collisions.
  const parentToBoundaryId = useMemo(() => {
    const inputMap = new Map<string, string>()
    const outputMap = new Map<string, string>()
    for (const node of nodes) {
      const data = nodeData(node)
      if (data.nodeType !== NODE_TYPES.SUBMODEL_PORT) continue
      const externalNodeIds = data.externalNodeIds
      if (!Array.isArray(externalNodeIds)) continue
      const targetMap =
        data.portDirection === "input"
          ? inputMap
          : data.portDirection === "output"
            ? outputMap
            : null
      if (!targetMap) continue
      for (const externalNodeId of externalNodeIds) {
        if (
          typeof externalNodeId === "string" &&
          externalNodeId.length > 0 &&
          !targetMap.has(externalNodeId)
        ) {
          targetMap.set(externalNodeId, node.id)
        }
      }
    }
    return { inputMap, outputMap }
  }, [nodes])

  const resolveTraceId = useCallback(
    (id: string) =>
      childToSubmodelId.get(id) ||
      parentToBoundaryId.inputMap.get(id) ||
      parentToBoundaryId.outputMap.get(id) ||
      id,
    [childToSubmodelId, parentToBoundaryId],
  )

  const allTraceNodeIds = useMemo(() => {
    if (!traceResult) return new Set<string>()
    const ids = new Set<string>()
    for (const s of traceResult.steps) {
      ids.add(resolveTraceId(s.node_id))
    }
    return ids
  }, [traceResult, resolveTraceId])

  const { traceValueMap, relevantNodeIds } = useMemo(() => {
    if (!traceResult) return { traceValueMap: new Map<string, unknown>(), relevantNodeIds: new Set<string>() }
    const valMap = new Map<string, unknown>()
    const relIds = new Set<string>()
    for (const s of traceResult.steps) {
      if (!s.column_relevant) continue
      const visibleId = resolveTraceId(s.node_id)
      relIds.add(visibleId)
      if (traceResult.column && s.output_values[traceResult.column] !== undefined) {
        valMap.set(visibleId, s.output_values[traceResult.column])
      } else {
        const k = s.schema_diff.columns_added[0] || s.schema_diff.columns_modified[0]
        if (k) valMap.set(visibleId, s.output_values[k])
      }
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
    status: "ok" | "error" | "running" | undefined
    traceActive: boolean
    traceDimmed: boolean
    hoverDimmed: boolean
    traceValue: unknown
    traceMotionLite: boolean
    projected: Node
  }
  const [projectionCache] = useState<Map<string, CachedProjection>>(() => new Map())

  const nodesWithStatus = useMemo(() => {
    const hasTrace = traceResult !== null
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
      const traceValue = traceValueMap.get(n.id)

      const cached = projectionCache.get(n.id)
      if (
        cached !== undefined &&
        cached.source === n &&
        cached.status === status &&
        cached.traceActive === traceActive &&
        cached.traceDimmed === traceDimmed &&
        cached.hoverDimmed === hoverDimmed &&
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
  }, [nodes, nodeStatuses, traceResult, allTraceNodeIds, relevantNodeIds, traceValueMap, hoverConnectedIds, projectionCache, traceMotionLite])

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
  }
}
