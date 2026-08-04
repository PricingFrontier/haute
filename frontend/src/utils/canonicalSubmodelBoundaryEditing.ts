import type { Connection, Edge, Node } from "@xyflow/react"
import {
  isSubmodelDefinition,
  isSubmodelInstanceConfig,
  type PipelineEdge,
  type SubmodelBoundaryEdgeData,
  type SubmodelDefinition,
  type SubmodelEndpoint,
  type SubmodelInputPort,
  type SubmodelOutputPort,
  type SubmodelPortData,
} from "../types/node"
import { normalizeDefaultTargetHandle } from "./flowHandles"
import { buildSubmodelViewGraph } from "./submodelViewGraph"
import type {
  SubmodelBoundaryEditResult,
  SubmodelBoundaryEditState,
} from "./submodelBoundaryEditing"

export type CanonicalSubmodelBoundaryEditState = SubmodelBoundaryEditState

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

function boundaryInfo(edge: Edge): SubmodelBoundaryEdgeData["submodelBoundary"] | null {
  if (!isRecord(edge.data) || !isRecord(edge.data.submodelBoundary)) return null
  const info = edge.data.submodelBoundary
  if (info.direction !== "input" && info.direction !== "output") return null
  return info as SubmodelBoundaryEdgeData["submodelBoundary"]
}

function boundaryNode(
  nodes: Node[],
  direction: "input" | "output",
): Node | undefined {
  return nodes.find(
    (node) =>
      node.type === "submodelPort"
      && (node.data as Partial<SubmodelPortData>).portDirection === direction,
  )
}

function childGraph(state: SubmodelBoundaryEditState): { nodes: Node[]; edges: Edge[] } {
  return {
    nodes: state.viewNodes.filter((node) => node.type !== "submodelPort"),
    edges: state.viewEdges.filter((edge) => boundaryInfo(edge) === null),
  }
}

function definitionFor(state: CanonicalSubmodelBoundaryEditState): SubmodelDefinition {
  const value = state.submodels[state.definitionId]
  if (!isSubmodelDefinition(value, state.definitionId)) {
    throw new Error(`Submodel definition ${state.definitionId} is missing or malformed`)
  }
  return value
}

function portId(info: SubmodelBoundaryEdgeData["submodelBoundary"], direction: "input" | "output"): string {
  if (info.direction !== direction || typeof info.portId !== "string" || info.portId.length === 0) {
    throw new Error(`Canonical ${direction} boundary edge is missing its public port id`)
  }
  return info.portId
}

function endpointKey(endpoint: SubmodelEndpoint): string {
  return JSON.stringify([endpoint.nodeId, endpoint.handleId])
}

function deriveInputPorts(
  state: CanonicalSubmodelBoundaryEditState,
  definition: SubmodelDefinition,
): SubmodelInputPort[] {
  const input = boundaryNode(state.viewNodes, "input")
  if (!input) throw new Error("Canonical submodel view is missing its Input boundary")
  const labels = new Map(
    (input.data as unknown as SubmodelPortData).ports.map((port) => [port.id, port.label]),
  )
  const edges = state.viewEdges.filter((edge) => edge.source === input.id)
  const result: SubmodelInputPort[] = []
  for (const existing of definition.inputPorts) {
    const targets: SubmodelEndpoint[] = []
    for (const edge of edges) {
      const info = boundaryInfo(edge)
      if (!info || info.direction !== "input" || portId(info, "input") !== existing.portId) continue
      const endpoint = {
        nodeId: edge.target,
        handleId: edge.targetHandle ?? null,
      }
      if (!targets.some((candidate) => endpointKey(candidate) === endpointKey(endpoint))) {
        targets.push(endpoint)
      }
    }
    if (targets.length === 0) continue
    result.push({
      ...existing,
      label: labels.get(existing.portId) ?? existing.label,
      targets,
    })
  }
  return result
}

function deriveOutputPorts(
  state: CanonicalSubmodelBoundaryEditState,
  definition: SubmodelDefinition,
): SubmodelOutputPort[] {
  const output = boundaryNode(state.viewNodes, "output")
  if (!output) throw new Error("Canonical submodel view is missing its Output boundary")
  const existingById = new Map(definition.outputPorts.map((port) => [port.portId, port]))
  const result: SubmodelOutputPort[] = []
  const seen = new Set<string>()
  for (const edge of state.viewEdges) {
    if (edge.target !== output.id) continue
    const info = boundaryInfo(edge)
    if (!info || info.direction !== "output") continue
    const id = portId(info, "output")
    if (seen.has(id)) throw new Error(`Canonical output port ${id} has more than one source`)
    seen.add(id)
    const existing = existingById.get(id)
    const childLabel = state.viewNodes.find((node) => node.id === edge.source)?.data.label
    result.push({
      portId: id,
      label: existing?.label
        ?? (typeof childLabel === "string" && childLabel.length > 0 ? childLabel : edge.source),
      source: {
        nodeId: edge.source,
        handleId: edge.sourceHandle ?? null,
      },
    })
  }
  return result
}

