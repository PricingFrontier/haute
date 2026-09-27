import { useEffect, useRef } from "react"
import { useReactFlow } from "@xyflow/react"
import useUIStore from "../stores/useUIStore"
import type { TraceResult } from "../types/trace"

const TRACE_FIT_OPTIONS = { padding: 0.2, duration: 300 }

/**
 * Keeps a trace's nodes in view: fits the canvas to the traced value's lineage
 * when a trace opens, and centres a node a trace card or derivation row asks
 * for at the current zoom. Rendered inside `<ReactFlow>`.
 */
export default function TraceViewFit({ traceResult }: { traceResult: TraceResult | null }) {
  const { fitView, getZoom } = useReactFlow()
  const centreRequest = useUIStore((s) => s.traceCentreRequest)
  const fittedTraceRef = useRef<TraceResult | null>(null)

  useEffect(() => {
    if (!traceResult || fittedTraceRef.current === traceResult) return
    fittedTraceRef.current = traceResult
    const lineage = traceResult.steps.filter((step) => step.column_relevant).map((step) => ({ id: step.node_id }))
    if (lineage.length > 0) void fitView({ nodes: lineage, ...TRACE_FIT_OPTIONS })
  }, [traceResult, fitView])

  useEffect(() => {
    if (!centreRequest) return
    const zoom = getZoom()
    void fitView({ nodes: [{ id: centreRequest.nodeId }], duration: 300, minZoom: zoom, maxZoom: zoom })
  }, [centreRequest, fitView, getZoom])

  return null
}
