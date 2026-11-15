/**
 * An open submodel as a view of the one document: what a canvas write inside
 * the submodel changes in its definition (`liftSubmodelCanvas`), and the canvas
 * a document shows for an occurrence (`projectSubmodelFocus`).
 *
 * A canvas write changes the definition only through its children and the
 * edges between them, and through the handles a boundary edge meets a child at;
 * a write that would add or remove a port, a boundary edge or a card belongs to
 * the boundary actions and is refused here, whole, with its reason. Children
 * enter the definition in their authored form: presentation fields and
 * execution annotations stay on the canvas, so a selection or a preview's
 * columns change nothing in the document.
 */
import type { Edge, Node } from "@xyflow/react"
import {
  isSubmodelDefinition,
  isSubmodelInstanceConfig,
  nodeData,
  type PipelineEdge,
  type SubmodelDefinition,
  type SubmodelEndpoint,
  type SubmodelPortData,
} from "../types/node"
import { isPlainObject } from "../types/guards"
import { canonicalJson } from "./canonicalJson"
import { authoredEdgeForm, authoredNodeForm, nodeExecutionAnnotations } from "./graphSnapshot"
import { normalizeEdges } from "./graphHelpers"
import { NODE_TYPES } from "./nodeTypes"
import { buildSubmodelViewGraph } from "./submodelViewGraph"

/** Which occurrence the canvas shows. */
export interface SubmodelFocusIdentity {
  instanceId: string
  definitionId: string
}

/** What a canvas write inside an open submodel does to the document. */
export type SubmodelCanvasLift =
  /** Nothing authored changed (a selection, a preview's columns): the write is the canvas's alone. */
  | { kind: "canvas" }
  /** The open definition as the write leaves it. */
  | { kind: "definition"; definition: SubmodelDefinition }
  /** The write cannot apply; nothing changes. */
  | { kind: "refused"; reason: string }

interface CanvasGraph {
  nodes: readonly Node[]
  edges: readonly Edge[]
}

const BOUNDARY_ACTIONS_REASON =
  "A submodel's inputs and outputs change only through its boundary: connect to or from its Input and Output cards, or remove an input from the Input card."

export function isSubmodelCard(node: Node): boolean {
  return node.type === NODE_TYPES.SUBMODEL_PORT || nodeData(node).nodeType === NODE_TYPES.SUBMODEL_PORT
}

interface BoundaryEdge {
  edge: Edge
  direction: "input" | "output"
  port: string
  /** The child the edge meets: the target of an input edge, the source of an output edge. */
  child: string
  childHandle: string | null
}

function boundaryEdge(edge: Edge, cardIds: ReadonlySet<string>): BoundaryEdge | null {
  if (!cardIds.has(edge.source) && !cardIds.has(edge.target)) return null
  const info = isPlainObject(edge.data) && isPlainObject(edge.data.submodelBoundary)
    ? edge.data.submodelBoundary
    : null
  const direction = info?.direction
  const port = info?.name
  if ((direction !== "input" && direction !== "output") || typeof port !== "string") {
    throw new Error(`Submodel boundary edge ${edge.id} has no port`)
  }
  return direction === "input"
    ? { edge, direction, port, child: edge.target, childHandle: edge.targetHandle ?? null }
    : { edge, direction, port, child: edge.source, childHandle: edge.sourceHandle ?? null }
}

function cardPortsKey(node: Node): string {
  const ports = (node.data as Partial<SubmodelPortData>).ports ?? []
  return canonicalJson(ports.map((port) => port.id))
}

function sameAuthoredNode(previous: Node | undefined, next: Node): boolean {
  if (previous === next) return true
  if (!previous) return false
  return canonicalJson(authoredNodeForm(previous)) === canonicalJson(authoredNodeForm(next))
}

function sameAuthoredEdge(previous: Edge | undefined, next: Edge): boolean {
  if (previous === next) return true
  if (!previous) return false
  return canonicalJson(authoredEdgeForm(previous as PipelineEdge)) === canonicalJson(authoredEdgeForm(next as PipelineEdge))
}

/**
 * What the write from `previous` to `next` (the canvas of an open occurrence of
 * `definition`) does to that definition. `readOnly` refuses any authored change
 * to a read-only occurrence, while its canvas still takes a selection or a
 * preview's columns.
 */