function changedPorts(
  definition: SubmodelDefinition,
  inputPorts: SubmodelInputPort[],
  outputPorts: SubmodelOutputPort[],
): Map<string, { direction: "input" | "output"; label: string }> {
  const changed = new Map<string, { direction: "input" | "output"; label: string }>()
  const nextInputs = new Set(inputPorts.map((port) => port.portId))
  for (const oldPort of definition.inputPorts) {
    if (!nextInputs.has(oldPort.portId)) {
      changed.set(oldPort.portId, { direction: "input", label: oldPort.label })
    }
  }
  const nextOutputs = new Set(outputPorts.map((port) => port.portId))
  for (const oldPort of definition.outputPorts) {
    if (!nextOutputs.has(oldPort.portId)) {
      changed.set(oldPort.portId, { direction: "output", label: oldPort.label })
    }
  }
  return changed
}

function assertCompatibleSharedEdit(
  state: CanonicalSubmodelBoundaryEditState,
  definition: SubmodelDefinition,
  inputPorts: SubmodelInputPort[],
  outputPorts: SubmodelOutputPort[],
): void {
  const changed = changedPorts(definition, inputPorts, outputPorts)
  if (changed.size === 0) return

  const affected: string[] = []
  for (const node of state.parentNodes) {
    const config = node.data.config
    if (
      node.data.nodeType !== "submodel"
      || !isSubmodelInstanceConfig(config)
      || config.definitionId !== state.definitionId
    ) continue
    const used = new Set<string>()
    for (const edge of state.parentEdges) {
      if (edge.target === node.id && typeof edge.targetHandle === "string" && edge.targetHandle.startsWith("in__")) {
        const id = edge.targetHandle.slice("in__".length)
        const port = changed.get(id)
        if (port?.direction === "input") used.add(id)
      }
      if (edge.source === node.id && typeof edge.sourceHandle === "string" && edge.sourceHandle.startsWith("out__")) {
        const id = edge.sourceHandle.slice("out__".length)
        const port = changed.get(id)
        if (port?.direction === "output") used.add(id)
      }
    }
    if (used.size === 0) continue
    const label = typeof node.data.label === "string" && node.data.label.length > 0
      ? node.data.label
      : config.alias
    const bindings = [...used].map((id) => {
      const port = changed.get(id)!
      return `${port.direction} ${port.label} [${id}]`
    })
    affected.push(`${label} (${node.id}): ${bindings.join(", ")}`)
  }
  if (affected.length > 0) {
    throw new Error(
      `Cannot change public ports that are bound by shared instances: ${affected.join("; ")}`,
    )
  }
}

function preserveBoundaryPositions(previous: Node[], next: Node[]): Node[] {
  const positions = new Map(
    previous
      .filter((node) => node.type === "submodelPort")
      .map((node) => [node.id, node.position]),
  )
  return next.map((node) => {
    const position = positions.get(node.id)
    return position ? { ...node, position } : node
  })
}

export function reconcileCanonicalSubmodelBoundaryState(
  state: CanonicalSubmodelBoundaryEditState,
): SubmodelBoundaryEditResult | null {
  if (!boundaryNode(state.viewNodes, "input") || !boundaryNode(state.viewNodes, "output")) {
    return null
  }
  const definition = definitionFor(state)
  const children = childGraph(state)
  const childIds = new Set(children.nodes.map((node) => node.id))
  for (const edge of state.viewEdges) {
    const info = boundaryInfo(edge)
    if (info?.direction === "input" && !childIds.has(edge.target)) {
      throw new Error(`Canonical input port ${portId(info, "input")} targets missing child ${edge.target}`)
    }
    if (info?.direction === "output" && !childIds.has(edge.source)) {
      throw new Error(`Canonical output port ${portId(info, "output")} sources missing child ${edge.source}`)
    }
  }

  const inputPorts = deriveInputPorts(state, definition)
  const outputPorts = deriveOutputPorts(state, definition)
  assertCompatibleSharedEdit(state, definition, inputPorts, outputPorts)
  const nextDefinition: SubmodelDefinition = {
    ...definition,
    graph: {
      ...definition.graph,
      nodes: children.nodes,
      edges: children.edges,
    },
    inputPorts,
    outputPorts,
  }
  const submodels = { ...state.submodels, [state.definitionId]: nextDefinition }
  const view = buildSubmodelViewGraph({
    submodelName: state.submodelName,
    instanceId: state.instanceId,
    definition: nextDefinition,
    childNodes: children.nodes,
    childEdges: children.edges,
    parentNodes: state.parentNodes,
    parentEdges: state.parentEdges,
  })
  return {
    submodelName: state.submodelName,
    instanceId: state.instanceId,
    definitionId: state.definitionId,
    viewNodes: preserveBoundaryPositions(state.viewNodes, view.nodes),
    viewEdges: view.edges as PipelineEdge[],
    parentNodes: state.parentNodes,
    parentEdges: state.parentEdges,
    submodels,
  }
}

