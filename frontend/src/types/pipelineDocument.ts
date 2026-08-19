import type { Node } from "@xyflow/react"

import type {
  PipelineEdge,
  SubmodelDefinition,
  SubmodelEndpoint,
  SubmodelInputPort,
  SubmodelOutputPort,
} from "./node"
import { PIPELINE_NODE_TYPES } from "./node"
import {
  expectArray,
  expectBoolean,
  expectExactKeys,
  expectNonBlankString,
  expectNullableString,
  expectNumber,
  expectPlainObject,
  expectSchemaVersionOne,
  expectString,
  expectStringLiteral,
} from "./guards"

const PARSER = "parsePipelineEditorDocument"
const AVAILABILITY = ["ready", "unavailable", "blocked"] as const
const STATUS = ["ready", "degraded", "source_only"] as const
const SEVERITY = ["warning", "error"] as const
const SCOPE = ["pipeline", "node", "edge", "submodel"] as const

type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue }

export type PipelineLoadStatus = (typeof STATUS)[number]
export type PipelineElementAvailability = (typeof AVAILABILITY)[number]

export interface SourceSpan {
  start_line: number
  start_column: number
  end_line: number
  end_column: number
}

export interface RecoveryNode {
  recovery_id: string
  authored_id: string
  label: string
  decorator_name: string
  node_type: string | null
  description: string
  availability: PipelineElementAvailability
  display_position: { x: number; y: number }
  config: Record<string, JsonValue> | null
  config_reference: string | null
  source_file: string | null
  source_span: SourceSpan | null
  diagnostic_ids: string[]
  blocking_path: string[]
}

export interface RecoveryEdge {
  recovery_id: string
  source_recovery_id: string
  target_recovery_id: string
  source_authored_id: string
  target_authored_id: string
  source_handle: string | null
  target_handle: string | null
  source_port: string | null
  target_port: string | null
  availability: PipelineElementAvailability
  source_span: SourceSpan | null
  diagnostic_ids: string[]
  blocking_path: string[]
}

export interface UnresolvedConnection {
  recovery_id: string
  source_recovery_id: string | null
  target_recovery_id: string | null
  source_authored_id: string
  target_authored_id: string
  source_handle: string | null
  target_handle: string | null
  source_port: string | null
  target_port: string | null
  source_span: SourceSpan | null
  diagnostic_ids: string[]
}

export interface RecoveryGraph {
  nodes: RecoveryNode[]
  edges: RecoveryEdge[]
  unresolved_connections: UnresolvedConnection[]
  submodels: Record<string, RecoverySubmodel> | null
}

export interface RecoverySubmodel {
  definition_id: string
  file: string
  availability: PipelineElementAvailability
  diagnostic_ids: string[]
  graph: RecoveryGraph
  input_ports: SubmodelInputPort[]
  output_ports: SubmodelOutputPort[]
}

export interface PipelineDiagnostic {
  diagnostic_id: string
  code: string
  severity: (typeof SEVERITY)[number]
  scope: (typeof SCOPE)[number]
  message: string
  element_id: string | null
  source_file: string | null
  source_span: SourceSpan | null
  remediation: string | null
  incident_id: string | null
}

export interface PipelineDocumentCapabilities {
  can_mutate: boolean
  can_save: boolean
  can_execute: boolean
  can_preview: boolean
  can_manage_submodels: boolean
  can_repair: boolean
}

export interface PipelineEditorDocument extends RecoveryGraph {
  document_kind: "haute.pipeline_editor_document"
  schema_version: 1
  load_status: PipelineLoadStatus
  pipeline_name: string | null
  pipeline_description: string | null
  preamble: string | null
  preserved_blocks: string[]
  source_file: string
  source_revision: string | null
  source_text: string
  sources: string[]
  active_source: string | null
  source_selection_trusted: boolean
  has_authored_content: boolean
  diagnostics: PipelineDiagnostic[]
  diagnostics_omitted: number
  capabilities: PipelineDocumentCapabilities
}

function exactKeys(
  object: Record<string, unknown>,
  field: string,
  expected: string[],
): void {
  expectExactKeys(PARSER, object, field, expected)
}

function stringArray(value: unknown, field: string): string[] {
  return expectArray(PARSER, value, field).map((item, index) =>
    expectString(PARSER, item, `${field}[${index}]`),
  )
}

function nullableString(
  object: Record<string, unknown>,
  key: string,
  field: string,
): string | null {
  return expectNullableString(PARSER, object[key], `${field}.${key}`)
}

