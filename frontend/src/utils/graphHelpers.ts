import type { Node, Edge } from "@xyflow/react"
import {
  isSubmodelDefinition,
  isSubmodelInstanceConfig,
} from "../types/node"
import { authoritativeSourceHandles } from "./apiInputPorts"
import {
  NODE_TYPES,
  SINK_ONLY_TYPES,
  SOURCE_ONLY_TYPES,
} from "./nodeTypes"
import {
  EDGE_JOIN_BASE_HANDLE,
  EDGE_JOIN_JOIN_BOTTOM_HANDLE,
  EDGE_JOIN_JOIN_HANDLE,
} from "./edgeJoinRoles"
import { DEFAULT_TARGET_HANDLE } from "./flowHandles"

/**
 * Compute the next node ID counter from an array of nodes.
 *
 * Scans node IDs for the pattern `_<number>` suffix and returns
 * `max + 1` so the next created node gets a unique suffix.
 */
export function computeNextNodeId(nodes: Node[]): number {
  return (
    nodes.reduce((max, n) => {
      const match = n.id.match(/_(\d+)$/)
      return match ? Math.max(max, parseInt(match[1], 10)) : max
    }, -1) + 1
  )
}

/**
 * Normalise edges to default (non-animated) type.
 *
 * Strips any custom edge types from the backend so React Flow
 * renders standard edges.
 */
export function normalizeEdges<T extends Edge>(edges: T[]): T[] {
  return edges.map((e) => ({ ...e, type: "default", animated: false }))
}

export type RejectedIncomingEdge = {
  edge: Edge
  reason: string
}

export type FilterIncomingEdgesResult = {
  validEdges: Edge[]
  rejectedEdges: RejectedIncomingEdge[]
}

type HandleDirection = "source" | "target"
type NodeDataRecord = Record<string, unknown>

function nodeData(node: Node): NodeDataRecord {
  return node.data && typeof node.data === "object"
    ? node.data as NodeDataRecord
    : {}
}

function nodeType(node: Node): string {
  const value = nodeData(node).nodeType
  return typeof value === "string" ? value : ""
}

function nodeConfig(node: Node): Record<string, unknown> {
  const value = nodeData(node).config
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function normalizedHandle(handle: string | null | undefined): string | null {
  return handle ?? null
}

/**
 * Return the exact handle ids rendered by the current node component.
 *
 * An empty set means the node does not render a handle in that direction.
 * `null` represents React Flow's default id-less handle.
 */
function liveHandles(
  node: Node,
  direction: HandleDirection,
  submodels: Readonly<Record<string, unknown>>,
): Set<string | null> {
  const type = nodeType(node)

  if (type === NODE_TYPES.API_INPUT) {
    if (direction === "target") return new Set()
    return new Set(authoritativeSourceHandles(node))
  }

  if (type === NODE_TYPES.EDGE_JOIN && direction === "target") {
    return new Set([
      EDGE_JOIN_BASE_HANDLE,
      EDGE_JOIN_JOIN_HANDLE,
      EDGE_JOIN_JOIN_BOTTOM_HANDLE,
    ])
  }

  if (type === NODE_TYPES.SUBMODEL) {
    const config = nodeConfig(node)
    if (!isSubmodelInstanceConfig(config)) return new Set()
    const definition = submodels[config.definitionId]
    if (!isSubmodelDefinition(definition, config.definitionId)) return new Set()
    if (direction === "target") {
      return new Set(definition.inputPorts.map(port => `in__${port.portId}`))
    }
    const outputPorts = definition.outputPorts
    return outputPorts.length > 0
      ? new Set(outputPorts.map(port => `out__${port.portId}`))
      : new Set()
  }

  if (type === NODE_TYPES.SUBMODEL_PORT) {
    const data = nodeData(node)
    if (!Array.isArray(data.ports)) return new Set()
    if (data.portDirection === "input") {
      const portIds = data.ports.flatMap((port) => {
        if (!port || typeof port !== "object" || Array.isArray(port)) return []
        const id = (port as Record<string, unknown>).id
        return typeof id === "string" && id.length > 0 ? [id] : []
      })
      return direction === "source" ? new Set(portIds) : new Set()
    }
    if (data.portDirection === "output") {
      return direction === "target"
        ? new Set([null, DEFAULT_TARGET_HANDLE])
        : new Set()
    }
    return new Set()
  }

  if (direction === "source") {
    if (SINK_ONLY_TYPES.has(type)) return new Set()
    return new Set([null])
  }

  if (SOURCE_ONLY_TYPES.has(type)) return new Set()
  return new Set([null, DEFAULT_TARGET_HANDLE])
}

function unavailableHandleReason(
  node: Node,
  direction: HandleDirection,
  handle: string | null,
  submodels: Readonly<Record<string, unknown>>,
): string {
  const rendered = liveHandles(node, direction, submodels)
  if (rendered.size === 0) {
    return `${direction} node "${node.id}" has no ${direction} handle`
  }
  const label = handle === null ? "<default>" : `"${handle}"`
  return `${direction} handle ${label} is not available on node "${node.id}"`
}

/**
 * Partition imported edges by whether both endpoint nodes and both endpoint
 * handles exist in the incoming graph's live node configuration. Submodel
 * occurrence handles are resolved from the canonical definition registry,
 * matching the handles rendered by SubmodelNode.
 */
export function filterIncomingEdges(
  nodes: Node[],
  edges: Edge[],
  submodels: Readonly<Record<string, unknown>>,
): FilterIncomingEdgesResult {
  const nodesById = new Map(nodes.map(node => [node.id, node]))
  const validEdges: Edge[] = []
  const rejectedEdges: RejectedIncomingEdge[] = []

  for (const edge of edges) {
    const sourceNode = nodesById.get(edge.source)
    if (!sourceNode) {
      rejectedEdges.push({
        edge,
        reason: `source node "${edge.source}" is missing`,
      })
      continue
    }
    const targetNode = nodesById.get(edge.target)
    if (!targetNode) {
      rejectedEdges.push({
        edge,
        reason: `target node "${edge.target}" is missing`,
      })
      continue
    }

    const sourceHandle = normalizedHandle(edge.sourceHandle)
    if (!liveHandles(sourceNode, "source", submodels).has(sourceHandle)) {
      rejectedEdges.push({
        edge,
        reason: unavailableHandleReason(sourceNode, "source", sourceHandle, submodels),
      })
      continue
    }

    const targetHandle = normalizedHandle(edge.targetHandle)
    if (!liveHandles(targetNode, "target", submodels).has(targetHandle)) {
      rejectedEdges.push({
        edge,
        reason: unavailableHandleReason(targetNode, "target", targetHandle, submodels),
      })
      continue
    }

    validEdges.push(edge)
  }

  return { validEdges, rejectedEdges }
}