export function liftSubmodelCanvas({
  definition,
  readOnly,
  previous,
  next,
}: {
  definition: SubmodelDefinition
  readOnly: boolean
  previous: CanvasGraph
  next: CanvasGraph
}): SubmodelCanvasLift {
  const previousCards = previous.nodes.filter(isSubmodelCard)
  const nextCards = next.nodes.filter(isSubmodelCard)
  const cardIds = new Set(previousCards.map((card) => card.id))
  const nextCardsById = new Map(nextCards.map((card) => [card.id, card]))
  if (
    nextCards.length !== previousCards.length
    || previousCards.some((card) => {
      const nextCard = nextCardsById.get(card.id)
      return !nextCard || cardPortsKey(nextCard) !== cardPortsKey(card)
    })
  ) {
    return { kind: "refused", reason: BOUNDARY_ACTIONS_REASON }
  }

  const previousBoundary = new Map<string, BoundaryEdge>()
  for (const edge of previous.edges) {
    const boundary = boundaryEdge(edge, cardIds)
    if (boundary) previousBoundary.set(edge.id, boundary)
  }
  const nextBoundary: BoundaryEdge[] = []
  for (const edge of next.edges) {
    const boundary = boundaryEdge(edge, cardIds)
    if (boundary) nextBoundary.push(boundary)
  }
  if (
    nextBoundary.length !== previousBoundary.size
    || nextBoundary.some((boundary) => {
      const before = previousBoundary.get(boundary.edge.id)
      return !before
        || before.direction !== boundary.direction
        || before.port !== boundary.port
        || before.child !== boundary.child
    })
  ) {
    return { kind: "refused", reason: BOUNDARY_ACTIONS_REASON }
  }

  const children = next.nodes.filter((node) => !isSubmodelCard(node))
  const childIds = new Set(children.map((node) => node.id))
  const missing = nextBoundary.find((boundary) => !childIds.has(boundary.child))
  if (missing) {
    return {
      kind: "refused",
      reason: `Port ${missing.port} of the submodel still uses ${missing.child}: remove it from the boundary first.`,
    }
  }
  const internal = next.edges.filter((edge) => childIds.has(edge.source) && childIds.has(edge.target))

  const previousChildren = new Map(previous.nodes.filter((node) => !isSubmodelCard(node)).map((node) => [node.id, node]))
  const previousInternal = new Map(
    previous.edges
      .filter((edge) => !previousBoundary.has(edge.id))
      .map((edge) => [edge.id, edge]),
  )
  const unchanged =
    children.length === previousChildren.size
    && children.every((node) => sameAuthoredNode(previousChildren.get(node.id), node))
    && internal.length === previousInternal.size
    && internal.every((edge) => sameAuthoredEdge(previousInternal.get(edge.id), edge))
    && nextBoundary.every((boundary) => {
      const before = previousBoundary.get(boundary.edge.id)!
      return before.childHandle === boundary.childHandle
    })
  if (unchanged) return { kind: "canvas" }
  if (readOnly) {
    return { kind: "refused", reason: "This submodel instance is read-only. Edit the original submodel to change its definition." }
  }

  const targetsOf = (port: string): SubmodelEndpoint[] =>
    nextBoundary
      .filter((boundary) => boundary.direction === "input" && boundary.port === port)
      .map((boundary) => ({ nodeId: boundary.child, handleId: boundary.childHandle }))
  const outputPorts = definition.outputPorts.map((port) => {
    const source = nextBoundary.find((boundary) => boundary.direction === "output" && boundary.port === port.name)
    if (!source) throw new Error(`Submodel output ${port.name} has no edge into the Output card`)
    return { name: port.name, source: { nodeId: source.child, handleId: source.childHandle } }
  })
  return {
    kind: "definition",
    definition: {
      ...definition,
      graph: {
        ...definition.graph,
        nodes: children.map(authoredNodeForm),
        edges: internal.map((edge) => authoredEdgeForm(edge as PipelineEdge)),
      },
      inputPorts: definition.inputPorts.map((port) => ({ name: port.name, targets: targetsOf(port.name) })),
      outputPorts,
    },
  }
}

/** The execution annotations a write leaves on each child, keyed by child id. */
export function childAnnotations(nodes: readonly Node[]): Record<string, Record<string, unknown>> {
  const annotations: Record<string, Record<string, unknown>> = {}
  for (const node of nodes) {
    if (isSubmodelCard(node)) continue
    const own = nodeExecutionAnnotations(node)
    if (Object.keys(own).length > 0) annotations[node.id] = own
  }
  return annotations
}

/**
 * The canvas `focus` shows of the document: the definition's children (with
 * the execution annotations this occurrence's view last held for them) and
 * edges, and the Input and Output cards. Cards keep where `previous` (the
 * canvas of the same occurrence) had them. Throws when the occurrence or its
 * definition is not in the document.
 */
export function projectSubmodelFocus({
  rootNodes,
  rootEdges,
  submodels,
  focus,
  annotations = {},
  previous,
}: {
  rootNodes: readonly Node[]
  rootEdges: readonly PipelineEdge[]
  submodels: Record<string, unknown>
  focus: SubmodelFocusIdentity
  annotations?: Record<string, Record<string, unknown>>
  previous?: CanvasGraph
}): { nodes: Node[]; edges: PipelineEdge[] } {
  const occurrence = rootNodes.find((node) => node.id === focus.instanceId)
  const config = occurrence?.data.config
  if (!occurrence || !isSubmodelInstanceConfig(config) || config.definition_id !== focus.definitionId) {
    throw new Error(`Submodel occurrence ${focus.instanceId} is not in the document`)
  }
  const definition = submodels[focus.definitionId]
  if (!isSubmodelDefinition(definition, focus.definitionId)) {
    throw new Error(`Submodel definition ${focus.definitionId} is not in the document`)
  }
  const childNodes = definition.graph.nodes.map((node) => {
    const own = annotations[node.id]
    return own ? { ...node, data: { ...node.data, ...own } } : node
  })
  const childEdges = normalizeEdges(definition.graph.edges ?? [])
  const view = buildSubmodelViewGraph({
    submodelName: config.alias,
    instanceId: focus.instanceId,
    definition: { ...definition, graph: { ...definition.graph, nodes: childNodes, edges: childEdges } },
    childNodes,
    childEdges,
    parentNodes: [...rootNodes],
    parentEdges: [...rootEdges],
  })
  const previousCardPositions = new Map(
    (previous?.nodes ?? []).filter(isSubmodelCard).map((node) => [node.id, node.position]),
  )
  return {
    nodes: view.nodes.map((node) => {
      const position = isSubmodelCard(node) ? previousCardPositions.get(node.id) : undefined
      return position ? { ...node, position } : node
    }),
    edges: view.edges as PipelineEdge[],
  }
}
