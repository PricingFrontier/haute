/**
 * Node-explosion peek window (node-explosion design §3.5).
 *
 * Rendered as a child of <ReactFlow> via ViewportPortal, so it lives in flow
 * coordinate space — it pans and zooms with the canvas like a node, reading as
 * "part of the canvas" rather than a modal. Anchored below the peeked node,
 * dashed accent border ("dotted line demarcating internals"), translucent
 * elevated card.
 *
 * Read-only with respect to editing: opening/closing mutates nothing (the
 * peek-mutates-nothing invariant). Click-off (handled in App's onPaneClick) and
 * Escape (handled at App level, topmost-first) close it. A click inside is inert
 * (`nodrag nopan` + stopPropagation) so the pane handlers never see it — the
 * "misclick minimally disruptive" guarantee.
 *
 * Auto-closes if the peeked node disappears (delete / graph refresh), mirroring
 * App's stale-selection effect (which resolves via the live node lookup).
 */
import { useEffect } from "react"
import { ViewportPortal, useStore, useReactFlow } from "@xyflow/react"
import { shallow } from "zustand/shallow"
import { X } from "lucide-react"
import NodeTypeIcon from "../components/NodeTypeIcon"
import { nodeData } from "../types/node"
import { nodeTypeColors } from "../utils/nodeTypes"
import { STRUCTURE_COLORS } from "../theme/colors"
import { withAlpha } from "../utils/color"
import { getPeekDescriptor } from "./peekRegistry"

const PEEK_WIDTH = 420
/** Flow-px gap between node bottom edge and peek top edge. */
const ANCHOR_GAP = 12
const FALLBACK_NODE_HEIGHT = 64

interface NodePeekProps {
  nodeId: string
  /** Close the peek (setPeek(null)). */
  onClose: () => void
  /**
   * Drill into the peeked submodel, optionally selecting a child afterwards.
   * Header "Open" passes no id; a mini-node click passes that child's id.
   * The caller closes the peek as part of this.
   */
  onDrillIn: (nodeId: string, selectChildId?: string) => void
}

export default function NodePeek({ nodeId, onClose, onDrillIn }: NodePeekProps) {
  const { getNode } = useReactFlow()

  // Subscribe to the live anchor so the peek tracks node drags. Reading from
  // nodeLookup (the internal store) re-renders on every position change. When
  // the node id vanishes (delete / refresh) the selector returns null and the
  // auto-close effect fires.
  // `shallow` equality keeps the always-mounted overlay from re-rendering on
  // every unrelated store mutation (selection, measurement, other-node drags):
  // the selector allocates a fresh {x,y} each call, so without it the default
  // Object.is reference check never matches and the peek churns on the canvas
  // hot path. With shallow it re-renders only when this node's x/y actually
  // move (or the node vanishes → null).
  const anchor = useStore((s) => {
    const internal = s.nodeLookup.get(nodeId)
    if (!internal) return null
    const pos = internal.internals.positionAbsolute
    const height = internal.measured?.height ?? FALLBACK_NODE_HEIGHT
    return { x: pos.x, y: pos.y + height + ANCHOR_GAP }
  }, shallow)

  // Stale-node auto-close: when the anchor goes null the node is gone.
  useEffect(() => {
    if (anchor === null) onClose()
  }, [anchor, onClose])

  if (anchor === null) return null

  // Resolve the node + its peek descriptor. getNode returns the public node
  // (with data); the registry decides the body.
  const node = getNode(nodeId)
  if (!node) return null
  const descriptor = getPeekDescriptor(node)
  if (!descriptor) return null

  const data = nodeData(node)
  const accent = (data.nodeType && nodeTypeColors[data.nodeType]) || STRUCTURE_COLORS.fallbackAccent
  const label = String(data.label || nodeId)
  const Body = descriptor.Body

  const stop = (event: React.SyntheticEvent) => event.stopPropagation()

  return (
    <ViewportPortal>
      <div
        data-testid="node-peek"
        className="nodrag nopan animate-fade-in"
        onClick={stop}
        onMouseDown={stop}
        style={{
          position: "absolute",
          transform: `translate(${anchor.x}px, ${anchor.y}px)`,
          width: PEEK_WIDTH,
          zIndex: 1001,
          border: `1.5px dashed ${accent}`,
          borderRadius: 12,
          background: "var(--bg-elevated)",
          opacity: 0.97,
          boxShadow: "var(--node-shadow)",
          ["--node-accent" as string]: accent,
        }}
      >
        <div
          data-testid="node-peek-header"
          className="flex items-center gap-2 px-3 py-2"
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
        <div className="px-2 py-2">
          <Body
            node={node}
            accent={accent}
            onDrillIn={(selectChildId) => onDrillIn(nodeId, selectChildId)}
          />
        </div>
      </div>
    </ViewportPortal>
  )
}
