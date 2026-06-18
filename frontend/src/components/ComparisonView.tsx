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
import { useEffect, useMemo, useState } from "react"
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  BackgroundVariant,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
} from "@xyflow/react"
import { X, History, AlertTriangle, Loader2 } from "lucide-react"

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
  /** The current working graph — a frozen snapshot rendered on the right. */
  currentNodes: Node[]
  currentEdges: Edge[]
  /** Bail out of the comparison, back to the live editor. */
  onClose: () => void
  /** A node was clicked in either canvas — open the read-only config inspector. */
  onSelectNode: (inspect: ComparisonInspect) => void
}

// ---------------------------------------------------------------------------
// Diff → node className
// ---------------------------------------------------------------------------

/** The highlight class for a node on one side, or undefined if unchanged. */
function diffClassFor(diff: GraphDiff, id: string, side: DiffSide): string | undefined {
  if (side === "historical" && diff.removed.has(id)) return "cmp-diff-removed"
  if (side === "current" && diff.added.has(id)) return "cmp-diff-added"
  if (diff.changed.has(id)) return "cmp-diff-changed"
  if (diff.moved.has(id)) return "cmp-diff-moved"
  return undefined
}

/**
 * Strip editor-only UI state (a `selected`/`dragging` flag carried over from the
 * live canvas would otherwise show a stray selection ring) and tag each node with
 * its diff highlight class for the given side.
 */
function prepNodes(nodes: Node[], diff: GraphDiff, side: DiffSide): Node[] {
  return nodes.map((n) => {
    const cls = diffClassFor(diff, n.id, side)
    return {
      ...n,
      selected: false,
      dragging: false,
      className: cls ? [n.className, cls].filter(Boolean).join(" ") : n.className,
    }
  })
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
}: {
  initialNodes: Node[]
  initialEdges: Edge[]
  testId: string
  selectedId: string | null
  onNodeSelect: (id: string) => void
}) {
  // Internal state so ReactFlow can still measure node dimensions (handles/edges
  // land correctly) while the user cannot mutate the graph. Seeded once — the
  // parent remounts via `key` when the inspected version changes.
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes)
  const [edges, , onEdgesChange] = useEdgesState(initialEdges)

  // Mirror the focused node onto BOTH canvases (the counterpart highlight): tag
  // the node whose id matches `selectedId` with `cmp-selected`, preserving the
  // diff classes and the measured dimensions already on each node.
  useEffect(() => {
    setNodes((prev) =>
      prev.map((n) => {
        const classes = (n.className ?? "").split(/\s+/).filter((c) => c && c !== "cmp-selected")
        if (n.id === selectedId) classes.push("cmp-selected")
        return { ...n, className: classes.join(" ") }
      }),
    )
  }, [selectedId, setNodes])

  return (
    <ReactFlow
      data-testid={testId}
      className="cmp-canvas"
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeClick={(_e, n) => onNodeSelect(n.id)}
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
        className="absolute bottom-3 left-1/2 -translate-x-1/2 z-10 px-3 py-1.5 rounded-full shadow-lg text-[11px]"
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
      className="absolute bottom-3 left-1/2 -translate-x-1/2 z-10 flex items-center gap-3 px-3 py-1.5 rounded-full shadow-lg"
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
  const [historical, setHistorical] = useState<{ nodes: Node[]; edges: Edge[] } | null>(null)
  const [error, setError] = useState<string | null>(null)
  // The focused node id, highlighted on BOTH canvases (its counterpart too).
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const shortSha = comparison.sha.slice(0, 7)

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
    () => (historical ? diffPipelineNodes(historical.nodes, currentNodes) : null),
    [historical, currentNodes],
  )
  const leftNodes = useMemo(
    () => (historical && diff ? prepNodes(historical.nodes, diff, "historical") : []),
    [historical, diff],
  )
  const rightNodes = useMemo(
    () => (diff ? prepNodes(currentNodes, diff, "current") : currentNodes),
    [currentNodes, diff],
  )

  // Resolve a clicked node id on both sides and hand it to the inspector (S11).
  const selectNode = useMemo(() => {
    const historicalById = new Map((historical?.nodes ?? []).map((n) => [n.id, n]))
    const currentById = new Map(currentNodes.map((n) => [n.id, n]))
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
  }, [historical, currentNodes, diff, onSelectNode])

  return (
    <div data-testid="comparison-view" className="flex-1 flex min-h-0 relative">
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
          {/* LEFT — historical version (removed + changed highlighted) */}
          <section
            className="flex-1 min-w-0 flex flex-col"
            style={{ borderRight: "1px solid var(--chrome-border)" }}
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
                />
              </ReactFlowProvider>
            </div>
          </section>

          {/* RIGHT — current version (added + changed highlighted) */}
          <section className="flex-1 min-w-0 flex flex-col">
            <CanvasHeader kicker="Current" title="Working pipeline" />
            <div className="flex-1 min-h-0 relative">
              <ReactFlowProvider>
                <ReadonlyCanvas
                  initialNodes={rightNodes}
                  initialEdges={currentEdges}
                  testId="comparison-canvas-current"
                  selectedId={selectedId}
                  onNodeSelect={selectNode}
                />
              </ReactFlowProvider>
            </div>
          </section>

          <DiffLegend diff={diff} />
        </>
      )}

      {/* Floating chip — names the version and carries the × bail-out (S11).
          Rendered above every state (incl. loading) so the user can always
          back out. */}
      <div
        data-testid="comparison-chip"
        className="absolute top-11 left-3 z-10 flex items-center gap-2 px-2.5 py-1.5 rounded-md shadow-lg"
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
    </div>
  )
}
