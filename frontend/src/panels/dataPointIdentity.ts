import type { SimpleEdge, SimpleNode } from "./editors"

type NodeDataCacheIdentityInput = {
  node: SimpleNode
  allNodes: SimpleNode[]
  edges: SimpleEdge[]
  submodels?: Record<string, unknown>
  preamble?: string
}

function isExploreNode(node: SimpleNode): boolean {
  return node.type === "explore" || node.data.nodeType === "explore"
}

function dataAffectingConfig(node: SimpleNode): Record<string, unknown> {
  const config = node.data.config ?? {}
  if (!isExploreNode(node)) return config

  const {
    overview: _overview,
    pivot_formulas: _pivotFormulas,
    pivots: _pivots,
    charts: _charts,
    ...dataConfig
  } = config
  void _overview
  void _pivotFormulas
  void _pivots
  void _charts
  return dataConfig
}

function instanceOriginalId(node: SimpleNode | undefined): string | null {
  const reference = node?.data.config?.instanceOf
  return typeof reference === "string" && reference ? reference : null
}

/**
 * Every node whose configuration can change the data the consumer reads: its
 * upstream subgraph, plus the original of any instance in it, because execution
 * runs an instance with its original's configuration and the original may sit
 * outside the consumer's own edges.
 */
function dataAffectingNodeIds(
  nodeId: string,
  edges: SimpleEdge[],
  nodesById: Map<string, SimpleNode>,
): Set<string> {
  const ids = new Set([nodeId])
  let changed = true

  while (changed) {
    changed = false
    for (const edge of edges) {
      if (ids.has(edge.target) && !ids.has(edge.source)) {
        ids.add(edge.source)
        changed = true
      }
    }
    for (const id of Array.from(ids)) {
      const original = instanceOriginalId(nodesById.get(id))
      if (original && !ids.has(original)) {
        ids.add(original)
        changed = true
      }
    }
  }

  return ids
}

/**
 * The identity that decides when a consumer must re-ask the backend about the
 * data it reads: its upstream subgraph, its own data-affecting configuration
 * (which sets its column demand), the submodels, and the preamble.
 *
 * Presentation-only Explore configuration is excluded, so choosing a pivot or
 * chart never re-asks. The backend's `point` response stays authoritative: this
 * identity only gates the request, so covering more than the backend's
 * signature costs an extra request rather than a wrong answer.
 */
export function buildNodeDataCacheIdentity({
  node,
  allNodes,
  edges,
  submodels,
  preamble,
}: NodeDataCacheIdentityInput): Record<string, unknown> {
  const nodesById = new Map(allNodes.map((graphNode) => [graphNode.id, graphNode]))
  nodesById.set(node.id, node)
  const nodeIds = dataAffectingNodeIds(node.id, edges, nodesById)

  const nodes = Array.from(nodeIds)
    .map((nodeId) => nodesById.get(nodeId))
    .filter((graphNode): graphNode is SimpleNode => Boolean(graphNode))
    .map((graphNode) => ({
      id: graphNode.id,
      type: graphNode.type ?? null,
      label: graphNode.data.label,
      nodeType: graphNode.data.nodeType,
      config: dataAffectingConfig(graphNode),
    }))
    .sort((a, b) => a.id.localeCompare(b.id))

  const graphEdges = edges
    .filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target))
    .map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      // A handle change re-wires which frame a consumer reads, so it belongs to
      // the identity even though the endpoints are unchanged.
      sourceHandle: edge.sourceHandle ?? null,
      targetHandle: edge.targetHandle ?? null,
    }))
    .sort((a, b) => a.id.localeCompare(b.id))

  return {
    nodeId: node.id,
    nodes,
    edges: graphEdges,
    submodels: submodels ?? {},
    preamble: preamble ?? "",
  }
}
