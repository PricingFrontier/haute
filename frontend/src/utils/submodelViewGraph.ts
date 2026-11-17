import type { Edge, Node } from "@xyflow/react"
import {
  isSubmodelDefinition,
  isSubmodelInstanceConfig,
  nodeData,
  type ColumnInfo,
  type PipelineEdge,
  type SubmodelBoundaryEdgeData,
  type SubmodelDefinition,
  type SubmodelPortData,
} from "../types/node"
import { attachEditorEdgeIdentities } from "./editorIdentities"
import { normalizeEdges } from "./graphHelpers"
import { NODE_TYPES } from "./nodeTypes"

export interface SubmodelViewGraphInput {
  submodelName: string
  instanceId: string
  definition: SubmodelDefinition
  childNodes: Node[]
  childEdges: Edge[]
  parentNodes: Node[]
  parentEdges: Edge[]
}

export interface SubmodelViewGraph {
  nodes: Node[]
  edges: Edge[]
}

/** How far a boundary card sits from the nearest child's position: a card's width and a gap. */
const BOUNDARY_CARD_SPACING = 360

/**
 * Where the boundary cards go: the Input card left of the children and the Output card
 * right of them, at their mean height. The children keep their authored positions, so
 * an untouched visit leaves the definition's layout, and the document, as they were.
 */
function boundaryCardPositions(childNodes: readonly Node[]): Record<"input" | "output", Node["position"]> {
  if (childNodes.length === 0) {
    return { input: { x: 0, y: 0 }, output: { x: BOUNDARY_CARD_SPACING, y: 0 } }
  }
  for (const node of childNodes) {
    if (!Number.isFinite(node.position?.x) || !Number.isFinite(node.position?.y)) {
      throw new Error(`Submodel child ${node.id} has no canvas position`)
    }
  }
  const xs = childNodes.map((node) => node.position.x)
  const y = Math.round(childNodes.reduce((sum, node) => sum + node.position.y, 0) / childNodes.length)
  return {
    input: { x: Math.min(...xs) - BOUNDARY_CARD_SPACING, y },
    output: { x: Math.max(...xs) + BOUNDARY_CARD_SPACING, y },
  }
}

