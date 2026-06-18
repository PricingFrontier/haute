/**
 * Side-by-side read-only comparison of a historical pipeline version against the
 * current working pipeline (S11).
 *
 * Left canvas  = the pipeline as it stood at the inspected commit (fetched via
 *                GET /api/git/show/{sha} — no checkout, the working tree is
 *                untouched).
 * Right canvas = the current working pipeline (a frozen snapshot taken on entry).
 *
 * Both canvases are read-only: nodes can't be dragged, connected, or edited —
 * only panned/zoomed for inspection. A floating chip over the historical canvas
 * names the version under inspection and carries the × bail-out.
 */
import { useEffect, useState } from "react"
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
import type { GitComparison } from "../stores/useGitStore"

const fitViewOptions = { padding: 0.2 }
const proOptions = { hideAttribution: true }
const defaultEdgeOptions = {
  type: "default" as const,
  animated: false,
  style: { stroke: "rgba(255,255,255,.25)", strokeWidth: 1.5 },
}

interface ComparisonViewProps {
  comparison: GitComparison
  /** The current working graph — a frozen snapshot rendered on the right. */
  currentNodes: Node[]
  currentEdges: Edge[]
  /** Bail out of the comparison, back to the live editor. */
  onClose: () => void
}

// ---------------------------------------------------------------------------
// One read-only canvas
// ---------------------------------------------------------------------------

function ReadonlyCanvas({
  initialNodes,
  initialEdges,
  testId,
}: {
  initialNodes: Node[]
  initialEdges: Edge[]
  testId: string
}) {
  // Internal state so ReactFlow can still measure node dimensions (handles/edges
  // land correctly) while the user cannot mutate the graph. Seeded once — the
  // parent remounts via `key` when the inspected version changes.
  const [nodes, , onNodesChange] = useNodesState(initialNodes)
  const [edges, , onEdgesChange] = useEdgesState(initialEdges)
  return (
    <ReactFlow
      data-testid={testId}
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
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
// ComparisonView
// ---------------------------------------------------------------------------

export default function ComparisonView({
  comparison,
  currentNodes,
  currentEdges,
  onClose,
}: ComparisonViewProps) {
  const [historical, setHistorical] = useState<{ nodes: Node[]; edges: Edge[] } | null>(null)
  const [error, setError] = useState<string | null>(null)
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

  return (
    <div data-testid="comparison-view" className="flex-1 flex min-h-0">
      {/* LEFT — historical version */}
      <section
        className="flex-1 min-w-0 flex flex-col relative"
        style={{ borderRight: "1px solid var(--chrome-border)" }}
      >
        <CanvasHeader kicker="Historical" title={comparison.label} />
        <div className="flex-1 min-h-0 relative">
          {historical ? (
            <ReactFlowProvider>
              <ReadonlyCanvas
                key={`historical:${comparison.sha}`}
                initialNodes={historical.nodes}
                initialEdges={historical.edges}
                testId="comparison-canvas-historical"
              />
            </ReactFlowProvider>
          ) : error ? (
            <div
              data-testid="comparison-error"
              className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-6 text-center"
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
          ) : (
            <div
              data-testid="comparison-loading"
              className="absolute inset-0 flex items-center justify-center gap-2"
            >
              <Loader2 size={14} className="animate-spin" style={{ color: "var(--text-muted)" }} />
              <span className="text-[12px]" style={{ color: "var(--text-muted)" }}>
                Loading version {shortSha}…
              </span>
            </div>
          )}
        </div>

        {/* Floating chip over the historical canvas — names the version and
            carries the × bail-out (S11). */}
        <div
          data-testid="comparison-chip"
          className="absolute top-11 left-3 z-10 flex items-center gap-2 px-2.5 py-1.5 rounded-md shadow-lg"
          style={{
            background: "var(--bg-elevated)",
            border: "1px solid var(--accent-soft-strong)",
          }}
        >
          <History size={12} style={{ color: "var(--accent)" }} />
          <span className="text-[11px]" style={{ color: "var(--text-secondary)" }}>
            Viewing{" "}
            <span style={{ color: "var(--text-primary)" }}>{comparison.label}</span>{" "}
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
      </section>

      {/* RIGHT — current working version */}
      <section className="flex-1 min-w-0 flex flex-col relative">
        <CanvasHeader kicker="Current" title="Working pipeline" />
        <div className="flex-1 min-h-0 relative">
          <ReactFlowProvider>
            <ReadonlyCanvas
              key="current"
              initialNodes={currentNodes}
              initialEdges={currentEdges}
              testId="comparison-canvas-current"
            />
          </ReactFlowProvider>
        </div>
      </section>
    </div>
  )
}
