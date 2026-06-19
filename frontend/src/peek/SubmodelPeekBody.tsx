/**
 * Submodel peek body (node-explosion design §3.5).
 *
 * On open, fetches the submodel's internal graph (`GET /api/submodel/{name}`, a
 * pre-existing read), derives the I/O boundary (input/output PORT nodes + dashed
 * child links) from the PARENT graph's edges to/from the peeked node via the
 * shared {@link buildSubmodelBoundary} helper — the SAME boundary the drill-in
 * builds — ELK-layouts the combined graph, and renders it in a READ-ONLY,
 * self-contained React Flow using haute's OWN node cards (the shared
 * {@link nodeTypes} registry). The result is a genuine window into the canvas
 * you'd land on if you drilled in (_SUBMODELS.md explode), not a bespoke
 * schematic: the cards, edges and I/O ports look exactly like the real canvas.
 *
 * The inner flow is its own ReactFlowProvider (isolated store — it does not
 * touch the outer canvas it is portalled into) and is non-editing: nodes can't
 * be dragged, connected, or selected, and wheel-zoom is suppressed so scrolling
 * over the peek never zooms the canvas behind it. Clicking an internal node
 * drills into the submodel and selects it there (Q4 resolution) via the same
 * handler the header "Open" uses; the boundary PORT nodes are read-only markers.
 *
 * Render-gate (AGENTS.md rule 3): every internal node + boundary port is handed
 * to the flow; none are dropped. Zero children → explicit empty state.
 */
import { useEffect, useMemo, useRef, useState, useCallback } from "react"
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  type Node,
  type Edge,
  type NodeMouseHandler,
} from "@xyflow/react"
import { loadSubmodel } from "../api/client"
import { getLayoutedElements } from "../utils/layout"
import { buildSubmodelBoundary } from "../utils/submodelBoundary"
import { effectiveNodeType } from "../types/node"
import { NODE_TYPES } from "../utils/nodeTypes"
import { nodeTypes } from "../nodes/nodeTypeRegistry"
import { withAlpha } from "../utils/color"
import type { PeekBodyProps } from "./peekRegistry"

/** The inner flow's reference height (px); width fills the peek card. */
const WINDOW_H = 280
const MIN_ZOOM = 0.1
const MAX_ZOOM = 1.5

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "loaded"; nodes: Node[]; edges: Edge[] }

