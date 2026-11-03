import type { Node } from "@xyflow/react"

import type {
  PipelineEditorDocument as GeneratedPipelineEditorDocument,
  RecoveryGraphSnapshot,
  RecoveryPipelineEdge,
  RecoveryPipelineNode,
  RecoverySubmodelDefinition,
  RecoveryUnresolvedConnection,
} from "../generated/api-contracts.generated"
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
  expectExactKeys,
  expectNonBlankString,
  expectNullableString,
  expectPlainObject,
} from "./guards"
import { expectGeneratedContract } from "./generatedContractValidation"
import type { GlobalConstant } from "../utils/globalConstants"

const PARSER = "parsePipelineEditorDocument"

// The document's structure is the generated `PipelineEditorDocument`. The UI reads a
// node's display position as a point, a submodel's server-open ports as the editor's
// port shapes, and a constant without a value or split as one without the key.
export type {
  PipelineDocumentCapabilities,
  PipelineNameViolation,
  PipelineNameViolationParty,
  PipelineNodeCompleteness,
  PipelineRecoveryDiagnostic as PipelineDiagnostic,
  RecoverySourceSpan as SourceSpan,
} from "../generated/api-contracts.generated"

export type PipelineLoadStatus = GeneratedPipelineEditorDocument["load_status"]
export type PipelineElementAvailability = RecoveryPipelineNode["availability"]
/** The editor setting that names a `reserved_input` or `support_input` violation's input. */
export type PipelineNameViolationBinding = NonNullable<
  GeneratedPipelineEditorDocument["name_violations"][number]["binding"]
>

export type RecoveryNode = Omit<RecoveryPipelineNode, "display_position"> & {
  display_position: { x: number; y: number }
}
export type RecoveryEdge = RecoveryPipelineEdge
export type UnresolvedConnection = RecoveryUnresolvedConnection

export interface RecoveryGraph {
  nodes: RecoveryNode[]
  edges: RecoveryEdge[]
  unresolved_connections: UnresolvedConnection[]
  submodels: Record<string, RecoverySubmodel> | null
}

export type RecoverySubmodel = Omit<
  RecoverySubmodelDefinition,
  "graph" | "input_ports" | "output_ports"
> & {
  graph: RecoveryGraph
  input_ports: SubmodelInputPort[]
  output_ports: SubmodelOutputPort[]
}

export type PipelineEditorDocument = Omit<
  GeneratedPipelineEditorDocument,
  "nodes" | "edges" | "unresolved_connections" | "submodels" | "global_constants"
> & RecoveryGraph & { global_constants: GlobalConstant[] }

type EditorDocumentValidators = typeof import("../generated/api-contracts.editor-document.validators.mjs")

let editorDocumentValidatorsLoad: Promise<EditorDocumentValidators> | null = null

/**
 * The editor-document validators, loaded once with the first document. Every reader
 * awaits this one promise, so live-sync frames keep their order.
 */
export function loadEditorDocumentValidators(): Promise<EditorDocumentValidators> {
  editorDocumentValidatorsLoad ??= import("../generated/api-contracts.editor-document.validators.mjs")
    .catch((error: unknown) => {
      editorDocumentValidatorsLoad = null
      throw error
    })
  return editorDocumentValidatorsLoad
}

function parseEndpoint(value: unknown, field: string): SubmodelEndpoint {
  const object = expectPlainObject(PARSER, value, field)
  expectExactKeys(PARSER, object, field, ["nodeId", "handleId"])
  return {
    nodeId: expectNonBlankString(PARSER, object.nodeId, `${field}.nodeId`),
    handleId: expectNullableString(PARSER, object.handleId, `${field}.handleId`),
  }
}

function parseInputPort(value: unknown, field: string): SubmodelInputPort {
  const object = expectPlainObject(PARSER, value, field)
  expectExactKeys(PARSER, object, field, ["name", "targets"])
  return {
    name: expectNonBlankString(PARSER, object.name, `${field}.name`),
    targets: expectArray(PARSER, object.targets, `${field}.targets`).map((target, index) =>
      parseEndpoint(target, `${field}.targets[${index}]`),
    ),
  }
}

function parseOutputPort(value: unknown, field: string): SubmodelOutputPort {
  const object = expectPlainObject(PARSER, value, field)
  expectExactKeys(PARSER, object, field, ["name", "source"])
  return {
    name: expectNonBlankString(PARSER, object.name, `${field}.name`),
    source: parseEndpoint(object.source, `${field}.source`),
  }
}

function displayPosition(node: RecoveryPipelineNode): { x: number; y: number } {
  const { x, y } = node.display_position
  if (x === undefined || y === undefined) {
    throw new Error(`${PARSER}: node ${node.recovery_id} display_position needs x and y`)
  }
  return { x, y }
}

