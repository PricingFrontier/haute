import { memo } from "react"
import { Handle, Position, type NodeProps } from "@xyflow/react"
import { ArrowRight, ArrowLeft } from "lucide-react"
import type { SubmodelPortFlowNode } from "../types/node"

const portColor = "#94a3b8"

function SubmodelPortNode({ data: nodeData }: NodeProps<SubmodelPortFlowNode>) {
  const isInput = nodeData.portDirection === "input"
  const Icon = isInput ? ArrowRight : ArrowLeft
  const traceActive = !!nodeData._traceActive
  const traceDimmed = !!nodeData._traceDimmed

  return (
    <div
      className="relative rounded-full px-4 py-2 flex items-center gap-2 min-w-[120px]"
      style={{
        background: "var(--bg-elevated)",
        border: traceActive
          ? `1.5px solid var(--accent)`
          : `1.5px dashed ${portColor}40`,
        boxShadow: traceActive
          ? `0 0 10px rgba(96,165,250,.35)`
          : "none",
        opacity: traceDimmed ? 0.3 : 0.85,
        transition: "border-color 0.15s ease, opacity 0.2s ease, box-shadow 0.2s ease",
      }}
    >
      {!isInput && <Handle type="target" position={Position.Left} />}

      <Icon size={12} style={{ color: portColor }} className="shrink-0" />
      <span
        className="text-[11px] font-mono font-medium truncate"
        style={{ color: portColor }}
      >
        {nodeData.portName || nodeData.label}
      </span>

      {isInput && <Handle type="source" position={Position.Right} />}
    </div>
  )
}

export default memo(SubmodelPortNode)
