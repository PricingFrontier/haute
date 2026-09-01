import type { Edge, Node, XYPosition } from "@xyflow/react"
import type { SimpleEdge, SimpleNode } from "../panels/editors/_shared"
import { NODE_TYPES } from "./nodeTypes"
import { appEdge, appNode, selectOnlyNode } from "./flowElements"
import {
  EDGE_JOIN_BASE_CONFIG_KEY,
  EDGE_JOIN_BASE_HANDLE,
  EDGE_JOIN_JOIN_CONFIG_KEY,
  EDGE_JOIN_JOIN_HANDLE,
  edgeJoinRoleConfigKey,
} from "./edgeJoinRoles"
import { edgeInputName, UNRESOLVED_INPUT_NAME } from "./apiInputPorts"

export type EdgeJoinFailureReason =
  | "target-edge-not-found"
  | "source-node-not-found"
  | "target-edge-node-not-found"
  | "self-join"
  | "cycle"

type DeferredInputMappingRewrite = {
  targetEdge: Edge
  oldCurrentInputName: string
}

export type EdgeJoinInsertSuccess = {
  ok: true
  nodes: Node[]
  edges: Edge[]
  newNodeId: string
  deferredInputMappingRewrite: DeferredInputMappingRewrite | null
}

export type EdgeJoinInsertResult =
  | EdgeJoinInsertSuccess
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
  submodels?: Record<string, unknown>
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

/**
 * Checks whether dropping a source connection onto an existing edge can create
 * an Edge Join. This deliberately mirrors the eventual edge rewrite so drag
 * feedback and the release action agree exactly.
 */
export function validateEdgeJoinInsertionCandidate(
  {
    nodes,
    edges,
    targetEdgeId,
    connection,
  }: Pick<
    InsertEdgeJoinNodeParams,
    "nodes" | "edges" | "targetEdgeId" | "connection"
  >,
): { ok: true } | { ok: false; reason: EdgeJoinFailureReason } {
  const targetEdge = edges.find((edge) => edge.id === targetEdgeId)
  const source = connection.source
  if (!targetEdge) return { ok: false, reason: "target-edge-not-found" }
  if (!source) return { ok: false, reason: "source-node-not-found" }

  const nodeIds = new Set(nodes.map((node) => node.id))
  if (!nodeIds.has(source)) return { ok: false, reason: "source-node-not-found" }
  if (!nodeIds.has(targetEdge.source) || !nodeIds.has(targetEdge.target)) {
    return { ok: false, reason: "target-edge-node-not-found" }
  }
  if (source === targetEdge.source) return { ok: false, reason: "self-join" }

  let candidateNodeId = "__edge_join_insertion_candidate__"
  while (nodeIds.has(candidateNodeId)) candidateNodeId += "_"
  const candidateNode = buildEdgeJoinNode({
    id: candidateNodeId,
    position: { x: 0, y: 0 },
    baseInput: targetEdge.source,
    joinInput: source,
  })
  const nextEdges = [
    ...edges.filter((edge) => edge.id !== targetEdgeId),
    ...edgeJoinReplacementEdges(targetEdge, candidateNodeId, { ...connection, source }),
  ]
  if (hasDirectedCycle([...nodes, candidateNode], nextEdges)) {
    return { ok: false, reason: "cycle" }
  }
  return { ok: true }
}

export function insertEdgeJoinNode({
  nodes,
  edges,
  submodels,
  targetEdgeId,
  connection,
  position,
  idFactory,
}: InsertEdgeJoinNodeParams): EdgeJoinInsertResult {
  const targetEdge = edges.find((edge) => edge.id === targetEdgeId)
  const source = connection.source
  const validation = validateEdgeJoinInsertionCandidate({ nodes, edges, targetEdgeId, connection })
  if (!validation.ok) return validation
  // The validator above establishes this invariant without allocating an id.
  if (!targetEdge || !source) {
    throw new Error("Validated Edge Join candidate was unavailable")
  }

  const sourceNode = nodes.find((node) => node.id === targetEdge.source)
  if (!sourceNode) {
    throw new Error("Validated Edge Join target edge source was unavailable")
  }
  const oldCurrentInputName = edgeInputName(
    targetEdge as unknown as SimpleEdge,
    sourceNode as unknown as SimpleNode,
    submodels,
  )
  if (oldCurrentInputName === UNRESOLVED_INPUT_NAME) {
    throw new Error("Cannot preserve the downstream input name for an unresolved source frame")
  }
  assertDownstreamInputMappingCanBeRewritten(nodes, targetEdge, oldCurrentInputName)

  const newNodeId = idFactory()
  const newNode = buildEdgeJoinNode({
    id: newNodeId,
    position,
    baseInput: targetEdge.source,
    joinInput: source,
  })

  const replacementEdges = edgeJoinReplacementEdges(targetEdge, newNodeId, { ...connection, source })
  const nextEdges = [
    ...edges.filter((edge) => edge.id !== targetEdgeId),
    ...replacementEdges,
  ]
  return {
    ok: true,
    nodes: selectOnlyNode([
      ...nodes.map((node) => rewriteDownstreamSplitTargetNodeStructure(
        node,
        targetEdge,
        newNodeId,
      )),
      newNode,
    ], newNodeId),
    edges: nextEdges,
    newNodeId,
    deferredInputMappingRewrite: { targetEdge, oldCurrentInputName },
  }
}

function edgeJoinReplacementEdges(
  targetEdge: Edge,
  newNodeId: string,
  connection: SourceEndpoint & { source: string },
): Edge[] {
  return [
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
    deferredInputMappingRewrite: null,
  }
}

