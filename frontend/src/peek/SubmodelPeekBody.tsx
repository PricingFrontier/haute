/**
 * Submodel peek body (node-explosion design §3.5).
 *
 * On open, fetches the submodel's internal graph (`GET /api/submodel/{name}`, a
 * pre-existing read), derives the I/O boundary (input/output PORT nodes + dashed
 * child links) from the PARENT graph's edges to/from the peeked node via the
 * shared {@link buildSubmodelBoundary} helper — the SAME boundary the drill-in
 * builds — ELK-layouts the combined graph, and renders it in a READ-ONLY,
 * NAVIGABLE React Flow using haute's OWN node cards (the shared
 * {@link nodeTypes} registry). The result is a genuine window into the canvas
 * you'd land on if you drilled in (_SUBMODELS.md explode).
 *
 * Navigation matches the main canvas exactly (entrained UX): pan with a
 * right- or middle-drag and zoom with the wheel, via the same {@link useCanvasPan}
 * gesture the canvas uses (which also suppresses the native context menu so a
 * right-drag never flashes a menu). Left-click an internal node to drill in. It
 * is non-editing: no node drag, connect or select. The peek opens fit-to-view
 * and reports a bounding-box-derived preferred panel size (see
 * {@link computePreferredSize}) so the WHOLE submodel is visible at a balanced
 * zoom; a ResizeObserver refits whenever the panel is resized.
 */
import { useEffect, useMemo, useRef, useState, useCallback } from "react"
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  useReactFlow,
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
import useCanvasPan from "../canvas/useCanvasPan"
import useUIStore from "../stores/useUIStore"
import { computePeekTraceLighting } from "./peekTraceLighting"
import { useFitViewOnResize } from "./useFitViewOnResize"
import type { PeekBodyProps } from "./peekRegistry"

/** ELK lays out with these node box dimensions (utils/layout.ts). */
const LAYOUT_NODE_W = 240
const LAYOUT_NODE_H = 70
const MIN_ZOOM = 0.1
const MAX_ZOOM = 1.5
const FIT_PADDING = 0.15
/** Default panel size when there's nothing to measure against. */
const DEFAULT_W = 560
const DEFAULT_H = 400
const MIN_W = 380
const MIN_H = 280

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "loaded"; nodes: Node[]; edges: Edge[] }

/**
 * The panel size (px) that frames the whole laid-out graph at a balanced zoom.
 * Penalises BOTH a large window (capped at ~80% of the canvas) AND a low zoom
 * (the graph is sized as if shown at ~0.8 zoom, not 1:1, so a big graph yields a
 * sensible window rather than a giant one); fitView then settles the exact zoom
 * so the whole graph is always visible. Tiny graphs floor at a min size.
 */
function computePreferredSize(laidOut: Node[]): { width: number; height: number } {
  if (laidOut.length === 0) return { width: DEFAULT_W, height: DEFAULT_H }
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const n of laidOut) {
    minX = Math.min(minX, n.position.x)
    minY = Math.min(minY, n.position.y)
    maxX = Math.max(maxX, n.position.x + LAYOUT_NODE_W)
    maxY = Math.max(maxY, n.position.y + LAYOUT_NODE_H)
  }
  const graphW = Math.max(maxX - minX, 1)
  const graphH = Math.max(maxY - minY, 1)
  const TARGET_ZOOM = 0.8
  const CHROME_W = 24 // panel border + body padding
  const CHROME_H = 88 // header + counts + padding
  const maxW = Math.round(window.innerWidth * 0.8)
  const maxH = Math.round(window.innerHeight * 0.8)
  const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(v, hi))
  return {
    width: clamp(Math.round(graphW * TARGET_ZOOM) + CHROME_W, MIN_W, Math.max(MIN_W, maxW)),
    height: clamp(Math.round(graphH * TARGET_ZOOM) + CHROME_H, MIN_H, Math.max(MIN_H, maxH)),
  }
}

/**
 * The inner flow itself (inside its own provider). Owns pan via {@link useCanvasPan}
 * (right/middle-drag, context menu suppressed) and refits on container resize so
 * the whole graph stays framed when the panel is resized.
 */
