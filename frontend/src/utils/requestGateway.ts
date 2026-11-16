/**
 * The one way a request that runs the pipeline is built (specs/frontend-shared/low-level.md,
 * "Request gateway"): a node's preview and frames, a trace, an estimate, Train and Solve, a
 * Data Output's destination and write, a profile, a whole-data answer, an Explore pivot, the
 * Workbench's sample, the cached-data list.
 *
 * A request runs the document of a `RequestScope` and names what it reads by address. It is
 * prepared once: the whole pipeline (also while a submodel is open), the document's identity,
 * its semantic generation, the target and the data source, captured together. Only the
 * gateway makes the `PreparedGraph` the client's run functions take. Until the server
 * resolves addresses, the gateway's adapter alone turns an address into the runtime id a
 * request sends and reads the ids an answer names back.
 */
import type { Node } from "@xyflow/react"
import type { PreparedGraph } from "../api/types"
import type { SimpleNode } from "../panels/editors/_shared"
import useDocumentStatusStore from "../stores/useDocumentStatusStore"
import useGraphStore from "../stores/useGraphStore"
import useSettingsStore from "../stores/useSettingsStore"
import { isSubmodelDefinition, nodeData, type SubmodelPortData } from "../types/node"
import { documentGraphPayload } from "./buildGraph"
import { addressKey, childAddress, nodeFrame, rootAddress, type FrameTarget, type NodeAddress } from "./nodeAddress"
import { encodeRuntimeIdPart, qualifiedRuntimeNodeId } from "./submodelRuntimeTarget"
import { NODE_TYPES } from "./nodeTypes"

/** A request prepared where there is no document to run (the comparison view's editors). */
export class RequestScopeError extends Error {
  constructor() {
    super("This view shows a saved version of the pipeline: it runs nothing.")
    this.name = "RequestScopeError"
  }
}

/** The document a request runs. */
export interface RequestScope {
  readonly kind: "document"
}

/** The pipeline open in the editor, as the graph store holds it. */
export const documentRequestScope: RequestScope = Object.freeze({ kind: "document" as const })

/** A request about the whole pipeline, captured once. */
export interface PreparedPipelineRequest {
  readonly graph: PreparedGraph
  /** The data source the request reads. */
  readonly source: string
  /** Which document it runs: its file and execution generation. */
  readonly document: { readonly sourceFile: string; readonly executionGeneration: number }
  /** The document's semantic generation when the request was prepared. */
  readonly semanticGeneration: number
}

/** A request about one frame of the pipeline, captured once. */
export interface PreparedNodeRequest extends PreparedPipelineRequest {
  readonly target: FrameTarget
  /** The node the request runs, by the id the server runs it under. */
  readonly nodeId: string
  /** The port of `nodeId` to read, when not its one output. */
  readonly portLabel?: string
  /** A node an answer names, by the id the request sent: one on the canvas, or one of the pipeline's own. */
  readonly nodeNamed: (id: string) => SimpleNode | null
}

function requireScope(scope: RequestScope | null | undefined): RequestScope {
  if (!scope) throw new RequestScopeError()
  return scope
}

function prepareGraph(): PreparedGraph {
  return documentGraphPayload() as PreparedGraph
}

/** The whole pipeline, its identity, generation and source. */
export function preparePipelineRequest(scope: RequestScope | null | undefined): PreparedPipelineRequest {
  requireScope(scope)
  const status = useDocumentStatusStore.getState()
  return {
    graph: prepareGraph(),
    source: useSettingsStore.getState().activeSource,
    document: { sourceFile: status.sourceFile, executionGeneration: status.executionGeneration },
    semanticGeneration: useGraphStore.getState().semanticGeneration,
  }
}

/** The pipeline's own nodes and edges, also while a submodel is open. */
function rootGraph() {
  const { focus, nodes, edges } = useGraphStore.getState()
  return focus ? { nodes: focus.rootNodes, edges: focus.rootEdges } : { nodes, edges }
}

