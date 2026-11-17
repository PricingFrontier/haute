/**
 * Edges in the order every identity of a computed answer signs them, as the server's do
 * (`haute._cache.input_ordered_edges`): by the node each feeds, and within one node in the
 * graph's own order, which is the order the executor gives that node its inputs. The same
 * edges connected the other way round are another computation, so an identity never sorts
 * a node's incoming edges; edges into different nodes keep their identity in any order.
 */
export function inputOrderedEdges<T extends { target: string }>(edges: readonly T[]): T[] {
  return [...edges].sort((a, b) => (a.target < b.target ? -1 : a.target > b.target ? 1 : 0))
}
