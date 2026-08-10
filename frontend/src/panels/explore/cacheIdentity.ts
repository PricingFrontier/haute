import type { SimpleEdge, SimpleNode } from "../editors"

type ExploreCacheIdentityInput = {
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

  const { overview: _overview, pivots: _pivots, charts: _charts, ...dataConfig } = config
  void _overview
  void _pivots
  void _charts
  return dataConfig
}

function upstreamNodeIds(nodeId: string, edges: SimpleEdge[]): Set<string> {
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
  }

  return ids
}

export function buildExploreCacheIdentity({
  node,
  allNodes,
  edges,
  submodels,
  preamble,
}: ExploreCacheIdentityInput): Record<string, unknown> {
  const nodeIds = upstreamNodeIds(node.id, edges)
  const nodesById = new Map(allNodes.map((graphNode) => [graphNode.id, graphNode]))
  nodesById.set(node.id, node)

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
