import type { Node } from "@xyflow/react"
import { nodeData, type SubmodelPortData } from "../types/node"
import { NODE_TYPES } from "./nodeTypes"

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
