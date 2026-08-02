import { memo, useEffect, useMemo, type CSSProperties } from "react"
import { Handle, Position, useStore, useUpdateNodeInternals, type InternalNode, type NodeProps, type ReactFlowState } from "@xyflow/react"
import { Radio, Link2 } from "lucide-react"
import PolarsIcon from "../components/PolarsIcon"
import { NODE_TYPES, NODE_TYPE_META, SOURCE_ONLY_TYPES, SINK_ONLY_TYPES, PILL_TYPES, nodeTypeIcons, nodeTypeColors, nodeTypeLabels, type NodeTypeValue } from "../utils/nodeTypes"
import { formatValueCompact } from "../utils/formatValue"
import useSettingsStore from "../stores/useSettingsStore"
import { STATUS_COLORS } from "../theme/colors"
import type { PipelineFlowNode } from "../types/node"
import { EDGE_JOIN_BASE_HANDLE, EDGE_JOIN_JOIN_BOTTOM_HANDLE, EDGE_JOIN_JOIN_HANDLE } from "../utils/edgeJoinRoles"
import { DEFAULT_TARGET_HANDLE } from "../utils/flowHandles"
import { apiInputFrameLabels } from "../utils/apiInputPorts"
import FramePortRows from "./FramePortRows"

const statusColors: Record<string, string> = {
  ok: "var(--success)",
  error: "var(--danger)",
  running: STATUS_COLORS.running,
}

/** Isolated component so only LiveSwitch nodes subscribe to the settings store. */
function LiveSwitchBadge({ accent }: { accent: string }) {
  const activeSource = useSettingsStore((s) => s.activeSource)
  if (activeSource !== "live") return null
  return (
    <span
      className="ml-auto inline-flex items-center px-1.5 py-0.5 rounded-full text-[9px] font-bold uppercase tracking-[0.08em] shrink-0"
      style={{ background: `${accent}1f`, color: accent, border: `1px solid ${accent}33` }}
    >
      LIVE
    </span>
  )
}

/** Zoom-level selector — only re-renders when crossing a threshold, not on every pixel. */
const zoomSelector = (s: { transform: [number, number, number] }) => {
  const z = s.transform[2]
  if (z > 0.55) return "full"
  if (z > 0.3) return "medium"
  return "compact"
}

type EdgeJoinJoinHandlePosition = Position.Top | Position.Bottom | "both"

const EDGE_JOIN_MARKER_HANDLE_OFFSET_X = 4
const EDGE_JOIN_MARKER_HANDLE_OFFSET_Y = 6
const EDGE_JOIN_HANDLE_CLASS_NAME = "edge-join-handle edge-join-handle--suppress-hover"
const EDGE_JOIN_OUTPUT_HANDLE_CLASS_NAME = `${EDGE_JOIN_HANDLE_CLASS_NAME} edge-join-output-handle`

function _nodeCenterY(node: InternalNode): number {
  return node.internals.positionAbsolute.y + (node.measured.height ?? node.height ?? 0) / 2
}

function _edgeJoinJoinHandlePosition(
  state: ReactFlowState,
  nodeId: string,
  nodeType: string,
): EdgeJoinJoinHandlePosition {
  if (nodeType !== NODE_TYPES.EDGE_JOIN) return Position.Top

  const joinEdge = state.edges.find(
    (edge) => edge.target === nodeId && edge.targetHandle === EDGE_JOIN_JOIN_HANDLE,
  )
  if (!joinEdge) return "both"

  const edgeJoinNode = state.nodeLookup.get(nodeId)
  const sourceNode = state.nodeLookup.get(joinEdge.source)
  if (!edgeJoinNode || !sourceNode) return Position.Top

  return _nodeCenterY(sourceNode) > _nodeCenterY(edgeJoinNode)
    ? Position.Bottom
    : Position.Top
}

function _isDraggingFromEdgeJoinOutput(state: ReactFlowState): boolean {
  const { inProgress, fromHandle } = state.connection
  if (!inProgress || fromHandle?.type !== "source") return false
  const sourceNode = state.nodeLookup.get(fromHandle.nodeId)
  return sourceNode?.internals.userNode.data.nodeType === NODE_TYPES.EDGE_JOIN
}

