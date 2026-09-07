/** Browser adapter for server-owned executable and configuration identities. */

import type { Edge, Node } from "@xyflow/react"

import { resolveEditorNodeIdentities } from "../api/client"
import type {
  EditorIdentityBatchRequest,
  EditorIdentityBatchResponse,
  EditorIdentityRequestNode,
  EditorNodeIdentity,
} from "../api/types"
import {
  PIPELINE_NODE_TYPES,
  effectiveNodeType,
  isSubmodelDefinition,
  isSubmodelInstanceConfig,
  type NodeTypeValue,
  type PipelineEdge,
  type SubmodelDefinition,
} from "../types/node"
import { apiInputFrameLabels } from "./apiInputPorts"
import { NODE_TYPES } from "./nodeTypes"

type SubmodelRegistry = Record<string, unknown>

const NODE_TYPE_VALUES = new Set<string>(Object.values(PIPELINE_NODE_TYPES))

function requireNodeType(node: Node): NodeTypeValue {
  const nodeType = effectiveNodeType(node)
  if (!NODE_TYPE_VALUES.has(nodeType)) {
    throw new Error(`Cannot resolve editor identity for node ${node.id}: unknown type ${nodeType}`)
  }
  return nodeType as NodeTypeValue
}

function submodelDefinition(
  node: Node,
  submodels: SubmodelRegistry,
): SubmodelDefinition {
  if (!isSubmodelInstanceConfig(node.data.config)) {
    throw new Error(`Cannot resolve editor identity for submodel ${node.id}: malformed occurrence`)
  }
  const definition = submodels[node.data.config.definitionId]
  if (!isSubmodelDefinition(definition, node.data.config.definitionId)) {
    throw new Error(
      `Cannot resolve editor identity for submodel ${node.id}: definition ${node.data.config.definitionId} is unavailable`,
    )
  }
  return definition
}

function submodelPortHandles(node: Node): string[] {
  if (node.data.portDirection !== "input") return []
  if (!Array.isArray(node.data.ports)) {
    throw new Error(`Cannot resolve editor identity for submodel port ${node.id}: ports are malformed`)
  }
  return node.data.ports.map((value, index) => {
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      throw new Error(`Cannot resolve editor identity for submodel port ${node.id}: port ${index} is malformed`)
    }
    const id = (value as Record<string, unknown>).id
    if (typeof id !== "string" || id.length === 0) {
      throw new Error(`Cannot resolve editor identity for submodel port ${node.id}: port ${index} has no id`)
    }
    return id
  })
}

function requestNode(
  node: Node,
  submodels: SubmodelRegistry,
  reservedApiInputFrameLabels: ReadonlySet<string>,
): EditorIdentityRequestNode {
  const nodeType = requireNodeType(node)
  const label = node.data.label
  if (typeof label !== "string" || label.length === 0) {
    throw new Error(`Cannot resolve editor identity for node ${node.id}: label is missing`)
  }
  let sourceHandles: string[] = []
  let alias: string | undefined
  if (nodeType === NODE_TYPES.API_INPUT) {
    sourceHandles = apiInputFrameLabels(
      node.data.config as Record<string, unknown> | undefined,
      reservedApiInputFrameLabels,
    )
  } else if (nodeType === NODE_TYPES.SUBMODEL) {
    if (!isSubmodelInstanceConfig(node.data.config)) {
      throw new Error(`Cannot resolve editor identity for submodel ${node.id}: malformed occurrence`)
    }
    alias = node.data.config.alias
    const definition = submodelDefinition(node, submodels)
    sourceHandles = definition.outputPorts.map((port) => `out__${port.name}`)
  } else if (nodeType === NODE_TYPES.SUBMODEL_PORT) {
    sourceHandles = submodelPortHandles(node)
  }
  if (new Set(sourceHandles).size !== sourceHandles.length) {
    throw new Error(`Cannot resolve editor identity for node ${node.id}: source handles are duplicated`)
  }
  return {
    node_id: node.id,
    label,
    node_type: nodeType,
    source_handles: sourceHandles,
    ...(alias !== undefined ? { alias } : {}),
  }
}

export function buildEditorIdentityRequest(
  nodes: readonly Node[],
  submodels: SubmodelRegistry,
  reservedApiInputFrameLabels: ReadonlySet<string>,
): EditorIdentityBatchRequest {
  if (new Set(nodes.map((node) => node.id)).size !== nodes.length) {
    throw new Error("Cannot resolve editor identities: node ids are duplicated")
  }
  return {
    nodes: nodes.map((node) => requestNode(
      node,
      submodels,
      reservedApiInputFrameLabels,
    )),
  }
}

function attachIdentity(node: Node, identity: EditorNodeIdentity): Node {
  const data: Record<string, unknown> = {
    ...node.data,
    _functionName: identity.function_name,
    _defaultInputName: identity.default_input_name,
    _sourceHandleInputNames: structuredClone(identity.source_handle_input_names),
  }
  if (identity.config_reference === null) {
    delete data._configReference
  } else {
    data._configReference = identity.config_reference
  }
  return { ...node, data }
}

export function applyEditorIdentityResponse(
  nodes: readonly Node[],
  response: EditorIdentityBatchResponse,
): Node[] {
  if (
    response.identities.length !== nodes.length
    || response.identities.some((identity, index) => identity.node_id !== nodes[index]?.id)
  ) {
    throw new Error("Cannot attach editor identities: response does not match node order")
  }
  return nodes.map((node, index) => attachIdentity(node, response.identities[index]))
}

