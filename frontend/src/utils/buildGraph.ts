import type { GraphPayload } from "../api/types"
import { documentRequestGraph } from "./documentRequest"
import { isPlainObject } from "../types/guards"
import useGraphStore, { documentGraph } from "../stores/useGraphStore"
import { constantsPayload } from "./globalConstants"

/**
 * The global constants every execution graph carries, read from the graph
 * store when the graph is built, so no caller can leave them out.
 */
function storeGlobalConstants() {
  const { globalConstants, globalConstantsError } = useGraphStore.getState()
  return {
    global_constants: globalConstantsError === null ? constantsPayload(globalConstants) : [],
    global_constants_error: globalConstantsError,
  }
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
  "_traceFocused",
  "_changeFocused",
  "_traceValue",
  "_traceMotionDisabled",
  "_diffStatus",
])

function nodeForRequestIdentity(node: unknown): unknown {
  if (!isPlainObject(node) || !isPlainObject(node.data)) return node
  // Where a node sits on the canvas runs nothing, so a move leaves the identity as it is.
  const { position: _position, ...placed } = node
  return {
    ...placed,
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
  graph: GraphPayload,
): Record<string, unknown> {
  return graphObjectForRequestIdentity(graph as unknown as Record<string, unknown>)
}

/**
 * The document the graph store holds, as a request payload: the pipeline's own
 * nodes and edges (also while a submodel is open), every definition, the
 * preamble and the global constants, each part nobody edited since the saved
 * baseline sent with its saved form (`documentRequest.documentRequestGraph`). The
 * store applies every edit before it returns, so a request built from this right
 * after an edit carries it.
 */
export function documentGraphPayload() {
  const state = useGraphStore.getState()
  const { nodes, edges } = documentGraph()
  return documentRequestGraph({
    nodes,
    edges,
    submodels: state.submodels,
    preamble: state.preamble,
    ...storeGlobalConstants(),
  }, state.lastSavedSnapshot)
}
