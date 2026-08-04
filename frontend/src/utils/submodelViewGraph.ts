import type { Edge, Node } from "@xyflow/react"
import {
  isSubmodelDefinition,
  isSubmodelInstanceConfig,
  type PipelineEdge,
  type SubmodelBoundaryEdgeData,
  type SubmodelDefinition,
  type SubmodelPortData,
} from "../types/node"
import { normalizeEdges } from "./graphHelpers"
import { NODE_TYPES } from "./nodeTypes"

export interface SubmodelViewGraphInput {
  submodelName: string
  instanceId: string
  definition: SubmodelDefinition
  childNodes: Node[]
  childEdges: Edge[]
  parentNodes: Node[]
  parentEdges: Edge[]
}

export interface SubmodelViewGraph {
  nodes: Node[]
  edges: Edge[]
}

function buildCanonicalSubmodelViewGraph(
  input: SubmodelViewGraphInput,
): SubmodelViewGraph {
  const {
    submodelName,
    instanceId,
    definition,
    childNodes,
    childEdges,
    parentNodes,
    parentEdges,
  } = input
  const definitionCandidate: unknown = definition
  if (!isSubmodelDefinition(definitionCandidate, definition.definitionId)) {
    throw new Error(`Submodel definition ${definition.definitionId} is malformed`)
  }
  const placeholder = parentNodes.find((node) => node.id === instanceId)
  if (!placeholder) throw new Error(`Submodel instance ${instanceId} is missing from the parent graph`)
  const config = placeholder.data.config
  if (!isSubmodelInstanceConfig(config) || config.definitionId !== definition.definitionId) {
    throw new Error(
      `Submodel instance ${instanceId} does not reference definition ${definition.definitionId}`,
    )
  }

  const childIds = new Set(childNodes.map((node) => node.id))
  const inputId = stableId("boundary", [instanceId, "input"])
  const outputId = stableId("boundary", [instanceId, "output"])
  if (childIds.has(inputId) || childIds.has(outputId)) {
    throw new Error(`Submodel boundary id collides with a child node for ${submodelName}`)
  }

  const inputHandles = new Set(definition.inputPorts.map((port) => `in__${port.portId}`))
  const outputHandles = new Set(definition.outputPorts.map((port) => `out__${port.portId}`))
  for (const edge of parentEdges as PipelineEdge[]) {
    if (
      edge.target === instanceId
      && (typeof edge.targetHandle !== "string" || !inputHandles.has(edge.targetHandle))
    ) {
      throw new Error(
        `Submodel instance ${instanceId} has undeclared input handle ${String(edge.targetHandle)}`,
      )
    }
    if (
      edge.source === instanceId
      && (typeof edge.sourceHandle !== "string" || !outputHandles.has(edge.sourceHandle))
    ) {
      throw new Error(
        `Submodel instance ${instanceId} has undeclared output handle ${String(edge.sourceHandle)}`,
      )
    }
  }

  const inputPorts: SubmodelPortData["ports"] = []
  const inputExternalIds: string[] = []
  const outputExternalIds: string[] = []
  const syntheticEdges: Edge[] = []
  const occupiedEdgeIds = new Set(childEdges.map((edge) => edge.id))

  for (const port of definition.inputPorts) {
    const handle = `in__${port.portId}`
    const consumers = (parentEdges as PipelineEdge[]).filter(
      (edge) => edge.target === instanceId && edge.targetHandle === handle,
    )
    inputPorts.push({
      id: port.portId,
      label: port.label,
      parentEdges: consumers,
    })
    for (const consumer of consumers) {
      if (!inputExternalIds.includes(consumer.source)) inputExternalIds.push(consumer.source)
    }
    for (const [index, target] of port.targets.entries()) {
      if (!childIds.has(target.nodeId)) {
        throw new Error(
          `Submodel definition ${definition.definitionId} input port ${port.portId} references missing child ${target.nodeId}`,
        )
      }
      syntheticEdges.push({
        id: uniqueEdgeId(
          stableId("input-edge", [instanceId, port.portId, target.nodeId, String(index)]),
          occupiedEdgeIds,
        ),
        source: inputId,
        sourceHandle: port.portId,
        target: target.nodeId,
        targetHandle: target.handleId,
        data: {
          submodelBoundary: {
            direction: "input",
            portId: port.portId,
            parentEdges: consumers,
          },
        } satisfies SubmodelBoundaryEdgeData,
      })
    }
  }

  for (const port of definition.outputPorts) {
    if (!childIds.has(port.source.nodeId)) {
      throw new Error(
        `Submodel definition ${definition.definitionId} output port ${port.portId} references missing child ${port.source.nodeId}`,
      )
    }
    const handle = `out__${port.portId}`
    const consumers = (parentEdges as PipelineEdge[]).filter(
      (edge) => edge.source === instanceId && edge.sourceHandle === handle,
    )
    for (const consumer of consumers) {
      if (!outputExternalIds.includes(consumer.target)) outputExternalIds.push(consumer.target)
    }
    syntheticEdges.push({
      id: uniqueEdgeId(stableId("output-edge", [instanceId, port.portId]), occupiedEdgeIds),
      source: port.source.nodeId,
      sourceHandle: port.source.handleId,
      target: outputId,
      targetHandle: null,
      data: {
        submodelBoundary: {
          direction: "output",
          portId: port.portId,
          parentConsumerEdges: consumers,
        },
      } satisfies SubmodelBoundaryEdgeData,
    })
  }

  return {
    nodes: [
      ...childNodes,
      boundaryNode(
        inputId,
        "input",
        inputPorts,
        inputExternalIds,
        instanceId,
        definition.definitionId,
      ),
      boundaryNode(
        outputId,
        "output",
        [],
        outputExternalIds,
        instanceId,
        definition.definitionId,
      ),
    ],
    edges: normalizeEdges([...childEdges, ...syntheticEdges]),
  }
}

type BoundaryData = SubmodelPortData & { nodeType: typeof NODE_TYPES.SUBMODEL_PORT }
function stableId(kind: string, values: readonly (string | null)[]): string { return `submodel-view__${kind}__${encodeURIComponent(JSON.stringify(values))}` }
function uniqueEdgeId(base: string, occupied: Set<string>): string { let candidate = base; let suffix = 1; while (occupied.has(candidate)) candidate = `${base}__${suffix++}`; occupied.add(candidate); return candidate }
function boundaryNode(
  id: string,
  direction: "input" | "output",
  ports: SubmodelPortData["ports"],
  externalNodeIds: string[],
  instanceId: string,
  definitionId: string,
): Node<BoundaryData> {
  return {
    id,
    type: NODE_TYPES.SUBMODEL_PORT,
    position: { x: 0, y: 0 },
    data: {
      label: direction === "input" ? "INPUT" : "OUTPUT",
      nodeType: NODE_TYPES.SUBMODEL_PORT,
      instanceId,
      definitionId,
      portDirection: direction,
      ports,
      externalNodeIds,
    },
  }
}

export function buildSubmodelViewGraph(input: SubmodelViewGraphInput): SubmodelViewGraph {
  return buildCanonicalSubmodelViewGraph(input)
}
