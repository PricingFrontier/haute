import { memo, useEffect } from "react"
import { useUpdateNodeInternals, type NodeProps } from "@xyflow/react"
import { ArrowRight } from "lucide-react"
import { STRUCTURE_COLORS } from "../theme/colors"
import type { SubmodelPortFlowNode } from "../types/node"
import FramePortRows, { DefaultInputPortRow } from "./FramePortRows"
import { deriveNodeCanvasFlags, NodeTraceValuePill } from "./nodeCanvasStyle"

const portColor = STRUCTURE_COLORS.port
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

function SubmodelPortNode({
  id,
  data: nodeData,
  selected,
}: NodeProps<SubmodelPortFlowNode>) {
  if (
    nodeData.portDirection !== "input" &&
    nodeData.portDirection !== "output"
  ) {
    throw new Error(
      `Submodel boundary portDirection must be "input" or "output"; received ${String(nodeData.portDirection)}`,
    )
  }
  if (!Array.isArray(nodeData.ports)) {
    throw new Error("Submodel boundary ports must be an array")
  }

  const isInput = nodeData.portDirection === "input"
  const {
    traceValue,
    showTraceValue,
    containerStyle,
  } = deriveNodeCanvasFlags(nodeData, {
    accent: portColor,
    selected,
    restBorder: `3px solid color-mix(in srgb, ${portColor} 35%, var(--bg-canvas))`,
  })
  const updateNodeInternals = useUpdateNodeInternals()
  const portSignature = JSON.stringify(nodeData.ports.map((port) => port.id))

  useEffect(() => {
    updateNodeInternals(id)
  }, [id, portSignature, updateNodeInternals])

  return (
    <div
      data-testid="submodel-boundary-card"
      aria-label={`Submodel ${nodeData.portDirection} boundary`}
      role="group"
      className="relative w-[240px] cursor-pointer rounded-xl"
      style={containerStyle}
    >
      <div
        className="flex items-center gap-2 px-3 py-1.5"
        style={{
          background: `${portColor}30`,
          borderRadius: "10.5px 10.5px 0 0",
          ...headerInset,
        }}
      >
        <ArrowRight
          size={16}
          style={{ color: portColor }}
          className="shrink-0"
        />
        <span
          className="shrink-0 text-[10px] font-bold uppercase tracking-[0.1em]"
          style={{ color: portColor }}
        >
          {nodeData.label}
        </span>
      </div>

      <div className="px-3 py-2" style={bodyStyle}>
        {showTraceValue && (
          <NodeTraceValuePill accent={portColor} value={traceValue} className="mb-1" />
        )}
        {isInput ? (
          nodeData.ports.length > 0 ? (
            <FramePortRows
              ports={nodeData.ports}
              direction="source"
              accent={portColor}
              testIdPrefix="submodel-input"
            />
          ) : (
            <div
              className="text-[11px] leading-tight"
              style={{ color: "var(--text-muted)" }}
            >
              No input frames
            </div>
          )
        ) : (
          <DefaultInputPortRow
            accent={portColor}
            rowTestId="submodel-output-input-frame-row"
          />
        )}
      </div>
    </div>
  )
}

export default memo(SubmodelPortNode)
