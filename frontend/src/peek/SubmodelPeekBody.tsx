/**
 * Submodel peek body (node-explosion design §3.5) — the v1 peek body.
 *
 * On mount, fetches the submodel's internal graph (`GET /api/submodel/{name}`,
 * a pre-existing read), derives the I/O boundary (input/output PORT nodes +
 * dashed child links) from the PARENT graph's edges to/from the peeked node
 * via the shared {@link buildSubmodelBoundary} helper — the SAME boundary the
 * drill-in builds — then ELK-layouts the combined graph and renders a
 * hand-rolled mini-DAG SVG inside a SCROLLABLE window. The result is a faithful,
 * read-only window into the canvas you'd land on if you drilled in
 * (_SUBMODELS.md explode). A graph that fits renders at fit scale; a larger one
 * holds a legibility floor and overflows into the scroll container rather than
 * shrinking to a thumbnail. Same dependency-free idiom as the trace
 * input-source tree (no nested ReactFlow, no new deps).
 *
 * Mini-nodes are navigation triggers (Q4 resolution): clicking one drills into
 * the submodel canvas via the same handler the header "Open" button uses, then
 * selects that child there. They are role="button" with Enter/Space activation.
 * No editing happens from inside the peek.
 *
 * Render-gate (AGENTS.md rule 3): every internal node surfaces as a mini-node
 * with a `node-peek-mini-node-<label>` testid; scale-to-fit shrinks but never
 * drops nodes. Zero children → explicit empty state, not a hidden affordance.
 */
import { useEffect, useMemo, useRef, useState, useCallback } from "react"
import type { Node, Edge } from "@xyflow/react"
import { loadSubmodel } from "../api/client"
import { getLayoutedElements } from "../utils/layout"
import { buildSubmodelBoundary } from "../utils/submodelBoundary"
import { nodeData, effectiveNodeType } from "../types/node"
import { NODE_TYPES, nodeTypeColors } from "../utils/nodeTypes"
import { STRUCTURE_COLORS } from "../theme/colors"
import { withAlpha } from "../utils/color"
import type { PeekBodyProps } from "./peekRegistry"

/** ELK lays out with these node box dimensions (utils/layout.ts). */
const LAYOUT_NODE_W = 240
const LAYOUT_NODE_H = 70
/** The floating window's reference size (flow-px); content larger than this
 *  scrolls inside it rather than shrinking. */
const WINDOW_W = 420
const WINDOW_H = 280
const PAD = 16
/** Don't shrink below this — a peek is a readable *window into the canvas*, not
 *  a thumbnail. A graph that fits stays at fit scale; a bigger one holds this
 *  legibility floor and overflows into the scroll container. */
const MIN_SCALE = 0.45

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "loaded"; nodes: Node[]; edges: Edge[] }

interface MiniLayout {
  nodes: { node: Node; x: number; y: number; w: number; h: number }[]
  /** `dashed` marks a boundary (port↔child) edge — drawn dashed to distinguish
   *  it from the submodel's own internal wiring. */
  edges: { id: string; sx: number; sy: number; tx: number; ty: number; dashed: boolean }[]
  /** Rendered content size (px) — the SVG is sized to this and scrolls. */
  width: number
  height: number
}

/**
 * Lay the ELK graph out as a scrollable window: fit it when it fits readably,
 * otherwise hold MIN_SCALE and let the content overflow (the SVG is sized to
 * the content, and the wrapping container scrolls). Never enlarges past 1 —
 * full fidelity is one click away via "Open".
 */