function parseJson(value: unknown, field: string): JsonValue {
  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return value
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new Error(`${PARSER}: expected ${field} to be finite`)
    }
    return value
  }
  if (Array.isArray(value)) {
    return value.map((item, index) => parseJson(item, `${field}[${index}]`))
  }
  const object = expectPlainObject(PARSER, value, field)
  return Object.fromEntries(
    Object.entries(object).map(([key, item]) => [key, parseJson(item, `${field}.${key}`)]),
  )
}

function parseJsonObject(value: unknown, field: string): Record<string, JsonValue> {
  return parseJson(expectPlainObject(PARSER, value, field), field) as Record<string, JsonValue>
}

function parseSpan(value: unknown, field: string): SourceSpan | null {
  if (value === null) return null
  const object = expectPlainObject(PARSER, value, field)
  exactKeys(object, field, ["start_line", "start_column", "end_line", "end_column"])
  const result = {
    start_line: expectNumber(PARSER, object.start_line, `${field}.start_line`),
    start_column: expectNumber(PARSER, object.start_column, `${field}.start_column`),
    end_line: expectNumber(PARSER, object.end_line, `${field}.end_line`),
    end_column: expectNumber(PARSER, object.end_column, `${field}.end_column`),
  }
  const ordered =
    result.end_line > result.start_line ||
    (result.end_line === result.start_line && result.end_column >= result.start_column)
  if (
    !Object.values(result).every(Number.isInteger) ||
    result.start_line < 1 ||
    result.end_line < 1 ||
    result.start_column < 0 ||
    result.end_column < 0 ||
    !ordered
  ) {
    throw new Error(`${PARSER}: invalid ${field}`)
  }
  return result
}

function parsePosition(value: unknown, field: string): { x: number; y: number } {
  const object = expectPlainObject(PARSER, value, field)
  exactKeys(object, field, ["x", "y"])
  const x = expectNumber(PARSER, object.x, `${field}.x`)
  const y = expectNumber(PARSER, object.y, `${field}.y`)
  if (!Number.isFinite(x) || !Number.isFinite(y)) {
    throw new Error(`${PARSER}: expected ${field} coordinates to be finite`)
  }
  return { x, y }
}

function parseRecoveryNode(value: unknown, field: string): RecoveryNode {
  const object = expectPlainObject(PARSER, value, field)
  exactKeys(object, field, [
    "recovery_id",
    "authored_id",
    "label",
    "decorator_name",
    "node_type",
    "description",
    "availability",
    "display_position",
    "config",
    "config_reference",
    "source_file",
    "source_span",
    "diagnostic_ids",
    "blocking_path",
  ])
  return {
    recovery_id: expectNonBlankString(PARSER, object.recovery_id, `${field}.recovery_id`),
    authored_id: expectNonBlankString(PARSER, object.authored_id, `${field}.authored_id`),
    label: expectNonBlankString(PARSER, object.label, `${field}.label`),
    decorator_name: expectNonBlankString(PARSER, object.decorator_name, `${field}.decorator_name`),
    node_type: nullableString(object, "node_type", field),
    description: expectString(PARSER, object.description, `${field}.description`),
    availability: expectStringLiteral(
      PARSER,
      object.availability,
      `${field}.availability`,
      AVAILABILITY,
    ),
    display_position: parsePosition(object.display_position, `${field}.display_position`),
    config:
      object.config === null ? null : parseJsonObject(object.config, `${field}.config`),
    config_reference: nullableString(object, "config_reference", field),
    source_file: nullableString(object, "source_file", field),
    source_span: parseSpan(object.source_span, `${field}.source_span`),
    diagnostic_ids: stringArray(object.diagnostic_ids, `${field}.diagnostic_ids`),
    blocking_path: stringArray(object.blocking_path, `${field}.blocking_path`),
  }
}

