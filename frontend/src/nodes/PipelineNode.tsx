import { memo, type CSSProperties } from "react"
import { Handle, Position, useStore, type NodeProps } from "@xyflow/react"
import { Radio, Link2 } from "lucide-react"
import PolarsIcon from "../components/PolarsIcon"
import { NODE_TYPES, NODE_TYPE_META, SOURCE_ONLY_TYPES, SINK_ONLY_TYPES, PILL_TYPES, nodeTypeIcons, nodeTypeColors, nodeTypeLabels, type NodeTypeValue } from "../utils/nodeTypes"
import { formatValueCompact } from "../utils/formatValue"
import useSettingsStore from "../stores/useSettingsStore"
import { STATUS_COLORS } from "../theme/colors"
import type { PipelineFlowNode } from "../types/node"

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

function PipelineNode({ data: nodeData, selected }: NodeProps<PipelineFlowNode>) {
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
  const traceActive = !!nodeData._traceActive
  const traceDimmed = !!nodeData._traceDimmed
  const hoverDimmed = !!nodeData._hoverDimmed
  const traceValue = nodeData._traceValue
  const traceMotionDisabled = !!nodeData._traceMotionDisabled
  const hasWarnings = (nodeData._schemaWarnings?.length ?? 0) > 0
  const zoomLevel = useStore(zoomSelector)

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

  // Compact mode: tinted background with icon + label — readable at far zoom
  if (zoomLevel === "compact") {
    return (
      <div
        data-testid={`node-${nodeData.label}`}
        aria-label={ariaLabel}
        role="button"
        className={`relative w-[160px] cursor-pointer ${isPill ? "rounded-full" : "rounded-lg"}`}
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
        {!isSourceOnly && <Handle type="target" position={Position.Left} data-testid={`handle-target-${nodeData.label}`} />}
        <div className="flex items-center gap-2 pl-3 pr-2.5 py-2">
          <Icon size={14} style={{ color: accent }} className="shrink-0" />
          <div className="font-bold text-[12px] leading-tight truncate" style={{ color: "var(--text-primary)" }}>
            {nodeData.label}
          </div>
        </div>
        {!isSinkOnly && <Handle type="source" position={Position.Right} data-testid={`handle-source-${nodeData.label}`} />}
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
        className={`relative w-[240px] cursor-pointer ${isPill ? "rounded-2xl" : "rounded-xl"}`}
        style={containerStyle}
      >
        {!isSourceOnly && <Handle type="target" position={Position.Left} data-testid={`handle-target-${nodeData.label}`} />}
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
        {!isSinkOnly && <Handle type="source" position={Position.Right} data-testid={`handle-source-${nodeData.label}`} />}
      </div>
    )
  }

  // Full mode: header bar with badges + body with label and trace
  return (
    <div
      data-testid={`node-${nodeData.label}`}
      aria-label={ariaLabel}
      role="button"
      className={`relative w-[240px] cursor-pointer ${isPill ? "rounded-2xl" : "rounded-xl"}`}
      style={containerStyle}
    >
      {!isSourceOnly && <Handle type="target" position={Position.Left} data-testid={`handle-target-${nodeData.label}`} />}

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

      {/* Body */}
      <div className="px-3 py-2" style={bodyStyle}>
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

      {!isSinkOnly && <Handle type="source" position={Position.Right} data-testid={`handle-source-${nodeData.label}`} />}
    </div>
  )
}

export default memo(PipelineNode)
