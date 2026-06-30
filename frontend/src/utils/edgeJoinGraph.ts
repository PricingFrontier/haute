import type { Edge, Node, XYPosition } from "@xyflow/react"
import { NODE_TYPES } from "./nodeTypes"
import { appEdge, appNode, selectOnlyNode } from "./flowElements"
import {
  EDGE_JOIN_BASE_CONFIG_KEY,
  EDGE_JOIN_BASE_HANDLE,
  EDGE_JOIN_JOIN_CONFIG_KEY,
  EDGE_JOIN_JOIN_HANDLE,
  edgeJoinRoleConfigKey,
} from "./edgeJoinRoles"

export type EdgeJoinFailureReason =
  | "target-edge-not-found"
  | "source-node-not-found"
  | "target-edge-node-not-found"
  | "self-join"
  | "cycle"

export type EdgeJoinInsertResult =
  | { ok: true; nodes: Node[]; edges: Edge[]; newNodeId: string }
  | { ok: false; reason: EdgeJoinFailureReason }

export type EdgeJoinSwapInputsFailureReason =
  | "edge-join-node-not-found"
  | "target-node-not-edge-join"
  | "base-input-not-found"
  | "join-input-not-found"
  | "base-input-ambiguous"
  | "join-input-ambiguous"

export type EdgeJoinSwapInputsResult =
  | { ok: true; nodes: Node[]; edges: Edge[] }
  | { ok: false; reason: EdgeJoinSwapInputsFailureReason }

type InsertEdgeJoinNodeParams = {
  nodes: Node[]
  edges: Edge[]
  targetEdgeId: string
  connection: {
    source: string | null | undefined
    sourceHandle?: string | null
  }
  position: XYPosition
  idFactory: () => string
}

type SwapEdgeJoinInputsParams = {
  nodes: Node[]
  edges: Edge[]
  edgeJoinNodeId: string
}

type SourceEndpoint = {
  source: string | null | undefined
  sourceHandle?: string | null
}

type InsertEdgeJoinNodeFromSourcesParams = {
  nodes: Node[]
  edges: Edge[]
  base: SourceEndpoint
  join: SourceEndpoint
  position: XYPosition
  idFactory: () => string
}

export function insertEdgeJoinNode({
  nodes,
  edges,
  targetEdgeId,
  connection,
  position,
  idFactory,
}: InsertEdgeJoinNodeParams): EdgeJoinInsertResult {
  const targetEdge = edges.find((edge) => edge.id === targetEdgeId)
  if (!targetEdge) return { ok: false, reason: "target-edge-not-found" }
  if (!connection.source) return { ok: false, reason: "source-node-not-found" }

  const nodeIds = new Set(nodes.map((node) => node.id))
  if (!nodeIds.has(connection.source)) return { ok: false, reason: "source-node-not-found" }
  if (!nodeIds.has(targetEdge.source) || !nodeIds.has(targetEdge.target)) {
    return { ok: false, reason: "target-edge-node-not-found" }
  }
  if (connection.source === targetEdge.source) return { ok: false, reason: "self-join" }

  const newNodeId = idFactory()
  const newNode = buildEdgeJoinNode({
    id: newNodeId,
    position,
    baseInput: targetEdge.source,
    joinInput: connection.source,
  })

  const replacementEdges: Edge[] = [
    appEdge({
      source: targetEdge.source,
      target: newNodeId,
      sourceHandle: targetEdge.sourceHandle ?? null,
      targetHandle: EDGE_JOIN_BASE_HANDLE,
    }),
    appEdge({
      source: newNodeId,
      target: targetEdge.target,
      targetHandle: targetEdge.targetHandle ?? null,
    }),
    appEdge({
      source: connection.source,
      target: newNodeId,
      sourceHandle: connection.sourceHandle ?? null,
      targetHandle: EDGE_JOIN_JOIN_HANDLE,
    }),
  ]

  const nextEdges = [
    ...edges.filter((edge) => edge.id !== targetEdgeId),
    ...replacementEdges,
  ]
  if (hasDirectedCycle([...nodes, newNode], nextEdges)) {
    return { ok: false, reason: "cycle" }
  }
  return {
    ok: true,
    nodes: selectOnlyNode([
      ...nodes.map((node) => rewriteDownstreamSplitTargetNode(node, targetEdge, newNodeId)),
      newNode,
    ], newNodeId),
    edges: nextEdges,
    newNodeId,
  }
}