/** The runtime id and port the server runs `target` under. */
function runtimeTarget(target: FrameTarget): { nodeId: string; portLabel?: string } {
  if (target.kind === "node") {
    const { occurrence, node } = target.address
    return {
      nodeId: occurrence === null ? node : qualifiedRuntimeNodeId(occurrence, node),
      portLabel: target.port ?? undefined,
    }
  }
  if (target.kind === "occurrenceInput") {
    // What feeds the input: the parent edge into the occurrence at that port.
    const feed = rootGraph().edges.find((edge) =>
      edge.target === target.occurrence && edge.targetHandle === `in__${target.port}`)
    if (!feed) throw new Error(`Input ${target.port} of ${target.occurrence} has nothing connected to it.`)
    return { nodeId: feed.source, portLabel: feed.sourceHandle ?? undefined }
  }
  const occurrence = rootGraph().nodes.find((node) => node.id === target.occurrence)
  const config = occurrence ? nodeData(occurrence).config as { definition_id?: unknown } : undefined
  const definitionId = typeof config?.definition_id === "string" ? config.definition_id : ""
  const definition = useGraphStore.getState().submodels[definitionId]
  const port = isSubmodelDefinition(definition, definitionId)
    ? definition.outputPorts.find((candidate) => candidate.name === target.port)
    : undefined
  if (!port) throw new Error(`${target.occurrence} has no output ${target.port}.`)
  return {
    nodeId: qualifiedRuntimeNodeId(target.occurrence, port.source.nodeId),
    portLabel: port.source.handleId ?? undefined,
  }
}

/** The runtime id the server runs and names `address` under. */
export function runtimeNodeId(address: NodeAddress): string {
  return address.occurrence === null ? address.node : qualifiedRuntimeNodeId(address.occurrence, address.node)
}

/** The view a request was prepared in: the open occurrence and its canvas, or the pipeline's own. */
interface ViewAtPreparation {
  readonly occurrence: string | null
  readonly canvas: readonly Node[]
  readonly rootNodes: readonly Node[]
}

function viewNow(): ViewAtPreparation {
  const { focus, nodes } = useGraphStore.getState()
  return { occurrence: focus?.instanceId ?? null, canvas: nodes, rootNodes: focus ? focus.rootNodes : nodes }
}

function addressInView(view: ViewAtPreparation, id: string): NodeAddress {
  if (view.occurrence !== null) {
    const prefix = qualifiedRuntimeNodeId(view.occurrence, "")
    if (id.startsWith(prefix)) {
      const child = view.canvas.find((node) => encodeRuntimeIdPart(node.id) === id.slice(prefix.length))
      if (child) return childAddress(view.occurrence, child.id)
    }
  }
  return rootAddress(id)
}

/**
 * The address an answer's node id names: a child of the open occurrence by its
 * runtime id, or one of the pipeline's own nodes by its id.
 */
export function addressOfRuntimeId(id: string): NodeAddress {
  return addressInView(viewNow(), id)
}

/** The node an answer's id names, in the view a request was prepared in. */
function namerFor(view: ViewAtPreparation): (id: string) => SimpleNode | null {
  return (id) => {
    const address = addressInView(view, id)
    const pool = address.occurrence === null ? view.rootNodes : view.canvas
    return (pool.find((node) => node.id === address.node) as unknown as SimpleNode | undefined) ?? null
  }
}

/** The nodes an answer names by id, in the view now, without preparing a request. */
export function answerNodeNamer(): (id: string) => SimpleNode | null {
  return namerFor(viewNow())
}

/**
 * The canvas id of each node an answer names by runtime id, in the view now (taken when
 * a request is sent): a child of the open occurrence by its own id, one of the pipeline's
 * own at the root; null for a node the canvas does not show.
 */
export function canvasIdNamer(): (runtimeId: string) => string | null {
  const view = viewNow()
  return (id) => {
    const address = addressInView(view, id)
    return address.occurrence === view.occurrence ? address.node : null
  }
}

/**
 * The key a canvas node's results are kept under (`addressKey`): its id on the main
 * pipeline, its address's key inside an open submodel, so two occurrences of one
 * definition keep their own. Taken when a request is prepared or a node selected,
 * never from an id an answer names.
 */
export function resultKey(nodeId: string): string {
  return addressKey(canvasNodeAddress(nodeId))
}

/**
 * The node on the canvas by its address: one of the pipeline's own at the root, a
 * child (or a boundary card) of the open occurrence inside one.
 */
