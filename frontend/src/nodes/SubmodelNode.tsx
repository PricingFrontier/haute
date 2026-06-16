import { memo } from "react"
import { Handle, Position, type NodeProps } from "@xyflow/react"
import { Package, Maximize2 } from "lucide-react"
import { STRUCTURE_COLORS } from "../theme/colors"
import { nodeTypeColors } from "../utils/nodeTypes"
import useUIStore from "../stores/useUIStore"
import type { SubmodelFlowNode } from "../types/node"

const accent = nodeTypeColors.submodel || STRUCTURE_COLORS.fallbackAccent

function SubmodelNode({ id, data: nodeData, selected }: NodeProps<SubmodelFlowNode>) {
  const config = nodeData.config || {}
  const inputPorts = config.inputPorts || []
  const outputPorts = config.outputPorts || []
  const childCount = (config.childNodeIds || []).length
  const traceActive = !!nodeData._traceActive
  const traceDimmed = !!nodeData._traceDimmed
  const hoverDimmed = !!nodeData._hoverDimmed
  const traceMotionDisabled = !!nodeData._traceMotionDisabled

  return (
    <div
      aria-label={`Submodel node: ${nodeData.label}, ${childCount} child nodes${traceActive ? ", trace active" : ""}`}
      role="button"
      className="relative rounded-xl w-[240px] cursor-pointer"
      style={{
        background: "var(--bg-elevated)",
        border: traceActive
          ? `1.5px solid ${accent}`
          : selected
            ? `1.5px solid ${accent}`
            : `1.5px dashed var(--border-bright)`,
        boxShadow: traceActive
          ? `0 0 12px ${accent}40, var(--node-shadow)`
          : "var(--node-shadow)",
        opacity: traceDimmed || hoverDimmed ? 0.3 : 1,
        transition: traceMotionDisabled ? "none" : "border-color 0.15s ease, opacity 0.2s ease, box-shadow 0.2s ease",
        ["--node-accent" as string]: accent,
      }}
    >
      <div
        className="absolute left-0 top-3 bottom-3 w-[3px] rounded-full"
        style={{ backgroundColor: accent, opacity: selected ? 1 : 0.6, transition: traceMotionDisabled ? "none" : "opacity 0.2s ease" }}
      />

      {/* Hidden per-port input handles so React Flow can resolve existing
          edges. `handle-hidden` keeps them 0×0 / inert in the stylesheet
          (inline 0s lose to the stylesheet's !important base size) so the
          body-drop resolver's zero-area exclusion holds and no phantom
          dots render now that connectors are visible by default. */}
      {inputPorts.map((port) => (
        <Handle
          key={`in__${port}`}
          id={`in__${port}`}
          type="target"
          position={Position.Left}
          className="handle-hidden"
          style={{ top: "50%" }}
        />
      ))}
      {/* Visible input handle for new connections */}
      <Handle type="target" position={Position.Left} />

      <div className="pl-4 pr-3 py-2.5">
        <div className="flex items-center gap-2 mb-1">
          <Package size={12} style={{ color: accent }} className="shrink-0 opacity-80" />
          <span
            className="text-[10px] font-bold uppercase tracking-[0.1em] shrink-0"
            style={{ color: accent, opacity: 0.8 }}
          >
            SUBMODEL
          </span>
          <span
            className="ml-auto text-[9px] font-mono px-1.5 py-0.5 rounded-full"
            style={{ background: `${accent}18`, color: accent, border: `1px solid ${accent}30` }}
          >
            {childCount} nodes
          </span>
        </div>
        <div className="font-semibold text-[13px] leading-tight truncate" style={{ color: "var(--text-primary)" }}>
          {nodeData.label}
        </div>
        {config.file && (
          <div className="text-[10px] truncate mt-0.5" style={{ color: "var(--text-muted)" }}>
            {config.file}
          </div>
        )}

        {outputPorts.length > 0 && (
          <div className="flex flex-col gap-0.5 items-end mt-1.5">
            {outputPorts.map((port) => (
              <span key={port} className="text-[9px] font-mono" style={{ color: "var(--text-muted)" }}>
                {port} →
              </span>
            ))}
          </div>
        )}
      </div>

      {outputPorts.length > 0 ? (
        outputPorts.map((port, i) => (
          <Handle
            key={`out__${port}`}
            id={`out__${port}`}
            type="source"
            position={Position.Right}
            style={{
              top: `${((i + 1) / (outputPorts.length + 1)) * 100}%`,
            }}
          />
        ))
      ) : (
        <Handle type="source" position={Position.Right} />
      )}

      {/* Node-explosion peek trigger (design §3.3.2). Bottom-LEFT, away from
          the right-edge output connectors and the left-centre input connector.
          `nodrag` is the only thing that stops React Flow's d3-drag (the
          pinned default nodeDragThreshold is 1, so a 1px wobble during the
          press would otherwise start a drag — selecting AND nudging the node,
          a position commit that dirties the pipeline). stopPropagation in
          pointer/mouse-down suppresses native selection/preview; in onClick it
          suppresses the App-level node-click handler. Together they keep the
          peek-mutates-nothing invariant (T4(g)/T5). */}
      <button
        type="button"
        data-testid={`node-peek-trigger-${nodeData.label}`}
        className="nodrag absolute -bottom-1.5 -left-1.5 flex items-center justify-center w-3.5 h-3.5 rounded-full"
        title="Peek inside"
        aria-label={`Peek inside ${nodeData.label}`}
        style={{
          background: "var(--bg-elevated)",
          border: `1px solid ${accent}40`,
          color: accent,
        }}
        onPointerDown={(e) => e.stopPropagation()}
        onMouseDown={(e) => e.stopPropagation()}
        onClick={(e) => {
          e.stopPropagation()
          useUIStore.getState().setPeek({ nodeId: id })
        }}
      >
        <Maximize2 size={8} aria-hidden="true" />
      </button>
    </div>
  )
}

export default memo(SubmodelNode)