/** Source-Handle setup for the right edge of the node.
 *
 * At medium/compact zoom, each apiInput frame renders one labelled Handle
 * (id = table label). Full-detail apiInput frame rows mount their own Handles
 * so the row name and output dot share one layout coordinate system.
 *
 * Returning a JSX list rather than mutating render order keeps the call
 * sites at the three zoom levels each a one-line switch.
 *
 * Test ids are positional, not semantic: `output-connector[<idx>]:<node
 * label>`, where idx is the visual top-to-bottom frame order. Ids derive from volatile editor
 * state (emit topology) and recompute on change — fine for a UI harness
 * reading the live DOM, as long as the harness isn't itself mutating
 * emit topology mid-assertion.
 */
function _SourceHandles({
  isApiInput,
  frameLabels,
  accent,
  isConnectableEnd,
  nodeLabel,
}: {
  isApiInput: boolean
  frameLabels: string[]
  accent: string
  isConnectableEnd: boolean
  nodeLabel: string
}) {
  if (!isApiInput) {
    return (
      <Handle
        type="source"
        position={Position.Right}
        isConnectableEnd={isConnectableEnd}
        data-testid={`output-connector[0]:${nodeLabel}`}
      />
    )
  }
  // Single source of truth for the frame labels — shared with the body
  // label column and the edit-time edge reconciler so the canvas, the
  // editor, and edge validation can never disagree about which frames
  // exist (see `utils/apiInputPorts`). Handle ids are the RAW table
  // labels (the id space the backend round-trips); blank/duplicate
  // labels yield NO handle — never a synthesized `port_<idx>`/`__<idx>`
  // id the executor could not resolve (W1.4). A sole eligible frame is
  // labelled exactly like every other eligible frame.
  if (frameLabels.length === 0) {
    return null
  }
  // One or more eligible frames: stack labelled Handles down the right edge. Each
  // Handle's `id` is the table's label — React Flow propagates this to
  // `onConnect.params.sourceHandle` when a user drags from it.
  return (
    <>
      {frameLabels.map((label, idx) => {
        // Stack the dots vertically; `top` is a percentage so the
        // Handles space evenly down the right edge regardless of node
        // height.
        const topPct = ((idx + 1) / (frameLabels.length + 1)) * 100
        return (
          <Handle
            key={label}
            id={label}
            type="source"
            position={Position.Right}
            isConnectableEnd={isConnectableEnd}
            style={{ top: `${topPct}%`, background: accent }}
            data-testid={`output-connector[${idx}]:${nodeLabel}`}
          />
        )
      })}
    </>
  )
}


function _TargetHandles({
  nodeType,
  accent,
  edgeJoinJoinHandlePosition,
  nodeLabel,
}: {
  nodeType: string
  accent: string
  edgeJoinJoinHandlePosition: EdgeJoinJoinHandlePosition
  nodeLabel: string
}) {
  if (nodeType !== NODE_TYPES.EDGE_JOIN) {
    return (
      <Handle
        id={DEFAULT_TARGET_HANDLE}
        type="target"
        position={Position.Left}
        data-testid={`input-connector[0]:${nodeLabel}`}
      />
    )
  }
  const topJoinHandleStyle = { left: "50%", top: `${EDGE_JOIN_MARKER_HANDLE_OFFSET_Y}px`, background: accent }
  const bottomJoinHandleStyle = { left: "50%", bottom: `${EDGE_JOIN_MARKER_HANDLE_OFFSET_Y}px`, background: accent }
  const renderJoinHandle = (
    position: Position.Top | Position.Bottom,
    id = EDGE_JOIN_JOIN_HANDLE,
    testId = "edge-join-join-handle",
  ) => (
    <Handle
      id={id}
      className={EDGE_JOIN_HANDLE_CLASS_NAME}
      type="target"
      position={position}
      style={position === Position.Bottom ? bottomJoinHandleStyle : topJoinHandleStyle}
      data-testid={testId}
    />
  )
  return (
    <>
      <Handle
        id={EDGE_JOIN_BASE_HANDLE}
        className={EDGE_JOIN_HANDLE_CLASS_NAME}
        type="target"
        position={Position.Left}
        style={{ left: `${EDGE_JOIN_MARKER_HANDLE_OFFSET_X}px`, top: "50%", background: accent }}
        data-testid="edge-join-base-handle"
      />
      {edgeJoinJoinHandlePosition === "both" ? (
        <>
          {renderJoinHandle(Position.Top)}
          {renderJoinHandle(Position.Bottom, EDGE_JOIN_JOIN_BOTTOM_HANDLE, "edge-join-join-bottom-handle")}
        </>
      ) : renderJoinHandle(edgeJoinJoinHandlePosition)}
    </>
  )
}

