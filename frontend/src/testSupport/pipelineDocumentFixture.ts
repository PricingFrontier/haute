import type { Edge, Node } from "@xyflow/react"

import type {
  PipelineDiagnostic,
  PipelineDocumentCapabilities,
  PipelineEditorDocument,
  PipelineElementAvailability,
  PipelineLoadStatus,
  RecoveryEdge,
  RecoveryGraph,
  RecoveryNode,
  RecoverySubmodel,
} from "../types/pipelineDocument"

interface CanonicalGraphFixture {
  nodes?: Node[]
  edges?: Edge[]
  submodels?: Record<string, unknown> | null
}

export interface PipelineDocumentFixture extends CanonicalGraphFixture {
  load_status?: PipelineLoadStatus
  pipeline_name?: string | null
  pipeline_description?: string | null
  preamble?: string | null
  preserved_blocks?: string[]
  source_file?: string
  source_revision?: string | null
  source_text?: string
  sources?: string[]
  active_source?: string | null
  source_selection_trusted?: boolean
  has_authored_content?: boolean
  diagnostics?: PipelineDiagnostic[]
  diagnostics_omitted?: number
  capabilities?: Partial<PipelineDocumentCapabilities>
  recoveryNodes?: RecoveryNode[]
  recoveryEdges?: RecoveryEdge[]
  recoverySubmodels?: Record<string, RecoverySubmodel> | null
}

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

function nullableString(value: unknown): string | null {
  return typeof value === "string" ? value : null
}

function nodeAvailability(node: Node): PipelineElementAvailability {
  const availability = record(node.data)._loadAvailability
  return availability === "unavailable" || availability === "blocked" ? availability : "ready"
}

function recoveryNode(node: Node, index: number): RecoveryNode {
  const data = record(node.data)
  const nodeType =
    typeof data.nodeType === "string"
      ? data.nodeType
      : typeof node.type === "string" && node.type !== "pipelineNode"
        ? node.type
        : "polars"
  const position = node.position ?? { x: index * 300, y: 0 }
  const functionName = typeof data._functionName === "string" ? data._functionName : node.id
  const explicitDefaultInputName = data._defaultInputName
  const defaultInputName =
    explicitDefaultInputName === null || typeof explicitDefaultInputName === "string"
      ? explicitDefaultInputName
      : nodeType === "apiInput" || nodeType === "submodel" || nodeType === "submodelPort"
        ? null
        : functionName
  return {
    recovery_id: node.id,
    authored_id: typeof data._authoredId === "string" ? data._authoredId : node.id,
    label: typeof data.label === "string" ? data.label : node.id,
    decorator_name:
      typeof data._authoredDecorator === "string" ? data._authoredDecorator : nodeType,
    node_type: node.type === "unavailablePipelineNode" ? null : nodeType,
    description: typeof data.description === "string" ? data.description : "",
    availability: nodeAvailability(node),
    display_position: { x: position.x, y: position.y },
    config: structuredClone(record(data.config)) as RecoveryNode["config"],
    config_reference: nullableString(data._configReference),
    function_name: functionName,
    default_input_name: defaultInputName,
    source_handle_input_names: structuredClone(record(data._sourceHandleInputNames)) as Record<string, string>,
    source_file: nullableString(data._sourceFile),
    source_span: null,
    diagnostic_ids: Array.isArray(data._loadDiagnosticIds)
      ? data._loadDiagnosticIds.filter((item): item is string => typeof item === "string")
      : [],
    blocking_path: Array.isArray(data._loadBlockingPath)
      ? data._loadBlockingPath.filter((item): item is string => typeof item === "string")
      : [],
  }
}

function recoveryEdge(edge: Edge, nodesById: ReadonlyMap<string, RecoveryNode>): RecoveryEdge {
  const data = record(edge.data)
  const pipelineEdge = edge as Edge & { sourcePort?: string | null; targetPort?: string | null }
  const availability = data._loadAvailability
  const sourceNode = nodesById.get(edge.source)
  const sourceHandle = edge.sourceHandle ?? null
  const suppliedInputName = nullableString(data._inputName)
    ?? (sourceHandle === null
      ? sourceNode?.default_input_name ?? null
      : sourceNode?.source_handle_input_names[sourceHandle] ?? null)
  return {
    recovery_id: edge.id,
    source_recovery_id: edge.source,
    target_recovery_id: edge.target,
    source_authored_id:
      typeof data._sourceAuthoredId === "string" ? data._sourceAuthoredId : edge.source,
    target_authored_id:
      typeof data._targetAuthoredId === "string" ? data._targetAuthoredId : edge.target,
    source_handle: sourceHandle,
    target_handle: edge.targetHandle ?? null,
    source_port: pipelineEdge.sourcePort ?? null,
    target_port: pipelineEdge.targetPort ?? null,
    input_name: suppliedInputName ?? edge.source,
    availability:
      availability === "unavailable" || availability === "blocked" ? availability : "ready",
    source_span: null,
    diagnostic_ids: Array.isArray(data._loadDiagnosticIds)
      ? data._loadDiagnosticIds.filter((item): item is string => typeof item === "string")
      : [],
    blocking_path: Array.isArray(data._loadBlockingPath)
      ? data._loadBlockingPath.filter((item): item is string => typeof item === "string")
      : [],
  }
}

