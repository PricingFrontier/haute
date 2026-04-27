import { useCallback, useMemo, useState, useSyncExternalStore } from "react"
import type { Node, Edge } from "@xyflow/react"
import { MarkerType, useStore } from "@xyflow/react"
import type { TraceResult } from "../types/trace"
import { NODE_TYPES } from "../utils/nodeTypes"
import { nodeData } from "../types/node"
import { traceCell } from "../api/client"
import { resolveGraphFromRefs } from "../utils/buildGraph"
import {
  GRAPH_EFFECTS_LITE_GRAPH_SIZE_LIMIT,
  shouldUseLiteGraphEffects,
} from "../utils/graphPerformance"
import useToastStore from "../stores/useToastStore"
import useSettingsStore from "../stores/useSettingsStore"

export const TRACE_MOTION_GRAPH_SIZE_LIMIT = GRAPH_EFFECTS_LITE_GRAPH_SIZE_LIMIT
const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)"
const TRACE_MOTION_LITE_CLASS = "trace-motion-lite"

interface TracingParams {
  nodes: Node[]
  edges: Edge[]
  selectedNode: Node | null
  graphRef: React.MutableRefObject<{ nodes: Node[]; edges: Edge[] }>
  parentGraphRef: React.MutableRefObject<{ nodes: Node[]; edges: Edge[]; submodels: Record<string, unknown> } | null>
  submodelsRef: React.MutableRefObject<Record<string, unknown>>
  preambleRef: React.MutableRefObject<string>
  nodeStatuses: Record<string, "ok" | "error" | "running">
  hoveredNodeId: string | null
}

export interface TracingReturn {
  traceResult: TraceResult | null
  tracedCell: { rowIndex: number; column: string } | null
  handleCellClick: (rowIndex: number, column: string, rowValues?: Record<string, unknown>) => void
  clearTrace: () => void
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

type EdgeVisualState = "trace-active" | "trace-active-lite" | "trace-dimmed" | "trace-dimmed-lite" | "hover-connected" | "hover-dimmed" | "zoomed-out"

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

export default function useTracing({
  nodes, edges, selectedNode,
  graphRef, parentGraphRef, submodelsRef,
  preambleRef,
  nodeStatuses,
  hoveredNodeId,
}: TracingParams): TracingReturn {
  const addToast = useToastStore((s) => s.addToast)
  const rowLimit = useSettingsStore((s) => s.rowLimit)
  const activeSource = useSettingsStore((s) => s.activeSource)
  // Boost edge contrast at low zoom — only re-renders on threshold change
  const zoomedOut = useStore((s) => s.transform[2] < 0.45)
  const prefersReducedMotion = usePrefersReducedMotion()
  const traceMotionLite = prefersReducedMotion || shouldUseLiteGraphEffects(nodes.length, edges.length)
  const [traceResult, setTraceResult] = useState<TraceResult | null>(null)
  const [tracedCell, setTracedCell] = useState<{ rowIndex: number; column: string } | null>(null)
  const edgeAdjacency = useMemo(() => buildEdgeAdjacency(edges), [edges])
  const [edgeProjectionCache] = useState<Map<string, CachedEdgeProjection>>(() => new Map())

  // clearTrace fully resets both traceResult and tracedCell, so trace
  // decorations (node highlights, edge styling) that depend on traceResult
  // are automatically cleaned up. This is called on node delete (via edge
  // handlers) ensuring deleted node IDs are never referenced by trace state.
  const clearTrace = useCallback(() => {
    setTraceResult(null)
    setTracedCell(null)
  }, [])

  const handleCellClick = useCallback((rowIndex: number, column: string, rowValues?: Record<string, unknown>) => {
    if (!selectedNode) return
    const graph = resolveGraphFromRefs(graphRef, parentGraphRef, submodelsRef, preambleRef)
    setTracedCell({ rowIndex, column })
    traceCell({ graph, row_index: rowIndex, target_node_id: selectedNode.id, column, row_limit: rowLimit, source: activeSource, row_values: rowValues })
      .then((data) => {
        if (data.status === "ok" && data.trace) {
          setTraceResult(data.trace as TraceResult)
        } else {
          addToast("error", data.error || "Trace failed")
          clearTrace()
        }
      })
      .catch((err) => {
        const detail = (err as { detail?: unknown })?.detail
        const message =
          typeof detail === "string"
            ? detail
            : err instanceof Error
              ? err.message
              : String(err)
        addToast("error", `Trace error: ${message}`)
        clearTrace()
      })
  }, [selectedNode, graphRef, parentGraphRef, submodelsRef, preambleRef, rowLimit, activeSource, addToast, clearTrace])

  // Map child node IDs → submodel placeholder node IDs
  const childToSubmodelId = useMemo(() => {
    const map = new Map<string, string>()
    for (const n of nodes) {
      const d = nodeData(n)
      if (d.nodeType === NODE_TYPES.SUBMODEL) {
        const cfg = d.config || {}
        const childIds: string[] = (cfg.childNodeIds as string[]) || []
        for (const cid of childIds) {
          map.set(cid, n.id)
        }
      }
    }
    return map
  }, [nodes])

  // Map external parent node IDs → port node IDs (separate in/out to avoid collision)
  const parentToPortId = useMemo(() => {
    const inMap = new Map<string, string>()
    const outMap = new Map<string, string>()
    for (const n of nodes) {
      if (n.id.startsWith("port_in__")) {
        inMap.set(n.id.replace("port_in__", ""), n.id)
      } else if (n.id.startsWith("port_out__")) {
        outMap.set(n.id.replace("port_out__", ""), n.id)
      }
    }
    return { inMap, outMap }
  }, [nodes])

  const resolveTraceId = useCallback(
    (id: string) =>
      childToSubmodelId.get(id) || parentToPortId.inMap.get(id) || parentToPortId.outMap.get(id) || id,
    [childToSubmodelId, parentToPortId],
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
      const seenIds = new Set<string>()
      const next = edges.map((e) => {
        seenIds.add(e.id)
        const endpoints = edgeAdjacency.endpointsByEdgeId.get(e.id)!
        if (hoverConnectedEdgeIds?.has(e.id)) {
          return projectEdgeWithCache(
            edgeProjectionCache,
            e,
            endpoints,
            "hover-connected",
            {
              style: { stroke: 'rgba(255,255,255,.55)', strokeWidth: 2 },
              markerEnd: { type: MarkerType.ArrowClosed as const, width: 14, height: 14, color: 'rgba(255,255,255,.55)' },
            },
          )
        }
        return projectEdgeWithCache(
          edgeProjectionCache,
          e,
          endpoints,
          "hover-dimmed",
          {
            style: { stroke: 'rgba(255,255,255,.06)', strokeWidth: 1 },
            markerEnd: { type: MarkerType.ArrowClosed as const, width: 14, height: 14, color: 'rgba(255,255,255,.06)' },
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
  }, [edges, edgeAdjacency, edgeProjectionCache, traceResult, traceConnectedEdgeIds, hoveredNodeId, hoverConnectedEdgeIds, zoomedOut, traceMotionLite])

  return {
    traceResult, tracedCell,
    handleCellClick, clearTrace,
    nodesWithStatus, edgesWithTrace,
  }
}
