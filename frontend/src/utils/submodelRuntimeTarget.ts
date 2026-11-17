import type { Node } from "@xyflow/react"
import { nodeData } from "../types/node"
import { canonicalSubmodelBoundary, resolveSubmodelBoundaryNodes } from "./apiInputPorts"
import { frameKey, type FrameTarget } from "./nodeAddress"

export interface SubmodelOutputPreview {
  /** The output port read, by address: the server resolves its producing child. */
  target: FrameTarget
  outputName: string
  outputHandle: string
  outputNames: string[]
  cacheKey: string
}

/** Resolve the frame selector's display name to the port's stable identity. */
export function submodelOutputHandle(node: Node, outputName: string): string {
  const handle = Object.entries(nodeData(node)._sourceHandleInputNames ?? {})
    .find(([key, name]) => key.startsWith("out__") && name === outputName)?.[0]
  if (handle === undefined) throw new Error(`Submodel ${node.id} has no output ${outputName}. Select a declared output.`)
  return handle
}

/** Public output identity stays on screen; only its qualified source crosses the API. */
export function submodelOutputPreview(
  node: Node,
  submodels: Record<string, unknown>,
  chosenOutputHandle?: string,
): SubmodelOutputPreview | null {
  const boundary = { id: node.id, data: nodeData(node) }
  const { definition } = canonicalSubmodelBoundary(boundary, submodels)
  const outputs = definition.outputPorts.map((port) => {
    const outputName = nodeData(node)._sourceHandleInputNames?.[`out__${port.name}`]
    if (typeof outputName !== "string" || !outputName) {
      throw new Error(`Submodel ${node.id} output ${port.name} has no authoritative identity. Reload the pipeline.`)
    }
    return { port, outputName }
  })
  if (outputs.length === 0) return null
  const selected = chosenOutputHandle === undefined ? outputs[0] : outputs.find(({ port }) => `out__${port.name}` === chosenOutputHandle)
  if (!selected) throw new Error(`Submodel ${node.id} has no output ${chosenOutputHandle}. Select a declared output.`)
  resolveSubmodelBoundaryNodes(boundary, `out__${selected.port.name}`, "out", submodels)
  const target: FrameTarget = { kind: "occurrenceOutput", occurrence: node.id, port: selected.port.name }
  return {
    target,
    outputName: selected.outputName,
    outputHandle: `out__${selected.port.name}`,
    outputNames: outputs.map((output) => output.outputName),
    cacheKey: frameKey(target),
  }
}

export interface DrilledOccurrenceIdentity {
  instanceId: string
  definitionId: string
}
