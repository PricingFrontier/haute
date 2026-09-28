import { useEffect, useRef } from "react"
import { useReactFlow } from "@xyflow/react"
import useUIStore from "../stores/useUIStore"
import type { TraceResult } from "../types/trace"

const TRACE_FIT_OPTIONS = { padding: 0.2, duration: 300 }

/**
 * Keeps a trace's nodes in view: fits the canvas to the traced value's lineage
 * when a trace opens, and centres a node a trace card or derivation row asks
 * for at the current zoom. Trace ids are runtime ids, so each is resolved to the
 * canvas node showing it (a submodel's card, a boundary). Rendered inside `<ReactFlow>`.
 */
export default function TraceViewFit({ traceResult, resolveNodeId }: {
  traceResult: TraceResult | null
  resolveNodeId: (id: string) => string
}) {
  const { fitView, getZoom } = useReactFlow()
  const centreRequest = useUIStore((s) => s.traceCentreRequest)
  const fittedTraceRef = useRef<TraceResult | null>(null)
  const centredRequestRef = useRef<typeof centreRequest>(null)

  useEffect(() => {
    if (!traceResult || fittedTraceRef.current === traceResult) return
    fittedTraceRef.current = traceResult
    const lineage = new Set(
      traceResult.steps.filter((step) => step.column_relevant).map((step) => resolveNodeId(step.node_id)),
    )
    if (lineage.size > 0) void fitView({ nodes: [...lineage].map((id) => ({ id })), ...TRACE_FIT_OPTIONS })
  }, [traceResult, fitView, resolveNodeId])

  // Each request centres once; a new resolver (the canvas changed) is no request.
  useEffect(() => {
    if (!centreRequest || centredRequestRef.current === centreRequest) return
    centredRequestRef.current = centreRequest
    const zoom = getZoom()
    void fitView({ nodes: [{ id: resolveNodeId(centreRequest.nodeId) }], duration: 300, minZoom: zoom, maxZoom: zoom })
  }, [centreRequest, fitView, getZoom, resolveNodeId])

  return null
}