function nextEdgeId(base: string, edges: readonly Edge[]): string {
  const ids = new Set(edges.map((edge) => edge.id))
  let candidate = base
  let suffix = 1
  while (ids.has(candidate)) candidate = `${base}__${suffix++}`
  return candidate
}

function nextOutputPortId(definition: SubmodelDefinition): string {
  const occupied = new Set([
    ...definition.inputPorts.map((port) => port.portId),
    ...definition.outputPorts.map((port) => port.portId),
  ])
  let index = 1
  while (occupied.has(`output_${index}`)) index += 1
  return `output_${index}`
}

export function applyCanonicalSubmodelBoundaryConnection(
  state: CanonicalSubmodelBoundaryEditState,
  connection: Connection,
): SubmodelBoundaryEditResult | null {
  const input = boundaryNode(state.viewNodes, "input")
  const output = boundaryNode(state.viewNodes, "output")
  const childIds = new Set(
    state.viewNodes.filter((node) => node.type !== "submodelPort").map((node) => node.id),
  )
  if (input && connection.source === input.id && connection.sourceHandle && connection.target) {
    if (!childIds.has(connection.target)) return null
    const definition = definitionFor(state)
    if (!definition.inputPorts.some((port) => port.portId === connection.sourceHandle)) return null
    const handleId = normalizeDefaultTargetHandle(connection.targetHandle)
    const duplicate = state.viewEdges.some(
      (edge) =>
        edge.source === input.id
        && edge.sourceHandle === connection.sourceHandle
        && edge.target === connection.target
        && (edge.targetHandle ?? null) === handleId,
    )
    if (duplicate) return null
    const row = (input.data as unknown as SubmodelPortData).ports.find(
      (port) => port.id === connection.sourceHandle,
    )
    if (!row) {
      throw new Error(`Canonical input boundary is missing public port ${connection.sourceHandle}`)
    }
    const parentEdges = row.parentEdges
    const edge: PipelineEdge = {
      id: nextEdgeId(
        `submodel-view__input-edge__${encodeURIComponent(JSON.stringify([
          state.instanceId,
          connection.sourceHandle,
          connection.target,
          handleId,
        ]))}`,
        state.viewEdges,
      ),
      source: input.id,
      sourceHandle: connection.sourceHandle,
      target: connection.target,
      targetHandle: handleId,
      data: {
        submodelBoundary: {
          direction: "input",
          portId: connection.sourceHandle,
          parentEdges,
        },
      } satisfies SubmodelBoundaryEdgeData,
    }
    return reconcileCanonicalSubmodelBoundaryState({
      ...state,
      viewEdges: [...state.viewEdges, edge],
    })
  }

  if (output && connection.target === output.id && connection.source) {
    if (!childIds.has(connection.source)) return null
    const handleId = connection.sourceHandle ?? null
    const duplicate = state.viewEdges.some(
      (edge) =>
        edge.target === output.id
        && edge.source === connection.source
        && (edge.sourceHandle ?? null) === handleId,
    )
    if (duplicate) return null
    const newPortId = nextOutputPortId(definitionFor(state))
    const edge: PipelineEdge = {
      id: nextEdgeId(
        `submodel-view__output-edge__${encodeURIComponent(JSON.stringify([
          state.instanceId,
          newPortId,
        ]))}`,
        state.viewEdges,
      ),
      source: connection.source,
      sourceHandle: handleId,
      target: output.id,
      targetHandle: null,
      data: {
        submodelBoundary: {
          direction: "output",
          portId: newPortId,
          parentConsumerEdges: [],
        },
      } satisfies SubmodelBoundaryEdgeData,
    }
    return reconcileCanonicalSubmodelBoundaryState({
      ...state,
      viewEdges: [...state.viewEdges, edge],
    })
  }
  return null
}

export function removeCanonicalSubmodelBoundaryEdges(
  state: CanonicalSubmodelBoundaryEditState,
  edgeIds: string[],
): SubmodelBoundaryEditResult | null {
  const wanted = new Set(edgeIds)
  const selected = state.viewEdges.filter(
    (edge) => wanted.has(edge.id) && boundaryInfo(edge) !== null,
  )
  if (selected.length === 0) return null
  return reconcileCanonicalSubmodelBoundaryState({
    ...state,
    viewEdges: state.viewEdges.filter((edge) => !wanted.has(edge.id)),
  })
}
