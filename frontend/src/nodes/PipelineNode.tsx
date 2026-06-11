import { memo, useEffect, useMemo } from "react"
import { Handle, Position, useStore, useUpdateNodeInternals, type InternalNode, type NodeProps, type ReactFlowState } from "@xyflow/react"
import { Radio, Link2 } from "lucide-react"
import PolarsIcon from "../components/PolarsIcon"
import Tooltip from "../components/Tooltip"
import NodeTypeTooltip from "../components/NodeTypeTooltip"
import { NODE_TYPES, NODE_TYPE_META, SOURCE_ONLY_TYPES, SINK_ONLY_TYPES, PILL_TYPES, nodeTypeIcons, nodeTypeColors, nodeTypeLabels, type NodeTypeValue } from "../utils/nodeTypes"
import { formatValueCompact } from "../utils/formatValue"
import useSettingsStore from "../stores/useSettingsStore"
import { STATUS_COLORS } from "../theme/colors"
import type { PipelineFlowNode } from "../types/node"
import { EDGE_JOIN_BASE_HANDLE, EDGE_JOIN_JOIN_BOTTOM_HANDLE, EDGE_JOIN_JOIN_HANDLE } from "../utils/edgeJoinRoles"
import { DEFAULT_TARGET_HANDLE } from "../utils/flowHandles"

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

/**
 * Node-TYPE tooltip open delay for the canvas (Nick's whole-body-trigger
 * ruling). The whole node card is the trigger, and the pointer transits
 * node bodies constantly during normal canvas work — so the delay sits
 * comfortably above incidental-transit dwell (~500 ms is a common
 * "intentional hover" threshold; 700 ms adds margin for the large target)
 * while staying under the ~1 s native-title delay this feature replaces.
 * The palette keeps the snappier 300 ms Tooltip default: it is a browsing
 * surface, the canvas is a working surface.
 */
export const CANVAS_TOOLTIP_DELAY_MS = 700

/**
 * True while any canvas gesture is in progress: an edge drag from a
 * connector, a rubber-band selection, or a canvas pan. Fed into the
 * Tooltip `disabled` prop (with the node's own `dragging` flag) so the
 * whole-body tooltip can never open mid-gesture or sit under a drag —
 * including click-to-connect flows where no pointer button is held.
 */