export default function SubmodelPeekBody({
  node,
  accent,
  onDrillIn,
  parentNodes,
  parentEdges,
}: PeekBodyProps) {
  const [state, setState] = useState<LoadState>({ kind: "loading" })
  const [attempt, setAttempt] = useState(0)
  const cancelledRef = useRef(false)

  const smName = node.id.replace("submodel__", "")

  // Stable identity of the boundary WIRING (not node positions): only the parent
  // edges touching this submodel node matter. Keying the fetch/layout effect on
  // this means a node drag — which moves positions but not wiring — never
  // re-fetches or re-lays-out, while an actual rewire does refresh the boundary.
  // The boundary (and the fetched internal graph) is an as-of-open snapshot.
  const boundarySig = useMemo(() => {
    return (parentEdges ?? [])
      .filter((e) => e.source === node.id || e.target === node.id)
      .map((e) => `${e.id}|${e.source}|${e.target}|${e.sourceHandle ?? ""}|${e.targetHandle ?? ""}`)
      .join(";")
  }, [parentEdges, node.id])

  // Reset to loading at render time when the fetch key changes — the React-docs
  // alternative to a synchronous setState in the effect body (the compiler lint
  // forbids the latter). The effect below performs the async fetch for the key.
  const loadKey = `${smName}#${attempt}`
  const [prevLoadKey, setPrevLoadKey] = useState(loadKey)
  if (loadKey !== prevLoadKey) {
    setPrevLoadKey(loadKey)
    setState({ kind: "loading" })
  }

  useEffect(() => {
    cancelledRef.current = false
    const run = async () => {
      try {
        const data = await loadSubmodel(smName)
        if (cancelledRef.current) return
        const fetchedNodes: Node[] = data.graph?.nodes ?? []
        const fetchedEdges: Edge[] = data.graph?.edges ?? []
        if (fetchedNodes.length === 0) {
          setState({ kind: "loaded", nodes: [], edges: [] })
          return
        }
        // Derive the I/O boundary from the PARENT graph (same helper the drill-in
        // uses) and fold it into the laid-out graph, so the peek shows the
        // wrapper's ports + their dashed links exactly as the drilled canvas does.
        const { portNodes, boundaryEdges } = buildSubmodelBoundary({
          smNodeId: node.id,
          parentNodes: parentNodes ?? [],
          parentEdges: parentEdges ?? [],
          childIds: new Set(fetchedNodes.map((n) => n.id)),
        })
        const combinedEdges = [...fetchedEdges, ...boundaryEdges]
        const laidOut = await getLayoutedElements([...fetchedNodes, ...portNodes], combinedEdges)
        if (cancelledRef.current) return
        setState({ kind: "loaded", nodes: laidOut, edges: combinedEdges })
      } catch (err: unknown) {
        if (cancelledRef.current) return
        setState({ kind: "error", message: err instanceof Error ? err.message : String(err) })
      }
    }
    void run()
    return () => {
      cancelledRef.current = true
    }
    // parentNodes/parentEdges are read but intentionally excluded from deps: the
    // boundary is re-derived only when the wiring to this submodel changes
    // (tracked by boundarySig), matching the one-shot fetch of the graph itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [smName, attempt, boundarySig])

  const retry = useCallback(() => setAttempt((a) => a + 1), [])

  // Click an internal node → drill in + select it there. Boundary PORT nodes are
  // read-only markers (not drillable), mirroring the live canvas.
  const handleNodeClick = useCallback<NodeMouseHandler>(
    (_event, clicked) => {
      if (effectiveNodeType(clicked) === NODE_TYPES.SUBMODEL_PORT) return
      onDrillIn?.(clicked.id)
    },
    [onDrillIn],
  )

  if (state.kind === "loading") {
    return (
      <div
        data-testid="node-peek-loading"
        className="flex items-center justify-center gap-2 py-10 text-[12px]"
        style={{ color: "var(--text-muted)" }}
      >
        <span className="inline-block w-3 h-3 rounded-full border-2 border-current border-t-transparent animate-spin" />
        Loading internals…
      </div>
    )
  }

  if (state.kind === "error") {
    return (
      <div
        data-testid="node-peek-error"
        className="flex flex-col items-center gap-2 py-8 text-[12px]"
        style={{ color: "var(--warning-strong)" }}
      >
        <span>Failed to load internals: {state.message}</span>
        <button
          type="button"
          onClick={retry}
          className="px-2.5 py-1 rounded text-[11px] font-medium hover-chrome"
          style={{ border: `1px solid ${withAlpha(accent, 0.251)}`, color: accent }}
        >
          Retry
        </button>
      </div>
    )
  }

  if (state.nodes.length === 0) {
    return (
      <div
        data-testid="node-peek-empty"
        className="py-10 text-center text-[12px]"
        style={{ color: "var(--text-muted)" }}
      >
        No internal nodes
      </div>
    )
  }

  // Counts reflect the wrapper's CONTENTS, not its derived boundary wiring.
  const internalNodeCount = state.nodes.filter(
    (n) => effectiveNodeType(n) !== NODE_TYPES.SUBMODEL_PORT,
  ).length
  const internalEdgeCount = state.edges.filter((e) => !e.style?.strokeDasharray).length

  return (
    <div className="px-1 pt-1">
      {/* `nowheel` keeps a scroll over the peek from zooming the canvas behind
          it; the inner flow is fit-to-view and pannable (zoom is off). */}
      <div
        data-testid="node-peek-canvas"
        className="nowheel"
        style={{
          height: WINDOW_H,
          width: "100%",
          borderRadius: 8,
          overflow: "hidden",
          border: "1px solid var(--border)",
          background: "var(--bg-canvas)",
        }}
      >
        <ReactFlowProvider>
          <ReactFlow
            nodes={state.nodes}
            edges={state.edges}
            nodeTypes={nodeTypes}
            fitView
            fitViewOptions={{ padding: 0.18 }}
            minZoom={MIN_ZOOM}
            maxZoom={MAX_ZOOM}
            nodesDraggable={false}
            nodesConnectable={false}
            nodesFocusable={false}
            edgesFocusable={false}
            elementsSelectable={false}
            zoomOnScroll={false}
            zoomOnPinch={false}
            zoomOnDoubleClick={false}
            onNodeClick={handleNodeClick}
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={16} size={1} />
          </ReactFlow>
        </ReactFlowProvider>
      </div>
      <div
        className="flex justify-end gap-2 px-2 pt-1 text-[10px] font-mono"
        style={{ color: "var(--text-muted)" }}
        data-testid="node-peek-counts"
      >
        <span>{internalNodeCount} nodes</span>
        <span>·</span>
        <span>{internalEdgeCount} edges</span>
      </div>
    </div>
  )
}
