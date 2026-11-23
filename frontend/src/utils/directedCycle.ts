type Endpoints = { source: string; target: string }

/** What the canvas says of a gesture whose edge would close a loop, after "Connection rejected: " or "Edge join rejected: ". */
export const CLOSES_A_LOOP = "that connection would create a cycle"

/**
 * Whether `edges` close a loop among `nodes`: a node that reaches itself. An edge whose
 * source is not among `nodes` is left out. The one cycle check of the canvas, shared by
 * the connection rule (`validatePipelineConnection`) and Edge Join insertion, so a loop is
 * refused before it reaches the graph whichever gesture would make it (FEG-03).
 */
export function hasDirectedCycle(nodes: readonly { id: string }[], edges: readonly Endpoints[]): boolean {
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
