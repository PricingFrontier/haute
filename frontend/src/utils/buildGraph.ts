import type { Node, Edge } from "@xyflow/react"
import type { SimpleNode, SimpleEdge } from "../panels/editors/_shared"
import type { PipelineEdge } from "../types/node"
import { toCanonicalGraphPayload } from "./graphSnapshot"
import { isPlainObject } from "../types/guards"

/** Build the graph payload expected by backend API calls. */
export function buildGraph(
  allNodes: SimpleNode[],
  edges: SimpleEdge[],
  submodels?: Record<string, unknown>,
  preamble?: string,
) {
  return toCanonicalGraphPayload({
    nodes: allNodes.map((n) => ({
      id: n.id,
      type: n.type || n.data.nodeType,
      data: n.data,
      position: { x: 0, y: 0 },
    })),
    edges: edges as PipelineEdge[],
    submodels,
    preamble,
  })
}

const VOLATILE_NODE_DATA_KEYS = new Set([
  "_columns",
  "_availableColumns",
  "_schemaWarnings",
  "_columnsSource",
  "_columnsStructuralVersion",
  "_status",
  "_traceActive",
  "_traceDimmed",
  "_hoverDimmed",
  "_traceValue",
  "_traceMotionDisabled",
  "_diffStatus",
])

function nodeForRequestIdentity(node: unknown): unknown {
  if (!isPlainObject(node) || !isPlainObject(node.data)) return node
  return {
    ...node,
    data: Object.fromEntries(
      Object.entries(node.data).filter(([key]) => !VOLATILE_NODE_DATA_KEYS.has(key)),
    ),
  }
}

function submodelsForRequestIdentity(submodels: unknown): unknown {
  if (!isPlainObject(submodels)) return submodels
  return Object.fromEntries(
    Object.entries(submodels).map(([name, definition]) => {
      if (!isPlainObject(definition) || !isPlainObject(definition.graph)) {
        return [name, definition]
      }
      return [
        name,
        {
          ...definition,
          graph: graphObjectForRequestIdentity(definition.graph),
        },
      ]
    }),
  )
}

function graphObjectForRequestIdentity(
  graph: Record<string, unknown>,
): Record<string, unknown> {
  return {
    ...graph,
    nodes: Array.isArray(graph.nodes)
      ? graph.nodes.map(nodeForRequestIdentity)
      : graph.nodes,
    submodels: submodelsForRequestIdentity(graph.submodels),
  }
}

/**
 * Project an executable graph onto the fields that identify an API request.
 * Preview, trace, and comparison metadata is intentionally excluded so an
 * in-flight result or overwrite grant survives unrelated UI refreshes.
 */
export function graphForRequestIdentity(
  graph: ReturnType<typeof buildGraph>,
): Record<string, unknown> {
  return graphObjectForRequestIdentity(graph)
}

/** Resolve graph payload from ref objects (parentGraph takes priority). */
export function resolveGraphFromRefs(
  graphRef: React.MutableRefObject<{ nodes: Node[]; edges: Edge[] }>,
  parentGraphRef: React.MutableRefObject<{ nodes: Node[]; edges: Edge[]; submodels: Record<string, unknown> } | null>,
  submodelsRef: React.MutableRefObject<Record<string, unknown>>,
  preambleRef: React.MutableRefObject<string>,
) {
  const graph = parentGraphRef.current
    ? { nodes: parentGraphRef.current.nodes, edges: parentGraphRef.current.edges, submodels: parentGraphRef.current.submodels, preamble: preambleRef.current }
    : { nodes: graphRef.current.nodes, edges: graphRef.current.edges, submodels: submodelsRef.current, preamble: preambleRef.current }
  return toCanonicalGraphPayload({
    ...graph,
    edges: graph.edges as PipelineEdge[],
  })
}
