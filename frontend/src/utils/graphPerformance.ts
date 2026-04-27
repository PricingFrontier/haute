export const GRAPH_EFFECTS_LITE_GRAPH_SIZE_LIMIT = 1000

export function shouldUseLiteGraphEffects(nodeCount: number, edgeCount: number): boolean {
  return nodeCount + edgeCount >= GRAPH_EFFECTS_LITE_GRAPH_SIZE_LIMIT
}