function buildMiniLayout(laidOut: Node[], edges: Edge[]): MiniLayout {
  if (laidOut.length === 0) return { nodes: [], edges: [], width: WINDOW_W, height: WINDOW_H }
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
  const layoutW = Math.max(maxX - minX, 1)
  const layoutH = Math.max(maxY - minY, 1)
  const fitScale = Math.min((WINDOW_W - PAD * 2) / layoutW, (WINDOW_H - PAD * 2) / layoutH)
  const scale = Math.max(Math.min(fitScale, 1), MIN_SCALE)
  const width = layoutW * scale + PAD * 2
  const height = layoutH * scale + PAD * 2
  const posMap = new Map<string, { x: number; y: number }>()
  const nodes = laidOut.map((node) => {
    const x = PAD + (node.position.x - minX) * scale
    const y = PAD + (node.position.y - minY) * scale
    const w = LAYOUT_NODE_W * scale
    const h = LAYOUT_NODE_H * scale
    posMap.set(node.id, { x: x + w / 2, y: y + h / 2 })
    return { node, x, y, w, h }
  })
  const miniEdges = edges
    .map((e) => {
      const s = posMap.get(e.source)
      const t = posMap.get(e.target)
      if (!s || !t) return null
      return { id: e.id, sx: s.x, sy: s.y, tx: t.x, ty: t.y, dashed: !!e.style?.strokeDasharray }
    })
    .filter((e): e is NonNullable<typeof e> => e !== null)
  return { nodes, edges: miniEdges, width, height }
}

