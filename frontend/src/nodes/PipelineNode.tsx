import { memo } from "react"
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

  // Accessible label: "{Type} node: {label}" + status
  const typeName = NODE_TYPE_META[nodeType as NodeTypeValue]?.name || typeLabel
  const statusText = nodeData._status ? `, status: ${nodeData._status}` : ""
  const ariaLabel = `${typeName} node: ${nodeData.label}${statusText}${isInstance ? ", instance" : ""}${traceActive ? ", trace active" : ""}`

  // Compact mode: tinted background with icon + label — readable at far zoom
  if (zoomLevel === "compact") {
    return (
      <div
        aria-label={ariaLabel}
        role="button"
        className={`relative w-[160px] cursor-pointer ${isPill ? "rounded-full" : "rounded-lg"}`}
        style={{
          background: `linear-gradient(${accent}28, ${accent}1a), var(--bg-elevated)`,
          border: selected ? `3px solid ${accent}` : `3px solid ${accent}40`,
          boxShadow: "var(--node-shadow)",
          opacity: dimmed ? 0.25 : 1,
          transition: traceMotionDisabled ? "none" : "opacity 0.2s ease",
        }}
      >
        {!isSourceOnly && <Handle type="target" position={Position.Left} />}
        <div className="flex items-center gap-2 pl-3 pr-2.5 py-2">
          <Icon size={14} style={{ color: accent }} className="shrink-0" />
          <div className="font-bold text-[12px] leading-tight truncate" style={{ color: "var(--text-primary)" }}>
            {nodeData.label}
          </div>
        </div>
        {!isSinkOnly && <Handle type="source" position={Position.Right} />}
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

  // Header bar border-radius: matches inner edge of container (outer radius minus border)
  const headerRadius = isPill ? "15px 15px 0 0" : "11px 11px 0 0"

  // Medium mode: header bar + label, no extra badges
  if (zoomLevel === "medium") {
    return (
      <div
        aria-label={ariaLabel}
        role="button"
        className={`relative w-[240px] cursor-pointer ${isPill ? "rounded-2xl" : "rounded-xl"}`}
        style={containerStyle}
      >
        {!isSourceOnly && <Handle type="target" position={Position.Left} />}
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
        {!isSinkOnly && <Handle type="source" position={Position.Right} />}
      </div>
    )
  }

  // Full mode: header bar with badges + body with label and trace
  return (
    <div
      aria-label={ariaLabel}
      role="button"
      className={`relative w-[240px] cursor-pointer ${isPill ? "rounded-2xl" : "rounded-xl"}`}
      style={containerStyle}
    >
      {!isSourceOnly && <Handle type="target" position={Position.Left} />}

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

      {/* Body */}
      <div className="px-3 py-2">
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

      {!isSinkOnly && <Handle type="source" position={Position.Right} />}
    </div>
  )
}

export default memo(PipelineNode)
