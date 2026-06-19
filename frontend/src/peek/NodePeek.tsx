/**
 * Node-explosion peek window (node-explosion design §3.5).
 *
 * A floating, screen-space panel (rendered via `createPortal` to `document.body`)
 * that opens near the peeked node and is the primary focus while open: it opens
 * sized to frame the WHOLE submodel (the body reports a bounding-box-derived
 * preferred size), is draggable by its header to reposition, and is resizable
 * from every edge and corner. Its body hosts an independent, navigable React
 * Flow (right/middle-drag pan + wheel-zoom, matching the canvas).
 *
 * Why screen-space (not an in-canvas ViewportPortal): the body's flow must pan
 * and wheel-zoom on its own, but @xyflow gates pan/zoom with an UNBOUNDED
 * `closest('.nopan'|'.nowheel')` check — an in-flow card sits inside `.nopan`,
 * which would block the inner flow too. Portalling to the body takes the inner
 * flow out of the outer flow's DOM, so it is a normal standalone flow. The panel
 * still anchors near the node (screen coords computed once on open). NodePeek
 * keeps React Flow context (to resolve the node + anchor).
 *
 * Read-only with respect to editing: opening/closing/navigating/moving/resizing
 * mutates nothing (the peek-mutates-nothing invariant). Escape (App level) and
 * the close button dismiss it. Auto-closes if the peeked node disappears.
 */
import { useCallback, useEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"
import { useStore, useReactFlow } from "@xyflow/react"
import { X } from "lucide-react"
import NodeTypeIcon from "../components/NodeTypeIcon"
import { nodeData } from "../types/node"
import { nodeTypeColors } from "../utils/nodeTypes"
import { STRUCTURE_COLORS } from "../theme/colors"
import { withAlpha } from "../utils/color"
import { getPeekDescriptor } from "./peekRegistry"

const DEFAULT_W = 560
const DEFAULT_H = 400
const MIN_W = 380
const MIN_H = 280
/** Screen-px gap between the node's bottom edge and the panel's top edge. */
const ANCHOR_GAP = 12
const FALLBACK_NODE_HEIGHT = 64
/** Keep the panel fully on screen. */
const MARGIN = 12

interface Rect {
  left: number
  top: number
  width: number
  height: number
}

/** Resize directions; "move" repositions without resizing. */
type Gesture = "move" | "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw"

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(v, hi))

/** Clamp a rect to the viewport (size first, then keep it fully on screen). */
function clampRect(r: Rect): Rect {
  const maxW = Math.max(MIN_W, window.innerWidth - 2 * MARGIN)
  const maxH = Math.max(MIN_H, window.innerHeight - 2 * MARGIN)
  const width = clamp(r.width, MIN_W, maxW)
  const height = clamp(r.height, MIN_H, maxH)
  return {
    width,
    height,
    left: clamp(r.left, MARGIN, Math.max(MARGIN, window.innerWidth - width - MARGIN)),
    top: clamp(r.top, MARGIN, Math.max(MARGIN, window.innerHeight - height - MARGIN)),
  }
}

/** Apply a move/resize gesture to the rect captured at gesture start. */
function applyGesture(mode: Gesture, start: Rect, dx: number, dy: number): Rect {
  if (mode === "move") {
    return clampRect({ ...start, left: start.left + dx, top: start.top + dy })
  }
  const maxW = Math.max(MIN_W, window.innerWidth - 2 * MARGIN)
  const maxH = Math.max(MIN_H, window.innerHeight - 2 * MARGIN)
  let { left, top, width, height } = start
  if (mode.includes("e")) width = clamp(start.width + dx, MIN_W, maxW)
  if (mode.includes("s")) height = clamp(start.height + dy, MIN_H, maxH)
  if (mode.includes("w")) {
    width = clamp(start.width - dx, MIN_W, maxW)
    left = start.left + (start.width - width) // anchor the right edge
  }
  if (mode.includes("n")) {
    height = clamp(start.height - dy, MIN_H, maxH)
    top = start.top + (start.height - height) // anchor the bottom edge
  }
  left = clamp(left, MARGIN, Math.max(MARGIN, window.innerWidth - width - MARGIN))
  top = clamp(top, MARGIN, Math.max(MARGIN, window.innerHeight - height - MARGIN))
  return { left, top, width, height }
}

interface NodePeekProps {
  nodeId: string
  /** Close the peek (setPeek(null)). */
  onClose: () => void
  /**
   * Drill into the peeked submodel, optionally selecting a child afterwards.
   * Header "Open" passes no id; a body node click passes that child's id.
   */
  onDrillIn: (nodeId: string, selectChildId?: string) => void
}