export default function SubmodelPeekBody({
  node,
  accent,
  onDrillIn,
  parentNodes,
  parentEdges,
}: PeekBodyProps) {
  const [state, setState] = useState<LoadState>({ kind: "loading" })
  // Layout is async; store the derived mini-layout in state once ready.
  const [mini, setMini] = useState<MiniLayout | null>(null)
  const [attempt, setAttempt] = useState(0)
  const cancelledRef = useRef(false)

  const smName = node.id.replace("submodel__", "")

  // Stable identity of the boundary WIRING (not node positions): only the parent
  // edges touching this submodel node matter. Keying the fetch/layout effect on
  // this means a node drag — which moves positions but not wiring — never
  // re-fetches or re-lays-out, while an actual rewire does refresh the boundary.
  // Port LABELS (resolved from connected parent nodes) are NOT tracked here, so
  // like the fetched internal graph they are an as-of-open snapshot: renaming a
  // connected parent node mid-peek refreshes the label only on rewire/Retry/
  // re-open. That's the same one-shot contract the whole peek already has.
  const boundarySig = useMemo(() => {
    return (parentEdges ?? [])
      .filter((e) => e.source === node.id || e.target === node.id)
      .map((e) => `${e.id}|${e.source}|${e.target}|${e.sourceHandle ?? ""}|${e.targetHandle ?? ""}`)
      .join(";")
  }, [parentEdges, node.id])

  // Reset to the loading state at render time when the fetch key changes — the
  // React-docs alternative to a synchronous setState in the effect body (the
  // compiler lint forbids the latter; see Tooltip.tsx's prevDisabled pattern).
  // The effect below then performs the async fetch for the new key.
  const loadKey = `${smName}#${attempt}`
  const [prevLoadKey, setPrevLoadKey] = useState(loadKey)
  if (loadKey !== prevLoadKey) {
    setPrevLoadKey(loadKey)
    setState({ kind: "loading" })
    setMini(null)
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
          setMini({ nodes: [], edges: [], width: WINDOW_W, height: WINDOW_H })
          return
        }
        // Derive the I/O boundary from the PARENT graph (same helper drill-in
        // uses) and fold it into the laid-out graph, so the peek shows the
        // wrapper's ports + their dashed links exactly as the drilled canvas does.
        const { portNodes, boundaryEdges } = buildSubmodelBoundary({
          smNodeId: node.id,
          parentNodes: parentNodes ?? [],
          parentEdges: parentEdges ?? [],
          childIds: new Set(fetchedNodes.map((n) => n.id)),
        })
        const combinedNodes = [...fetchedNodes, ...portNodes]
        const combinedEdges = [...fetchedEdges, ...boundaryEdges]
        const laidOut = await getLayoutedElements(combinedNodes, combinedEdges)
        if (cancelledRef.current) return
        setState({ kind: "loaded", nodes: laidOut, edges: combinedEdges })
        setMini(buildMiniLayout(laidOut, combinedEdges))
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
    // boundary (ports, links, AND their as-of-open labels) is re-derived only
    // when the wiring to this submodel changes (tracked by boundarySig), never on
    // a node drag or a connected-parent rename — matching the one-shot fetch of
    // the internal graph itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [smName, attempt, boundarySig])

  const retry = useCallback(() => setAttempt((a) => a + 1), [])

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

  const contentW = mini?.width ?? WINDOW_W
  const contentH = mini?.height ?? WINDOW_H

  return (
    <div className="px-1 pt-1">
      {/* Scroll container: the SVG is sized to the content, so a graph larger
          than the window scrolls inside it — a read-only window into the
          wrapper's canvas (node-explosion design §3.5; _SUBMODELS.md explode). */}
      <div
        data-testid="node-peek-scroll"
        style={{ maxHeight: WINDOW_H, maxWidth: "100%", overflow: "auto" }}
      >
      <svg
        width={contentW}
        height={contentH}
        viewBox={`0 0 ${contentW} ${contentH}`}
        style={{ display: "block" }}
        role="group"
        aria-label="Wrapper internal graph"
      >
        {/* Edges under the nodes; boundary (port↔child) links drawn dashed. */}
        {mini?.edges.map((e) => (
          <line
            key={e.id}
            x1={e.sx}
            y1={e.sy}
            x2={e.tx}
            y2={e.ty}
            stroke="rgba(255,255,255,.25)"
            strokeWidth={1}
            strokeDasharray={e.dashed ? "4 2" : undefined}
          />
        ))}
        {mini?.nodes.map(({ node: child, x, y, w, h }) => {
          const label = String(nodeData(child).label || child.id)
          const childType = effectiveNodeType(child)
          const isPort = childType === NODE_TYPES.SUBMODEL_PORT
          const childAccent = nodeTypeColors[childType] || STRUCTURE_COLORS.fallbackAccent
          const fontSize = Math.max(7, Math.min(11, h * 0.34))
          const display = label.length > 18 ? `${label.slice(0, 17)}…` : label
          const text = (
            <text
              x={x + w / 2}
              y={y + h / 2}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={fontSize}
              fill="var(--text-primary)"
              style={{ pointerEvents: "none" }}
            >
              {display}
            </text>
          )

          // Ports are boundary markers, not children — read-only, not drillable.
          // Pill-shaped + dashed outline to read as the wrapper's I/O surface.
          if (isPort) {
            const direction = String(nodeData(child).portDirection ?? "")
            return (
              <g
                key={child.id}
                data-testid={`node-peek-port-${label}`}
                role="img"
                aria-label={`${direction === "output" ? "Output" : "Input"} port: ${label}`}
              >
                <title>{label}</title>
                <rect
                  x={x}
                  y={y}
                  width={w}
                  height={h}
                  rx={h / 2}
                  fill={withAlpha(childAccent, 0.145)}
                  stroke={withAlpha(childAccent, 0.502)}
                  strokeWidth={1}
                  strokeDasharray="3 2"
                />
                {text}
              </g>
            )
          }

          return (
            <g
              key={child.id}
              data-testid={`node-peek-mini-node-${label}`}
              role="button"
              tabIndex={0}
              aria-label={`Open ${label} in submodel`}
              style={{ cursor: "pointer", outline: "none" }}
              onClick={() => onDrillIn?.(child.id)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault()
                  onDrillIn?.(child.id)
                }
              }}
            >
              <title>{label}</title>
              <rect
                x={x}
                y={y}
                width={w}
                height={h}
                rx={Math.min(6, h * 0.2)}
                fill={`${withAlpha(childAccent, 0.102)}`}
                stroke={`${withAlpha(childAccent, 0.502)}`}
                strokeWidth={1}
              />
              {text}
            </g>
          )
        })}
      </svg>
      </div>
      <div
        className="flex justify-end gap-2 px-2 pt-1 text-[10px] font-mono"
        style={{ color: "var(--text-muted)" }}
        data-testid="node-peek-counts"
      >
        {/* Internal complexity only — derived boundary ports/links are excluded
            so the count reflects the wrapper's contents, not its wiring. */}
        <span>{state.nodes.filter((n) => effectiveNodeType(n) !== NODE_TYPES.SUBMODEL_PORT).length} nodes</span>
        <span>·</span>
        <span>{state.edges.filter((e) => !e.style?.strokeDasharray).length} edges</span>
      </div>
    </div>
  )
}
