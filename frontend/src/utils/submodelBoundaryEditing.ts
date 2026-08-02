import type { Connection, Edge, Node } from "@xyflow/react"
import type { PipelineEdge, SubmodelBoundaryEdgeData, SubmodelPortData } from "../types/node"
import { normalizeDefaultTargetHandle } from "./flowHandles"
import { buildSubmodelViewGraph } from "./submodelViewGraph"

export interface SubmodelBoundaryEditState {
  submodelName: string
  viewNodes: Node[]
  viewEdges: PipelineEdge[]
  parentNodes: Node[]
  parentEdges: PipelineEdge[]
  submodels: Record<string, unknown>
}
export type SubmodelBoundaryEditResult = Pick<SubmodelBoundaryEditState, "submodelName" | "viewNodes" | "viewEdges" | "parentNodes" | "parentEdges" | "submodels">

type Metadata = Record<string, unknown>
const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value)
const boundary = (node: Node | undefined, direction: "input" | "output") => node?.type === "submodelPort" && (node.data as unknown as SubmodelPortData).portDirection === direction
type BoundaryEdge = Edge & { data: SubmodelBoundaryEdgeData }
const isBoundaryEdge = (edge: Edge): edge is BoundaryEdge => isRecord(edge.data) && isRecord(edge.data.submodelBoundary) && (edge.data.submodelBoundary.direction === "input" || edge.data.submodelBoundary.direction === "output")
const placeholderId = (name: string) => `submodel__${name}`

function childGraph(state: SubmodelBoundaryEditState): { nodes: Node[]; edges: Edge[] } {
  return {
    nodes: state.viewNodes.filter(node => node.type !== "submodelPort"),
    edges: state.viewEdges.filter(edge => !isBoundaryEdge(edge)),
  }
}
function configFor(state: SubmodelBoundaryEditState): Record<string, unknown> {
  const node = state.parentNodes.find(candidate => candidate.id === placeholderId(state.submodelName))
  return isRecord(node?.data.config) ? node.data.config : {}
}
function reconcile(state: SubmodelBoundaryEditState, parentEdges: PipelineEdge[], inputPorts: string[], outputPorts: string[], labels: Record<string, string>): SubmodelBoundaryEditResult {
  const children = childGraph(state)
  const childNodeIds = children.nodes.map(node => node.id)
  const oldConfig = configFor(state)
  const config = { ...oldConfig, childNodeIds, inputPorts, outputPorts, outputPortLabels: labels }
  const parentNodes = state.parentNodes.map(node => node.id === placeholderId(state.submodelName)
    ? { ...node, data: { ...node.data, config } }
    : node)
  const oldMetadata = state.submodels[state.submodelName]
  if (!isRecord(oldMetadata)) throw new Error(`Submodel ${state.submodelName} metadata is missing`)
  const oldGraph = isRecord(oldMetadata.graph) ? oldMetadata.graph : {}
  const metadata: Metadata = { ...oldMetadata, childNodeIds, inputPorts, outputPorts, outputPortLabels: labels, graph: { ...oldGraph, nodes: children.nodes, edges: children.edges } }
  const submodels = { ...state.submodels, [state.submodelName]: metadata }
  const view = buildSubmodelViewGraph({ submodelName: state.submodelName, childNodes: children.nodes, childEdges: children.edges, parentNodes, parentEdges })
  const boundaryPositions = new Map(
    state.viewNodes
      .filter(node => node.type === "submodelPort")
      .map(node => [node.id, node.position]),
  )
  const viewNodes = view.nodes.map(node => {
    const position = boundaryPositions.get(node.id)
    return position ? { ...node, position } : node
  })
  return { submodelName: state.submodelName, viewNodes, viewEdges: view.edges as PipelineEdge[], parentNodes, parentEdges, submodels }
}
function nextId(base: string, edges: readonly PipelineEdge[]): string {
  const ids = new Set(edges.map(edge => edge.id)); let id = base; let n = 1
  while (ids.has(id)) id = `${base}__${n++}`
  return id
}
function currentPorts(state: SubmodelBoundaryEditState) {
  const config = configFor(state)
  return {
    input: Array.isArray(config.inputPorts) ? config.inputPorts.filter((id): id is string => typeof id === "string") : [],
    output: Array.isArray(config.outputPorts) ? config.outputPorts.filter((id): id is string => typeof id === "string") : [],
    labels: isRecord(config.outputPortLabels) ? Object.fromEntries(Object.entries(config.outputPortLabels).filter((entry): entry is [string, string] => typeof entry[1] === "string")) : {},
  }
}

