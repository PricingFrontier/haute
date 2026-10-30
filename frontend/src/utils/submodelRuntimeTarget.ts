import type { Node } from "@xyflow/react"
import { nodeData, type SubmodelPortData } from "../types/node"
import { NODE_TYPES } from "./nodeTypes"
import { canonicalSubmodelBoundary, resolveSubmodelBoundaryNodes } from "./apiInputPorts"

export interface SubmodelOutputPreview {
  nodeId: string
  portLabel: string | undefined
  outputName: string
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
  return {
    nodeId: qualifiedRuntimeNodeId(node.id, selected.port.source.nodeId),
    portLabel: selected.port.source.handleId ?? undefined,
    outputName: selected.outputName,
    outputNames: outputs.map((output) => output.outputName),
    cacheKey: `submodel_output/${encodeRuntimeIdPart(node.id)}/${encodeRuntimeIdPart(selected.port.name)}`,
  }
}

export interface DrilledOccurrenceIdentity {
  instanceId: string
  definitionId: string
}

/** Matches Python urllib.parse.quote(value, safe='') for runtime path parts. */
export function encodeRuntimeIdPart(value: string): string {
  return encodeURIComponent(value).replace(
    /[!'()*]/g,
    (character) => `%${character.charCodeAt(0).toString(16).toUpperCase()}`,
  )
}

export function qualifiedRuntimeNodeId(instanceId: string, localNodeId: string): string {
  return `submodel_runtime/${encodeRuntimeIdPart(instanceId)}/${encodeRuntimeIdPart(localNodeId)}`
}

function canonicalOccurrenceIdentity(
  data: Partial<DrilledOccurrenceIdentity>,
  owner: string,
): DrilledOccurrenceIdentity {
  if (
    typeof data.instanceId !== "string" || data.instanceId.length === 0 || data.instanceId.trim() !== data.instanceId
    || typeof data.definitionId !== "string" || data.definitionId.length === 0 || data.definitionId.trim() !== data.definitionId
  ) throw new Error(`${owner} has malformed canonical identity`)
  return { instanceId: data.instanceId, definitionId: data.definitionId }
}

/** Returns null for a root view; validates every drilled boundary otherwise. */
export function resolveDrilledOccurrenceIdentity(nodes: Node[]): DrilledOccurrenceIdentity | null {
  let identity: DrilledOccurrenceIdentity | null = null
  for (const node of nodes) {
    const data = nodeData(node)
    if (data.nodeType !== NODE_TYPES.SUBMODEL_PORT) continue
    const boundary = canonicalOccurrenceIdentity(
      data as Partial<SubmodelPortData>,
      `Submodel boundary ${node.id}`,
    )
    if (identity && (identity.instanceId !== boundary.instanceId || identity.definitionId !== boundary.definitionId)) {
      throw new Error("Drilled submodel boundaries disagree on canonical identity")
    }
    identity = boundary
  }
  return identity
}

export function runtimeNodeIdForVisibleNode(
  nodes: Node[],
  localNodeId: string,
  activeOccurrence: DrilledOccurrenceIdentity | null,
): string {
  const boundaryOccurrence = resolveDrilledOccurrenceIdentity(nodes)
  if (activeOccurrence === null) {
    if (boundaryOccurrence) {
      throw new Error("Drilled submodel boundaries require active navigation identity")
    }
    return localNodeId
  }
  const occurrence = canonicalOccurrenceIdentity(
    activeOccurrence,
    "Active submodel navigation",
  )
  if (
    boundaryOccurrence
    && (
      boundaryOccurrence.instanceId !== occurrence.instanceId
      || boundaryOccurrence.definitionId !== occurrence.definitionId
    )
  ) {
    throw new Error("Drilled submodel boundaries disagree with active navigation identity")
  }
  return qualifiedRuntimeNodeId(occurrence.instanceId, localNodeId)
}