function PipelineNode({ id, data: nodeData, selected }: NodeProps<PipelineFlowNode>) {
  const nodeType = nodeData.nodeType || NODE_TYPES.POLARS
  const Icon = nodeTypeIcons[nodeType] || PolarsIcon
  const accent = nodeTypeColors[nodeType] || nodeTypeColors[NODE_TYPES.POLARS]
  const typeLabel = nodeTypeLabels[nodeType] || "NODE"
  const isDeployInput = nodeType === NODE_TYPES.API_INPUT
  const isLiveSwitch = nodeType === NODE_TYPES.LIVE_SWITCH
  const isInstance = !!(nodeData.config?.instanceOf)
  const isSourceOnly = SOURCE_ONLY_TYPES.has(nodeType)
  const isSinkOnly = SINK_ONLY_TYPES.has(nodeType)
  const isPill = PILL_TYPES.has(nodeType)
  const isCompactNode = NODE_TYPE_META[nodeType as NodeTypeValue]?.size === "compact"
  const sourceHandlesCanEnd = !useStore(_isDraggingFromEdgeJoinOutput)

  // Bundle 3c — one ordered frame-label list is the source of truth for
  // source Handles, full-detail body rows, and the re-measure signature.
  const frameLabels = useMemo<string[]>(
    () => (isDeployInput ? apiInputFrameLabels(nodeData.config) : []),
    [isDeployInput, nodeData.config],
  )

  // JSON serialization is a collision-safe value-equality proxy for the
  // labels arrays; raw labels are unrestricted strings, so delimiter joins
  // could alias distinct label sets (for example, ["a|b", "c"] and
  // ["a", "b|c"]) and skip a required re-measure. The effect still
  // refires only when labels actually change, not on a fresh config object
  // whose topology is unchanged (e.g. a column edit inside a table).
  const frameLabelsSig = JSON.stringify(frameLabels)
  const zoomLevel = useStore(zoomSelector)
  const edgeJoinJoinHandlePosition = useStore((s) =>
    _edgeJoinJoinHandlePosition(s, id, nodeType),
  )
  const updateNodeInternals = useUpdateNodeInternals()
  useEffect(() => {
    updateNodeInternals(id)
  }, [
    id,
    frameLabelsSig,
    zoomLevel,
    edgeJoinJoinHandlePosition,
    updateNodeInternals,
  ])

  const sourceHandles = !isSinkOnly ? (
    <_SourceHandles
      isApiInput={isDeployInput}
      frameLabels={frameLabels}
      accent={accent}
      isConnectableEnd={sourceHandlesCanEnd}
      nodeLabel={nodeData.label}
    />
  ) : null
  const targetHandles = !isSourceOnly ? (
    <_TargetHandles
      nodeType={nodeType}
      accent={accent}
      edgeJoinJoinHandlePosition={edgeJoinJoinHandlePosition}
      nodeLabel={nodeData.label}
    />
  ) : null
  // At full detail, visible frame rows own the API-input source Handles. At
  // medium/compact detail, `_SourceHandles` renders the same labelled handles
  // on the node container; with no visible frame it renders nothing.
  const showApiInputFrameRows =
    isDeployInput && zoomLevel === "full" && frameLabels.length > 0
  const traceActive = !!nodeData._traceActive
  const traceDimmed = !!nodeData._traceDimmed
  const hoverDimmed = !!nodeData._hoverDimmed
  const traceValue = nodeData._traceValue
  const traceMotionDisabled = !!nodeData._traceMotionDisabled
  const hasWarnings = (nodeData._schemaWarnings?.length ?? 0) > 0
  const dimmed = traceDimmed || hoverDimmed

  // Comparison-view diff highlight (S11): a ring on the CARD — the same element as
  // the selection border — so the highlight is consistent across views and the
  // correct shape for every node type (pills follow the card's border-radius).
  // Solid glow for add/remove/change; dashed outline for a moved-only node.
  const diffStatus = nodeData._diffStatus
  const diffVar = diffStatus ? `var(--diff-${diffStatus})` : null
  const diffShadow =
    diffVar && diffStatus !== "moved"
      ? `0 0 0 2px ${diffVar}, 0 0 14px 2px color-mix(in srgb, ${diffVar} 45%, transparent)`
      : null
  const diffOutline: CSSProperties =
    diffStatus === "moved" ? { outline: "2px dashed var(--diff-moved)", outlineOffset: "3px" } : {}

  // Accessible label: "{Type} node: {label}" + status
  const typeName = NODE_TYPE_META[nodeType as NodeTypeValue]?.name || typeLabel
  const statusText = nodeData._status ? `, status: ${nodeData._status}` : ""
  const ariaLabel = `${typeName} node: ${nodeData.label}${statusText}${isInstance ? ", instance" : ""}${traceActive ? ", trace active" : ""}`

  if (nodeType === NODE_TYPES.EDGE_JOIN) {
    const markerBackground = `${accent}30`
    const markerBorder = traceActive || selected
      ? `2px solid ${accent}`
      : `1px solid ${accent}60`

    return (
      <div
        data-testid={`node-${nodeData.label}`}
        aria-label={ariaLabel}
        role="button"
        className="edge-join-node-root relative w-[40px] h-[34px] cursor-pointer rounded-full"
        style={{
          opacity: dimmed ? 0.25 : 1,
          transition: traceMotionDisabled ? "none" : "opacity 0.2s ease",
        }}
      >
        <div
          aria-hidden="true"
          data-testid="edge-join-marker"
          className="pointer-events-none absolute left-1/2 top-1/2 w-[32px] h-[22px] -translate-x-1/2 -translate-y-1/2 rounded-full"
          style={{
            background: markerBackground,
            border: markerBorder,
          }}
        />
        {nodeData._status && (
          <span
            className={`pointer-events-none absolute right-[6px] bottom-[8px] size-1.5 rounded-full ${nodeData._status === "running" ? "animate-pulse-dot" : ""}`}
            style={{ backgroundColor: statusColors[nodeData._status] }}
            role="status"
            aria-label={`Node ${nodeData._status}`}
            data-testid="edge-join-status-indicator"
          />
        )}
        {hasWarnings && nodeData._status !== "error" && (
          <span
            className="pointer-events-none absolute right-[6px] top-[8px] size-1.5 rounded-full"
            style={{ backgroundColor: "var(--warning-strong)" }}
            role="status"
            aria-label="Node has schema warnings"
            data-testid="edge-join-warning-indicator"
          />
        )}
        {targetHandles}
        {!isSinkOnly && (
          <Handle
            className={EDGE_JOIN_OUTPUT_HANDLE_CLASS_NAME}
            type="source"
            position={Position.Right}
            isConnectableEnd={sourceHandlesCanEnd}
            style={{ right: `${EDGE_JOIN_MARKER_HANDLE_OFFSET_X}px`, background: accent }}
            data-testid="edge-join-output-handle"
          />
        )}
      </div>
    )
  }

  // Compact mode: tinted background with icon + label — readable at far zoom
  if (zoomLevel === "compact") {
    return (
      <div
        data-testid={`node-${nodeData.label}`}
        aria-label={ariaLabel}
        role="button"
        className={`relative ${isCompactNode ? "w-[112px]" : "w-[160px]"} cursor-pointer ${isPill ? "rounded-full" : "rounded-lg"}`}
        style={{
          background: `linear-gradient(${accent}28, ${accent}1a), var(--bg-elevated)`,
          border: selected
            ? `3px solid ${accent}`
            : `3px solid color-mix(in srgb, ${accent} 25%, var(--bg-canvas))`,
          boxShadow: [diffShadow, "var(--node-shadow)"].filter(Boolean).join(", "),
          ...diffOutline,
          opacity: dimmed ? 0.25 : 1,
          transition: traceMotionDisabled ? "none" : "opacity 0.2s ease",
        }}
      >
        {targetHandles}
        <div className="flex items-center gap-2 pl-3 pr-2.5 py-2">
          <Icon size={14} style={{ color: accent }} className="shrink-0" />
          <div className="font-bold text-[12px] leading-tight truncate" style={{ color: "var(--text-primary)" }}>
            {nodeData.label}
          </div>
        </div>
        {sourceHandles}
      </div>
    )
  }

  // Shared styling for medium + full modes. Every layer is OPAQUE so none of the
  // canvas bleeds through (S38): the tinted border and banner are composited as
  // solid colours via color-mix (over the canvas for the border, over the card
  // surface for the banner) rather than drawn as semi-transparent overlays.
  const border = traceActive || selected
    ? `3px solid ${accent}`
    : isInstance
      ? `3px dashed color-mix(in srgb, ${accent} 38%, var(--bg-canvas))`
      : `3px solid color-mix(in srgb, ${accent} 19%, var(--bg-canvas))`
  const shadow = traceActive
    ? `0 0 12px ${accent}40, var(--node-shadow)`
    : "var(--node-shadow)"
  // No background on the container itself — the opaque face (banner + body) is
  // sized to the border MEDIAN below, so the card background never extends under
  // the full border (where it would otherwise read as a tinted bleed-through).
  const containerStyle: CSSProperties = {
    border,
    boxShadow: [diffShadow, shadow].filter(Boolean).join(", "),
    ...diffOutline,
    opacity: dimmed ? 0.25 : 1,
    transition: traceMotionDisabled ? "none" : "border-color 0.15s ease, opacity 0.2s ease, box-shadow 0.2s ease",
  }

  // The banner AND the body track the MEDIAN of the 3px border on every boundary
  // — curves and straight edges. Radius = outer − border/2 (rounded-2xl 16→14.5,
  // rounded-xl 12→10.5); a −1.5px (half-border) negative margin pulls each face
  // edge out to the border centreline, so the face is one opaque shape bounded by
  // the median and the visible border stays a uniform half-border wide all round.
  // (This opaque-face approach supersedes the earlier inner-edge radius — 9 / 13
  // for a 3px border — that aimed to stop corner "whiskers"; the median radius
  // plus the −1.5px inset solves the same whisker artefact more uniformly.)
  const bannerBg = `color-mix(in srgb, ${accent} 19%, var(--bg-elevated))`
  const headerRadius = isPill ? "14.5px 14.5px 0 0" : "10.5px 10.5px 0 0"
  const headerInset = { marginTop: "-1.5px", marginLeft: "-1.5px", marginRight: "-1.5px" }
  const bodyStyle = {
    background: "var(--bg-elevated)",
    borderRadius: isPill ? "0 0 14.5px 14.5px" : "0 0 10.5px 10.5px",
    marginLeft: "-1.5px",
    marginRight: "-1.5px",
    marginBottom: "-1.5px",
  }

  // Medium mode: header bar + label, no extra badges
  if (zoomLevel === "medium") {
    return (
      <div
        data-testid={`node-${nodeData.label}`}
        aria-label={ariaLabel}
        role="button"
        className={`relative ${isCompactNode ? "w-[128px]" : "w-[240px]"} cursor-pointer ${isPill ? "rounded-2xl" : "rounded-xl"}`}
        style={containerStyle}
      >
        {targetHandles}
        {/* Header bar */}
        <div
          className="flex items-center gap-2 px-3 py-1.5"
          style={{ background: bannerBg, borderRadius: headerRadius, ...headerInset }}
        >
          <Icon size={14} style={{ color: accent }} className="shrink-0" />
          <span className="text-[10px] font-bold uppercase tracking-[0.1em] shrink-0" style={{ color: accent }}>
            {typeLabel}
          </span>
        </div>
        {/* Body */}
        <div className="px-3 py-1.5" style={bodyStyle}>
          <div className="font-semibold text-[13px] leading-tight truncate" style={{ color: "var(--text-primary)" }}>
            {nodeData.label}
          </div>
        </div>
        {sourceHandles}
      </div>
    )
  }

  // Full mode: header bar with badges + body with label and trace
  return (
    <div
      data-testid={`node-${nodeData.label}`}
      aria-label={ariaLabel}
      role="button"
      className={`relative ${isCompactNode ? "w-[128px]" : "w-[240px]"} cursor-pointer ${isPill ? "rounded-2xl" : "rounded-xl"}`}
      style={containerStyle}
    >
      {targetHandles}

      {/* Header bar */}
      <div
        className="flex items-center gap-2 px-3 py-1.5"
        style={{ background: `${accent}30`, borderRadius: headerRadius, ...headerInset }}
      >
        <Icon size={16} style={{ color: accent }} className="shrink-0" />
        <span
          className="text-[10px] font-bold uppercase tracking-[0.1em] shrink-0"
          style={{ color: accent }}
        >
          {typeLabel}
        </span>
        {isInstance && (
          <span
            className="ml-auto inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[9px] font-bold uppercase tracking-[0.08em] shrink-0"
            style={{ background: `${accent}15`, color: accent, border: `1px solid ${accent}25` }}
            title={`Instance of ${nodeData.config?.instanceOf}`}
          >
            <Link2 size={8} />
            Instance
          </span>
        )}
        {isDeployInput && (
          <span
            className="ml-auto inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[9px] font-bold uppercase tracking-[0.08em] shrink-0"
            style={{ background: `${accent}1f`, color: accent, border: `1px solid ${accent}33` }}
          >
            <Radio size={8} />
            API
          </span>
        )}
        {isLiveSwitch && <LiveSwitchBadge accent={accent} />}
        {nodeData._status && (
          <span
            className={`${isDeployInput ? "" : "ml-auto "} w-[7px] h-[7px] rounded-full shrink-0 ${nodeData._status === "running" ? "animate-pulse-dot" : ""}`}
            style={{ backgroundColor: statusColors[nodeData._status] }}
            role="status"
            aria-label={`Node ${nodeData._status}`}
          />
        )}
        {hasWarnings && nodeData._status !== "error" && (
          <span
            className={`${!nodeData._status && !isDeployInput ? "ml-auto " : ""}w-[7px] h-[7px] rounded-full shrink-0`}
            style={{ backgroundColor: "var(--warning-strong)" }}
            role="status"
            aria-label="Node has schema warnings"
          />
        )}
      </div>

      {/* Body — Bundle 3c: when this is a multi-frame apiInput, the
          full-detail frame rows own the right-edge Handle and display each
          visible frame in top-to-bottom order. Medium/compact modes render the
          same handles through `_SourceHandles`; zero-frame apiInputs render
          none. All non-apiInput types retain their existing body.
          `bodyStyle` keeps the opaque-face surface consistent with
          medium mode (VC S38 opaque-face redesign). */}
      <div className="px-3 py-2" style={bodyStyle}>
        {showApiInputFrameRows ? (
          <>
            {traceActive && traceValue !== undefined && (
              <div
                className="mb-1 px-1.5 py-0.5 rounded text-[11px] font-mono truncate"
                style={{
                  background: `${accent}18`,
                  color: accent,
                  border: `1px solid ${accent}30`,
                  maxWidth: "100%",
                }}
              >
                {formatValueCompact(traceValue)}
              </div>
            )}
            <FramePortRows
              ports={frameLabels.map((label) => ({ id: label, label }))}
              direction="source"
              accent={accent}
              testIdPrefix="api-input"
              isConnectableEnd={sourceHandlesCanEnd}
              handleTestId={(_port, index) =>
                `output-connector[${index}]:${nodeData.label}`
              }
            />
          </>
        ) : (
          <>
            <div className="font-semibold text-[13px] leading-tight truncate" style={{ color: "var(--text-primary)" }}>
              {nodeData.label}
            </div>
            {isDeployInput && (
              <div className="mt-1 text-[11px] leading-tight truncate" style={{ color: "var(--text-muted)" }}>
                No emitted frames
              </div>
            )}
            {traceActive && traceValue !== undefined && (
              <div
                className="mt-1 px-1.5 py-0.5 rounded text-[11px] font-mono truncate"
                style={{
                  background: `${accent}18`,
                  color: accent,
                  border: `1px solid ${accent}30`,
                  maxWidth: "100%",
                }}
              >
                {formatValueCompact(traceValue)}
              </div>
            )}
          </>
        )}
      </div>

      {!showApiInputFrameRows && sourceHandles}
    </div>
  )
}

export default memo(PipelineNode)
