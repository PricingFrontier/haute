/**
 * Side-by-side read-only comparison of a historical pipeline version against the
 * current working pipeline (S11).
 *
 * Left canvas  = the pipeline as it stood at the inspected commit (fetched via
 *                GET /api/git/show/{sha} — no checkout, the working tree is
 *                untouched).
 * Right canvas = the current working pipeline (a frozen snapshot taken on entry).
 *
 * Changed components are highlighted: nodes removed or changed are ringed on the
 * left, nodes added or changed are ringed on the right (diffPipelineNodes). Both
 * canvases are read-only — nodes can't be dragged, connected, or edited, only
 * panned/zoomed. A floating chip names the version and carries the × bail-out.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  BackgroundVariant,
  useNodesState,
  useEdgesState,
  useReactFlow,
  type Node,
  type Edge,
} from "@xyflow/react"
import { X, History, AlertTriangle, Loader2, Columns2, Rows2 } from "lucide-react"

import { getCommitPipeline } from "../api/client"
import { nodeTypes } from "../utils/nodeTypeRegistry"
import { diffPipelineNodes, type GraphDiff } from "../utils/graphDiff"
import type { GitComparison } from "../stores/useGitStore"

const fitViewOptions = { padding: 0.2 }
const proOptions = { hideAttribution: true }
const defaultEdgeOptions = {
  type: "default" as const,
  animated: false,
  style: { stroke: "rgba(255,255,255,.25)", strokeWidth: 1.5 },
}

type DiffSide = "historical" | "current"

/** One side's view of a node (its editable surface) for the inspector. */
export interface ComparisonNodeFacet {
  label: string
  nodeType: string
  config: unknown
}

/** The node the user clicked, resolved on both sides for read-only inspection. */
export interface ComparisonInspect {
  id: string
  status: "added" | "removed" | "changed" | "unchanged"
  /** Current-version facet, or null if the node was removed. */
  current: ComparisonNodeFacet | null
  /** Historical-version facet, or null if the node was added. */
  historical: ComparisonNodeFacet | null
}

interface ComparisonViewProps {
  comparison: GitComparison
  /** The current working graph (the live array) — frozen internally on entry so
   *  the right canvas, diff, and legend stay mutually consistent. */
  currentNodes: Node[]
  currentEdges: Edge[]
  /** Bail out of the comparison, back to the live editor. */
  onClose: () => void
  /** A node was clicked in either canvas (open the read-only config inspector),
   *  or null when the blank canvas was clicked (deselect → back to the VC pane). */
  onSelectNode: (inspect: ComparisonInspect | null) => void
}

// ---------------------------------------------------------------------------
// Diff → node className
// ---------------------------------------------------------------------------

type DiffStatus = "added" | "removed" | "changed" | "moved"

/** The diff status of a node on one side, or undefined if unchanged. */
function diffStatusFor(diff: GraphDiff, id: string, side: DiffSide): DiffStatus | undefined {
  if (side === "historical" && diff.removed.has(id)) return "removed"
  if (side === "current" && diff.added.has(id)) return "added"
  if (diff.changed.has(id)) return "changed"
  if (diff.moved.has(id)) return "moved"
  return undefined
}

/**
 * Strip editor-only UI state (a `selected`/`dragging` flag carried over from the
 * live canvas would otherwise show a stray ring) and stamp each node's diff
 * status into its data, so PipelineNode draws the highlight on the CARD — the
 * same element selection uses, consistent and correctly shaped for every type.
 */
function prepNodes(nodes: Node[], diff: GraphDiff, side: DiffSide): Node[] {
  return nodes.map((n) => ({
    ...n,
    selected: false,
    dragging: false,
    data: { ...n.data, _diffStatus: diffStatusFor(diff, n.id, side) },
  }))
}

// ---------------------------------------------------------------------------
// One read-only canvas
// ---------------------------------------------------------------------------

