import type { Node } from "@xyflow/react"
import { isProtectedSubmodelNodeData, nodeData } from "../types/node"
import { NODE_TYPES } from "./nodeTypes"

/**
 * Stamp React Flow's `deletable` flag so native deletion enforces the shared
 * owner-aware rule: definition owners (and malformed occurrences) are never
 * natively deletable, instance copies are. React Flow excludes non-deletable
 * nodes from any deletion set before acting and preserves the edges of nodes
 * it does not delete, so this flag is the sole native gate; an explicitly
 * selected boundary edge remains deletable, which is legitimate unbinding.
 * Non-submodel nodes pass through untouched, and unchanged nodes keep their
 * object identity for memoization.
 */
export function withNativeDeletePolicy(nodes: Node[]): Node[] {
  return nodes.map((node) => {
    const data = nodeData(node)
    if (data.nodeType !== NODE_TYPES.SUBMODEL) return node
    const deletable = !isProtectedSubmodelNodeData(data)
    return node.deletable === deletable ? node : { ...node, deletable }
  })
}