export function insertEdgeJoinNodeFromSources({
  nodes,
  edges,
  base,
  join,
  position,
  idFactory,
}: InsertEdgeJoinNodeFromSourcesParams): EdgeJoinInsertResult {
  if (!base.source || !join.source) return { ok: false, reason: "source-node-not-found" }
  if (base.source === join.source) return { ok: false, reason: "self-join" }

  const nodeIds = new Set(nodes.map((node) => node.id))
  if (!nodeIds.has(base.source) || !nodeIds.has(join.source)) {
    return { ok: false, reason: "source-node-not-found" }
  }

  const newNodeId = idFactory()
  const newNode = buildEdgeJoinNode({
    id: newNodeId,
    position,
    baseInput: base.source,
    joinInput: join.source,
  })

  const nextEdges = [
    ...edges,
    appEdge({
      source: base.source,
      target: newNodeId,
      sourceHandle: base.sourceHandle ?? null,
      targetHandle: EDGE_JOIN_BASE_HANDLE,
    }),
    appEdge({
      source: join.source,
      target: newNodeId,
      sourceHandle: join.sourceHandle ?? null,
      targetHandle: EDGE_JOIN_JOIN_HANDLE,
    }),
  ]

  if (hasDirectedCycle([...nodes, newNode], nextEdges)) {
    return { ok: false, reason: "cycle" }
  }

  return {
    ok: true,
    nodes: selectOnlyNode([...nodes, newNode], newNodeId),
    edges: nextEdges,
    newNodeId,
  }
}

export function swapEdgeJoinInputs({
  nodes,
  edges,
  edgeJoinNodeId,
}: SwapEdgeJoinInputsParams): EdgeJoinSwapInputsResult {
  const edgeJoinNode = nodes.find((node) => node.id === edgeJoinNodeId)
  if (!edgeJoinNode) return { ok: false, reason: "edge-join-node-not-found" }
  if (edgeJoinNode.data.nodeType !== NODE_TYPES.EDGE_JOIN) {
    return { ok: false, reason: "target-node-not-edge-join" }
  }

  const incomingEdges = edges.filter((edge) => edge.target === edgeJoinNodeId)
  const baseEdges = incomingEdges.filter((edge) => edge.targetHandle === EDGE_JOIN_BASE_HANDLE)
  const joinEdges = incomingEdges.filter((edge) => edge.targetHandle === EDGE_JOIN_JOIN_HANDLE)

  if (baseEdges.length === 0) return { ok: false, reason: "base-input-not-found" }
  if (joinEdges.length === 0) return { ok: false, reason: "join-input-not-found" }
  if (baseEdges.length > 1) return { ok: false, reason: "base-input-ambiguous" }
  if (joinEdges.length > 1) return { ok: false, reason: "join-input-ambiguous" }

  const baseEdge = baseEdges[0]
  const joinEdge = joinEdges[0]
  const config = { ...(edgeJoinNode.data.config as Record<string, unknown> | undefined) }

  return {
    ok: true,
    nodes: nodes.map((node) => {
      if (node.id !== edgeJoinNodeId) return node
      return {
        ...node,
        data: {
          ...node.data,
          config: {
            ...config,
            [EDGE_JOIN_BASE_CONFIG_KEY]: joinEdge.source,
            [EDGE_JOIN_JOIN_CONFIG_KEY]: baseEdge.source,
          },
        },
      }
    }),
    edges: edges.map((edge) => {
      if (edge === baseEdge) return { ...edge, targetHandle: EDGE_JOIN_JOIN_HANDLE }
      if (edge === joinEdge) return { ...edge, targetHandle: EDGE_JOIN_BASE_HANDLE }
      return edge
    }),
  }
}

