import type { SubmodelBoundaryPort } from "../../types/node"
import { InputSourcesBar, type InputSource, type SimpleNode } from "./_shared"

type SubmodelPortEditorProps = {
  node: SimpleNode
  onDeleteInputPort?: (portId: string) => void
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
    </div>
  )
}