const _canvasGestureActive = (s: ReactFlowState): boolean =>
  s.connection.inProgress || s.userSelectionActive || s.paneDragging

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
 * Commit-6 multi-port: when an apiInput has 2+ `emit: true` tables, we
 * render one labelled Handle per table (id = table label). Otherwise the
 * legacy single Handle covers the default single-port use.
 *
 * Returning a JSX list rather than mutating render order keeps the call
 * sites at the three zoom levels each a one-line switch.
 *
 * Test ids are positional, not semantic: `output-connector[<idx>]:<node
 * label>`, where idx is the visual top-to-bottom port order. Single-port
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
  const tables = Array.isArray((config as { tables?: unknown })?.tables)
    ? ((config as { tables: unknown[] }).tables as Array<Record<string, unknown>>)
    : []
  const emitTables = tables.filter(
    (t) => t && typeof t === "object" && (t as { emit?: unknown }).emit === true,
  )
  if (emitTables.length < 2) {
    // Single-port fallback (one or zero emit:true tables, or no tables key):
    // preserve the legacy default Handle so existing single-port pipelines
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
  // Multi-port: stack labelled Handles down the right edge. Each
  // Handle's `id` is the table's label — React Flow propagates this to
  // `onConnect.params.sourceHandle` when a user drags from it.
  //
  // Defence in depth per the adversarial review's S2: substitute a
  // synthetic `port_<idx>` when the label is missing / non-string /
  // empty / whitespace-only. Two Handles with the same id break React
  // Flow's edge resolution — the backend's B2 sanitised-label
  // collision check (in `validate_v2_schema`) catches this for the
  // load-bearing case, but the frontend doesn't always re-validate
  // before render, so we also collapse same-id duplicates here to
  // avoid the React-Flow-internal failure mode.
  const seenIds = new Set<string>()
  return (
    <>
      {emitTables.map((table, idx) => {
        const rawLabel = (table as { label?: unknown }).label
        const candidate =
          typeof rawLabel === "string" && rawLabel.trim() ? rawLabel : `port_${idx}`
        // If a later table reuses an earlier id, fall back to a synthetic
        // id so React Flow doesn't see duplicates (last-writer-wins on
        // React keys breaks edge routing). The schema-validation path
        // rejects this case before persistence; this branch only fires
        // for the transient pre-save state.
        const label = seenIds.has(candidate) ? `${candidate}__${idx}` : candidate
        seenIds.add(label)
        // Stack the dots vertically; `top` is a percentage so the
        // Handles space evenly down the right edge regardless of node
        // height.
        const topPct = ((idx + 1) / (emitTables.length + 1)) * 100
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

function PipelineNode({ id, data: nodeData, selected, dragging }: NodeProps<PipelineFlowNode>) {
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

  // ── Node-TYPE tooltip (whole-body trigger, Nick's ruling) ──────────
  // The render-prop spreads pure hover-observation props onto each
  // branch's EXISTING root div: no wrapper DOM, no nodrag/nopan class,
  // no change to drag/select/click behaviour. Suppressed during any
  // drag/connection/selection gesture (store state + `dragging` prop;
  // the Tooltip primitive additionally dismisses on pointerdown).
  const isKnownNodeType = Object.hasOwn(NODE_TYPE_META, nodeType)
  const canvasGestureActive = useStore(_canvasGestureActive)
  const typeTooltipDisabled = !isKnownNodeType || canvasGestureActive || !!dragging
  const typeTooltipContent = isKnownNodeType
    ? <NodeTypeTooltip type={nodeType as NodeTypeValue} />
    : null

  // Bundle 3c — emit-table labels for the right-edge handles.  Source
  // of truth shared between (a) `_SourceHandles` which renders the
  // Handles, (b) the body label list, and (c) the
  // `useUpdateNodeInternals` effect that nudges React Flow to re-measure
  // when the topology changes.  Logic mirrors `_SourceHandles`: only
  // emit:true tables count; missing/blank labels fall back to
  // `port_<idx>`; duplicates are disambiguated with `__<idx>` suffix.
  const emitTableLabels = useMemo<string[]>(() => {
    if (!isDeployInput) return []
    const tables = Array.isArray((nodeData.config as { tables?: unknown })?.tables)
      ? ((nodeData.config as { tables: unknown[] }).tables as Array<Record<string, unknown>>)
      : []
    const emit = tables.filter(
      (t) => t && typeof t === "object" && (t as { emit?: unknown }).emit === true,
    )
    const seen = new Set<string>()
    const out: string[] = []
    emit.forEach((t, idx) => {
      const raw = (t as { label?: unknown }).label
      const candidate =
        typeof raw === "string" && raw.trim() ? raw : `port_${idx}`
      const label = seen.has(candidate) ? `${candidate}__${idx}` : candidate
      seen.add(label)
      out.push(label)
    })
    return out
  }, [isDeployInput, nodeData.config])

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
  // 2+ emit tables = multi-port; render the visual port-to-label
  // mapping on the body.  0/1 emit = single-port fallback (Handle is
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

    // Tooltip anchor (Nick's ruling): the join-node marker — the node's
    // entire on-canvas appearance — i.e. this branch's root div. The
    // inner oval is pointer-events-none, the three connector Handles are
    // untouched, and the popover itself is pointer-events: none, so
    // connector drags are never intercepted.
    return (
      <Tooltip
        content={typeTooltipContent}
        placement="top"
        delayMs={CANVAS_TOOLTIP_DELAY_MS}
        disabled={typeTooltipDisabled}
      >
        {(tooltipTriggerProps) => (
          <div
            {...tooltipTriggerProps}
            data-testid="node-type-tooltip-trigger"
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
        )}
      </Tooltip>
    )
  }

  // Compact mode: tinted background with icon + label — readable at far zoom
  if (zoomLevel === "compact") {
    return (
      <Tooltip
        content={typeTooltipContent}
        placement="top"
        delayMs={CANVAS_TOOLTIP_DELAY_MS}
        disabled={typeTooltipDisabled}
      >
        {(tooltipTriggerProps) => (
          <div
            {...tooltipTriggerProps}
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
        )}
      </Tooltip>
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
      <Tooltip
        content={typeTooltipContent}
        placement="top"
        delayMs={CANVAS_TOOLTIP_DELAY_MS}
        disabled={typeTooltipDisabled}
      >
        {(tooltipTriggerProps) => (
          <div
            {...tooltipTriggerProps}
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
        )}
      </Tooltip>
    )
  }

  // Full mode: header bar with badges + body with label and trace
  return (
    <Tooltip
      content={typeTooltipContent}
      placement="top"
      delayMs={CANVAS_TOOLTIP_DELAY_MS}
      disabled={typeTooltipDisabled}
    >
      {(tooltipTriggerProps) => (
        <div
          {...tooltipTriggerProps}
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

          {/* Body — Bundle 3c: when this is a multi-port apiInput, the
              right-aligned label column visually maps each emit table to
              its handle on the right edge, in the same top-to-bottom order
              the Handles are stacked.  Hidden for 0/1 emit (single-port
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
      )}
    </Tooltip>
  )
}

export default memo(PipelineNode)
