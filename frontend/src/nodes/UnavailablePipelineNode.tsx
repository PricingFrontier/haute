import { Handle, Position, type Node, type NodeProps } from "@xyflow/react"
import { AlertTriangle } from "lucide-react"
import type { HauteNodeData } from "../types/node"

type UnavailableNode = Node<HauteNodeData, "unavailablePipelineNode">

/** Editor-only card for an unknown or otherwise unrenderable authored node. */
export default function UnavailablePipelineNode({ data, selected }: NodeProps<UnavailableNode>) {
  const decorator = typeof data._authoredDecorator === "string"
    ? data._authoredDecorator
    : String(data.nodeType || "unknown")
  return (
    <div
      role="button"
      aria-label={`Unavailable ${decorator} node: ${data.label}`}
      data-testid={`unavailable-node-${data.label}`}
      className="relative w-[240px] rounded-xl px-3 py-3"
      style={{
        color: "var(--danger-text)",
        background: "var(--bg-elevated)",
        border: `${selected ? 3 : 2}px solid var(--danger)`,
        boxShadow: "var(--node-shadow)",
      }}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <div className="flex items-center gap-2">
        <AlertTriangle size={16} aria-hidden="true" />
        <span className="text-[10px] font-bold uppercase tracking-[0.1em]">
          Unavailable
        </span>
      </div>
      <div className="mt-2 truncate text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>
        {data.label}
      </div>
      <div className="mt-1 truncate font-mono text-[10px]" style={{ color: "var(--text-muted)" }}>
        @pipeline.{decorator}
      </div>
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div>
  )
}