function parseRecoveryEdge(value: unknown, field: string): RecoveryEdge {
  const object = expectPlainObject(PARSER, value, field)
  exactKeys(object, field, [
    "recovery_id",
    "source_recovery_id",
    "target_recovery_id",
    "source_authored_id",
    "target_authored_id",
    "source_handle",
    "target_handle",
    "source_port",
    "target_port",
    "availability",
    "source_span",
    "diagnostic_ids",
    "blocking_path",
  ])
  return {
    recovery_id: expectNonBlankString(PARSER, object.recovery_id, `${field}.recovery_id`),
    source_recovery_id: expectNonBlankString(
      PARSER,
      object.source_recovery_id,
      `${field}.source_recovery_id`,
    ),
    target_recovery_id: expectNonBlankString(
      PARSER,
      object.target_recovery_id,
      `${field}.target_recovery_id`,
    ),
    source_authored_id: expectNonBlankString(
      PARSER,
      object.source_authored_id,
      `${field}.source_authored_id`,
    ),
    target_authored_id: expectNonBlankString(
      PARSER,
      object.target_authored_id,
      `${field}.target_authored_id`,
    ),
    source_handle: nullableString(object, "source_handle", field),
    target_handle: nullableString(object, "target_handle", field),
    source_port: nullableString(object, "source_port", field),
    target_port: nullableString(object, "target_port", field),
    availability: expectStringLiteral(
      PARSER,
      object.availability,
      `${field}.availability`,
      AVAILABILITY,
    ),
    source_span: parseSpan(object.source_span, `${field}.source_span`),
    diagnostic_ids: stringArray(object.diagnostic_ids, `${field}.diagnostic_ids`),
    blocking_path: stringArray(object.blocking_path, `${field}.blocking_path`),
  }
}

function parseUnresolvedConnection(value: unknown, field: string): UnresolvedConnection {
  const object = expectPlainObject(PARSER, value, field)
  exactKeys(object, field, [
    "recovery_id",
    "source_recovery_id",
    "target_recovery_id",
    "source_authored_id",
    "target_authored_id",
    "source_handle",
    "target_handle",
    "source_port",
    "target_port",
    "source_span",
    "diagnostic_ids",
  ])
  return {
    recovery_id: expectNonBlankString(PARSER, object.recovery_id, `${field}.recovery_id`),
    source_recovery_id: nullableString(object, "source_recovery_id", field),
    target_recovery_id: nullableString(object, "target_recovery_id", field),
    source_authored_id: expectNonBlankString(
      PARSER,
      object.source_authored_id,
      `${field}.source_authored_id`,
    ),
    target_authored_id: expectNonBlankString(
      PARSER,
      object.target_authored_id,
      `${field}.target_authored_id`,
    ),
    source_handle: nullableString(object, "source_handle", field),
    target_handle: nullableString(object, "target_handle", field),
    source_port: nullableString(object, "source_port", field),
    target_port: nullableString(object, "target_port", field),
    source_span: parseSpan(object.source_span, `${field}.source_span`),
    diagnostic_ids: stringArray(object.diagnostic_ids, `${field}.diagnostic_ids`),
  }
}

function parseEndpoint(value: unknown, field: string): SubmodelEndpoint {
  const object = expectPlainObject(PARSER, value, field)
  exactKeys(object, field, ["nodeId", "handleId"])
  return {
    nodeId: expectNonBlankString(PARSER, object.nodeId, `${field}.nodeId`),
    handleId: nullableString(object, "handleId", field),
  }
}

function parseInputPort(value: unknown, field: string): SubmodelInputPort {
  const object = expectPlainObject(PARSER, value, field)
  exactKeys(object, field, ["portId", "label", "targets"])
  return {
    portId: expectNonBlankString(PARSER, object.portId, `${field}.portId`),
    label: expectNonBlankString(PARSER, object.label, `${field}.label`),
    targets: expectArray(PARSER, object.targets, `${field}.targets`).map((target, index) =>
      parseEndpoint(target, `${field}.targets[${index}]`),
    ),
  }
}

function parseOutputPort(value: unknown, field: string): SubmodelOutputPort {
  const object = expectPlainObject(PARSER, value, field)
  exactKeys(object, field, ["portId", "label", "source"])
  return {
    portId: expectNonBlankString(PARSER, object.portId, `${field}.portId`),
    label: expectNonBlankString(PARSER, object.label, `${field}.label`),
    source: parseEndpoint(object.source, `${field}.source`),
  }
}