export function canvasNodeAddress(nodeId: string): NodeAddress {
  const focus = useGraphStore.getState().focus
  return focus ? childAddress(focus.instanceId, nodeId) : rootAddress(nodeId)
}

/**
 * The frame a canvas node shows at `handle`: one of the pipeline's own nodes at the
 * root, a child of the open occurrence inside one, and the open occurrence's input
 * port for the Input card (the card's one port when no handle is given).
 */
export function canvasTarget(nodeId: string, handle?: string | null): FrameTarget {
  const { focus, nodes } = useGraphStore.getState()
  if (!focus) return nodeFrame(rootAddress(nodeId), handle ?? null)
  const node = nodes.find((candidate) => candidate.id === nodeId)
  const data = node ? nodeData(node) : undefined
  if (data?.nodeType === NODE_TYPES.SUBMODEL_PORT) {
    const card = data as unknown as Partial<SubmodelPortData>
    const output = card.portDirection === "output"
    // The Input card lists its ports; the Output card's are the definition's outputs.
    const definition = useGraphStore.getState().submodels[focus.definitionId]
    const names = output
      ? (isSubmodelDefinition(definition, focus.definitionId) ? definition.outputPorts.map((port) => port.name) : [])
      : (card.ports ?? []).map((port) => port.id)
    const port = handle ? names.find((name) => name === handle) : names.length === 1 ? names[0] : undefined
    const bound = port !== undefined && (output || rootGraph().edges.some((edge) =>
      edge.target === focus.instanceId && edge.targetHandle === `in__${port}`))
    // A card whose port is not named, or an input nothing feeds yet, is read as the card itself.
    if (port === undefined || !bound) return nodeFrame(childAddress(focus.instanceId, nodeId), handle ?? null)
    return output
      ? { kind: "occurrenceOutput", occurrence: focus.instanceId, port }
      : { kind: "occurrenceInput", occurrence: focus.instanceId, port }
  }
  return nodeFrame(childAddress(focus.instanceId, nodeId), handle ?? null)
}

/**
 * Every key the results of a node about to be deleted from the canvas are kept under:
 * on the main pipeline, the node's own and, for an occurrence of a submodel, every one
 * of its children's; inside an open submodel, the child's in every occurrence of the
 * definition, which all lose it. Taken before the node is deleted.
 */
export function resultKeysOf(nodeId: string): string[] {
  const { focus, nodes, submodels } = useGraphStore.getState()
  const definitionOf = (node: Node | undefined) => {
    const config = node ? nodeData(node).config as { definition_id?: unknown } : undefined
    return typeof config?.definition_id === "string" ? config.definition_id : null
  }
  if (focus) {
    const occurrences = focus.rootNodes.filter((node) => definitionOf(node) === focus.definitionId)
    return occurrences.map((occurrence) => addressKey(childAddress(occurrence.id, nodeId)))
  }
  const keys = [addressKey(rootAddress(nodeId))]
  const definitionId = definitionOf(nodes.find((node) => node.id === nodeId))
  const definition = definitionId ? submodels[definitionId] : undefined
  if (definitionId && isSubmodelDefinition(definition, definitionId)) {
    for (const child of definition.graph.nodes) keys.push(addressKey(childAddress(nodeId, child.id)))
  }
  return keys
}

/** A request about a canvas node, with the key its results are kept under. */
export interface PreparedCanvasRequest extends PreparedNodeRequest {
  readonly resultKey: string
}

/** A request about a canvas node at `handle` (`canvasTarget`), and its results' key. */
export function prepareCanvasRequest(
  scope: RequestScope | null | undefined,
  nodeId: string,
  handle?: string | null,
): PreparedCanvasRequest {
  return { ...prepareNodeRequest(scope, canvasTarget(nodeId, handle)), resultKey: resultKey(nodeId) }
}

/** A request about `target`: the whole pipeline, and the id and port the server runs it under. */
export function prepareNodeRequest(
  scope: RequestScope | null | undefined,
  target: FrameTarget,
): PreparedNodeRequest {
  const pipeline = preparePipelineRequest(scope)
  return { ...pipeline, target, ...runtimeTarget(target), nodeNamed: namerFor(viewNow()) }
}
