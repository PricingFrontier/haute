/**
 * What a request about one node of the canvas sends: the graph and the node's id in it.
 *
 * Inside a drilled submodel the canvas holds that submodel's view: its nodes and its
 * Input and Output cards, beside every submodel definition, the open one included. No
 * request can run that graph, whose nodes stand in it twice, so a request about one of
 * its nodes sends the whole pipeline and the node's runtime id in the open occurrence,
 * as Preview does. App registers where the whole pipeline and the open occurrence are
 * read from; outside a submodel, or with nothing registered, a request sends the graph
 * the caller holds.
 */
import type { SimpleEdge, SimpleNode } from "../panels/editors/_shared"
import { buildGraph } from "./buildGraph"
import { encodeRuntimeIdPart, qualifiedRuntimeNodeId, type DrilledOccurrenceIdentity } from "./submodelRuntimeTarget"

type GraphPayload = ReturnType<typeof buildGraph>

/** The open submodel: the whole pipeline as the editor holds it now, and the occurrence on screen. */
export interface DrilledRequestScope {
  graph: GraphPayload
  occurrence: DrilledOccurrenceIdentity
}

let resolveScope: (() => DrilledRequestScope | null) | null = null

/** Register where the open submodel is read from, or null to forget it. */
export function setDrilledRequestScope(resolve: (() => DrilledRequestScope | null) | null): void {
  resolveScope = resolve
}

export interface NodeRequestTarget {
  graph: GraphPayload
  /** The node's id in `graph`. */
  nodeId: string
  /**
   * A node an answer names, by its id in `graph`: a node of the pipeline outside the
   * open submodel, or one on screen, named by its runtime id.
   */
  nodeNamed: (id: string) => SimpleNode | null
}

/** The request target for *nodeId*, a node of the graph the caller holds. */
export function nodeRequestTarget(
  allNodes: SimpleNode[],
  edges: SimpleEdge[],
  submodels: Record<string, unknown> | undefined,
  nodeId: string,
): NodeRequestTarget {
  const onScreen = (id: string) => allNodes.find((node) => node.id === id) ?? null
  const scope = resolveScope?.() ?? null
  if (!scope) {
    return { graph: buildGraph(allNodes, edges, submodels), nodeId, nodeNamed: onScreen }
  }
  const prefix = qualifiedRuntimeNodeId(scope.occurrence.instanceId, "")
  const rootNodes = scope.graph.nodes as unknown as SimpleNode[]
  return {
    graph: scope.graph,
    nodeId: qualifiedRuntimeNodeId(scope.occurrence.instanceId, nodeId),
    nodeNamed: (id) => {
      if (id.startsWith(prefix)) {
        const local = allNodes.find((node) => encodeRuntimeIdPart(node.id) === id.slice(prefix.length))
        if (local) return local
      }
      return rootNodes.find((node) => node.id === id) ?? null
    },
  }
}