function parseRecoveryGraph(value: Record<string, unknown>, field: string): RecoveryGraph {
  exactKeys(value, field, ["nodes", "edges", "unresolved_connections", "submodels"])
  const nodes = expectArray(PARSER, value.nodes, `${field}.nodes`).map((item, index) =>
    parseRecoveryNode(item, `${field}.nodes[${index}]`),
  )
  const edges = expectArray(PARSER, value.edges, `${field}.edges`).map((item, index) =>
    parseRecoveryEdge(item, `${field}.edges[${index}]`),
  )
  const unresolvedConnections = expectArray(
    PARSER,
    value.unresolved_connections,
    `${field}.unresolved_connections`,
  ).map((item, index) =>
    parseUnresolvedConnection(item, `${field}.unresolved_connections[${index}]`),
  )

  const recoveryIds = new Set<string>()
  for (const item of [...nodes, ...edges, ...unresolvedConnections]) {
    if (recoveryIds.has(item.recovery_id)) {
      throw new Error(`${PARSER}: duplicate recovery id ${item.recovery_id}`)
    }
    recoveryIds.add(item.recovery_id)
  }
  const nodeIds = new Set(nodes.map((item) => item.recovery_id))
  for (const edge of edges) {
    if (!nodeIds.has(edge.source_recovery_id) || !nodeIds.has(edge.target_recovery_id)) {
      throw new Error(
        `${PARSER}: ${field} edge ${edge.recovery_id} references a missing recovery node`,
      )
    }
  }
  for (const connection of unresolvedConnections) {
    if (
      (connection.source_recovery_id !== null && !nodeIds.has(connection.source_recovery_id)) ||
      (connection.target_recovery_id !== null && !nodeIds.has(connection.target_recovery_id))
    ) {
      throw new Error(
        `${PARSER}: ${field} unresolved connection ${connection.recovery_id} references a missing recovery node`,
      )
    }
  }

  const rawSubmodels = value.submodels
  const submodels =
    rawSubmodels === null
      ? null
      : Object.fromEntries(
        Object.entries(expectPlainObject(PARSER, rawSubmodels, `${field}.submodels`)).map(
          ([id, item]) => {
            const submodel = parseRecoverySubmodel(item, `${field}.submodels.${id}`)
            if (submodel.definition_id !== id) {
              throw new Error(
                `${PARSER}: ${field} submodel registry key ${id} does not match definition_id`,
              )
            }
            return [id, submodel]
          },
        ),
      )
  return { nodes, edges, unresolved_connections: unresolvedConnections, submodels }
}

function parseRecoverySubmodel(value: unknown, field: string): RecoverySubmodel {
  const object = expectPlainObject(PARSER, value, field)
  exactKeys(object, field, [
    "definition_id",
    "file",
    "availability",
    "diagnostic_ids",
    "graph",
    "input_ports",
    "output_ports",
  ])
  return {
    definition_id: expectNonBlankString(
      PARSER,
      object.definition_id,
      `${field}.definition_id`,
    ),
    file: expectNonBlankString(PARSER, object.file, `${field}.file`),
    availability: expectStringLiteral(
      PARSER,
      object.availability,
      `${field}.availability`,
      AVAILABILITY,
    ),
    diagnostic_ids: stringArray(object.diagnostic_ids, `${field}.diagnostic_ids`),
    graph: parseRecoveryGraph(
      expectPlainObject(PARSER, object.graph, `${field}.graph`),
      `${field}.graph`,
    ),
    input_ports: expectArray(PARSER, object.input_ports, `${field}.input_ports`).map(
      (port, index) => parseInputPort(port, `${field}.input_ports[${index}]`),
    ),
    output_ports: expectArray(PARSER, object.output_ports, `${field}.output_ports`).map(
      (port, index) => parseOutputPort(port, `${field}.output_ports[${index}]`),
    ),
  }
}

function parseDiagnostic(value: unknown, field: string): PipelineDiagnostic {
  const object = expectPlainObject(PARSER, value, field)
  exactKeys(object, field, [
    "diagnostic_id",
    "code",
    "severity",
    "scope",
    "message",
    "element_id",
    "source_file",
    "source_span",
    "remediation",
    "incident_id",
  ])
  return {
    diagnostic_id: expectNonBlankString(
      PARSER,
      object.diagnostic_id,
      `${field}.diagnostic_id`,
    ),
    code: expectNonBlankString(PARSER, object.code, `${field}.code`),
    severity: expectStringLiteral(PARSER, object.severity, `${field}.severity`, SEVERITY),
    scope: expectStringLiteral(PARSER, object.scope, `${field}.scope`, SCOPE),
    message: expectNonBlankString(PARSER, object.message, `${field}.message`),
    element_id: nullableString(object, "element_id", field),
    source_file: nullableString(object, "source_file", field),
    source_span: parseSpan(object.source_span, `${field}.source_span`),
    remediation: nullableString(object, "remediation", field),
    incident_id: nullableString(object, "incident_id", field),
  }
}

