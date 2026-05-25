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

/** Source-Handle setup for the right edge of the node.
 *
 * Commit-6 multi-port: when an apiInput has 2+ `emit: true` tables, we
 * render one labelled Handle per table (id = table label). Otherwise the
 * legacy single Handle covers the default single-port use.
 *
 * Returning a JSX list rather than mutating render order keeps the call
 * sites at the three zoom levels each a one-line switch.
 */
function _SourceHandles({
  isApiInput,
  config,
  accent,
}: {
  isApiInput: boolean
  config: Record<string, unknown> | undefined
  accent: string
}) {
  if (!isApiInput) {
    return <Handle type="source" position={Position.Right} />
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
    return <Handle type="source" position={Position.Right} />
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
            style={{ top: `${topPct}%`, background: accent }}
            data-testid={`api-input-port-${label}`}
          />
        )
      })}
    </>
  )
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
  const sourceHandles = !isSinkOnly ? (
    <_SourceHandles
      isApiInput={isDeployInput}
      config={nodeData.config as Record<string, unknown> | undefined}
      accent={accent}
    />
  ) : null
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
        {sourceHandles}
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

      {sourceHandles}
    </div>
  )
}

export default memo(PipelineNode)
