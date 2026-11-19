/**
 * The one way a request that runs the pipeline is built (specs/frontend-shared/low-level.md,
 * "Request gateway"): a node's preview and frames, a trace, an estimate, Train and Solve, a
 * Data Output's destination and write, a profile, a whole-data answer, an Explore pivot, the
 * Workbench's sample, the cached-data list.
 *
 * A request runs the document of a `RequestScope` and names what it reads by address. It is
 * prepared once: the whole pipeline (also while a submodel is open), the document's identity,
 * its semantic generation, the target and the data source, captured together. Only the
 * gateway makes the `PreparedGraph` the client's run functions take. A request sends its
 * target by address (`frameTargetPayload`, and `node` for an operation on a node) and finds
 * the nodes an answer addresses with `nodeAt`. The browser builds no runtime id.
 */
import type { Node } from "@xyflow/react"
import type { PreparedGraph } from "../api/types"
import type {
  NodeAddressPayload,
  NodeFrameTargetPayload,
  OccurrenceInputTargetPayload,
  OccurrenceOutputTargetPayload,
} from "../generated/api-contracts.generated"
import type { SimpleNode } from "../panels/editors/_shared"
import useDocumentStatusStore from "../stores/useDocumentStatusStore"
import useGraphStore from "../stores/useGraphStore"
import useSettingsStore from "../stores/useSettingsStore"
import type { Part } from "./answers"
import { isSubmodelDefinition, nodeData, type SubmodelPortData } from "../types/node"
import { documentGraphPayload } from "./buildGraph"
import { addressKey, childAddress, nodeFrame, rootAddress, type FrameTarget, type NodeAddress } from "./nodeAddress"
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

/** A frame target as a request sends it (snake_case, every field present). */
export type FrameTargetPayload =
  | NodeFrameTargetPayload
  | OccurrenceInputTargetPayload
  | OccurrenceOutputTargetPayload

/** A node address as a request sends it. */
export function nodeAddressPayload(address: NodeAddress): NodeAddressPayload {
  return { occurrence: address.occurrence, node_id: address.node }
}

/**
 * The one conversion of a frame target to the wire's shape. `ports` names several ports of a
 * node read in one run (the Workbench's tables), in place of the target's one port.
 */
export function frameTargetPayload(target: FrameTarget, ports?: readonly string[] | null): FrameTargetPayload {
  if (target.kind === "node") {
    return {
      kind: "node",
      ...nodeAddressPayload(target.address),
      port_label: ports ? null : target.port,
      port_labels: ports ? [...ports] : null,
    }
  }
  return target.kind === "occurrenceInput"
    ? { kind: "occurrence_input", occurrence: target.occurrence, port: target.port }
    : { kind: "occurrence_output", occurrence: target.occurrence, port: target.port }
}

