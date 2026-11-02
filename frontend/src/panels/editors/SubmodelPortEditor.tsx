import type { SubmodelBoundaryPort } from "../../types/node"
import type { PipelineNameViolation } from "../../types/pipelineDocument"
import { portViolationMessages } from "../../utils/inputBindingViolations"
import { InputSourcesBar, type InputSource, type SimpleNode } from "./_shared"

type SubmodelPortEditorProps = {
  node: SimpleNode
  onDeleteInputPort?: (portName: string) => void
  /** The document's violations a frame or a public input port names. */
  inputBindingViolations?: readonly PipelineNameViolation[]
}

const isNonBlankText = (value: unknown): value is string =>
  typeof value === "string" && value.trim().length > 0

function isBoundaryPort(value: unknown): value is SubmodelBoundaryPort {
  if (typeof value !== "object" || value === null) return false
  const port = value as Partial<SubmodelBoundaryPort>
  return isNonBlankText(port.id)
    && isNonBlankText(port.label)
    && Array.isArray(port.parentEdges)
}

export default function SubmodelPortEditor({
  node,
  onDeleteInputPort,
  inputBindingViolations = [],
}: SubmodelPortEditorProps) {
  const { definitionId, instanceId, portDirection, ports } = node.data
  const validPorts = Array.isArray(ports) && ports.every(isBoundaryPort)
  const portIds = validPorts ? ports.map((port) => port.id) : []
  if (
    (portDirection !== "input" && portDirection !== "output")
    || !isNonBlankText(instanceId)
    || !isNonBlankText(definitionId)
    || !validPorts
    || new Set(portIds).size !== portIds.length
  ) {
    return (
      <div role="alert" className="px-4 py-3 text-xs" style={{ color: "var(--danger)" }}>
        Submodel boundary data is invalid.
      </div>
    )
  }

  if (portDirection === "output") return null

  const inputSources: InputSource[] = ports.map((port) => ({
    sourceNodeId: instanceId,
    sourceLabel: "Submodel input",
    name: port.label,
    edgeId: port.id,
  }))
  // A port's id is its public name.
  const violations = portViolationMessages(inputBindingViolations, definitionId)
  const portViolations = ports.flatMap((port) => {
    const message = violations.get(port.id)
    return message === undefined ? [] : [{ port: port.id, message }]
  })

  return (
    <div className="flex-1 flex flex-col min-h-0 px-3 py-2 gap-2">
      {inputSources.length > 0 ? (
        <InputSourcesBar
          inputSources={inputSources}
          onDeleteInput={onDeleteInputPort}
          deleteTitle={(name) =>
            `Remove public input "${name}" from this submodel, including its internal routes and every occurrence's connection`}
        />
      ) : (
        <div
          className="rounded-lg px-3 py-2 text-[11px]"
          style={{
            background: "var(--bg-input)",
            border: "1px solid var(--border)",
            color: "var(--text-muted)",
          }}
        >
          No input frames
        </div>
      )}
      {portViolations.map(({ port, message }) => (
        <div
          key={port}
          role="alert"
          data-testid={`submodel-port-${port}-violation`}
          className="px-1.5 py-0.5 rounded text-[10px] leading-snug"
          style={{ background: "var(--danger-soft)", color: "var(--danger-text)" }}
        >
          {message}
        </div>
      ))}
    </div>
  )
}