// Every edge and connection of a graph names one of its nodes, every element id is
// unique, and a submodel sits under its own definition id: the canvas adapter relies
// on all three.
function recoveryGraphFromContract(graph: RecoveryGraphSnapshot, field: string): RecoveryGraph {
  const recoveryIds = new Set<string>()
  for (const item of [...graph.nodes, ...graph.edges, ...graph.unresolved_connections]) {
    if (recoveryIds.has(item.recovery_id)) {
      throw new Error(`${PARSER}: duplicate recovery id ${item.recovery_id}`)
    }
    recoveryIds.add(item.recovery_id)
  }
  const nodeIds = new Set(graph.nodes.map((item) => item.recovery_id))
  for (const edge of graph.edges) {
    if (!nodeIds.has(edge.source_recovery_id) || !nodeIds.has(edge.target_recovery_id)) {
      throw new Error(
        `${PARSER}: ${field} edge ${edge.recovery_id} references a missing recovery node`,
      )
    }
  }
  for (const connection of graph.unresolved_connections) {
    if (
      (connection.source_recovery_id !== null && !nodeIds.has(connection.source_recovery_id)) ||
      (connection.target_recovery_id !== null && !nodeIds.has(connection.target_recovery_id))
    ) {
      throw new Error(
        `${PARSER}: ${field} unresolved connection ${connection.recovery_id} references a missing recovery node`,
      )
    }
  }
  const submodels = graph.submodels === null
    ? null
    : Object.fromEntries(Object.entries(graph.submodels).map(([id, submodel]) => {
      const at = `${field}.submodels.${id}`
      if (submodel.definition_id !== id) {
        throw new Error(`${PARSER}: ${field} submodel registry key ${id} does not match definition_id`)
      }
      return [id, {
        ...submodel,
        graph: recoveryGraphFromContract(submodel.graph, `${at}.graph`),
        input_ports: submodel.input_ports.map((port, index) =>
          parseInputPort(port, `${at}.input_ports[${index}]`)),
        output_ports: submodel.output_ports.map((port, index) =>
          parseOutputPort(port, `${at}.output_ports[${index}]`)),
      } satisfies RecoverySubmodel]
    }))
  return {
    nodes: graph.nodes.map((node) => ({ ...node, display_position: displayPosition(node) })),
    edges: graph.edges,
    unresolved_connections: graph.unresolved_connections,
    submodels,
  }
}

/** A generated document as the editor reads it. */
export function editorDocumentFromContract(
  document: GeneratedPipelineEditorDocument,
): PipelineEditorDocument {
  const diagnosticIds = document.diagnostics.map((item) => item.diagnostic_id)
  if (new Set(diagnosticIds).size !== diagnosticIds.length) {
    throw new Error(`${PARSER}: duplicate document diagnostic id`)
  }
  return {
    ...document,
    ...recoveryGraphFromContract(document, "document"),
    global_constants: document.global_constants.map(({ value, by_source, ...constant }) => ({
      ...constant,
      ...(value === null ? {} : { value }),
      ...(by_source === null ? {} : { by_source }),
    })),
  }
}

/** The editor document a load, a live-sync update or a scoped node save answers. */
export async function parsePipelineEditorDocument(value: unknown): Promise<PipelineEditorDocument> {
  const { validatePipelineEditorDocument } = await loadEditorDocumentValidators()
  return editorDocumentFromContract(
    expectGeneratedContract("PipelineEditorDocument", validatePipelineEditorDocument, value),
  )
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
    if (node.node_type === PIPELINE_NODE_TYPES.SUBMODEL) {
      const alias = (node.config as { alias?: unknown } | null)?.alias
      if (node.label !== alias) {
        throw new Error(`${PARSER}: submodel node ${node.recovery_id} label must equal its alias`)
      }
      // The parser keys an occurrence by its name; a duplicate name is the one
      // case where the recovery id gains a line suffix, so compare the authored id.
      if (node.authored_id !== alias) {
        throw new Error(`${PARSER}: submodel node ${node.recovery_id} id must equal its alias`)
      }
    }
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
        editor: { ...node.editor },
        _functionName: node.function_name,
        _recoveryId: node.recovery_id,
        ...(node.source_file === null ? {} : { _sourceFile: node.source_file }),
        _defaultInputName: node.default_input_name,
        _sourceHandleInputNames: structuredClone(node.source_handle_input_names),
        ...(node.config_reference === null
          ? {}
          : { _configReference: node.config_reference }),
        ...(includeRecoveryMetadata
          ? {
              _loadAvailability: node.availability,
              _loadDiagnosticIds: [...node.diagnostic_ids],
              _loadBlockingPath: [...node.blocking_path],
              _scopedEditable: node.scoped_editable,
              _authoredId: node.authored_id,
              _authoredDecorator: node.decorator_name,
              _authoredReceiver: receiver,
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
    data: {
      ...(edge.input_name === null ? {} : { _inputName: edge.input_name }),
      ...(includeRecoveryMetadata
      ? {
          _loadAvailability: edge.availability,
          _loadDiagnosticIds: [...edge.diagnostic_ids],
          _loadBlockingPath: [...edge.blocking_path],
          _recoveryId: edge.recovery_id,
          _sourceAuthoredId: edge.source_authored_id,
          _targetAuthoredId: edge.target_authored_id,
          ...(edge.source_span === null
            ? {}
            : { _sourceSpan: { ...edge.source_span } }),
        }
      : {}),
    },
    sourceHandle: edge.source_handle,
    targetHandle: edge.target_handle,
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