/** A request about one frame of the pipeline, captured once. */
export interface PreparedNodeRequest extends PreparedPipelineRequest {
  readonly target: FrameTarget
  /** The target as the request sends it. */
  readonly wireTarget: FrameTargetPayload
  /**
   * The node the request runs, by address, as an operation on a node sends it (`target`).
   * A port of a submodel card is no node: reading it there throws.
   */
  readonly node: NodeAddressPayload
  /** A node an answer addresses, in the view the request was prepared in. */
  readonly nodeAt: (address: Pick<NodeAddressPayload, "occurrence" | "node_id">) => SimpleNode | null
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

/** The view a request was prepared in: the open occurrence and its canvas, or the pipeline's own. */
interface ViewAtPreparation {
  readonly occurrence: string | null
  readonly canvas: readonly Node[]
  readonly rootNodes: readonly Node[]
  readonly submodels: Readonly<Record<string, unknown>>
}

function viewNow(): ViewAtPreparation {
  const { focus, nodes, submodels } = useGraphStore.getState()
  return { occurrence: focus?.instanceId ?? null, canvas: nodes, rootNodes: focus ? focus.rootNodes : nodes, submodels }
}

/**
 * The node at an address in the view a request was prepared in: one of the pipeline's own,
 * a child of the open occurrence on the canvas, or a child of another occurrence in its
 * definition.
 */
function nodeAtFor(view: ViewAtPreparation): (address: Pick<NodeAddressPayload, "occurrence" | "node_id">) => SimpleNode | null {
  return ({ occurrence, node_id }) => {
    const pool = occurrence === null
      ? view.rootNodes
      : occurrence === view.occurrence
        ? view.canvas
        : definitionNodes(view, occurrence)
    return (pool.find((node) => node.id === node_id) as unknown as SimpleNode | undefined) ?? null
  }
}

/**
 * Whether the node at *address* is in the document as it stands: one of the pipeline's own,
 * a child of the open occurrence, or a child of another occurrence in its definition. What a
 * late answer about a node is checked against (`utils/answers.ts`, `node`).
 */
export function nodeExists(address: NodeAddress): boolean {
  return nodeAtFor(viewNow())({ occurrence: address.occurrence, node_id: address.node }) !== null
}

/** The node at *address*, as an answer's part (`utils/answers.ts`): it holds while the node is there. */
export function nodePart(address: NodeAddress): Part {
  return { holds: () => nodeExists(address), subscribe: (listener) => useGraphStore.subscribe(() => listener()) }
}

function definitionNodes(view: ViewAtPreparation, occurrence: string): readonly Node[] {
  const card = view.rootNodes.find((node) => node.id === occurrence)
  const config = card ? nodeData(card).config as { definition_id?: unknown } : undefined
  const definitionId = typeof config?.definition_id === "string" ? config.definition_id : ""
  const definition = view.submodels[definitionId]
  return isSubmodelDefinition(definition, definitionId) ? definition.graph.nodes as readonly Node[] : []
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
  const node = nodes.find((candidate) => candidate.id === nodeId)
  const data = node ? nodeData(node) : undefined
  if (!focus) {
    // A collapsed occurrence's frame at an output handle is that output port's.
    return data?.nodeType === NODE_TYPES.SUBMODEL && handle?.startsWith("out__")
      ? { kind: "occurrenceOutput", occurrence: nodeId, port: handle.slice("out__".length) }
      : nodeFrame(rootAddress(nodeId), handle ?? null)
  }
  if (data?.nodeType === NODE_TYPES.SUBMODEL_PORT) {
    const card = data as unknown as Partial<SubmodelPortData>
    const output = card.portDirection === "output"
    // The Input card lists its ports; the Output card's are the definition's outputs.
    const definition = useGraphStore.getState().submodels[focus.definitionId]
    const names = output
      ? (isSubmodelDefinition(definition, focus.definitionId) ? definition.outputPorts.map((port) => port.name) : [])
      : (card.ports ?? []).map((port) => port.id)
    const port = handle ? names.find((name) => name === handle) : names.length === 1 ? names[0] : undefined
    // A card whose port is not named is read as the card itself. A named input nothing feeds
    // is still that input, which the server refuses by name.
    if (port === undefined) return nodeFrame(childAddress(focus.instanceId, nodeId), handle ?? null)
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
  return preparedNodeRequest(scope, canvasTarget(nodeId, handle), { resultKey: resultKey(nodeId) })
}

/** A request about `target`: the whole pipeline, and the target as the request sends it. */
export function prepareNodeRequest(
  scope: RequestScope | null | undefined,
  target: FrameTarget,
): PreparedNodeRequest {
  return preparedNodeRequest(scope, target, {})
}

function preparedNodeRequest<Extra extends object>(
  scope: RequestScope | null | undefined,
  target: FrameTarget,
  extra: Extra,
): PreparedNodeRequest & Extra {
  const pipeline = preparePipelineRequest(scope)
  const view = viewNow()
  return {
    ...pipeline,
    ...extra,
    target,
    wireTarget: frameTargetPayload(target),
    get node() {
      if (target.kind !== "node") throw new Error(`A ${target.kind === "occurrenceInput" ? "submodel input" : "submodel output"} is not a node.`)
      return nodeAddressPayload(target.address)
    },
    nodeAt: nodeAtFor(view),
  }
}