function ReadonlyCanvas({
  initialNodes,
  initialEdges,
  testId,
  selectedId,
  onNodeSelect,
  onPaneClick,
  refitKey,
}: {
  initialNodes: Node[]
  initialEdges: Edge[]
  testId: string
  selectedId: string | null
  onNodeSelect: (id: string) => void
  onPaneClick: () => void
  /** Changes when the pane is reshaped (orientation flip) → re-fit the graph. */
  refitKey: string
}) {
  // Internal state so ReactFlow can still measure node dimensions (handles/edges
  // land correctly) while the user cannot mutate the graph. Seeded once — the
  // parent remounts via `key` when the inspected version changes.
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes)
  const [edges, , onEdgesChange] = useEdgesState(initialEdges)
  const { fitView } = useReactFlow()

  // Re-centre the graph when the pane is reshaped (orientation flip).
  useEffect(() => {
    const id = requestAnimationFrame(() => fitView({ padding: 0.2 }))
    return () => cancelAnimationFrame(id)
  }, [refitKey, fitView])

  // Mirror the focused node onto BOTH canvases (the counterpart highlight) via
  // ReactFlow's native `selected` flag, so PipelineNode draws its normal
  // selection border on the CARD — identical to the live editor, correct shape.
  useEffect(() => {
    setNodes((prev) => prev.map((n) => ({ ...n, selected: n.id === selectedId })))
  }, [selectedId, setNodes])

  return (
    <ReactFlow
      data-testid={testId}
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeClick={(_e, n) => onNodeSelect(n.id)}
      onPaneClick={onPaneClick}
      nodeTypes={nodeTypes}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={false}
      edgesFocusable={false}
      deleteKeyCode={null}
      minZoom={0.1}
      fitView
      fitViewOptions={fitViewOptions}
      proOptions={proOptions}
      defaultEdgeOptions={defaultEdgeOptions}
    >
      <Background variant={BackgroundVariant.Dots} gap={24} size={1} color="rgba(255,255,255,.06)" />
    </ReactFlow>
  )
}

// ---------------------------------------------------------------------------
// Header strip naming one side of the comparison
// ---------------------------------------------------------------------------