function recoverySubmodel(id: string, value: unknown): RecoverySubmodel {
  const definition = record(value)
  const graph = record(definition.graph)
  const inputPorts = Array.isArray(definition.inputPorts)
    ? structuredClone(definition.inputPorts)
    : []
  return {
    definition_id:
      typeof definition.definitionId === "string" ? definition.definitionId : id,
    file: typeof definition.file === "string" ? definition.file : `${id}.py`,
    availability: "ready",
    diagnostic_ids: [],
    graph: recoveryGraph({
      nodes: Array.isArray(graph.nodes) ? (graph.nodes as Node[]) : [],
      edges: Array.isArray(graph.edges) ? (graph.edges as Edge[]) : [],
      submodels: record(graph.submodels),
    }),
    input_ports: inputPorts,
    output_ports: Array.isArray(definition.outputPorts)
      ? structuredClone(definition.outputPorts)
      : [],
  }
}

function recoveryGraph(fixture: CanonicalGraphFixture): RecoveryGraph {
  const nodes = (fixture.nodes ?? []).map(recoveryNode)
  const nodesById = new Map(nodes.map((node) => [node.recovery_id, node]))
  return {
    nodes,
    edges: (fixture.edges ?? []).map((edge) => recoveryEdge(edge, nodesById)),
    unresolved_connections: [],
    submodels:
      fixture.submodels === null
        ? null
        : Object.fromEntries(
          Object.entries(fixture.submodels ?? {}).map(([id, value]) => [
            id,
            recoverySubmodel(id, value),
          ]),
        ),
  }
}

function capabilitiesFor(
  status: PipelineLoadStatus,
  trusted: boolean,
  overrides: Partial<PipelineDocumentCapabilities> | undefined,
): PipelineDocumentCapabilities {
  const ready = status === "ready"
  return {
    can_mutate: ready,
    can_save: ready,
    can_execute: ready,
    can_preview: ready && trusted,
    can_manage_submodels: ready,
    can_repair: status === "degraded",
    reserved_api_input_frame_labels: [],
    ...overrides,
  }
}

/** Convert compact canonical graph fixtures into the editor-load wire contract. */
export function makePipelineEditorDocument(
  fixture: PipelineDocumentFixture = {},
): PipelineEditorDocument {
  const status = fixture.load_status ?? "ready"
  const trusted = fixture.source_selection_trusted ?? true
  const graph = recoveryGraph(fixture)
  const nodes = fixture.recoveryNodes ?? graph.nodes
  const edges = fixture.recoveryEdges ?? graph.edges
  const submodels = fixture.recoverySubmodels === undefined
    ? graph.submodels
    : fixture.recoverySubmodels
  return {
    document_kind: "haute.pipeline_editor_document",
    schema_version: 1,
    load_status: status,
    pipeline_name: fixture.pipeline_name ?? null,
    pipeline_description: fixture.pipeline_description ?? null,
    preamble: fixture.preamble ?? "",
    preserved_blocks: fixture.preserved_blocks ?? [],
    source_file: fixture.source_file ?? "",
    source_revision: fixture.source_revision ?? "revision-test",
    source_text: fixture.source_text ?? "",
    sources: fixture.sources ?? (trusted ? ["live"] : []),
    active_source: fixture.active_source === undefined ? (trusted ? "live" : null) : fixture.active_source,
    source_selection_trusted: trusted,
    has_authored_content:
      fixture.has_authored_content ?? (nodes.length > 0 || fixture.source_text !== undefined),
    nodes,
    edges,
    unresolved_connections: [],
    submodels,
    diagnostics: fixture.diagnostics ?? [],
    diagnostics_omitted: fixture.diagnostics_omitted ?? 0,
    capabilities: capabilitiesFor(status, trusted, fixture.capabilities),
  }
}