function buildCanonicalSubmodelViewGraph(
  input: SubmodelViewGraphInput,
): SubmodelViewGraph {
  const {
    submodelName,
    instanceId,
    definition,
    childNodes,
    childEdges,
    parentNodes,
    parentEdges,
  } = input
  const definitionCandidate: unknown = definition
  if (!isSubmodelDefinition(definitionCandidate, definition.definitionId)) {
    throw new Error(`Submodel definition ${definition.definitionId} is malformed`)
  }
  const placeholder = parentNodes.find((node) => node.id === instanceId)
  if (!placeholder) throw new Error(`Submodel instance ${instanceId} is missing from the parent graph`)
  const config = placeholder.data.config
  if (!isSubmodelInstanceConfig(config) || config.definition_id !== definition.definitionId) {
    throw new Error(
      `Submodel instance ${instanceId} does not reference definition ${definition.definitionId}`,
    )
  }

  const childIds = new Set(childNodes.map((node) => node.id))
  const inputId = stableId("boundary", [instanceId, "input"])
  const outputId = stableId("boundary", [instanceId, "output"])
  if (childIds.has(inputId) || childIds.has(outputId)) {
    throw new Error(`Submodel boundary id collides with a child node for ${submodelName}`)
  }

  const inputHandles = new Set(definition.inputPorts.map((port) => `in__${port.name}`))
  const outputHandles = new Set(definition.outputPorts.map((port) => `out__${port.name}`))
  const occurrenceIds = new Set([instanceId, ...parentNodes.flatMap((node) => {
    const candidateConfig = node.data.config
    return node.data.nodeType === "submodel"
      && isSubmodelInstanceConfig(candidateConfig)
      && candidateConfig.definition_id === definition.definitionId
      ? [node.id]
      : []
  })])
  for (const edge of parentEdges as PipelineEdge[]) {
    if (
      occurrenceIds.has(edge.target)
      && (typeof edge.targetHandle !== "string" || !inputHandles.has(edge.targetHandle))
    ) {
      throw new Error(
        `Submodel instance ${edge.target} has undeclared input handle ${String(edge.targetHandle)}`,
      )
    }
    if (
      occurrenceIds.has(edge.source)
      && (typeof edge.sourceHandle !== "string" || !outputHandles.has(edge.sourceHandle))
    ) {
      throw new Error(
        `Submodel instance ${edge.source} has undeclared output handle ${String(edge.sourceHandle)}`,
      )
    }
  }

  const inputPorts: SubmodelPortData["ports"] = []
  const inputFeeds: [string, PipelineEdge][] = []
  const inputExternalIds: string[] = []
  const outputExternalIds: string[] = []
  const syntheticEdges: Edge[] = []
  const occupiedEdgeIds = new Set(childEdges.map((edge) => edge.id))

  for (const port of definition.inputPorts) {
    const handle = `in__${port.name}`
    const definitionConsumers = (parentEdges as PipelineEdge[]).filter(
      (edge) => occurrenceIds.has(edge.target) && edge.targetHandle === handle,
    )
    const activeConsumers = definitionConsumers.filter(
      (edge) => edge.target === instanceId,
    )
    inputPorts.push({ id: port.name, label: port.name })
    for (const consumer of activeConsumers) {
      if (!inputExternalIds.includes(consumer.source)) inputExternalIds.push(consumer.source)
    }
    if (activeConsumers[0]) inputFeeds.push([port.name, activeConsumers[0]])
    for (const [index, target] of port.targets.entries()) {
      if (!childIds.has(target.nodeId)) {
        throw new Error(
          `Submodel definition ${definition.definitionId} input port ${port.name} references missing child ${target.nodeId}`,
        )
      }
      syntheticEdges.push({
        id: uniqueEdgeId(
          stableId("input-edge", [instanceId, port.name, target.nodeId, String(index)]),
          occupiedEdgeIds,
        ),
        source: inputId,
        sourceHandle: port.name,
        target: target.nodeId,
        targetHandle: target.handleId,
        data: { submodelBoundary: { direction: "input", name: port.name } } satisfies SubmodelBoundaryEdgeData,
      })
    }
  }

  for (const port of definition.outputPorts) {
    if (!childIds.has(port.source.nodeId)) {
      throw new Error(
        `Submodel definition ${definition.definitionId} output port ${port.name} references missing child ${port.source.nodeId}`,
      )
    }
    const handle = `out__${port.name}`
    const consumers = (parentEdges as PipelineEdge[]).filter(
      (edge) => edge.source === instanceId && edge.sourceHandle === handle,
    )
    for (const consumer of consumers) {
      if (!outputExternalIds.includes(consumer.target)) outputExternalIds.push(consumer.target)
    }
    syntheticEdges.push({
      id: uniqueEdgeId(stableId("output-edge", [instanceId, port.name]), occupiedEdgeIds),
      source: port.source.nodeId,
      sourceHandle: port.source.handleId,
      target: outputId,
      targetHandle: null,
      data: { submodelBoundary: { direction: "output", name: port.name } } satisfies SubmodelBoundaryEdgeData,
    })
  }

  const cardPositions = boundaryCardPositions(childNodes)
  return {
    nodes: [
      ...childNodes,
      withInputColumns(boundaryNode(
        inputId,
        cardPositions.input,
        "input",
        inputPorts,
        inputExternalIds,
        instanceId,
        definition.definitionId,
      ), parentNodes, inputFeeds),
      boundaryNode(
        outputId,
        cardPositions.output,
        "output",
        [],
        outputExternalIds,
        instanceId,
        definition.definitionId,
      ),
    ],
    edges: normalizeEdges([...childEdges, ...syntheticEdges]),
  }
}