function edgeInputIdentity(edge: Edge, sourceNode: Node): string {
  const nodeType = requireNodeType(sourceNode)
  if (
    nodeType === NODE_TYPES.API_INPUT
    || nodeType === NODE_TYPES.SUBMODEL
    || nodeType === NODE_TYPES.SUBMODEL_PORT
  ) {
    const handle = edge.sourceHandle
    const mappings = sourceNode.data._sourceHandleInputNames
    const inputName =
      typeof handle === "string"
      && typeof mappings === "object"
      && mappings !== null
      && !Array.isArray(mappings)
        ? (mappings as Record<string, unknown>)[handle]
        : undefined
    if (typeof inputName !== "string" || inputName.length === 0) {
      throw new Error(
        `Cannot attach identity for edge ${edge.id}: source handle ${String(handle)} has no authoritative identity`,
      )
    }
    return inputName
  }
  const inputName = sourceNode.data._defaultInputName
  if (typeof inputName !== "string" || inputName.length === 0) {
    throw new Error(
      `Cannot attach identity for edge ${edge.id}: source ${sourceNode.id} has no authoritative default identity`,
    )
  }
  return inputName
}

export function attachEditorEdgeIdentities(
  edges: readonly Edge[],
  nodes: readonly Node[],
): PipelineEdge[] {
  const nodesById = new Map(nodes.map((node) => [node.id, node]))
  return edges.map((edge) => {
    const sourceNode = nodesById.get(edge.source)
    if (!sourceNode) {
      throw new Error(`Cannot attach identity for edge ${edge.id}: source ${edge.source} is missing`)
    }
    return {
      ...edge,
      data: {
        ...edge.data,
        _inputName: edgeInputIdentity(edge, sourceNode),
      },
    }
  })
}

type IdentityResolver = (
  request: EditorIdentityBatchRequest,
) => Promise<EditorIdentityBatchResponse>

export async function resolveEditorGraphIdentities({
  nodes,
  edges,
  submodels,
  reservedApiInputFrameLabels,
  resolve = resolveEditorNodeIdentities,
}: {
  nodes: readonly Node[]
  edges: readonly Edge[]
  submodels: SubmodelRegistry
  reservedApiInputFrameLabels: ReadonlySet<string>
  resolve?: IdentityResolver
}): Promise<{ nodes: Node[]; edges: PipelineEdge[] }> {
  const request = buildEditorIdentityRequest(nodes, submodels, reservedApiInputFrameLabels)
  const response = await resolve(request)
  const resolvedNodes = applyEditorIdentityResponse(nodes, response)
  return {
    nodes: resolvedNodes,
    edges: attachEditorEdgeIdentities(edges, resolvedNodes),
  }
}

function syntheticSubmodelPortNode(definition: SubmodelDefinition): Node {
  const childIds = new Set(definition.graph.nodes.map((node) => node.id))
  const baseId = "__submodel_input_ports__"
  let id = baseId
  let suffix = 1
  while (childIds.has(id)) {
    id = `${baseId}_${suffix}`
    suffix += 1
  }
  return {
    id,
    type: NODE_TYPES.SUBMODEL_PORT,
    position: { x: 0, y: 0 },
    data: {
      label: "Submodel inputs",
      nodeType: NODE_TYPES.SUBMODEL_PORT,
      portDirection: "input",
      ports: definition.inputPorts.map((port) => ({ id: port.name, label: port.name })),
    },
  }
}

/**
 * Resolves server-owned identities for a root graph and every canonical submodel definition.
 * Each definition is resolved in its own identity scope with a transient input-boundary node.
 */
export async function resolveCanonicalGraphIdentities({
  nodes,
  edges,
  submodels,
  reservedApiInputFrameLabels,
  resolve = resolveEditorNodeIdentities,
}: {
  nodes: readonly Node[]
  edges: readonly Edge[]
  submodels: SubmodelRegistry
  reservedApiInputFrameLabels: ReadonlySet<string>
  resolve?: IdentityResolver
}): Promise<{ nodes: Node[]; edges: PipelineEdge[]; submodels: Record<string, SubmodelDefinition> }> {
  const definitions = Object.entries(submodels).map(([definitionId, definition]) => {
    if (!isSubmodelDefinition(definition, definitionId)) {
      throw new Error(`Cannot resolve canonical submodel ${definitionId}: definition is malformed`)
    }
    return [definitionId, definition] as const
  })

  const root = await resolveEditorGraphIdentities({
    nodes,
    edges,
    submodels,
    reservedApiInputFrameLabels,
    resolve,
  })
  const resolvedSubmodels: Record<string, SubmodelDefinition> = {}
  for (const [definitionId, definition] of definitions) {
    const boundary = syntheticSubmodelPortNode(definition)
    const graph = await resolveEditorGraphIdentities({
      nodes: [...definition.graph.nodes, boundary],
      edges: definition.graph.edges,
      submodels,
      reservedApiInputFrameLabels,
      resolve,
    })
    const resolvedBoundary = graph.nodes.at(-1)
    if (!resolvedBoundary) {
      throw new Error(`Cannot resolve canonical submodel ${definitionId}: boundary node is missing`)
    }
    resolvedSubmodels[definitionId] = {
      ...definition,
      graph: {
        ...definition.graph,
        nodes: graph.nodes.slice(0, -1),
        edges: graph.edges,
      },
    }
  }
  return { ...root, submodels: resolvedSubmodels }
}
