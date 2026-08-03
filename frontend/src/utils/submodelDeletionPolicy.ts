import type { Edge, Node } from "@xyflow/react"
import { isProtectedSubmodelNodeData, nodeData } from "../types/node"
import { NODE_TYPES } from "./nodeTypes"

/**
 * Stamp React Flow's `deletable` flag so native deletion mirrors the shared
 * owner-aware rule: definition owners (and malformed occurrences) are never
 * natively deletable, instance copies are. Non-submodel nodes pass through
 * untouched, and unchanged nodes keep their object identity for memoization.
 */
export function withNativeDeletePolicy(nodes: Node[]): Node[] {
  return nodes.map((node) => {
    const data = nodeData(node)
    if (data.nodeType !== NODE_TYPES.SUBMODEL) return node
    const deletable = !isProtectedSubmodelNodeData(data)
    return node.deletable === deletable ? node : { ...node, deletable }
  })
}

export interface OwnerSafeDeletion {
  nodes: Node[]
  edges: Edge[]
  /** Owners removed from the doomed set; non-empty means the UI should explain. */
  sparedOwnerIds: string[]
}

/**
 * Filter a React Flow deletion set so protected owners — and the edges
 * incident to them — survive, while the rest of the selection still deletes.
 */
export function spareProtectedOwners(
  doomedNodes: Node[],
  doomedEdges: Edge[],
): OwnerSafeDeletion {
  const sparedOwnerIds = doomedNodes
    .filter((node) => isProtectedSubmodelNodeData(nodeData(node)))
    .map((node) => node.id)
  if (sparedOwnerIds.length === 0) {
    return { nodes: doomedNodes, edges: doomedEdges, sparedOwnerIds }
  }
  const spared = new Set(sparedOwnerIds)
  return {
    nodes: doomedNodes.filter((node) => !spared.has(node.id)),
    edges: doomedEdges.filter(
      (edge) => !spared.has(edge.source) && !spared.has(edge.target),
    ),
    sparedOwnerIds,
  }
}
