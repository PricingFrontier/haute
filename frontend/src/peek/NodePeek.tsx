/**
 * Node-explosion peek window (node-explosion design §3.5).
 *
 * A floating, screen-space panel (rendered via `createPortal` to `document.body`)
 * that opens near the peeked node and becomes the primary focus while open: it
 * is larger than a node, user-resizable (drag the bottom-right corner), and its
 * body hosts an independent, navigable React Flow (pan + wheel-zoom) showing the
 * wrapper's internals with haute's own node cards.
 *
 * Why screen-space (not an in-canvas ViewportPortal): the body's flow must pan
 * and wheel-zoom on its own, but @xyflow gates pan/zoom with an UNBOUNDED
 * `closest('.nopan'|'.nowheel')` check — an in-flow card sits under the outer
 * pane and inside `.nopan`, which would block the inner flow too. Portalling to
 * the body takes the inner flow out of the outer flow's DOM entirely, so it is a
 * normal standalone flow. The panel still anchors near the node (screen
 * coordinates computed once on open) and reads as a window into the canvas.
 *
 * Read-only with respect to editing: opening/closing/navigating mutates nothing
 * (the peek-mutates-nothing invariant). Escape (handled at App level) and the
 * close button dismiss it. Auto-closes if the peeked node disappears (delete /
 * graph refresh).
 */
import { useEffect, useState } from "react"
import { createPortal } from "react-dom"
import { useStore, useReactFlow } from "@xyflow/react"
import { X } from "lucide-react"
import NodeTypeIcon from "../components/NodeTypeIcon"
import { nodeData } from "../types/node"
import { nodeTypeColors } from "../utils/nodeTypes"
import { STRUCTURE_COLORS } from "../theme/colors"
import { withAlpha } from "../utils/color"
import { getPeekDescriptor } from "./peekRegistry"

/** Default panel size (screen px). User-resizable from here via CSS resize. */
const PEEK_WIDTH = 640
const PEEK_HEIGHT = 460
/** Screen-px gap between the node's bottom edge and the panel's top edge. */
const ANCHOR_GAP = 12
const FALLBACK_NODE_HEIGHT = 64
/** Keep the panel fully on screen. */
const MARGIN = 12

interface NodePeekProps {
  nodeId: string
  /** Close the peek (setPeek(null)). */
  onClose: () => void
  /**
   * Drill into the peeked submodel, optionally selecting a child afterwards.
   * Header "Open" passes no id; a body node click passes that child's id.
   * The caller closes the peek as part of this.
   */
  onDrillIn: (nodeId: string, selectChildId?: string) => void
}

export default function NodePeek({ nodeId, onClose, onDrillIn }: NodePeekProps) {
  const { getNode, getNodes, getEdges, getInternalNode, flowToScreenPosition } = useReactFlow()

  // Auto-close when the node vanishes (delete / refresh). Subscribe only to its
  // EXISTENCE — not its position — so panning/dragging the canvas behind the
  // open panel doesn't churn it (and doesn't reset a user resize).
  const nodeExists = useStore((s) => s.nodeLookup.has(nodeId))

  // Anchor once, on open: the node's bottom edge in SCREEN coordinates. The
  // panel then stays put (a focus window), constant-size regardless of canvas
  // zoom. Clamped so it can't open off-screen.
  const [anchor] = useState(() => {
    const internal = getInternalNode(nodeId)
    const pos = internal?.internals.positionAbsolute ?? { x: 0, y: 0 }
    const height = internal?.measured?.height ?? FALLBACK_NODE_HEIGHT
    const screen = flowToScreenPosition({ x: pos.x, y: pos.y + height + ANCHOR_GAP })
    const left = Math.min(Math.max(MARGIN, screen.x), Math.max(MARGIN, window.innerWidth - PEEK_WIDTH - MARGIN))
    const top = Math.min(Math.max(MARGIN, screen.y), Math.max(MARGIN, window.innerHeight - PEEK_HEIGHT - MARGIN))
    return { left, top }
  })

  // When the peeked node vanishes (delete / graph refresh), close the peek.
  useEffect(() => {
    if (!nodeExists) onClose()
  }, [nodeExists, onClose])

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

  return createPortal(
    <div
      data-testid="node-peek"
      className="animate-fade-in"
      onClick={stop}
      onMouseDown={stop}
      style={{
        position: "fixed",
        left: anchor.left,
        top: anchor.top,
        width: PEEK_WIDTH,
        height: PEEK_HEIGHT,
        minWidth: 360,
        minHeight: 240,
        maxWidth: "calc(100vw - 24px)",
        maxHeight: "calc(100vh - 24px)",
        resize: "both",
        overflow: "hidden",
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
        className="flex items-center gap-2 px-3 py-2 shrink-0"
        style={{ borderBottom: `1px solid ${withAlpha(accent, 0.188)}` }}
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
        />
      </div>
    </div>,
    document.body,
  )
}
