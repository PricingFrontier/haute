import { memo } from "react"
import { Handle, Position, type NodeProps } from "@xyflow/react"
import { Package } from "lucide-react"
import { STRUCTURE_COLORS } from "../theme/colors"
import { nodeTypeColors } from "../utils/nodeTypes"
import type { SubmodelFlowNode } from "../types/node"

const accent = nodeTypeColors.submodel || STRUCTURE_COLORS.fallbackAccent

function SubmodelNode({ data: nodeData, selected }: NodeProps<SubmodelFlowNode>) {
  const config = nodeData.config || {}
  const inputPorts = config.inputPorts || []
  const outputPorts = config.outputPorts || []
  const childCount = (config.childNodeIds || []).length
  const traceActive = !!nodeData._traceActive
  const traceDimmed = !!nodeData._traceDimmed
  const hoverDimmed = !!nodeData._hoverDimmed

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
        transition: "border-color 0.15s ease, opacity 0.2s ease, box-shadow 0.2s ease",
      }}
    >
      <div
        className="absolute left-0 top-3 bottom-3 w-[3px] rounded-full"
        style={{ backgroundColor: accent, opacity: selected ? 1 : 0.6, transition: "opacity 0.2s ease" }}
      />

      {/* Hidden per-port input handles so React Flow can resolve existing edges */}
      {inputPorts.map((port) => (
        <Handle
          key={`in__${port}`}
          id={`in__${port}`}
          type="target"
          position={Position.Left}
          style={{ top: "50%", opacity: 0, width: 0, height: 0, pointerEvents: "none" }}
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
    </div>
  )
}

export default memo(SubmodelNode)
