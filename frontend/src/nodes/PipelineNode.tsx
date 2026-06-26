import { memo, useEffect, useMemo } from "react"
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
import { apiInputEmitPortLabels } from "../utils/apiInputPorts"

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
 * Commit-6 multi-frame: when an apiInput has 2+ `emit: true` tables, we
 * render one labelled Handle per table (id = table label). Otherwise the
 * legacy single Handle covers the default single-frame use.
 *
 * Returning a JSX list rather than mutating render order keeps the call
 * sites at the three zoom levels each a one-line switch.
 *
 * Test ids are positional, not semantic: `output-connector[<idx>]:<node
 * label>`, where idx is the visual top-to-bottom frame order. Single-frame
 * nodes (all non-apiInput types today, and apiInputs with 0–1 emit
 * tables) are always index 0, which stays stable when single-frame
 * emission moves to singleton-dict. Ids derive from volatile editor
 * state (emit topology) and recompute on change — fine for a UI harness
 * reading the live DOM, as long as the harness isn't itself mutating
 * emit topology mid-assertion.
 */
function _SourceHandles({
  isApiInput,
  config,
  accent,
  isConnectableEnd,
  nodeLabel,
}: {
  isApiInput: boolean
  config: Record<string, unknown> | undefined
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
  // id the executor could not resolve (W1.4). Returns `[]` for the
  // 0/1-emit case.
  const labels = apiInputEmitPortLabels(config)
  if (labels.length === 0) {
    // Single-frame fallback (one or zero emit:true tables, or no tables key):
    // preserve the legacy default Handle so existing single-frame pipelines
    // continue to work unchanged.
    return (
      <Handle
        type="source"
        position={Position.Right}
        isConnectableEnd={isConnectableEnd}
        data-testid={`output-connector[0]:${nodeLabel}`}
      />
    )
  }
  // Multi-frame: stack labelled Handles down the right edge. Each
  // Handle's `id` is the table's label — React Flow propagates this to
  // `onConnect.params.sourceHandle` when a user drags from it.
  return (
    <>
      {labels.map((label, idx) => {
        // Stack the dots vertically; `top` is a percentage so the
        // Handles space evenly down the right edge regardless of node
        // height.
        const topPct = ((idx + 1) / (labels.length + 1)) * 100
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

  // Bundle 3c — emit-table labels for the right-edge handles.  Source
  // of truth shared between (a) `_SourceHandles` which renders the
  // Handles, (b) the body label list, and (c) the
  // `useUpdateNodeInternals` effect that nudges React Flow to re-measure
  // when the topology changes.  Logic mirrors `_SourceHandles`: only
  // emit:true tables with a valid (non-blank, non-duplicate) label
  // count — handle ids are raw labels, never synthesized (W1.4).
  const emitTableLabels = useMemo<string[]>(
    () => (isDeployInput ? apiInputEmitPortLabels(nodeData.config) : []),
    [isDeployInput, nodeData.config],
  )

  // Pipe-joined signature is a stable value-equality proxy for the
  // labels array; useEffect's value-equality on strings means it
  // refires only when the labels actually change, not on every parent
  // re-render that produces a fresh `nodeData.config` reference with
  // unchanged emit topology (e.g. a column edit inside a table).
  const emitTablesSig = emitTableLabels.join("|")
  const edgeJoinJoinHandlePosition = useStore((s) =>
    _edgeJoinJoinHandlePosition(s, id, nodeType),
  )
  const updateNodeInternals = useUpdateNodeInternals()
  useEffect(() => {
    updateNodeInternals(id)
  }, [id, emitTablesSig, edgeJoinJoinHandlePosition, updateNodeInternals])

  const sourceHandles = !isSinkOnly ? (
    <_SourceHandles
      isApiInput={isDeployInput}
      config={nodeData.config as Record<string, unknown> | undefined}
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
  // 2+ emit tables = multi-frame; render the visual frame-to-label
  // mapping on the body.  0/1 emit = single-frame fallback (Handle is
  // unambiguous; no labels needed).
  const showBodyLabels = isDeployInput && emitTableLabels.length >= 2
  const traceActive = !!nodeData._traceActive
  const traceDimmed = !!nodeData._traceDimmed
  const hoverDimmed = !!nodeData._hoverDimmed
  const traceValue = nodeData._traceValue
  const traceMotionDisabled = !!nodeData._traceMotionDisabled
  const hasWarnings = (nodeData._schemaWarnings?.length ?? 0) > 0
  const zoomLevel = useStore(zoomSelector)

  const dimmed = traceDimmed || hoverDimmed

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
            className={`pointer-events-none absolute -right-0.5 bottom-1 size-1.5 rounded-full ${nodeData._status === "running" ? "animate-pulse-dot" : ""}`}
            style={{ backgroundColor: statusColors[nodeData._status] }}
            role="status"
            aria-label={`Node ${nodeData._status}`}
            data-testid="edge-join-status-indicator"
          />
        )}
        {hasWarnings && nodeData._status !== "error" && (
          <span
            className="pointer-events-none absolute -right-0.5 top-1 size-1.5 rounded-full"
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
          border: selected ? `3px solid ${accent}` : `3px solid ${accent}40`,
          boxShadow: "var(--node-shadow)",
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

  // Shared styling for medium + full modes
  const border = traceActive || selected
    ? `3px solid ${accent}`
    : isInstance
      ? `3px dashed ${accent}60`
      : `3px solid ${accent}30`
  const shadow = traceActive
    ? `0 0 12px ${accent}40, var(--node-shadow)`
    : "var(--node-shadow)"
  const containerStyle = {
    background: "var(--bg-elevated)",
    border,
    boxShadow: shadow,
    opacity: dimmed ? 0.25 : 1,
    transition: traceMotionDisabled ? "none" : "border-color 0.15s ease, opacity 0.2s ease, box-shadow 0.2s ease",
  }

  // Header bar border-radius: matches inner edge of container (outer radius minus
  // 3px border).  Container is rounded-xl (12px) → inner 9px, or rounded-2xl
  // (16px, pill) → inner 13px.  Previous values (11 / 15) assumed a 1px border
  // and showed as "whiskers" poking past the container corners at high zoom.
  const headerRadius = isPill ? "13px 13px 0 0" : "9px 9px 0 0"

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
          style={{ background: `${accent}30`, borderRadius: headerRadius }}
        >
          <Icon size={14} style={{ color: accent }} className="shrink-0" />
          <span className="text-[10px] font-bold uppercase tracking-[0.1em] shrink-0" style={{ color: accent }}>
            {typeLabel}
          </span>
        </div>
        {/* Body */}
        <div className="px-3 py-1.5">
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
        style={{ background: `${accent}30`, borderRadius: headerRadius }}
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
          right-aligned label column visually maps each emit table to
          its handle on the right edge, in the same top-to-bottom order
          the Handles are stacked.  Hidden for 0/1 emit (single-frame
          fallback is unambiguous) and for all non-apiInput types. */}
      <div className="px-3 py-2">
        <div className="flex items-start gap-2">
          <div className="flex-1 min-w-0">
            <div className="font-semibold text-[13px] leading-tight truncate" style={{ color: "var(--text-primary)" }}>
              {nodeData.label}
            </div>
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
          </div>
          {showBodyLabels && (
            <div className="flex flex-col gap-0.5 shrink-0 text-right">
              {emitTableLabels.map((label) => (
                <span
                  key={label}
                  data-testid={`api-input-body-label-${label}`}
                  className="text-[10px] font-mono leading-tight truncate max-w-[100px]"
                  style={{ color: "var(--text-muted)" }}
                  title={label}
                >
                  {label}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>

      {sourceHandles}
    </div>
  )
}

export default memo(PipelineNode)
