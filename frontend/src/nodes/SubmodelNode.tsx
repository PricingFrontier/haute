import { memo, useEffect } from "react"
import { useUpdateNodeInternals, type NodeProps } from "@xyflow/react"
import { Package } from "lucide-react"
import { STRUCTURE_COLORS } from "../theme/colors"
import { nodeTypeColors } from "../utils/nodeTypes"
import {
  isSubmodelDefinition,
  isSubmodelInstanceConfig,
  type SubmodelFlowNode,
  type SubmodelOutputPort,
} from "../types/node"
import { SUBMODEL_INPUT_HANDLE } from "../utils/flowHandles"
import FramePortRows, { DefaultInputPortRow } from "./FramePortRows"
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
  isConnectable,
}: NodeProps<SubmodelFlowNode>) {
  const config = nodeData.config
  const canonicalIdentityValid = isSubmodelInstanceConfig(config)
  const definitionId = canonicalIdentityValid ? config.definitionId : ""
  const definition = useGraphStore((state) => state.submodels[definitionId])
  const canonicalDefinition = canonicalIdentityValid && isSubmodelDefinition(definition, definitionId)
    ? definition
    : undefined
  const definitionInvalid = canonicalDefinition === undefined
  const hasInputSocket = canonicalDefinition !== undefined
  const inputAnchorIds = canonicalDefinition?.inputPorts.map(
    (port) => `in__${port.name}`,
  ) ?? []
  const outputFrames = canonicalDefinition?.outputPorts.map(toOutputFrame) ?? []
  const childCount = canonicalDefinition?.graph.nodes.length ?? 0
  const hasBody = hasInputSocket
    || outputFrames.length > 0
    || definitionInvalid
  const traceActive = !!nodeData._traceActive
  const traceDimmed = !!nodeData._traceDimmed
  const hoverDimmed = !!nodeData._hoverDimmed
  const traceMotionDisabled = !!nodeData._traceMotionDisabled
  const updateNodeInternals = useUpdateNodeInternals()
  const portSignature = JSON.stringify([
    inputAnchorIds,
    outputFrames.map((frame) => frame.id),
    hasInputSocket,
    isConnectable,
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
          className="ml-auto min-w-0 max-w-[110px] truncate rounded-full px-1.5 py-0.5 text-[13px] font-semibold leading-tight"
          style={{
            background: `${accent}18`,
            border: `1px solid ${accent}30`,
            color: "var(--text-primary)",
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
          {hasInputSocket && outputFrames.length === 0 && (
            <DefaultInputPortRow
              accent={accent}
              handleId={SUBMODEL_INPUT_HANDLE}
              rowTestId="submodel-input-row"
              handleTestId="submodel-input-handle"
              edgeAnchorIds={inputAnchorIds}
              isConnectable={isConnectable}
            />
          )}
          {outputFrames.length > 0 && (
            <FramePortRows
              ports={outputFrames}
              direction="source"
              accent={accent}
              testIdPrefix="submodel-output"
              firstRowInput={hasInputSocket
                ? {
                    handleId: SUBMODEL_INPUT_HANDLE,
                    handleTestId: "submodel-input-handle",
                    edgeAnchorIds: inputAnchorIds,
                    isConnectable,
                  }
                : undefined}
            />
          )}
        </div>
      )}
    </div>
  )
}

function toOutputFrame(port: SubmodelOutputPort) {
  return { id: `out__${port.name}`, label: port.name, parentEdges: [] }
}

export default memo(SubmodelNode)