function buildEdgeJoinNode({
  id,
  position,
  baseInput,
  joinInput,
}: {
  id: string
  position: XYPosition
  baseInput: string
  joinInput: string
}): Node {
  return appNode({
    id,
    type: NODE_TYPES.EDGE_JOIN,
    position,
    config: {
      [EDGE_JOIN_BASE_CONFIG_KEY]: baseInput,
      [EDGE_JOIN_JOIN_CONFIG_KEY]: joinInput,
    },
  })
}

function rewriteDownstreamSplitTargetNode(
  node: Node,
  targetEdge: Edge,
  newNodeId: string,
): Node {
  const roleRewritten = rewriteDownstreamEdgeJoinNode(node, targetEdge, newNodeId)
  return rewriteDownstreamInputsByParentContract(roleRewritten, targetEdge, newNodeId)
}

function rewriteDownstreamEdgeJoinNode(
  node: Node,
  targetEdge: Edge,
  newNodeId: string,
): Node {
  if (node.id !== targetEdge.target || node.data.nodeType !== NODE_TYPES.EDGE_JOIN) {
    return { ...node, selected: false }
  }
  const config = { ...(node.data.config as Record<string, unknown> | undefined) }
  const roleKey = edgeJoinRoleConfigKey(targetEdge.targetHandle) ?? roleKeyFromConfig(config, targetEdge.source)
  if (!roleKey) return { ...node, selected: false }

  return {
    ...node,
    selected: false,
    data: {
      ...node.data,
      config: {
        ...config,
        [roleKey]: newNodeId,
      },
    },
  }
}

function rewriteDownstreamInputsByParentContract(
  node: Node,
  targetEdge: Edge,
  newNodeId: string,
): Node {
  if (node.id !== targetEdge.target) return node
  const config = node.data.config
  if (!isRecord(config)) return node
  const contract = config.contract
  if (!isRecord(contract)) return node
  const inputsByParent = contract.inputs_by_parent
  if (!isRecord(inputsByParent)) return node
  if (!Object.prototype.hasOwnProperty.call(inputsByParent, targetEdge.source)) return node

  const nextInputsByParent: Record<string, unknown> = {}
  for (const [parentId, columns] of Object.entries(inputsByParent)) {
    nextInputsByParent[parentId === targetEdge.source ? newNodeId : parentId] = columns
  }

  return {
    ...node,
    data: {
      ...node.data,
      config: {
        ...config,
        contract: {
          ...contract,
          inputs_by_parent: nextInputsByParent,
        },
      },
    },
  }
}

function roleKeyFromConfig(
  config: Record<string, unknown>,
  sourceId: string,
): typeof EDGE_JOIN_BASE_CONFIG_KEY | typeof EDGE_JOIN_JOIN_CONFIG_KEY | null {
  if (config[EDGE_JOIN_BASE_CONFIG_KEY] === sourceId && config[EDGE_JOIN_JOIN_CONFIG_KEY] !== sourceId) {
    return EDGE_JOIN_BASE_CONFIG_KEY
  }
  if (config[EDGE_JOIN_JOIN_CONFIG_KEY] === sourceId && config[EDGE_JOIN_BASE_CONFIG_KEY] !== sourceId) {
    return EDGE_JOIN_JOIN_CONFIG_KEY
  }
  return null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function hasDirectedCycle(nodes: Node[], edges: Edge[]): boolean {
  const adjacency = new Map<string, string[]>()
  for (const node of nodes) adjacency.set(node.id, [])
  for (const edge of edges) {
    const children = adjacency.get(edge.source)
    if (!children) continue
    children.push(edge.target)
  }

  const visiting = new Set<string>()
  const visited = new Set<string>()

  const visit = (nodeId: string): boolean => {
    if (visiting.has(nodeId)) return true
    if (visited.has(nodeId)) return false
    visiting.add(nodeId)
    for (const child of adjacency.get(nodeId) ?? []) {
      if (visit(child)) return true
    }
    visiting.delete(nodeId)
    visited.add(nodeId)
    return false
  }

  return nodes.some((node) => visit(node.id))
}