/**
 * Complete an Edge Join insertion after the server has attached executable
 * identities to the whole candidate graph. Structural changes are safe to
 * prepare locally, but the downstream inputMapping cannot be rewritten until
 * the new join's server-owned default input name is known.
 */
export function finalizeResolvedEdgeJoinInsertion(
  insertion: EdgeJoinInsertSuccess,
  resolved: { nodes: Node[]; edges: Edge[] },
): { nodes: Node[]; edges: Edge[]; newNodeId: string } {
  assertSameOrderedIds("node", insertion.nodes, resolved.nodes)
  assertSameOrderedIds("edge", insertion.edges, resolved.edges)

  const rewrite = insertion.deferredInputMappingRewrite
  if (!rewrite) return { ...resolved, newNodeId: insertion.newNodeId }

  const resolvedJoin = resolved.nodes.find((node) => node.id === insertion.newNodeId)
  const newCurrentInputName = resolvedJoin?.data._defaultInputName
  if (typeof newCurrentInputName !== "string" || newCurrentInputName.length === 0) {
    throw new Error(
      `identity resolver did not provide a default input name for ${insertion.newNodeId}`,
    )
  }

  return {
    nodes: resolved.nodes.map((node) => rewriteDownstreamInputMapping(
      node,
      rewrite.targetEdge,
      rewrite.oldCurrentInputName,
      newCurrentInputName,
    )),
    edges: resolved.edges,
    newNodeId: insertion.newNodeId,
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

function rewriteDownstreamSplitTargetNodeStructure(
  node: Node,
  targetEdge: Edge,
  newNodeId: string,
): Node {
  const roleRewritten = rewriteDownstreamEdgeJoinNode(node, targetEdge, newNodeId)
  return rewriteDownstreamInputsByParentContract(roleRewritten, targetEdge, newNodeId)
}

function assertDownstreamInputMappingCanBeRewritten(
  nodes: Node[],
  targetEdge: Edge,
  oldCurrentInputName: string,
): void {
  const target = nodes.find((node) => node.id === targetEdge.target)
  if (!target) return

  const config = { ...(target.data.config as Record<string, unknown> | undefined) }
  const rawInputMapping = config.inputMapping
  if (rawInputMapping !== undefined && !isRecord(rawInputMapping)) {
    throw new Error("Cannot rewrite a malformed inputMapping; expected an object")
  }
  const hasInputMapping = isRecord(rawInputMapping)
  const shouldCreateMapping = target.data.nodeType === NODE_TYPES.POLARS && !config.instanceOf
  if (!hasInputMapping && !shouldCreateMapping) return

  let matchedOldCurrentInput = false
  for (const currentName of Object.values(hasInputMapping ? rawInputMapping : {})) {
    if (typeof currentName !== "string") {
      throw new Error("Cannot rewrite a malformed inputMapping; values must be strings")
    }
    if (currentName === oldCurrentInputName) matchedOldCurrentInput = true
  }
  if (
    shouldCreateMapping
    && !matchedOldCurrentInput
    && Object.prototype.hasOwnProperty.call(rawInputMapping ?? {}, oldCurrentInputName)
  ) {
    throw new Error(
      `Cannot preserve input name "${oldCurrentInputName}" because inputMapping already uses it`,
    )
  }
}

function rewriteDownstreamInputMapping(
  node: Node,
  targetEdge: Edge,
  oldCurrentInputName: string,
  newCurrentInputName: string,
): Node {
  if (node.id !== targetEdge.target) return node

  const config = { ...(node.data.config as Record<string, unknown> | undefined) }
  const rawInputMapping = config.inputMapping
  if (rawInputMapping !== undefined && !isRecord(rawInputMapping)) {
    throw new Error("Cannot rewrite a malformed inputMapping; expected an object")
  }
  const hasInputMapping = isRecord(rawInputMapping)
  const shouldCreateMapping = node.data.nodeType === NODE_TYPES.POLARS && !config.instanceOf
  if (!hasInputMapping && !shouldCreateMapping) return node

  const inputMapping = hasInputMapping ? rawInputMapping : {}
  let matchedOldCurrentInput = false
  const nextInputMapping: Record<string, unknown> = {}
  for (const [inputName, currentName] of Object.entries(inputMapping)) {
    if (typeof currentName !== "string") {
      throw new Error("Cannot rewrite a malformed inputMapping; values must be strings")
    }
    if (currentName === oldCurrentInputName) {
      nextInputMapping[inputName] = newCurrentInputName
      matchedOldCurrentInput = true
    } else {
      nextInputMapping[inputName] = currentName
    }
  }
  if (shouldCreateMapping && !matchedOldCurrentInput) {
    if (Object.prototype.hasOwnProperty.call(nextInputMapping, oldCurrentInputName)) {
      throw new Error(
        `Cannot preserve input name "${oldCurrentInputName}" because inputMapping already uses it`,
      )
    }
    nextInputMapping[oldCurrentInputName] = newCurrentInputName
  }

  return {
    ...node,
    data: {
      ...node.data,
      config: {
        ...config,
        inputMapping: nextInputMapping,
      },
    },
  }
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
  const roleKey = edgeJoinRoleConfigKey(targetEdge.targetHandle)
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function assertSameOrderedIds(
  kind: "node" | "edge",
  expected: Array<{ id: string }>,
  actual: Array<{ id: string }>,
): void {
  if (
    actual.length !== expected.length
    || actual.some((item, index) => item.id !== expected[index]?.id)
  ) {
    throw new Error(`identity resolver returned an invalid ${kind} sequence`)
  }
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