function PeekFlow({
  nodes,
  edges,
  onNodeClick,
  onNodeMouseEnter,
  onNodeMouseLeave,
}: {
  nodes: Node[]
  edges: Edge[]
  onNodeClick: NodeMouseHandler
  onNodeMouseEnter?: NodeMouseHandler
  onNodeMouseLeave?: NodeMouseHandler
}) {
  const { fitView } = useReactFlow()
  // useCanvasPan owns pan + native-context-menu suppression (no peek menu, so
  // the gesture's onContextMenu is a no-op): right/middle-drag pans, identical
  // to the main canvas. Left button is left to React Flow for the node click.
  const panRef = useCanvasPan({ onContextMenu: () => {} })
  const containerRef = useRef<HTMLDivElement | null>(null)

  // Refit whenever the panel resizes (incl. the open-time jump to the
  // bounding-box-derived size), so the whole graph stays framed.
  useFitViewOnResize(containerRef, fitView, FIT_PADDING)

  return (
    <div
      ref={(el) => {
        containerRef.current = el
        panRef(el)
      }}
      style={{ width: "100%", height: "100%" }}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: FIT_PADDING }}
        minZoom={MIN_ZOOM}
        maxZoom={MAX_ZOOM}
        nodesDraggable={false}
        nodesConnectable={false}
        nodesFocusable={false}
        edgesFocusable={false}
        elementsSelectable={false}
        panOnDrag={false}
        zoomOnDoubleClick={false}
        zoomOnScroll
        onNodeClick={onNodeClick}
        onNodeMouseEnter={onNodeMouseEnter}
        onNodeMouseLeave={onNodeMouseLeave}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={16} size={1} />
      </ReactFlow>
    </div>
  )
}

