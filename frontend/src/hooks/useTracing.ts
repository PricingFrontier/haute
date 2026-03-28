import { useCallback, useMemo, useState } from "react"
import type { Node, Edge } from "@xyflow/react"
import { MarkerType, useStore } from "@xyflow/react"
import type { TraceResult } from "../types/trace"
import { NODE_TYPES } from "../utils/nodeTypes"
import { nodeData } from "../types/node"
import { traceCell } from "../api/client"
import { resolveGraphFromRefs } from "../utils/buildGraph"
import useToastStore from "../stores/useToastStore"
import useSettingsStore from "../stores/useSettingsStore"

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
  const [traceResult, setTraceResult] = useState<TraceResult | null>(null)
  const [tracedCell, setTracedCell] = useState<{ rowIndex: number; column: string } | null>(null)

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
          setTraceResult(data.trace as unknown as TraceResult)
        } else {
          addToast("error", data.error || "Trace failed")
          clearTrace()
        }
      })
      .catch((err) => {
        addToast("error", `Trace error: ${err.message}`)
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
    const ids = new Set<string>([hoveredNodeId])
    for (const e of edges) {
      if (e.source === hoveredNodeId) ids.add(e.target)
      if (e.target === hoveredNodeId) ids.add(e.source)
    }
    return ids
  }, [hoveredNodeId, edges])

  const nodesWithStatus = useMemo(() => {
    const hasTrace = traceResult !== null
    return nodes.map((n) => {
      const status = nodeStatuses[n.id]
      const inTrace = allTraceNodeIds.has(n.id)
      const traceDimmed = hasTrace && !inTrace
      // Hover dim: when hovering a node and no trace is active, dim unconnected nodes
      const hoverDimmed = !hasTrace && hoverConnectedIds !== null && !hoverConnectedIds.has(n.id)
      return {
        ...n,
        data: {
          ...n.data,
          _status: status,
          _traceActive: hasTrace && relevantNodeIds.has(n.id),
          _traceDimmed: traceDimmed,
          _hoverDimmed: hoverDimmed,
          _traceValue: traceValueMap.get(n.id),
        },
        style: {
          ...(n.style || {}),
          transition: 'opacity 0.2s ease',
        },
      }
    })
  }, [nodes, nodeStatuses, traceResult, allTraceNodeIds, relevantNodeIds, traceValueMap, hoverConnectedIds])

  const edgesWithTrace = useMemo(() => {
    // Trace styling takes priority over hover styling
    if (traceResult) {
      return edges.map((e) => {
        const srcInTrace = allTraceNodeIds.has(e.source)
        const tgtInTrace = allTraceNodeIds.has(e.target)
        if (srcInTrace && tgtInTrace) {
          return {
            ...e,
            style: { stroke: 'var(--accent)', strokeWidth: 2.5, filter: 'drop-shadow(0 0 4px var(--accent))' },
            markerEnd: { type: MarkerType.ArrowClosed as const, width: 14, height: 14, color: 'var(--accent)' },
            animated: true,
          }
        }
        return {
          ...e,
          style: { stroke: 'rgba(255,255,255,.05)', strokeWidth: 1 },
          markerEnd: { type: MarkerType.ArrowClosed as const, width: 14, height: 14, color: 'rgba(255,255,255,.05)' },
        }
      })
    }

    // Hover highlighting: when hovering a node, brighten connected edges, dim others
    if (hoveredNodeId) {
      return edges.map((e) => {
        const connected = e.source === hoveredNodeId || e.target === hoveredNodeId
        if (connected) {
          return {
            ...e,
            style: { stroke: 'rgba(255,255,255,.55)', strokeWidth: 2 },
            markerEnd: { type: MarkerType.ArrowClosed as const, width: 14, height: 14, color: 'rgba(255,255,255,.55)' },
          }
        }
        return {
          ...e,
          style: { stroke: 'rgba(255,255,255,.06)', strokeWidth: 1 },
          markerEnd: { type: MarkerType.ArrowClosed as const, width: 14, height: 14, color: 'rgba(255,255,255,.06)' },
        }
      })
    }

    // At low zoom, boost edge contrast so connections remain visible
    if (zoomedOut) {
      return edges.map((e) => ({
        ...e,
        style: { stroke: 'rgba(255,255,255,.38)', strokeWidth: 2 },
        markerEnd: { type: MarkerType.ArrowClosed as const, width: 16, height: 16, color: 'rgba(255,255,255,.38)' },
      }))
    }

    return edges
  }, [edges, traceResult, allTraceNodeIds, hoveredNodeId, zoomedOut])

  return {
    traceResult, tracedCell,
    handleCellClick, clearTrace,
    nodesWithStatus, edgesWithTrace,
  }
}
