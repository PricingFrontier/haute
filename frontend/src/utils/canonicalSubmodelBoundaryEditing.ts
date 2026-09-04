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
import {
  normalizeDefaultTargetHandle,
  SUBMODEL_INPUT_HANDLE,
} from "./flowHandles"
import { attachEditorEdgeIdentities } from "./editorIdentities"
import {
  edgeInputName,
  submodelInputPortIdForName,
  UNRESOLVED_INPUT_NAME,
} from "./apiInputPorts"
import { appEdge } from "./flowElements"
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
    // A parent-created port is intentionally visible before it has an
    // internal route. Preserve that draft declaration; a previously routed
    // and now unbound port retains the established delete-last-edge behavior.
    if (targets.length === 0 && existing.targets.length > 0) continue
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

interface BoundaryIdentity {
  functionName: string
  defaultInputName: null
  sourceHandleInputNames: Record<string, string>
  configReference?: string
}

function boundaryIdentity(
  previous: Node[],
  direction: "input" | "output",
  nextHandleIds: readonly string[],
): BoundaryIdentity {
  const node = boundaryNode(previous, direction)
  if (!node) throw new Error(`Canonical submodel view is missing its ${direction} boundary`)
  const functionName = node.data._functionName
  const defaultInputName = node.data._defaultInputName
  const mappings = node.data._sourceHandleInputNames
  const configReference = node.data._configReference
  if (typeof functionName !== "string" || functionName.length === 0) {
    throw new Error(`Canonical submodel ${direction} boundary has no authoritative function identity`)
  }
  if (defaultInputName !== null) {
    throw new Error(`Canonical submodel ${direction} boundary has malformed default identity`)
  }
  if (!isRecord(mappings)) {
    throw new Error(`Canonical submodel ${direction} boundary has no authoritative source-handle identities`)
  }
  if (configReference !== undefined && (typeof configReference !== "string" || configReference.length === 0)) {
    throw new Error(`Canonical submodel ${direction} boundary has malformed config identity`)
  }
  const sourceHandleInputNames: Record<string, string> = {}
  for (const handleId of nextHandleIds) {
    const value = mappings[handleId]
    if (typeof value !== "string" || value.length === 0) {
      throw new Error(
        `Canonical submodel ${direction} boundary handle ${handleId} has no authoritative identity`,
      )
    }
    sourceHandleInputNames[handleId] = value
  }
  return {
    functionName,
    defaultInputName,
    sourceHandleInputNames,
    ...(configReference === undefined ? {} : { configReference }),
  }
}

