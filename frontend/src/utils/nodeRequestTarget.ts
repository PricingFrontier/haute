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
import type { SubmodelPortData } from "../types/node"
import { NODE_TYPES } from "./nodeTypes"
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
   * The port of `nodeId` to read: the caller's port, or, for the open submodel's
   * Input card, the port of the parent node feeding the card's.
   */
  portLabel?: string
  /**
   * A node an answer names, by its id in `graph`: a node of the pipeline outside the
   * open submodel, or one on screen, named by its runtime id.
   */
  nodeNamed: (id: string) => SimpleNode | null
}

/** The graph a request about the whole pipeline sends: the whole of it, even inside a submodel. */
export function pipelineRequestGraph(
  allNodes: SimpleNode[],
  edges: SimpleEdge[],
  submodels: Record<string, unknown> | undefined,
): GraphPayload {
  return resolveScope?.()?.graph ?? buildGraph(allNodes, edges, submodels)
}

/**
 * The parent edge feeding the open occurrence through *card*'s port *handle*, or its
 * one port when no handle is given; the Input card's columns are that node's
 * (`boundaryInputFrames` in usePipelineAPI reads them the same way).
 */
function inputCardFeed(card: SimpleNode, handle: string | null | undefined, occurrence: DrilledOccurrenceIdentity) {
  const data = card.data as unknown as Partial<SubmodelPortData>
  if (data.portDirection !== "input" || data.instanceId !== occurrence.instanceId) return null
  const ports = data.ports ?? []
  const port = handle ? ports.find((candidate) => candidate.id === handle) : ports.length === 1 ? ports[0] : undefined
  return port?.parentEdges.find((edge) => edge.target === occurrence.instanceId) ?? null
}

function namer(allNodes: SimpleNode[], scope: DrilledRequestScope | null): (id: string) => SimpleNode | null {
  const onScreen = (id: string) => allNodes.find((node) => node.id === id) ?? null
  if (!scope) return onScreen
  const prefix = qualifiedRuntimeNodeId(scope.occurrence.instanceId, "")
  const rootNodes = scope.graph.nodes as unknown as SimpleNode[]
  return (id) => {
    if (id.startsWith(prefix)) {
      const local = allNodes.find((node) => encodeRuntimeIdPart(node.id) === id.slice(prefix.length))
      if (local) return local
    }
    return rootNodes.find((node) => node.id === id) ?? null
  }
}

/**
 * The node an answer names by its id in the graph a request sent, as
 * `NodeRequestTarget.nodeNamed` finds it, without building that graph outside a submodel.
 */
export function requestNodeNamer(allNodes: SimpleNode[]): (id: string) => SimpleNode | null {
  return namer(allNodes, resolveScope?.() ?? null)
}

/**
 * The request target for *nodeId*, a node of the graph the caller holds, read at its
 * port *sourceHandle* when one is given.
 */
export function nodeRequestTarget(
  allNodes: SimpleNode[],
  edges: SimpleEdge[],
  submodels: Record<string, unknown> | undefined,
  nodeId: string,
  sourceHandle?: string | null,
): NodeRequestTarget {
  const scope = resolveScope?.() ?? null
  const nodeNamed = namer(allNodes, scope)
  const portLabel = sourceHandle ?? undefined
  if (!scope) {
    return { graph: buildGraph(allNodes, edges, submodels), nodeId, portLabel, nodeNamed }
  }
  const node = allNodes.find((candidate) => candidate.id === nodeId)
  const feed = node?.data.nodeType === NODE_TYPES.SUBMODEL_PORT
    ? inputCardFeed(node, sourceHandle, scope.occurrence)
    : null
  if (feed) {
    return { graph: scope.graph, nodeId: feed.source, portLabel: feed.sourceHandle ?? undefined, nodeNamed }
  }
  return { graph: scope.graph, nodeId: qualifiedRuntimeNodeId(scope.occurrence.instanceId, nodeId), portLabel, nodeNamed }
}