export function parsePipelineEditorDocument(value: unknown): PipelineEditorDocument {
  const object = expectPlainObject(PARSER, value)
  exactKeys(object, "document", [
    "document_kind",
    "schema_version",
    "load_status",
    "pipeline_name",
    "pipeline_description",
    "preamble",
    "preserved_blocks",
    "source_file",
    "source_revision",
    "source_text",
    "sources",
    "active_source",
    "source_selection_trusted",
    "has_authored_content",
    "nodes",
    "edges",
    "unresolved_connections",
    "submodels",
    "diagnostics",
    "diagnostics_omitted",
    "capabilities",
  ])
  const graph = parseRecoveryGraph(
    {
      nodes: object.nodes,
      edges: object.edges,
      unresolved_connections: object.unresolved_connections,
      submodels: object.submodels,
    },
    "document graph",
  )
  const capabilities = expectPlainObject(PARSER, object.capabilities, "document.capabilities")
  exactKeys(capabilities, "document.capabilities", [
    "can_mutate",
    "can_save",
    "can_execute",
    "can_preview",
    "can_manage_submodels",
    "can_repair",
  ])
  const diagnostics = expectArray(PARSER, object.diagnostics, "document.diagnostics").map(
    (item, index) => parseDiagnostic(item, `document.diagnostics[${index}]`),
  )
  if (new Set(diagnostics.map((item) => item.diagnostic_id)).size !== diagnostics.length) {
    throw new Error(`${PARSER}: duplicate document diagnostic id`)
  }
  const diagnosticsOmitted = expectNumber(
    PARSER,
    object.diagnostics_omitted,
    "document.diagnostics_omitted",
  )
  if (!Number.isInteger(diagnosticsOmitted) || diagnosticsOmitted < 0) {
    throw new Error(
      `${PARSER}: expected document.diagnostics_omitted to be a non-negative integer`,
    )
  }
  return {
    ...graph,
    document_kind: expectStringLiteral(
      PARSER,
      object.document_kind,
      "document.document_kind",
      ["haute.pipeline_editor_document"] as const,
    ),
    schema_version: expectSchemaVersionOne(
      PARSER,
      object.schema_version,
      "document.schema_version",
    ),
    load_status: expectStringLiteral(PARSER, object.load_status, "document.load_status", STATUS),
    pipeline_name: nullableString(object, "pipeline_name", "document"),
    pipeline_description: nullableString(object, "pipeline_description", "document"),
    preamble: nullableString(object, "preamble", "document"),
    preserved_blocks: stringArray(object.preserved_blocks, "document.preserved_blocks"),
    source_file: expectString(PARSER, object.source_file, "document.source_file"),
    source_revision: nullableString(object, "source_revision", "document"),
    source_text: expectString(PARSER, object.source_text, "document.source_text"),
    sources: stringArray(object.sources, "document.sources"),
    active_source: nullableString(object, "active_source", "document"),
    source_selection_trusted: expectBoolean(
      PARSER,
      object.source_selection_trusted,
      "document.source_selection_trusted",
    ),
    has_authored_content: expectBoolean(
      PARSER,
      object.has_authored_content,
      "document.has_authored_content",
    ),
    diagnostics,
    diagnostics_omitted: diagnosticsOmitted,
    capabilities: {
      can_mutate: expectBoolean(PARSER, capabilities.can_mutate, "capabilities.can_mutate"),
      can_save: expectBoolean(PARSER, capabilities.can_save, "capabilities.can_save"),
      can_execute: expectBoolean(PARSER, capabilities.can_execute, "capabilities.can_execute"),
      can_preview: expectBoolean(PARSER, capabilities.can_preview, "capabilities.can_preview"),
      can_manage_submodels: expectBoolean(
        PARSER,
        capabilities.can_manage_submodels,
        "capabilities.can_manage_submodels",
      ),
      can_repair: expectBoolean(PARSER, capabilities.can_repair, "capabilities.can_repair"),
    },
  }
}

export interface AdaptedPipelineEditorDocument {
  nodes: Node[]
  edges: PipelineEdge[]
  submodels: Record<string, SubmodelDefinition>
}

const supportedNodeTypes = new Set<string>(Object.values(PIPELINE_NODE_TYPES))