export default function SubmodelPeekBody({
  node,
  accent,
  onDrillIn,
  parentNodes,
  parentEdges,
  onPreferredSize,
}: PeekBodyProps) {
  const [state, setState] = useState<LoadState>({ kind: "loading" })
  const [attempt, setAttempt] = useState(0)
  const cancelledRef = useRef(false)
  // Keep the latest callback without re-running the fetch effect when it changes.
  const onPreferredSizeRef = useRef(onPreferredSize)
  useEffect(() => {
    onPreferredSizeRef.current = onPreferredSize
  }, [onPreferredSize])

  const smName = node.id.replace("submodel__", "")

  const boundarySig = useMemo(() => {
    return (parentEdges ?? [])
      .filter((e) => e.source === node.id || e.target === node.id)
      .map((e) => `${e.id}|${e.source}|${e.target}|${e.sourceHandle ?? ""}|${e.targetHandle ?? ""}`)
      .join(";")
  }, [parentEdges, node.id])

  // The parent edges incident to THIS wrapper — the only ones the lighting reads.
  // Keyed on the value-stable boundarySig (NOT the parentEdges array identity,
  // which React Flow re-creates on every parent render), so the lighting BFS does
  // not re-run on unrelated re-renders (node drag, selection, zoom, peek-panel
  // drag). boundarySig encodes exactly the fields computePeekTraceLighting reads.
  const boundaryParentEdges = useMemo(
    () => (parentEdges ?? []).filter((e) => e.source === node.id || e.target === node.id),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [boundarySig, node.id],
  )

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
        const { portNodes, boundaryEdges } = buildSubmodelBoundary({
          smNodeId: node.id,
          parentNodes: parentNodes ?? [],
          parentEdges: parentEdges ?? [],
          childIds: new Set(fetchedNodes.map((n) => n.id)),
        })
        const combinedEdges = [...fetchedEdges, ...boundaryEdges]
        const laidOut = await getLayoutedElements([...fetchedNodes, ...portNodes], combinedEdges)
        if (cancelledRef.current) return
        onPreferredSizeRef.current?.(computePreferredSize(laidOut))
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [smName, attempt, boundarySig])

  const retry = useCallback(() => setAttempt((a) => a + 1), [])

  const handleNodeClick = useCallback<NodeMouseHandler>(
    (_event, clicked) => {
      if (effectiveNodeType(clicked) === NODE_TYPES.SUBMODEL_PORT) return
      onDrillIn?.(clicked.id)
    },
    [onDrillIn],
  )

  // Trace lighting INSIDE the peek (#3, Facet 1): a data-path that crosses this
  // wrapper's boundary lights its relevant internal segment here — the only place
  // the children are drawn. Driven by the parent-canvas hover (shared UI store)
  // and by self-hover within the peek (which takes priority). See
  // computePeekTraceLighting for the rules.
  const hoveredNodeId = useUIStore((s) => s.hoveredNodeId)
  const [peekHoverId, setPeekHoverId] = useState<string | null>(null)
  const handlePeekMouseEnter = useCallback<NodeMouseHandler>((_event, hovered) => {
    // Ports are boundary markers, not internal nodes — they don't drive
    // self-hover (the parent hover already lights boundary cones).
    setPeekHoverId(effectiveNodeType(hovered) === NODE_TYPES.SUBMODEL_PORT ? null : hovered.id)
  }, [])
  const handlePeekMouseLeave = useCallback(() => setPeekHoverId(null), [])

  const loaded = state.kind === "loaded" ? state : null
  const lighting = useMemo(
    () =>
      loaded
        ? computePeekTraceLighting({
            peekNodes: loaded.nodes,
            peekEdges: loaded.edges,
            parentEdges: boundaryParentEdges,
            wrapperNodeId: node.id,
            hoveredNodeId,
            peekHoverId,
          })
        : null,
    [loaded, boundaryParentEdges, node.id, hoveredNodeId, peekHoverId],
  )
  // Dim non-lit nodes/edges via the node WRAPPER opacity so it works uniformly
  // across pipeline and port cards (the port card doesn't read _hoverDimmed).
  const projectedNodes = useMemo(() => {
    if (!loaded) return []
    if (!lighting?.active) return loaded.nodes
    return loaded.nodes.map((n) =>
      lighting.litNodeIds.has(n.id)
        ? n
        : { ...n, style: { ...(n.style || {}), opacity: 0.18, transition: "opacity 0.2s ease" } },
    )
  }, [loaded, lighting])
  const projectedEdges = useMemo(() => {
    if (!loaded) return []
    if (!lighting?.active) return loaded.edges
    return loaded.edges.map((e) =>
      lighting.litEdgeIds.has(e.id)
        ? { ...e, style: { ...(e.style || {}), stroke: "rgba(255,255,255,.55)", strokeWidth: 2, opacity: 1 } }
        : { ...e, style: { ...(e.style || {}), opacity: 0.1 } },
    )
  }, [loaded, lighting])

  if (state.kind === "loading") {
    return (
      <div
        data-testid="node-peek-loading"
        className="flex items-center justify-center gap-2 h-full text-[12px]"
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
        className="flex flex-col items-center justify-center gap-2 h-full text-[12px]"
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
        className="flex items-center justify-center h-full text-[12px]"
        style={{ color: "var(--text-muted)" }}
      >
        No internal nodes
      </div>
    )
  }

  const internalNodeCount = state.nodes.filter(
    (n) => effectiveNodeType(n) !== NODE_TYPES.SUBMODEL_PORT,
  ).length
  const internalEdgeCount = state.edges.filter((e) => !e.style?.strokeDasharray).length

  return (
    <div className="flex flex-col h-full">
      <div
        data-testid="node-peek-canvas"
        style={{
          flex: 1,
          minHeight: 0,
          width: "100%",
          borderRadius: 8,
          overflow: "hidden",
          border: "1px solid var(--border)",
          background: "var(--bg-canvas)",
        }}
      >
        <ReactFlowProvider>
          <PeekFlow
            nodes={projectedNodes}
            edges={projectedEdges}
            onNodeClick={handleNodeClick}
            onNodeMouseEnter={handlePeekMouseEnter}
            onNodeMouseLeave={handlePeekMouseLeave}
          />
        </ReactFlowProvider>
      </div>
      <div
        className="flex justify-end gap-2 px-2 pt-1 shrink-0 text-[10px] font-mono"
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