export default function NodePeek({ nodeId, onClose, onDrillIn }: NodePeekProps) {
  const { getNode, getNodes, getEdges, getInternalNode, flowToScreenPosition } = useReactFlow()

  // Auto-close when the node vanishes. Subscribe to EXISTENCE only so panning /
  // dragging the canvas behind the open panel doesn't churn (or reset) it.
  const nodeExists = useStore((s) => s.nodeLookup.has(nodeId))

  // Panel geometry. Position anchored once on open (screen coords). Size starts
  // at a default and jumps to the body's bounding-box preferred size on load
  // (unless the user has already resized). Both then user-adjustable.
  const [rect, setRect] = useState<Rect>(() => {
    const internal = getInternalNode(nodeId)
    const pos = internal?.internals.positionAbsolute ?? { x: 0, y: 0 }
    const height = internal?.measured?.height ?? FALLBACK_NODE_HEIGHT
    const screen = flowToScreenPosition({ x: pos.x, y: pos.y + height + ANCHOR_GAP })
    return clampRect({ left: screen.x, top: screen.y, width: DEFAULT_W, height: DEFAULT_H })
  })
  const rectRef = useRef(rect)
  useEffect(() => {
    rectRef.current = rect
  }, [rect])
  const userSizedRef = useRef(false)

  useEffect(() => {
    if (!nodeExists) onClose()
  }, [nodeExists, onClose])

  // Open at the size that frames the whole submodel — unless the user resized.
  const handlePreferredSize = useCallback((size: { width: number; height: number }) => {
    if (userSizedRef.current) return
    setRect((r) => clampRect({ ...r, width: size.width, height: size.height }))
  }, [])

  // Move / resize gesture: snapshot the rect, then track on window pointer moves.
  const startGesture = useCallback(
    (mode: Gesture) => (e: React.PointerEvent) => {
      if (mode === "move" && (e.target as HTMLElement).closest("button")) return
      e.preventDefault()
      e.stopPropagation()
      if (mode !== "move") userSizedRef.current = true
      const start = rectRef.current
      const startX = e.clientX
      const startY = e.clientY
      const onMove = (ev: PointerEvent) =>
        setRect(applyGesture(mode, start, ev.clientX - startX, ev.clientY - startY))
      const onUp = () => {
        window.removeEventListener("pointermove", onMove)
        window.removeEventListener("pointerup", onUp)
      }
      window.addEventListener("pointermove", onMove)
      window.addEventListener("pointerup", onUp)
    },
    [],
  )

  if (!nodeExists) return null

  const node = getNode(nodeId)
  if (!node) return null
  const descriptor = getPeekDescriptor(node)
  if (!descriptor) return null

  const data = nodeData(node)
  const accent = (data.nodeType && nodeTypeColors[data.nodeType]) || STRUCTURE_COLORS.fallbackAccent
  const label = String(data.label || nodeId)
  const Body = descriptor.Body

  const stop = (event: React.SyntheticEvent) => event.stopPropagation()

  // Edge + corner resize handles (8). Thin strips along edges, small squares at
  // corners; absolute within the panel.
  const handle = (mode: Gesture, style: React.CSSProperties, cursor: string) => (
    <div
      data-testid={`node-peek-resize-${mode}`}
      onPointerDown={startGesture(mode)}
      style={{ position: "absolute", cursor, touchAction: "none", zIndex: 2, ...style }}
    />
  )
  const T = 7 // edge handle thickness
  const C = 12 // corner handle size

  return createPortal(
    <div
      data-testid="node-peek"
      className="animate-fade-in"
      onClick={stop}
      onMouseDown={stop}
      style={{
        position: "fixed",
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height,
        display: "flex",
        flexDirection: "column",
        zIndex: 1001,
        border: `1.5px dashed ${accent}`,
        borderRadius: 12,
        background: "var(--bg-elevated)",
        boxShadow: "var(--node-shadow)",
        ["--node-accent" as string]: accent,
      }}
    >
      <div
        data-testid="node-peek-header"
        onPointerDown={startGesture("move")}
        className="flex items-center gap-2 px-3 py-2 shrink-0"
        style={{ borderBottom: `1px solid ${withAlpha(accent, 0.188)}`, cursor: "move", touchAction: "none" }}
      >
        <NodeTypeIcon nodeType={data.nodeType} size={13} />
        <span
          className="text-[10px] font-bold uppercase tracking-[0.1em] truncate"
          style={{ color: accent }}
          title={label}
        >
          PEEK · {label}
        </span>
        <button
          type="button"
          data-testid="node-peek-drill-in"
          onClick={() => onDrillIn(nodeId)}
          className="ml-auto px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-[0.08em] hover-chrome"
          style={{ border: `1px solid ${withAlpha(accent, 0.314)}`, color: accent }}
        >
          Open
        </button>
        <button
          type="button"
          data-testid="node-peek-close"
          aria-label="Close peek"
          onClick={onClose}
          className="node-close-btn flex items-center justify-center w-5 h-5 rounded text-white"
        >
          <X size={12} aria-hidden="true" />
        </button>
      </div>
      <div className="flex-1 min-h-0 px-2 py-2">
        <Body
          node={node}
          accent={accent}
          onDrillIn={(selectChildId) => onDrillIn(nodeId, selectChildId)}
          parentNodes={getNodes()}
          parentEdges={getEdges()}
          onPreferredSize={handlePreferredSize}
        />
      </div>

      {/* Resize handles: 4 edges + 4 corners. */}
      {handle("n", { top: 0, left: C, right: C, height: T }, "ns-resize")}
      {handle("s", { bottom: 0, left: C, right: C, height: T }, "ns-resize")}
      {handle("e", { top: C, bottom: C, right: 0, width: T }, "ew-resize")}
      {handle("w", { top: C, bottom: C, left: 0, width: T }, "ew-resize")}
      {handle("nw", { top: 0, left: 0, width: C, height: C }, "nwse-resize")}
      {handle("se", { bottom: 0, right: 0, width: C, height: C }, "nwse-resize")}
      {handle("ne", { top: 0, right: 0, width: C, height: C }, "nesw-resize")}
      {handle("sw", { bottom: 0, left: 0, width: C, height: C }, "nesw-resize")}
    </div>,
    document.body,
  )
}
