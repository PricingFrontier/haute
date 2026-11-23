import { memo, useEffect } from "react"
import { Handle, Position, useUpdateNodeInternals, type NodeProps } from "@xyflow/react"
import { Package } from "lucide-react"
import { STRUCTURE_COLORS } from "../theme/colors"
import { nodeTypeColors } from "../utils/nodeTypes"
import {
  isSubmodelDefinition,
  isSubmodelInstanceConfig,
  type SubmodelFlowNode,
} from "../types/node"
import { SUBMODEL_INPUT_HANDLE } from "../utils/flowHandles"
import FramePortRows, { DefaultInputPortRow } from "./FramePortRows"
import { deriveNodeCanvasFlags, NodeTraceValuePill } from "./nodeCanvasStyle"
import useGraphStore from "../stores/useGraphStore"
import { useSubmodelDefinitions } from "./submodelDefinitions"

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
  const definitionId = canonicalIdentityValid ? config.definition_id : ""
  const definition = useSubmodelDefinitions()[definitionId]
  const canonicalDefinition = canonicalIdentityValid && isSubmodelDefinition(definition, definitionId)
    ? definition
    : undefined
  const definitionInvalid = canonicalDefinition === undefined
  const hasInputSocket = canonicalDefinition !== undefined
  const inputAnchorIds = canonicalDefinition?.inputPorts.map(
    (port) => `in__${port.name}`,
  ) ?? []
  // An output the server has not named yet (one just connected) has no handle until it is:
  // the card is drawn without it, never by throwing, which would take down the whole view.
  const outputFrames = canonicalDefinition?.outputPorts.flatMap((port) => {
    const handle = `out__${port.name}`
    const label = nodeData._sourceHandleInputNames?.[handle]
    return typeof label === "string" && label ? [{ id: handle, label }] : []
  }) ?? []
  const loadAvailability = nodeData._loadAvailability ?? "ready"
  const showRecoveryHandles = definitionInvalid && loadAvailability !== "ready"
  const childCount = canonicalDefinition?.graph.nodes.length ?? 0
  const {
    traceActive,
    traceValue,
    showTraceValue,
    containerStyle,
  } = deriveNodeCanvasFlags(nodeData, {
    accent,
    selected,
    restBorder: "3px dashed var(--border-bright)",
  })
  const hasPortOrErrorBody = hasInputSocket
    || outputFrames.length > 0
    || definitionInvalid
  const hasBody = hasPortOrErrorBody || showTraceValue
  const updateNodeInternals = useUpdateNodeInternals()
  const portSignature = JSON.stringify([
    inputAnchorIds,
    outputFrames.map((frame) => frame.id),
    showRecoveryHandles,
    hasInputSocket,
    isConnectable,
  ])

  useEffect(() => {
    updateNodeInternals(id)
  }, [id, portSignature, updateNodeInternals])

  return (
    <div
      aria-label={`Submodel node: ${nodeData.label}, ${childCount} child nodes${loadAvailability !== "ready" ? `, ${loadAvailability}` : ""}${traceActive ? ", trace active" : ""}`}
      role="button"
      className="relative w-[240px] cursor-pointer rounded-xl"
      style={containerStyle}
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
          data-testid="submodel-title"
          title={nodeData.label}
          className="min-w-0 flex-1 truncate text-[13px] font-semibold leading-tight"
          style={{ color: "var(--text-primary)" }}
        >
          {nodeData.label}
        </span>
        {loadAvailability !== "ready" && (
          <span
            role="status"
            data-testid="submodel-load-availability"
            className="text-[10px] font-semibold uppercase"
            style={{ color: loadAvailability === "unavailable" ? "var(--danger)" : "var(--warning)" }}
          >
            {loadAvailability}
          </span>
        )}
      </div>

      {hasBody && (
        <div data-testid="submodel-body" className="px-3 py-2" style={bodyStyle}>
          {showTraceValue && (
            <NodeTraceValuePill
              accent={accent}
              value={traceValue}
              className={hasPortOrErrorBody ? "mb-1" : ""}
            />
          )}
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
          {showRecoveryHandles && <RecoveryHandles id={id} data={nodeData} />}
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

function RecoveryHandles({ id, data: nodeData }: Pick<NodeProps<SubmodelFlowNode>, "id" | "data">) {
  const recoveryEdges = useGraphStore((state) => state.edges)
  const inputHandleIds = [...new Set(recoveryEdges
    .filter((edge) => edge.target === id && typeof edge.targetHandle === "string" && edge.targetHandle.startsWith("in__"))
    .map((edge) => edge.targetHandle as string))]
  const outputHandleIds = Object.keys(nodeData._sourceHandleInputNames ?? {})
    .filter((handle) => handle.startsWith("out__"))

  if (inputHandleIds.length === 0 && outputHandleIds.length === 0) return null
  return (
    <div
      data-testid="submodel-recovery-ports"
      className="relative mt-2 flex min-h-5 items-center justify-between text-[10px]"
      style={{ color: "var(--text-muted)" }}
    >
      <span>Retained recovery connections</span>
      {inputHandleIds.map((handleId) => (
        <Handle
          key={handleId}
          id={handleId}
          type="target"
          position={Position.Left}
          isConnectable={false}
          aria-label={`Unavailable input ${handleId}`}
          style={{ top: "50%", opacity: 0.45, pointerEvents: "none" }}
        />
      ))}
      {outputHandleIds.map((handleId) => (
        <Handle
          key={handleId}
          id={handleId}
          type="source"
          position={Position.Right}
          isConnectable={false}
          aria-label={`Unavailable output ${handleId}`}
          style={{ top: "50%", opacity: 0.45, pointerEvents: "none" }}
        />
      ))}
    </div>
  )
}

export default memo(SubmodelNode)
