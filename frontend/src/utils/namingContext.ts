/**
 * The document's naming context for the editor identity request: the whole
 * graph as save would receive it, with its preserved blocks. Inside a drilled
 * submodel the live child graph replaces only its definition's nodes and
 * edges; the definition keeps its own preamble and preserved blocks.
 */

import type { Edge, Node } from "@xyflow/react"

import type { EditorIdentityBatchRequest } from "../api/types"
import type { PipelineEdge } from "../types/node"
import { toCanonicalGraphPayload } from "./graphSnapshot"
import { NODE_TYPES } from "./nodeTypes"

type NamingGraph = NonNullable<EditorIdentityBatchRequest["graph"]>

export function namingContextGraph({
  graph,
  preservedBlocks,
  drilledDefinition,
  liveNodes,
  liveEdges,
}: {
  /** The canonical root graph, with the submodel registry. */
  graph: ReturnType<typeof toCanonicalGraphPayload>
  preservedBlocks: readonly string[]
  /** The definition a drilled view edits, or null at the root. */
  drilledDefinition: string | null
  /** The drilled view's nodes and edges (its port nodes are left out). */
  liveNodes: readonly Node[]
  liveEdges: readonly Edge[]
}): NamingGraph {
  const definition = drilledDefinition === null ? undefined : graph.submodels?.[drilledDefinition]
  if (drilledDefinition === null || !definition || typeof definition !== "object") {
    return { ...graph, preserved_blocks: [...preservedBlocks] }
  }
  const live = toCanonicalGraphPayload({
    nodes: liveNodes.filter((node) => node.type !== NODE_TYPES.SUBMODEL_PORT),
    edges: liveEdges as PipelineEdge[],
  })
  const definitionGraph = (definition as { graph: Record<string, unknown> }).graph
  return {
    ...graph,
    submodels: {
      ...graph.submodels,
      [drilledDefinition]: {
        ...definition,
        graph: { ...definitionGraph, nodes: live.nodes, edges: live.edges },
      },
    },
    preserved_blocks: [...preservedBlocks],
  }
}
