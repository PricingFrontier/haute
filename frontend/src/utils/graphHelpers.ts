import type { Node, Edge } from "@xyflow/react"

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