function adaptRecoveryGraph(
  graph: RecoveryGraph,
  includeRecoveryMetadata: boolean,
  receiver: "pipeline" | "submodel",
): AdaptedPipelineEditorDocument {
  const nodes = graph.nodes.map((node): Node => {
    const knownNodeType = node.node_type !== null && supportedNodeTypes.has(node.node_type)
    const nodeType = knownNodeType ? node.node_type! : node.decorator_name
    return {
      id: node.recovery_id,
      type: knownNodeType ? nodeType : "unavailablePipelineNode",
      position: { ...node.display_position },
      data: {
        label: node.label,
        description: node.description,
        nodeType,
        ...(node.config === null ? {} : { config: structuredClone(node.config) }),
        ...(includeRecoveryMetadata
          ? {
              _loadAvailability: node.availability,
              _loadDiagnosticIds: [...node.diagnostic_ids],
              _loadBlockingPath: [...node.blocking_path],
              _recoveryId: node.recovery_id,
              _authoredId: node.authored_id,
              _authoredDecorator: node.decorator_name,
              _authoredReceiver: receiver,
              ...(node.config_reference === null
                ? {}
                : { _configReference: node.config_reference }),
              ...(node.source_file === null ? {} : { _sourceFile: node.source_file }),
              ...(node.source_span === null
                ? {}
                : { _sourceSpan: { ...node.source_span } }),
            }
          : {}),
      },
    }
  })
  const edges = graph.edges.map((edge): PipelineEdge => ({
    id: edge.recovery_id,
    source: edge.source_recovery_id,
    target: edge.target_recovery_id,
    ...(includeRecoveryMetadata
      ? {
          ...(edge.source_handle === null ? {} : { sourceHandle: edge.source_handle }),
          ...(edge.target_handle === null ? {} : { targetHandle: edge.target_handle }),
          ...(edge.source_port === null ? {} : { sourcePort: edge.source_port }),
          ...(edge.target_port === null ? {} : { targetPort: edge.target_port }),
          data: {
            _loadAvailability: edge.availability,
            _loadDiagnosticIds: [...edge.diagnostic_ids],
            _loadBlockingPath: [...edge.blocking_path],
            _recoveryId: edge.recovery_id,
            _sourceAuthoredId: edge.source_authored_id,
            _targetAuthoredId: edge.target_authored_id,
            ...(edge.source_span === null
              ? {}
              : { _sourceSpan: { ...edge.source_span } }),
          },
        }
      : {
          sourceHandle: edge.source_handle,
          targetHandle: edge.target_handle,
          ...(edge.source_port === null ? {} : { sourcePort: edge.source_port }),
          ...(edge.target_port === null ? {} : { targetPort: edge.target_port }),
        }),
  }))
  for (const connection of graph.unresolved_connections) {
    if (connection.source_recovery_id === null || connection.target_recovery_id === null) {
      continue
    }
    edges.push({
      id: connection.recovery_id,
      source: connection.source_recovery_id,
      target: connection.target_recovery_id,
      ...(connection.source_handle === null
        ? {}
        : { sourceHandle: connection.source_handle }),
      ...(connection.target_handle === null
        ? {}
        : { targetHandle: connection.target_handle }),
      selectable: false,
      focusable: false,
      interactionWidth: 0,
      style: {
        stroke: "var(--danger)",
        strokeDasharray: "5 4",
        opacity: 0.8,
      },
      data: {
        _loadAvailability: "unavailable",
        _loadDiagnosticIds: [...connection.diagnostic_ids],
        _loadBlockingPath: [],
        _recoveryId: connection.recovery_id,
        _sourceAuthoredId: connection.source_authored_id,
        _targetAuthoredId: connection.target_authored_id,
        _unresolvedConnection: true,
        ...(connection.source_span === null
          ? {}
          : { _sourceSpan: { ...connection.source_span } }),
      },
    })
  }
  const submodels = Object.fromEntries(
    Object.entries(graph.submodels ?? {}).map(([id, submodel]) => {
      const adaptedGraph = adaptRecoveryGraph(submodel.graph, includeRecoveryMetadata, "submodel")
      return [
        id,
        {
          definitionId: submodel.definition_id,
          file: submodel.file,
          graph: { nodes: adaptedGraph.nodes, edges: adaptedGraph.edges },
          inputPorts: structuredClone(submodel.input_ports),
          outputPorts: structuredClone(submodel.output_ports),
        } satisfies SubmodelDefinition,
      ]
    }),
  )
  return { nodes, edges, submodels }
}

export function adaptPipelineEditorDocument(
  document: PipelineEditorDocument,
): AdaptedPipelineEditorDocument {
  return adaptRecoveryGraph(document, document.load_status !== "ready", "pipeline")
}