type BoundaryData = SubmodelPortData & { nodeType: typeof NODE_TYPES.SUBMODEL_PORT }

/** The editor identity of a boundary card, as the server would resolve it. */
export interface SubmodelPortIdentity {
  _functionName: "INPUT" | "OUTPUT"
  _defaultInputName: null
  _sourceHandleInputNames: Record<string, string>
}

/**
 * A boundary card's identity follows from its direction and its declared ports
 * alone: its name is its label, and a frame leaving the Input card is named by
 * its port, since a port name is already a canonical identifier
 * (`executable_input_name` names a port's frame by its sanitised handle, and
 * the server refuses a port name that is not its own sanitised form). Built
 * here rather than asked of the server, so a submodel view is a pure
 * projection. `tests/fixtures/ui_contracts/submodel_port_identities.json`
 * holds the server's answers this must match.
 */
export function submodelPortIdentity(
  direction: "input" | "output",
  portNames: readonly string[],
): SubmodelPortIdentity {
  return {
    _functionName: direction === "input" ? "INPUT" : "OUTPUT",
    _defaultInputName: null,
    _sourceHandleInputNames: Object.fromEntries(
      (direction === "input" ? portNames : []).map((name) => [name, name]),
    ),
  }
}

/**
 * The Input card runs nothing, so its ports start with the columns the parent
 * nodes feeding them last recorded: an editor fed by the card lists them
 * before any preview of this view, and after one served from the cache.
 * A stash without a source tag was invalidated, so it seeds nothing; ports
 * fed under another source than the first seeded one are left for a preview.
 */
function withInputColumns(
  card: Node<BoundaryData>,
  parentNodes: readonly Node[],
  feeds: readonly [string, PipelineEdge][],
): Node<BoundaryData> {
  const frames: Record<string, ColumnInfo[]> = {}
  let source: string | undefined
  for (const [port, edge] of feeds) {
    const feeder = parentNodes.find((node) => node.id === edge.source)
    const data = feeder ? nodeData(feeder) : undefined
    if (!data?._columnsSource || (source !== undefined && data._columnsSource !== source)) continue
    const columns = (edge.sourceHandle ? data._frameColumns?.[edge.sourceHandle] : undefined) ?? data._columns
    if (!columns) continue
    frames[port] = columns
    source = data._columnsSource
  }
  if (source === undefined) return card
  return { ...card, data: { ...card.data, _frameColumns: frames, _columnsSource: source } }
}
function stableId(kind: string, values: readonly (string | null)[]): string { return `submodel-view__${kind}__${encodeURIComponent(JSON.stringify(values))}` }
function uniqueEdgeId(base: string, occupied: Set<string>): string { let candidate = base; let suffix = 1; while (occupied.has(candidate)) candidate = `${base}__${suffix++}`; occupied.add(candidate); return candidate }
function boundaryNode(
  id: string,
  position: Node["position"],
  direction: "input" | "output",
  ports: SubmodelPortData["ports"],
  externalNodeIds: string[],
  instanceId: string,
  definitionId: string,
): Node<BoundaryData> {
  return {
    id,
    type: NODE_TYPES.SUBMODEL_PORT,
    position,
    data: {
      label: direction === "input" ? "INPUT" : "OUTPUT",
      ...submodelPortIdentity(direction, ports.map((port) => port.id)),
      nodeType: NODE_TYPES.SUBMODEL_PORT,
      instanceId,
      definitionId,
      portDirection: direction,
      ports,
      externalNodeIds,
    },
  }
}

/**
 * The view of one occurrence of a definition: its children, its Input and
 * Output cards and the edges between them, every node and edge named as the
 * editor names them. A pure function of the document, so opening a submodel
 * asks the server nothing.
 */
export function buildSubmodelViewGraph(input: SubmodelViewGraphInput): SubmodelViewGraph {
  const view = buildCanonicalSubmodelViewGraph(input)
  return { nodes: view.nodes, edges: attachEditorEdgeIdentities(view.edges, view.nodes) }
}