export function applySubmodelBoundaryConnection(state: SubmodelBoundaryEditState, connection: Connection): SubmodelBoundaryEditResult | null {
  const source = state.viewNodes.find(node => node.id === connection.source)
  const target = state.viewNodes.find(node => node.id === connection.target)
  const ports = currentPorts(state)
  if (source && boundary(source, "input") && connection.sourceHandle) {
    const port = (source.data as unknown as SubmodelPortData).ports.find(candidate => candidate.id === connection.sourceHandle)
    if (!port || !connection.target || !state.viewNodes.some(node => node.id === connection.target && node.type !== "submodelPort")) return null
    const targetPort = normalizeDefaultTargetHandle(connection.targetHandle)
    const handle = `in__${connection.target}`
    if (state.parentEdges.some(edge => edge.target === placeholderId(state.submodelName) && edge.targetHandle === handle && (edge.targetPort ?? null) === targetPort && port.parentEdges?.some(backing => backing.id === edge.id))) return null
    const available = port.parentEdges?.find(edge => edge.targetHandle === null || edge.targetHandle === undefined)
    const backing = available ?? port.parentEdges?.[0]
    if (!backing) return null
    const replacement: PipelineEdge = { ...backing, id: available ? backing.id : nextId(backing.id, state.parentEdges), target: placeholderId(state.submodelName), targetHandle: handle, targetPort }
    const parentEdges = available ? state.parentEdges.map(edge => edge.id === backing.id ? replacement : edge) : [...state.parentEdges, replacement]
    return reconcile(state, parentEdges, [...new Set([...ports.input, connection.target])], ports.output, ports.labels)
  }
  if (boundary(target, "output") && connection.source && state.viewNodes.some(node => node.id === connection.source && node.type !== "submodelPort")) {
    if (ports.output.includes(connection.source)) return null
    const label = state.viewNodes.find(node => node.id === connection.source)?.data.label
    return reconcile(state, state.parentEdges, ports.input, [...ports.output, connection.source], { ...ports.labels, [connection.source]: typeof label === "string" && label.length > 0 ? label : connection.source })
  }
  return null
}

export function removeSubmodelBoundaryEdges(state: SubmodelBoundaryEditState, edgeIds: string[]): SubmodelBoundaryEditResult | null {
  const wanted = new Set(edgeIds)
  const selected = state.viewEdges.filter(edge => wanted.has(edge.id) && isBoundaryEdge(edge))
  if (selected.length === 0) return null
  let parentEdges = [...state.parentEdges]
  let { input, output, labels } = currentPorts(state)
  for (const edge of selected) {
    const info = (edge.data as SubmodelBoundaryEdgeData).submodelBoundary
    if (info.direction === "input") {
      const backing = info.parentEdge
      const logical = state.parentEdges.filter(candidate => candidate.target === placeholderId(state.submodelName) && candidate.source === backing.source && (candidate.sourceHandle ?? null) === (backing.sourceHandle ?? null))
      const otherMappings = logical.filter(candidate => candidate.id !== backing.id && candidate.targetHandle !== null && candidate.targetHandle !== undefined)
      if (otherMappings.length === 0) parentEdges = parentEdges.map(candidate => candidate.id === backing.id ? { ...candidate, targetHandle: null, targetPort: null } : candidate)
      else parentEdges = parentEdges.filter(candidate => candidate.id !== backing.id)
      const childId = edge.target
      if (!parentEdges.some(candidate => candidate.target === placeholderId(state.submodelName) && candidate.targetHandle === `in__${childId}`)) input = input.filter(id => id !== childId)
    } else {
      const childId = edge.source
      parentEdges = parentEdges.filter(candidate => !(candidate.source === placeholderId(state.submodelName) && candidate.sourceHandle === `out__${childId}`))
      output = output.filter(id => id !== childId)
      const { [childId]: _removed, ...rest } = labels
      labels = rest
    }
  }
  return reconcile(state, parentEdges, input, output, labels)
}




/** Rebuild persisted submodel state from a visible drilled-in snapshot. */
export function reconcileSubmodelBoundaryState(state: SubmodelBoundaryEditState): SubmodelBoundaryEditResult {
  const inputNode = state.viewNodes.find(node => boundary(node, "input"))
  const outputNode = state.viewNodes.find(node => boundary(node, "output"))
  const inputData = inputNode ? inputNode.data as unknown as SubmodelPortData : undefined
  const placeholder = placeholderId(state.submodelName)
  const currentBoundary = new Map<string, PipelineEdge>()
  for (const port of inputData?.ports ?? []) for (const edge of port.parentEdges ?? []) currentBoundary.set(edge.id, edge)
  const inputEdges = state.viewEdges.filter(edge => edge.source === inputNode?.id && isBoundaryEdge(edge))
  const outputEdges = state.viewEdges.filter(edge => edge.target === outputNode?.id && isBoundaryEdge(edge))
  for (const edge of inputEdges) {
    const info = (edge.data as SubmodelBoundaryEdgeData).submodelBoundary
    if (info.direction === "input") currentBoundary.set(info.parentEdge.id, info.parentEdge)
  }
  for (const edge of outputEdges) {
    const info = (edge.data as SubmodelBoundaryEdgeData).submodelBoundary
    if (info.direction === "output") for (const consumer of info.parentConsumerEdges) currentBoundary.set(consumer.id, consumer)
  }
  const isPersistedBoundary = (edge: PipelineEdge) =>
    (edge.target === placeholder && (edge.targetHandle === null || edge.targetHandle === undefined || edge.targetHandle.startsWith("in__"))) ||
    (edge.source === placeholder && typeof edge.sourceHandle === "string" && edge.sourceHandle.startsWith("out__"))
  const parentEdges = [...state.parentEdges.filter(edge => !isPersistedBoundary(edge)), ...currentBoundary.values()]
  const inputPorts = [...new Set(inputEdges.map(edge => edge.target))]
  const outputPorts = [...new Set(outputEdges.map(edge => edge.source))]
  const labels = Object.fromEntries(outputPorts.map(id => {
    const label = state.viewNodes.find(node => node.id === id)?.data.label
    return [id, typeof label === "string" && label.length > 0 ? label : id]
  }))
  return reconcile(state, parentEdges, inputPorts, outputPorts, labels)
}
