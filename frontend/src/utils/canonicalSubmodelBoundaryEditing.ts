import type { Connection, Edge, Node, XYPosition } from "@xyflow/react"
import {
  isSubmodelDefinition,
  isSubmodelInstanceConfig,
  type PipelineEdge,
  type SubmodelBoundaryEdgeData,
  type SubmodelDefinition,
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
  UNRESOLVED_INPUT_NAME,
} from "./apiInputPorts"
import { withEdgeIdentity } from "./edgeIds"
import { appEdge } from "./flowElements"
import { buildSubmodelViewGraph } from "./submodelViewGraph"
import type {
  SubmodelBoundaryEditResult,
  SubmodelBoundaryEditState,
} from "./submodelBoundaryEditing"
import { isPlainObject } from "../types/guards"

export type CanonicalSubmodelBoundaryEditState = SubmodelBoundaryEditState

function boundaryInfo(edge: Edge): SubmodelBoundaryEdgeData["submodelBoundary"] | null {
  if (!isPlainObject(edge.data) || !isPlainObject(edge.data.submodelBoundary)) return null
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

function definitionFor(state: CanonicalSubmodelBoundaryEditState): SubmodelDefinition {
  const value = state.submodels[state.definitionId]
  if (!isSubmodelDefinition(value, state.definitionId)) {
    throw new Error(`Submodel definition ${state.definitionId} is missing or malformed`)
  }
  return value
}

function boundaryPortName(info: SubmodelBoundaryEdgeData["submodelBoundary"], direction: "input" | "output"): string {
  if (info.direction !== direction || typeof info.name !== "string" || info.name.length === 0) {
    throw new Error(`Canonical ${direction} boundary edge is missing its public port name`)
  }
  return info.name
}

/** The occurrences of the open definition in the pipeline, the open one included. */
function occurrenceIds(state: CanonicalSubmodelBoundaryEditState): Set<string> {
  return new Set([state.instanceId, ...state.parentNodes.flatMap((node) => {
    const config = node.data.config
    return node.data.nodeType === "submodel"
      && isSubmodelInstanceConfig(config)
      && config.definition_id === state.definitionId
      ? [node.id]
      : []
  })])
}

/**
 * The inputs of `after`, retiring each one `before` routed that `after` routes nowhere: an
 * input whose last route goes is retired, while one never routed (a port the pipeline
 * created, waiting for its route) stays.
 */
function retiringUnroutedInputs(
  before: readonly SubmodelInputPort[],
  after: readonly SubmodelInputPort[],
): SubmodelInputPort[] {
  const routed = new Set(before.filter((port) => port.targets.length > 0).map((port) => port.name))
  return after.filter((port) => port.targets.length > 0 || !routed.has(port.name))
}

function changedPorts(
  definition: SubmodelDefinition,
  inputPorts: SubmodelInputPort[],
  outputPorts: SubmodelOutputPort[],
): Map<string, { direction: "input" | "output"; name: string }> {
  const changed = new Map<string, { direction: "input" | "output"; name: string }>()
  const nextInputs = new Set(inputPorts.map((port) => port.name))
  for (const oldPort of definition.inputPorts) {
    if (!nextInputs.has(oldPort.name)) {
      changed.set(oldPort.name, { direction: "input", name: oldPort.name })
    }
  }
  const nextOutputs = new Set(outputPorts.map((port) => port.name))
  for (const oldPort of definition.outputPorts) {
    if (!nextOutputs.has(oldPort.name)) {
      changed.set(oldPort.name, { direction: "output", name: oldPort.name })
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
      || config.definition_id !== state.definitionId
    ) continue
    const used = new Set<string>()
    for (const edge of state.parentEdges) {
      if (edge.target === node.id && typeof edge.targetHandle === "string" && edge.targetHandle.startsWith("in__")) {
        const name = edge.targetHandle.slice("in__".length)
        const port = changed.get(name)
        if (port?.direction === "input") used.add(name)
      }
      if (edge.source === node.id && typeof edge.sourceHandle === "string" && edge.sourceHandle.startsWith("out__")) {
        const name = edge.sourceHandle.slice("out__".length)
        const port = changed.get(name)
        if (port?.direction === "output") used.add(name)
      }
    }
    if (used.size === 0) continue
    const label = config.alias
    const bindings = [...used].map((name) => {
      const port = changed.get(name)!
      return `${port.direction} ${port.name}`
    })
    affected.push(`${label} (${node.id}): ${bindings.join(", ")}`)
  }
  if (affected.length > 0) {
    throw new Error(
      `Cannot change public ports that are bound by shared instances: ${affected.join("; ")}`,
    )
  }
}

/** The cards keep where the user put them; everything else is the projection's. */
function preserveBoundaryPositions(previous: Node[], next: Node[]): Node[] {
  const previousPositions = new Map(
    previous
      .filter((node) => node.type === "submodelPort")
      .map((node) => [node.id, node.position]),
  )
  return next.map((node) => {
    if (node.type !== "submodelPort") return node
    const position = previousPositions.get(node.id)
    return position ? { ...node, position } : node
  })
}

/**
 * The document a boundary command leaves (submodels low-level, "Boundary commands"): the
 * open definition as `definition`, the pipeline's own edges as `parentEdges`, refused whole
 * when a port it retires is bound by an occurrence. The view is drawn from it with the
 * canvas's own children (`canvas`), so their selection, size and annotations stay on the
 * canvas while the definition holds them as the document does, and with the cards where
 * they were.
 */
function committed(
  state: CanonicalSubmodelBoundaryEditState,
  definition: SubmodelDefinition,
  parentEdges: PipelineEdge[] = state.parentEdges,
  canvas: { nodes: Node[]; edges: Edge[] } = { nodes: state.viewNodes, edges: state.viewEdges },
): SubmodelBoundaryEditResult {
  if (!boundaryNode(canvas.nodes, "input") || !boundaryNode(canvas.nodes, "output")) {
    throw new Error("Canonical submodel view is missing its Input or Output boundary")
  }
  assertCompatibleSharedEdit(
    { ...state, parentEdges },
    definitionFor(state),
    definition.inputPorts,
    definition.outputPorts,
  )
  const children = new Set(definition.graph.nodes.map((node) => node.id))
  const internal = new Set((definition.graph.edges ?? []).map((edge) => edge.id))
  const view = buildSubmodelViewGraph({
    submodelName: state.submodelName,
    instanceId: state.instanceId,
    definition,
    childNodes: canvas.nodes.filter((node) => node.type !== "submodelPort" && children.has(node.id)),
    childEdges: canvas.edges.filter((edge) => boundaryInfo(edge) === null && internal.has(edge.id)),
    parentNodes: state.parentNodes,
    parentEdges,
  })
  return {
    submodelName: state.submodelName,
    instanceId: state.instanceId,
    definitionId: state.definitionId,
    viewNodes: preserveBoundaryPositions(canvas.nodes, view.nodes),
    viewEdges: view.edges as PipelineEdge[],
    parentNodes: state.parentNodes,
    parentEdges,
    submodels: { ...state.submodels, [state.definitionId]: definition },
  }
}

function mintPortName(definition: SubmodelDefinition, base: string): string {
  const occupied = new Set([
    ...definition.inputPorts.map((port) => port.name),
    ...definition.outputPorts.map((port) => port.name),
  ])
  let candidate = base
  let suffix = 2
  while (occupied.has(candidate)) {
    candidate = `${base}_${suffix++}`
  }
  return candidate
}

export interface CanonicalSubmodelInputConnectionState {
  nodes: Node[]
  edges: PipelineEdge[]
  submodels: Record<string, unknown>
}

export interface CanonicalSubmodelInputConnectionResult
  extends CanonicalSubmodelInputConnectionState {
  name: string
}

/** Resolve an instance's binding from public declarations, never from edge order. */
export function chooseInstanceInput(
  target: { id: string; data: { label?: unknown } },
  ports: readonly SubmodelInputPort[],
  edges: readonly { target: string; targetHandle?: string | null }[],
  inputName: string,
  chosenInput?: string,
): { name: string } | { choices: string[] } {
  const bound = new Set(edges.filter((edge) => edge.target === target.id).map((edge) => edge.targetHandle))
  const unbound = ports.filter((port) => !bound.has(`in__${port.name}`)).map((port) => port.name)
  if (unbound.length === 0) throw new Error(`Every input of ${String(target.data.label)} is connected`)
  const matching = ports.find((port) => port.name === inputName)
  if (matching) {
    if (!unbound.includes(matching.name)) throw new Error(`Public input "${matching.name}" is already bound on ${target.id}`)
    return { name: matching.name }
  }
  if (chosenInput !== undefined) {
    if (!unbound.includes(chosenInput)) throw new Error(`Public input "${chosenInput}" is not unbound on ${target.id}`)
    return { name: chosenInput }
  }
  return unbound.length === 1 ? { name: unbound[0] } : { choices: unbound }
}

/** Move an existing occurrence connection, removing a displaced binding atomically. */
export function retargetSubmodelInput(
  state: CanonicalSubmodelInputConnectionState,
  occurrenceId: string,
  portName: string,
  edgeId: string | null,
): CanonicalSubmodelInputConnectionState {
  const node = state.nodes.find((candidate) => candidate.id === occurrenceId)
  if (!node || node.data.nodeType !== "submodel" || !isSubmodelInstanceConfig(node.data.config)) {
    throw new Error(`Submodel occurrence ${occurrenceId} is missing or malformed`)
  }
  const definition = state.submodels[node.data.config.definition_id]
  if (!isSubmodelDefinition(definition, node.data.config.definition_id)
    || !definition.inputPorts.some((port) => port.name === portName)) {
    throw new Error(`Public input ${portName} is missing on ${occurrenceId}`)
  }
  if (edgeId !== null && !state.edges.some((edge) => edge.id === edgeId && edge.target === occurrenceId)) {
    throw new Error(`Connection ${edgeId} is not incoming to ${occurrenceId}`)
  }
  const handle = `in__${portName}`
  return {
    ...state,
    edges: state.edges.filter((edge) => edge.id === edgeId || edge.target !== occurrenceId || edge.targetHandle !== handle)
      .map((edge) => edge.id === edgeId ? withEdgeIdentity(edge, { targetHandle: handle }) : edge),
  }
}

/**
 * Bind a parent frame through a submodel's one visible input socket.
 * An instance selects an unbound public input; the owner may extend the
 * definition for a new incoming frame identity. The
 * committed edge always uses the canonical named handle, so the interaction-
 * only generic id never enters graph state.
 */
export function connectCanonicalSubmodelInputFromParentConnection(
  state: CanonicalSubmodelInputConnectionState,
  connection: Connection,
  chosenInput?: string,
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
  const definitionValue = state.submodels[config.definition_id]
  if (!isSubmodelDefinition(definitionValue, config.definition_id)) {
    throw new Error(`Submodel definition ${config.definition_id} is missing or malformed`)
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
  const existingPort = definition.inputPorts.find((port) => port.name === inputName)
  let name = existingPort?.name ?? null
  let nextSubmodels = state.submodels
  if (config.instance_of !== undefined) {
    const selection = chooseInstanceInput(target, definition.inputPorts, state.edges, inputName, chosenInput)
    if ("choices" in selection) throw new Error(`Choose an input of ${String(target.data.label)}: ${selection.choices.join(", ")}`)
    name = selection.name
  }
  if (name === null) {
    name = mintPortName(definition, inputName)
    const nextDefinition: SubmodelDefinition = {
      ...definition,
      inputPorts: [
        ...definition.inputPorts,
        {
          name,
          targets: [],
        },
      ],
    }
    nextSubmodels = {
      ...state.submodels,
      [config.definition_id]: nextDefinition,
    }
  }

  const canonicalTargetHandle = `in__${name}`
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
    throw new Error(`Public input ${name} could not retain its authoritative frame identity`)
  }
  return {
    name,
    nodes: state.nodes,
    edges: [...state.edges, edge],
    submodels: nextSubmodels,
  }
}

/** Connect to or from a card: route an input to a child, or declare an output. */
export function applyCanonicalSubmodelBoundaryConnection(
  state: CanonicalSubmodelBoundaryEditState,
  connection: Connection,
): SubmodelBoundaryEditResult | null {
  const input = boundaryNode(state.viewNodes, "input")
  const output = boundaryNode(state.viewNodes, "output")
  const definition = definitionFor(state)
  const childIds = new Set(definition.graph.nodes.map((node) => node.id))
  if (input && connection.source === input.id && connection.sourceHandle && connection.target) {
    if (!childIds.has(connection.target)) return null
    const port = definition.inputPorts.find((candidate) => candidate.name === connection.sourceHandle)
    if (!port) return null
    const handleId = normalizeDefaultTargetHandle(connection.targetHandle)
    if (port.targets.some((target) => target.nodeId === connection.target && (target.handleId ?? null) === handleId)) {
      return null
    }
    return committed(state, {
      ...definition,
      inputPorts: definition.inputPorts.map((candidate) => candidate === port
        ? { ...candidate, targets: [...candidate.targets, { nodeId: connection.target!, handleId }] }
        : candidate),
    })
  }

  if (output && connection.target === output.id && connection.source) {
    if (!childIds.has(connection.source)) return null
    const handleId = connection.sourceHandle ?? null
    if (definition.outputPorts.some((port) => (
      port.source.nodeId === connection.source && (port.source.handleId ?? null) === handleId
    ))) return null
    const childNode = state.viewNodes.find((node) => node.id === connection.source)
    if (!childNode) return null
    const probe = appEdge({
      source: connection.source,
      sourceHandle: handleId,
      target: output.id,
      targetHandle: null,
    })
    const outputName = edgeInputName(
      probe,
      childNode as unknown as Parameters<typeof edgeInputName>[1],
      state.submodels,
    )
    if (outputName === UNRESOLVED_INPUT_NAME) {
      throw new Error("The child frame has no authoritative identity")
    }
    return committed(state, {
      ...definition,
      outputPorts: [
        ...definition.outputPorts,
        { name: mintPortName(definition, outputName), source: { nodeId: connection.source, handleId } },
      ],
    })
  }
  return null
}

/**
 * What a removal takes away: children, and edges (boundary or internal) by id. `moves` are
 * the positions the same gesture gave surviving children (a React Flow batch can carry
 * them), written to the definition as part of the step.
 */
export interface CanonicalSubmodelRemoval {
  nodeIds?: ReadonlySet<string>
  edgeIds?: ReadonlySet<string>
  moves?: ReadonlyMap<string, XYPosition>
}

/**
 * Remove children and edges in one command. Each removed input edge's target leaves its
 * port and each removed output edge retires its port; each removed internal edge leaves the
 * definition. A removed child takes with it, read from the definition, its routes, the
 * outputs it sources and every edge touching it. An input left with no route is retired.
 * `canvas` is the canvas the view keeps its children from, when the same gesture also
 * changed them (a selection).
 */
export function removeCanonicalSubmodelElements(
  state: CanonicalSubmodelBoundaryEditState,
  { nodeIds = new Set(), edgeIds = new Set(), moves = new Map() }: CanonicalSubmodelRemoval,
  canvas?: { nodes: Node[]; edges: Edge[] },
): SubmodelBoundaryEditResult {
  const definition = definitionFor(state)
  const removedTargets = new Set<string>()
  const removedOutputs = new Set<string>()
  const removedInternal = new Set<string>()
  for (const edge of state.viewEdges) {
    if (!edgeIds.has(edge.id)) continue
    const info = boundaryInfo(edge)
    if (info?.direction === "input") {
      removedTargets.add(JSON.stringify([boundaryPortName(info, "input"), edge.target, edge.targetHandle ?? null]))
    } else if (info?.direction === "output") {
      removedOutputs.add(boundaryPortName(info, "output"))
    } else {
      removedInternal.add(edge.id)
    }
  }
  const inputPorts = retiringUnroutedInputs(
    definition.inputPorts,
    definition.inputPorts.map((port) => ({
      ...port,
      targets: port.targets.filter((target) => (
        !nodeIds.has(target.nodeId)
        && !removedTargets.has(JSON.stringify([port.name, target.nodeId, target.handleId ?? null]))
      )),
    })),
  )
  const outputPorts = definition.outputPorts.filter((port) => (
    !removedOutputs.has(port.name) && !nodeIds.has(port.source.nodeId)
  ))
  return committed(state, {
    ...definition,
    graph: {
      ...definition.graph,
      nodes: definition.graph.nodes.flatMap((node) => {
        if (nodeIds.has(node.id)) return []
        const position = moves.get(node.id)
        return [position ? { ...node, position } : node]
      }),
      edges: (definition.graph.edges ?? []).filter((edge) => (
        !removedInternal.has(edge.id) && !nodeIds.has(edge.source) && !nodeIds.has(edge.target)
      )),
    },
    inputPorts,
    outputPorts,
  }, state.parentEdges, canvas)
}

/** Remove boundary edges by id; null when none of them is one. */
export function removeCanonicalSubmodelBoundaryEdges(
  state: CanonicalSubmodelBoundaryEditState,
  edgeIds: string[],
): SubmodelBoundaryEditResult | null {
  const wanted = new Set(edgeIds)
  if (!state.viewEdges.some((edge) => wanted.has(edge.id) && boundaryInfo(edge) !== null)) return null
  return removeCanonicalSubmodelElements(state, { edgeIds: wanted })
}

/**
 * Retire a public input from the shared definition and every matching parent
 * occurrence. Unlike ordinary boundary-edge deletion, this is an explicit
 * contract change and intentionally removes existing parent bindings first.
 */
export function removeCanonicalSubmodelInputPort(
  state: CanonicalSubmodelBoundaryEditState,
  inputPortName: string,
): SubmodelBoundaryEditResult | null {
  const definition = definitionFor(state)
  if (!definition.inputPorts.some((port) => port.name === inputPortName)) return null
  const occurrences = occurrenceIds(state)
  const parentHandle = `in__${inputPortName}`
  return committed(
    state,
    { ...definition, inputPorts: definition.inputPorts.filter((port) => port.name !== inputPortName) },
    state.parentEdges.filter((edge) => !occurrences.has(edge.target) || edge.targetHandle !== parentHandle),
  )
}

/**
 * The submodels with definition `definitionId`'s public output at `from` moved to `to`.
 * Every occurrence draws its output rows in this order and a preview opens on the first;
 * edges follow their outputs by name, so nothing else changes.
 */
export function moveSubmodelOutput(
  submodels: Record<string, unknown>,
  definitionId: string,
  from: number,
  to: number,
): Record<string, unknown> {
  const definition = submodels[definitionId]
  if (!isSubmodelDefinition(definition, definitionId)) {
    throw new Error(`Submodel definition ${definitionId} is missing or malformed`)
  }
  const outputPorts = [...definition.outputPorts]
  if (outputPorts[from] === undefined || outputPorts[to] === undefined) {
    throw new Error(`Cannot move output ${from} to ${to}: ${definitionId} has ${outputPorts.length} outputs`)
  }
  const [moved] = outputPorts.splice(from, 1)
  outputPorts.splice(to, 0, moved)
  return { ...submodels, [definitionId]: { ...definition, outputPorts } }
}
