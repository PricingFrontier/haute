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
import type { PreparedGraph } from "../api/types"
import type { SimpleNode } from "../panels/editors/_shared"
import useDocumentStatusStore from "../stores/useDocumentStatusStore"
import useGraphStore from "../stores/useGraphStore"
import useSettingsStore from "../stores/useSettingsStore"
import { isSubmodelDefinition, nodeData, type SubmodelPortData } from "../types/node"
import { documentGraphPayload } from "./buildGraph"
import { childAddress, nodeFrame, rootAddress, type FrameTarget, type NodeAddress } from "./nodeAddress"
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

/**
 * The address an answer's node id names: a child of the open occurrence by its
 * runtime id, or one of the pipeline's own nodes by its id.
 */
export function addressOfRuntimeId(id: string): NodeAddress {
  const focus = useGraphStore.getState().focus
  if (focus) {
    const prefix = qualifiedRuntimeNodeId(focus.instanceId, "")
    if (id.startsWith(prefix)) {
      const child = useGraphStore.getState().nodes.find((node) => encodeRuntimeIdPart(node.id) === id.slice(prefix.length))
      if (child) return childAddress(focus.instanceId, child.id)
    }
  }
  return rootAddress(id)
}

/** The node on the canvas, or of the pipeline's own, an answer's node id names. */
function nodeNamedByRuntimeId(id: string): SimpleNode | null {
  const address = addressOfRuntimeId(id)
  const { focus, nodes } = useGraphStore.getState()
  const pool = address.occurrence === null ? (focus ? focus.rootNodes : nodes) : nodes
  return (pool.find((node) => node.id === address.node) as unknown as SimpleNode | undefined) ?? null
}

/** The nodes an answer names by id, without preparing a request. */
export function answerNodeNamer(): (id: string) => SimpleNode | null {
  return nodeNamedByRuntimeId
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
    if (port === undefined) {
      throw new Error(handle
        ? `The ${output ? "Output" : "Input"} card has no port ${handle}.`
        : `Choose one of the ${output ? "Output" : "Input"} card's ports to read.`)
    }
    return output
      ? { kind: "occurrenceOutput", occurrence: focus.instanceId, port }
      : { kind: "occurrenceInput", occurrence: focus.instanceId, port }
  }
  return nodeFrame(childAddress(focus.instanceId, nodeId), handle ?? null)
}

/** A request about `target`: the whole pipeline, and the id and port the server runs it under. */
export function prepareNodeRequest(
  scope: RequestScope | null | undefined,
  target: FrameTarget,
): PreparedNodeRequest {
  const pipeline = preparePipelineRequest(scope)
  return { ...pipeline, target, ...runtimeTarget(target), nodeNamed: nodeNamedByRuntimeId }
}