function preserveBoundaryProjection(
  previous: Node[],
  next: Node[],
  inputIdentity: BoundaryIdentity,
  outputIdentity: BoundaryIdentity,
): Node[] {
  const previousPositions = new Map(
    previous
      .filter((node) => node.type === "submodelPort")
      .map((node) => [node.id, node.position]),
  )
  return next.map((node) => {
    if (node.type !== "submodelPort") return node
    const direction = (node.data as Partial<SubmodelPortData>).portDirection
    const identity = direction === "input" ? inputIdentity : outputIdentity
    if (direction !== "input" && direction !== "output") {
      throw new Error(`Canonical submodel boundary ${node.id} has malformed direction`)
    }
    const data: Record<string, unknown> = {
      ...node.data,
      _functionName: identity.functionName,
      _defaultInputName: identity.defaultInputName,
      _sourceHandleInputNames: { ...identity.sourceHandleInputNames },
    }
    if (identity.configReference === undefined) delete data._configReference
    else data._configReference = identity.configReference
    const position = previousPositions.get(node.id)
    return {
      ...node,
      ...(position ? { position } : {}),
      data,
    }
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
  const inputIdentity = boundaryIdentity(
    state.viewNodes,
    "input",
    inputPorts.map((port) => port.portId),
  )
  const outputIdentity = boundaryIdentity(state.viewNodes, "output", [])
  const nextDefinition: SubmodelDefinition = {
    ...definition,
    graph: {
      ...definition.graph,
      nodes: children.nodes,
      edges: children.edges,
    },
    inputPorts,
    outputPorts,
    _inputPortInputNames: { ...inputIdentity.sourceHandleInputNames },
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
  const viewNodes = preserveBoundaryProjection(
    state.viewNodes,
    view.nodes,
    inputIdentity,
    outputIdentity,
  )
  const viewEdges = attachEditorEdgeIdentities(view.edges, viewNodes)
  return {
    submodelName: state.submodelName,
    instanceId: state.instanceId,
    definitionId: state.definitionId,
    viewNodes,
    viewEdges,
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

function nextInputPortId(definition: SubmodelDefinition): string {
  const occupied = new Set([
    ...definition.inputPorts.map((port) => port.portId),
    ...definition.outputPorts.map((port) => port.portId),
  ])
  let index = 1
  while (occupied.has(`input_${index}`)) index += 1
  return `input_${index}`
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

export interface CanonicalSubmodelInputConnectionState {
  nodes: Node[]
  edges: PipelineEdge[]
  submodels: Record<string, unknown>
}

export interface CanonicalSubmodelInputConnectionResult
  extends CanonicalSubmodelInputConnectionState {
  portId: string
}

/**
 * Bind a parent frame through a submodel's one visible input socket.
 * Existing frame identities reuse their stable public port on any occurrence;
 * only the owner may extend the definition with a genuinely new identity. The
 * committed edge always uses the canonical named handle, so the interaction-
 * only generic id never enters graph state.
 */
export function connectCanonicalSubmodelInputFromParentConnection(
  state: CanonicalSubmodelInputConnectionState,
  connection: Connection,
): CanonicalSubmodelInputConnectionResult | null {
  if (connection.targetHandle !== SUBMODEL_INPUT_HANDLE) return null
  if (!connection.source || !connection.target) {
    throw new Error("Submodel input connection requires complete endpoints")
  }

  const target = state.nodes.find((node) => node.id === connection.target)
  if (!target || target.data.nodeType !== "submodel") {
    throw new Error("The generic submodel input handle must target a submodel")
  }
  const config = target.data.config
  if (!isSubmodelInstanceConfig(config)) {
    throw new Error(`Submodel instance ${target.id} has malformed canonical identity`)
  }
  const definitionValue = state.submodels[config.definitionId]
  if (!isSubmodelDefinition(definitionValue, config.definitionId)) {
    throw new Error(`Submodel definition ${config.definitionId} is missing or malformed`)
  }
  const source = state.nodes.find((node) => node.id === connection.source)
  if (!source) throw new Error(`Input source ${connection.source} is missing`)
  const probe = appEdge({
    source: connection.source,
    sourceHandle: connection.sourceHandle ?? null,
    target: connection.target,
    targetHandle: null,
  })
  const inputName = edgeInputName(
    probe,
    source as unknown as Parameters<typeof edgeInputName>[1],
    state.submodels,
  )
  if (inputName === UNRESOLVED_INPUT_NAME) {
    throw new Error("The incoming frame has no authoritative identity")
  }
  const definition = definitionValue
  let portId = submodelInputPortIdForName(definition, inputName)
  let nextSubmodels = state.submodels
  if (portId === null) {
    if (config.instanceOf !== undefined) {
      throw new Error("New public inputs can only be added through the definition owner")
    }
    portId = nextInputPortId(definition)
    const nextDefinition: SubmodelDefinition = {
      ...definition,
      inputPorts: [
        ...definition.inputPorts,
        {
          portId,
          label: inputName,
          targets: [],
        },
      ],
      _inputPortInputNames: {
        ...definition._inputPortInputNames,
        [portId]: inputName,
      },
    }
    nextSubmodels = {
      ...state.submodels,
      [config.definitionId]: nextDefinition,
    }
  }

  const canonicalTargetHandle = `in__${portId}`
  if (state.edges.some(
    (candidate) => candidate.target === target.id
      && candidate.targetHandle === canonicalTargetHandle,
  )) {
    throw new Error(`Public input "${inputName}" is already bound on ${target.id}`)
  }
  const edge = attachEditorEdgeIdentities([
    appEdge({
      source: connection.source,
      sourceHandle: connection.sourceHandle ?? null,
      target: connection.target,
      targetHandle: canonicalTargetHandle,
    }),
  ], state.nodes)[0]
  if (!edge || edge.data?._inputName !== inputName) {
    throw new Error(`Public input ${portId} could not retain its authoritative frame identity`)
  }
  return {
    portId,
    nodes: state.nodes,
    edges: [...state.edges, edge],
    submodels: nextSubmodels,
  }
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
