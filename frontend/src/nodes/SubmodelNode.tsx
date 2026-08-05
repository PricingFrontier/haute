import { memo, useEffect } from "react"
import { useUpdateNodeInternals, type NodeProps } from "@xyflow/react"
import { Package } from "lucide-react"
import { STRUCTURE_COLORS } from "../theme/colors"
import { nodeTypeColors } from "../utils/nodeTypes"
import {
  isSubmodelDefinition,
  isSubmodelInstanceConfig,
  type SubmodelFlowNode,
  type SubmodelInputPort,
  type SubmodelOutputPort,
} from "../types/node"
import FramePortRows from "./FramePortRows"
import useGraphStore from "../stores/useGraphStore"

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
  const config = nodeData.config
  const canonicalIdentityValid = isSubmodelInstanceConfig(config)
  const definitionId = canonicalIdentityValid ? config.definitionId : ""
  const definition = useGraphStore((state) => state.submodels[definitionId])
  const canonicalDefinition = canonicalIdentityValid && isSubmodelDefinition(definition, definitionId)
    ? definition
    : undefined
  const definitionInvalid = canonicalDefinition === undefined
  const inputFrames = canonicalDefinition?.inputPorts.map(toInputFrame) ?? []
  const outputFrames = canonicalDefinition?.outputPorts.map(toOutputFrame) ?? []
  const childCount = canonicalDefinition?.graph.nodes.length ?? 0
  const hasBody = inputFrames.length > 0 || outputFrames.length > 0 || definitionInvalid
  const traceActive = !!nodeData._traceActive
  const traceDimmed = !!nodeData._traceDimmed
  const hoverDimmed = !!nodeData._hoverDimmed
  const traceMotionDisabled = !!nodeData._traceMotionDisabled
  const updateNodeInternals = useUpdateNodeInternals()
  const portSignature = JSON.stringify([
    inputFrames.map((frame) => frame.id),
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
      <div
        data-testid="submodel-header"
        className="flex items-center gap-2 px-3 py-1.5"
        style={{
          background: `${accent}30`,
          borderRadius: hasBody ? "10.5px 10.5px 0 0" : "10.5px",
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
          data-testid="submodel-name-badge"
          title={nodeData.label}
          className="ml-auto min-w-0 max-w-[110px] truncate rounded-full px-1.5 py-0.5 font-mono text-[9px]"
          style={{
            background: `${accent}18`,
            border: `1px solid ${accent}30`,
            color: accent,
          }}
        >
          {nodeData.label}
        </span>
      </div>

      {hasBody && (
        <div data-testid="submodel-body" className="px-3 py-2" style={bodyStyle}>
          {definitionInvalid && (
            <div
              role="alert"
              data-testid="submodel-definition-error"
              className="text-[11px] font-semibold"
              style={{ color: "var(--danger)" }}
            >
              Definition unavailable or invalid
            </div>
          )}
          {inputFrames.length > 0 && (
            <FramePortRows
              ports={inputFrames}
              direction="target"
              accent={accent}
              testIdPrefix="submodel-input"
            />
          )}
          {outputFrames.length > 0 && (
            <FramePortRows
              ports={outputFrames}
              direction="source"
              accent={accent}
              testIdPrefix="submodel-output"
            />
          )}
        </div>
      )}
    </div>
  )
}

function toInputFrame(port: SubmodelInputPort) {
  return { id: `in__${port.portId}`, label: port.label, parentEdges: [] }
}

function toOutputFrame(port: SubmodelOutputPort) {
  return { id: `out__${port.portId}`, label: port.label, parentEdges: [] }
}

export default memo(SubmodelNode)