function CanvasHeader({ kicker, title }: { kicker: string; title: string }) {
  return (
    <div
      className="shrink-0 flex items-baseline gap-2 px-3 py-1.5"
      style={{ background: "var(--bg-panel)", borderBottom: "1px solid var(--chrome-border)" }}
    >
      <span
        className="text-[10px] font-bold uppercase tracking-[0.1em]"
        style={{ color: "var(--text-muted)" }}
      >
        {kicker}
      </span>
      <span className="text-[12px] font-medium truncate" style={{ color: "var(--text-primary)" }}>
        {title}
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Floating diff legend
// ---------------------------------------------------------------------------

function DiffLegend({ diff }: { diff: GraphDiff }) {
  const total = diff.added.size + diff.removed.size + diff.changed.size + diff.moved.size
  if (total === 0) {
    return (
      <div
        data-testid="comparison-legend"
        className="px-3 py-1.5 rounded-md shadow-lg text-[11px]"
        style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-muted)" }}
      >
        No differences from the current pipeline
      </div>
    )
  }
  const items: { key: string; color: string; label: string; n: number }[] = [
    { key: "added", color: "var(--diff-added)", label: "Added", n: diff.added.size },
    { key: "changed", color: "var(--diff-changed)", label: "Changed", n: diff.changed.size },
    { key: "removed", color: "var(--diff-removed)", label: "Removed", n: diff.removed.size },
    { key: "moved", color: "var(--diff-moved)", label: "Moved", n: diff.moved.size },
  ].filter((it) => it.n > 0)
  return (
    <div
      data-testid="comparison-legend"
      className="flex items-center gap-3 px-3 py-1.5 rounded-md shadow-lg"
      style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}
    >
      {items.map((it) => (
        <span
          key={it.key}
          className="flex items-center gap-1.5 text-[11px]"
          style={{ color: "var(--text-secondary)" }}
        >
          <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: it.color }} />
          {it.label} <span style={{ color: "var(--text-muted)" }}>{it.n}</span>
        </span>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// ComparisonView
// ---------------------------------------------------------------------------

function facetOf(n: Node): ComparisonNodeFacet {
  const data = (n.data ?? {}) as Record<string, unknown>
  return {
    label: typeof data.label === "string" ? data.label : n.id,
    nodeType: typeof data.nodeType === "string" ? data.nodeType : "",
    config: data.config,
  }
}

export default function ComparisonView({
  comparison,
  currentNodes,
  currentEdges,
  onClose,
  onSelectNode,
}: ComparisonViewProps) {
  // Freeze the current graph on entry — a true snapshot, so the right canvas, the
  // diff, and the legend all stay mutually consistent even if the live pipeline
  // is edited (toolbar/websocket) while comparing. Re-snapshots on remount, which
  // is keyed by comparison.sha at the call site.
  const [current] = useState(() => ({ nodes: currentNodes, edges: currentEdges }))
  const [historical, setHistorical] = useState<{ nodes: Node[]; edges: Edge[] } | null>(null)
  const [error, setError] = useState<string | null>(null)
  // The focused node id, highlighted on BOTH canvases (its counterpart too).
  const [selectedId, setSelectedId] = useState<string | null>(null)
  // Split layout: "vertical" = side by side (default); "horizontal" = stacked
  // (historical on top, current below) — better for wide-not-tall pipelines.
  // `splitFraction` is the first pane's size; the divider is draggable, and a
  // double-click resets it to an equal split.
  const [orientation, setOrientation] = useState<"vertical" | "horizontal">("vertical")
  const [splitFraction, setSplitFraction] = useState(0.5)
  const [dragging, setDragging] = useState(false)
  const splitRef = useRef<HTMLDivElement>(null)
  const shortSha = comparison.sha.slice(0, 7)

  // Drag the divider to resize both panes (clamped so neither collapses).
  useEffect(() => {
    if (!dragging) return
    const onMove = (e: PointerEvent) => {
      const el = splitRef.current
      if (!el) return
      const rect = el.getBoundingClientRect()
      const f =
        orientation === "vertical"
          ? (e.clientX - rect.left) / rect.width
          : (e.clientY - rect.top) / rect.height
      setSplitFraction(Math.min(0.9, Math.max(0.1, f)))
    }
    const onUp = () => setDragging(false)
    window.addEventListener("pointermove", onMove)
    window.addEventListener("pointerup", onUp)
    return () => {
      window.removeEventListener("pointermove", onMove)
      window.removeEventListener("pointerup", onUp)
    }
  }, [dragging, orientation])

  // Fetch the historical pipeline for the left canvas. The component is keyed by
  // `comparison.sha` at the call site, so inspecting a different version remounts
  // it with fresh state — no in-effect reset needed. The request is aborted if
  // the view unmounts (remount or bail-out) before it lands.
  useEffect(() => {
    const ctrl = new AbortController()
    const load = async () => {
      const graph = await getCommitPipeline(comparison.sha, { signal: ctrl.signal })
      if (!ctrl.signal.aborted) setHistorical({ nodes: graph.nodes, edges: graph.edges })
    }
    load().catch((err) => {
      if (ctrl.signal.aborted) return
      setError(err instanceof Error ? err.message : "Could not load this version.")
    })
    return () => ctrl.abort()
  }, [comparison.sha])

  // Diff once the historical graph is in hand; class each side's nodes from it.
  const diff = useMemo(
    () => (historical ? diffPipelineNodes(historical.nodes, current.nodes) : null),
    [historical, current.nodes],
  )
  const leftNodes = useMemo(
    () => (historical && diff ? prepNodes(historical.nodes, diff, "historical") : []),
    [historical, diff],
  )
  const rightNodes = useMemo(
    () => (diff ? prepNodes(current.nodes, diff, "current") : current.nodes),
    [current.nodes, diff],
  )

  // Resolve a clicked node id on both sides and hand it to the inspector (S11).
  const selectNode = useMemo(() => {
    const historicalById = new Map((historical?.nodes ?? []).map((n) => [n.id, n]))
    const currentById = new Map(current.nodes.map((n) => [n.id, n]))
    return (id: string) => {
      if (!diff) return
      setSelectedId(id)
      const hist = historicalById.get(id) ?? null
      const curr = currentById.get(id) ?? null
      const status = diff.added.has(id)
        ? "added"
        : diff.removed.has(id)
          ? "removed"
          : diff.changed.has(id)
            ? "changed"
            : "unchanged"
      onSelectNode({
        id,
        status,
        current: curr ? facetOf(curr) : null,
        historical: hist ? facetOf(hist) : null,
      })
    }
  }, [historical, current.nodes, diff, onSelectNode])

  // Clicking blank canvas deselects → the VC sidepane returns (the aside is
  // always present, so nothing resizes), anchoring the compare experience.
  const clearSelection = useCallback(() => {
    setSelectedId(null)
    onSelectNode(null)
  }, [onSelectNode])

  return (
    <div
      ref={splitRef}
      data-testid="comparison-view"
      data-orientation={orientation}
      className={`flex-1 flex min-h-0 relative ${orientation === "horizontal" ? "flex-col" : ""}`}
    >
      {error ? (
        <div
          data-testid="comparison-error"
          className="flex-1 flex flex-col items-center justify-center gap-2 px-6 text-center"
        >
          <AlertTriangle size={20} style={{ color: "var(--danger)" }} />
          <span className="text-[12px]" style={{ color: "var(--text-secondary)" }}>
            {error}
          </span>
          <button
            onClick={onClose}
            className="mt-1 px-2.5 py-1 text-[12px] rounded-md"
            style={{ background: "var(--chrome)", color: "var(--text-primary)" }}
          >
            Back to editor
          </button>
        </div>
      ) : !historical || !diff ? (
        <div
          data-testid="comparison-loading"
          className="flex-1 flex items-center justify-center gap-2"
        >
          <Loader2 size={14} className="animate-spin" style={{ color: "var(--text-muted)" }} />
          <span className="text-[12px]" style={{ color: "var(--text-muted)" }}>
            Loading version {shortSha}…
          </span>
        </div>
      ) : (
        <>
          {/* FIRST pane — historical (removed + changed highlighted). Sized to
              `splitFraction` along the split axis; the gutter resizes it. */}
          <section
            data-testid="comparison-pane-first"
            className="min-w-0 min-h-0 flex flex-col"
            style={{ flexBasis: `${splitFraction * 100}%`, flexGrow: 0, flexShrink: 0 }}
          >
            <CanvasHeader kicker="Historical" title={comparison.label} />
            <div className="flex-1 min-h-0 relative">
              <ReactFlowProvider>
                <ReadonlyCanvas
                  initialNodes={leftNodes}
                  initialEdges={historical.edges}
                  testId="comparison-canvas-historical"
                  selectedId={selectedId}
                  onNodeSelect={selectNode}
                  onPaneClick={clearSelection}
                  refitKey={orientation}
                />
              </ReactFlowProvider>
            </div>
          </section>

          {/* Draggable divider with the orientation toggle centred on it. */}
          <div
            data-testid="comparison-divider"
            onPointerDown={(e) => {
              e.preventDefault()
              setDragging(true)
            }}
            onDoubleClick={() => setSplitFraction(0.5)}
            className={`relative shrink-0 flex items-center justify-center ${
              orientation === "vertical" ? "w-1 cursor-col-resize" : "h-1 cursor-row-resize"
            }`}
            style={{ background: "var(--chrome-border)" }}
          >
            <button
              data-testid="comparison-orientation-toggle"
              onPointerDown={(e) => e.stopPropagation()}
              onDoubleClick={(e) => e.stopPropagation()}
              onClick={() =>
                setOrientation((o) => (o === "vertical" ? "horizontal" : "vertical"))
              }
              title={
                orientation === "vertical"
                  ? "Stack (historical on top, current below)"
                  : "Side by side"
              }
              aria-label="Toggle split orientation"
              className="absolute z-20 flex items-center justify-center w-6 h-6 rounded-full shadow hover:brightness-110"
              style={{
                background: "var(--bg-elevated)",
                border: "1px solid var(--border)",
                color: "var(--text-secondary)",
              }}
            >
              {orientation === "vertical" ? <Rows2 size={13} /> : <Columns2 size={13} />}
            </button>
          </div>

          {/* SECOND pane — current (added + changed highlighted), takes the rest. */}
          <section className="flex-1 min-w-0 min-h-0 flex flex-col">
            <CanvasHeader kicker="Current" title="Working pipeline" />
            <div className="flex-1 min-h-0 relative">
              <ReactFlowProvider>
                <ReadonlyCanvas
                  initialNodes={rightNodes}
                  initialEdges={current.edges}
                  testId="comparison-canvas-current"
                  selectedId={selectedId}
                  onNodeSelect={selectNode}
                  onPaneClick={clearSelection}
                  refitKey={orientation}
                />
              </ReactFlowProvider>
            </div>
          </section>
        </>
      )}

      {/* Top-left stack: the read-only chip (names the version + × bail-out) with
          the change legend directly beneath it (S11). Above every state so the
          user can always back out. */}
      <div className="absolute top-11 left-3 z-10 flex flex-col items-start gap-2">
        <div
          data-testid="comparison-chip"
          className="flex items-center gap-2 px-2.5 py-1.5 rounded-md shadow-lg"
          style={{ background: "var(--bg-elevated)", border: "1px solid var(--accent-soft-strong)" }}
        >
          <History size={12} style={{ color: "var(--accent)" }} />
          <span className="text-[11px]" style={{ color: "var(--text-secondary)" }}>
            Viewing <span style={{ color: "var(--text-primary)" }}>{comparison.label}</span>{" "}
            <span className="font-mono" style={{ color: "var(--text-muted)" }}>
              {shortSha}
            </span>{" "}
            — read-only
          </span>
          <button
            data-testid="comparison-chip-close"
            onClick={onClose}
            aria-label="Exit comparison"
            className="shrink-0 -mr-1 p-0.5 rounded hover:bg-[var(--bg-hover)]"
            style={{ color: "var(--text-muted)" }}
          >
            <X size={13} />
          </button>
        </div>
        {diff && <DiffLegend diff={diff} />}
      </div>
    </div>
  )
}
