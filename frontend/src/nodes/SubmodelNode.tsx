import { memo, useEffect } from "react"
import {
  Handle,
  Position,
  useUpdateNodeInternals,
  type NodeProps,
} from "@xyflow/react"
import { Package } from "lucide-react"
import { STRUCTURE_COLORS } from "../theme/colors"
import { nodeTypeColors } from "../utils/nodeTypes"
import type { SubmodelFlowNode } from "../types/node"
import { DEFAULT_TARGET_HANDLE } from "../utils/flowHandles"
import FramePortRows from "./FramePortRows"

const accent = nodeTypeColors.submodel || STRUCTURE_COLORS.fallbackAccent
const headerInset = {
  marginTop: "-1.5px",
  marginLeft: "-1.5px",
  marginRight: "-1.5px",
}
const bodyStyle = {
  background: "var(--bg-elevated)",
  borderRadius: "0 0 10.5px 10.5px",
  marginLeft: "-1.5px",
  marginRight: "-1.5px",
  marginBottom: "-1.5px",
}

function SubmodelNode({
  id,
  data: nodeData,
  selected,
}: NodeProps<SubmodelFlowNode>) {
  const config = nodeData.config || {}
  const inputPorts = config.inputPorts || []
  const outputPorts = config.outputPorts || []
  const outputPortLabels = config.outputPortLabels || {}
  const outputFrames = outputPorts.map((childId) => {
    const configuredLabel = outputPortLabels[childId]
    return {
      id: `out__${childId}`,
      label:
        typeof configuredLabel === "string" && configuredLabel.length > 0
          ? configuredLabel
          : childId,
    }
  })
  const childCount = (config.childNodeIds || []).length
  const traceActive = !!nodeData._traceActive
  const traceDimmed = !!nodeData._traceDimmed
  const hoverDimmed = !!nodeData._hoverDimmed
  const traceMotionDisabled = !!nodeData._traceMotionDisabled
  const updateNodeInternals = useUpdateNodeInternals()
  const portSignature = JSON.stringify([
    inputPorts,
    outputFrames.map((frame) => frame.id),
  ])

  useEffect(() => {
    updateNodeInternals(id)
  }, [id, portSignature, updateNodeInternals])

  return (
    <div
      aria-label={`Submodel node: ${nodeData.label}, ${childCount} child nodes${traceActive ? ", trace active" : ""}`}
      role="button"
      className="relative w-[240px] cursor-pointer rounded-xl"
      style={{
        border:
          traceActive || selected
            ? `3px solid ${accent}`
            : "3px dashed var(--border-bright)",
        boxShadow: traceActive
          ? `0 0 12px ${accent}40, var(--node-shadow)`
          : "var(--node-shadow)",
        opacity: traceDimmed || hoverDimmed ? 0.3 : 1,
        transition: traceMotionDisabled
          ? "none"
          : "border-color 0.15s ease, opacity 0.2s ease, box-shadow 0.2s ease",
      }}
    >
      {inputPorts.map((childId) => (
        <Handle
          key={`in__${childId}`}
          id={`in__${childId}`}
          type="target"
          position={Position.Left}
          isConnectable={false}
          style={{
            top: "50%",
            width: 0,
            height: 0,
            opacity: 0,
            pointerEvents: "none",
          }}
        />
      ))}
      <Handle
        type="target"
        position={Position.Left}
        id={DEFAULT_TARGET_HANDLE}
        style={{ background: accent }}
      />

      <div
        data-testid="submodel-header"
        className="flex items-center gap-2 px-3 py-1.5"
        style={{
          background: `${accent}30`,
          borderRadius: "10.5px 10.5px 0 0",
          ...headerInset,
        }}
      >
        <Package size={16} style={{ color: accent }} className="shrink-0" />
        <span
          className="shrink-0 text-[10px] font-bold uppercase tracking-[0.1em]"
          style={{ color: accent }}
        >
          SUBMODEL
        </span>
        <span
          className="ml-auto shrink-0 rounded-full px-1.5 py-0.5 font-mono text-[9px]"
          style={{
            background: `${accent}18`,
            border: `1px solid ${accent}30`,
            color: accent,
          }}
        >
          {childCount} {childCount === 1 ? "node" : "nodes"}
        </span>
      </div>

      <div className="px-3 py-2" style={bodyStyle}>
        <div
          className="truncate text-[13px] font-semibold leading-tight"
          style={{ color: "var(--text-primary)" }}
        >
          {nodeData.label}
        </div>
        {config.file && (
          <div
            className="mt-0.5 truncate text-[10px]"
            style={{ color: "var(--text-muted)" }}
          >
            {config.file}
          </div>
        )}
        {outputFrames.length > 0 && (
          <div className="mt-1.5">
            <FramePortRows
              ports={outputFrames}
              direction="source"
              accent={accent}
              testIdPrefix="submodel-output"
            />
          </div>
        )}
      </div>
    </div>
  )
}

export default memo(SubmodelNode)
