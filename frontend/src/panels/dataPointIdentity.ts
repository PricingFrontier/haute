import { authoredPolarsConfig } from "../utils/polarsStepInputs"
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
  // Steps are authored; their generated code and validation message are caches
  // the render endpoint refreshes, so on this node or any stepped ancestor they
  // must not change what the report was computed from.
  const authored = authoredPolarsConfig(config)
  if (!isExploreNode(node)) return authored

  const {
    overview: _overview,
    pivot_formulas: _pivotFormulas,
    pivots: _pivots,
    charts: _charts,
    ...dataConfig
  } = authored
  void _overview
  void _pivotFormulas
  void _pivots
  void _charts
  return dataConfig
}

function sortedColumns(columns: unknown[]): string[] {
  return Array.from(
    new Set(columns.filter((column): column is string => typeof column === "string" && column !== "")),
  ).sort()
}

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    : []
}

/**
 * A Banding or Rating Step node reads its input rather than its own output, so
 * its own configuration changes the point only through the columns it demands,
 * as the backend derives them (`_data_points._banding_demand` and
 * `_rating_step_demand`). Its rules and table entries do not, and editing them
 * must not re-ask about the data it reads. Null for every other node.
 */
function consumerDemand(node: SimpleNode): Record<string, unknown> | null {
  const config = node.data.config ?? {}
  if (node.data.nodeType === "banding") {
    return { demand: sortedColumns(records(config.factors).map((factor) => factor.column)) }
  }
  if (node.data.nodeType === "ratingStep") {
    const factors = records(config.tables).flatMap((table) =>
      Array.isArray(table.factors) ? (table.factors as unknown[]) : [],
    )
    return { demand: sortedColumns(factors) }
  }
  return null
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
 * (which sets its column demand; for Banding and Rating Step, only that
 * demand), the submodels, and the preamble.
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
      config:
        (graphNode.id === node.id ? consumerDemand(graphNode) : null) ?? dataAffectingConfig(graphNode),
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
