/** Shared node data shape used across hooks and components. */

import type { Edge, Node } from "@xyflow/react"

/** Persisted node-type vocabulary shared by runtime guards and canvas metadata. */
export const PIPELINE_NODE_TYPES = {
  API_INPUT: "apiInput",
  DATA_INPUT: "dataInput",
  DATA_OUTPUT: "dataOutput",
  POLARS: "polars",
  EDGE_JOIN: "edgeJoin",
  MODEL_SCORE: "modelScore",
  BANDING: "banding",
  RATING_STEP: "ratingStep",
  OUTPUT: "output",
  EXPLORE: "explore",
  EXTERNAL_FILE: "externalFile",
  LIVE_SWITCH: "liveSwitch",
  MODELLING: "modelling",
  OPTIMISER: "optimiser",
  OPTIMISER_APPLY: "optimiserApply",
  SCENARIO_EXPANDER: "scenarioExpander",
  CONSTANT: "constant",
  SUBMODEL: "submodel",
  SUBMODEL_PORT: "submodelPort",
} as const

export type NodeTypeValue =
  typeof PIPELINE_NODE_TYPES[keyof typeof PIPELINE_NODE_TYPES]

export interface ColumnInfo {
  name: string
  dtype: string
}

/**
 * Per-node execution status shared across the API contract and node data.
 *
 * The backend emits `"ok"` / `"error"` for completed node results (see
 * `NodeResult.status` in `src/haute/executor.py`). `"running"` is a
 * client-only transient the editor sets while a trace/preview is in flight.
 * Keeping these separate lets the runtime guard fail loud on backend drift.
 */
export type BackendNodeStatus = "ok" | "error"
export type NodeStatus = BackendNodeStatus | "running"

/**
 * Base data shape for all Haute pipeline nodes.
 *
 * ReactFlow's Node.data is typed as Record<string, any>. This interface gives
 * typed access to the fields the app actually uses, avoiding scattered
 * `as Record<string, unknown>` casts.
 */
export interface HauteNodeData extends Record<string, unknown> {
  label: string
  nodeType: NodeTypeValue | string
  description?: string
  config?: Record<string, unknown>
  code?: string
  func_name?: string
  /** Runtime columns from last preview/run — set by usePipelineAPI */
  _columns?: ColumnInfo[]
  /** Full column set before selected_columns filtering — set by usePipelineAPI */
  _availableColumns?: ColumnInfo[]
  /** Schema warnings from last preview — set by usePipelineAPI */
  _schemaWarnings?: { column: string; status: string }[]
  /** Active source the column stash (_columns/_availableColumns/_schemaWarnings)
   *  was captured under — set by usePipelineAPI. A stash whose source no longer
   *  matches the active source is stale and gets invalidated, never served. */
  _columnsSource?: string
  /** Node execution status — set by useTracing */
  _status?: NodeStatus
  _traceActive?: boolean
  _traceDimmed?: boolean
  _hoverDimmed?: boolean
  _traceValue?: unknown
  _traceMotionDisabled?: boolean
  /** Diff status in the read-only comparison view (S11) — drives a ring on the
   *  CARD (same element as selection) so the highlight is consistent and the
   *  correct shape for every node type, pills included. Never set in the editor. */
  _diffStatus?: "added" | "removed" | "changed" | "moved"
}

export type PipelineNodeData = HauteNodeData
export type PipelineFlowNode = Node<PipelineNodeData>

/**
 * Persisted pipeline edge shape.
 *
 * A submodel boundary consumes sourceHandle/targetHandle to identify the
 * child node represented by the placeholder. These supplemental fields keep
 * the authored connect ports alive until the backend flattens or regenerates
 * the graph.
 */
export type PipelineEdge = Edge & {
  sourcePort?: string | null
  targetPort?: string | null
}

/** A definition-owned endpoint; never used as the parent graph handle id. */
export interface SubmodelEndpoint {
  nodeId: string
  handleId: string | null
}

export interface SubmodelInputPort {
  portId: string
  label: string
  targets: SubmodelEndpoint[]
}

export interface SubmodelOutputPort {
  portId: string
  label: string
  source: SubmodelEndpoint
}

/** Reusable submodel graph and its public boundary contract. */
export interface SubmodelDefinition {
  definitionId: string
  file: string
  graph: { nodes: Node[]; edges: Edge[] }
  inputPorts: SubmodelInputPort[]
  outputPorts: SubmodelOutputPort[]
}

const isNonBlankText = (value: unknown): value is string =>
  typeof value === "string" && value.length > 0 && value.trim() === value

export function isSubmodelEndpoint(value: unknown): value is SubmodelEndpoint {
  if (typeof value !== "object" || value === null) return false
  const endpoint = value as Partial<SubmodelEndpoint>
  return isNonBlankText(endpoint.nodeId)
    && (endpoint.handleId === null || isNonBlankText(endpoint.handleId))
}

export function isSubmodelInputPort(value: unknown): value is SubmodelInputPort {
  if (typeof value !== "object" || value === null) return false
  const port = value as Partial<SubmodelInputPort>
  return isNonBlankText(port.portId)
    && isNonBlankText(port.label)
    && Array.isArray(port.targets)
    && port.targets.length > 0
    && port.targets.every(isSubmodelEndpoint)
}

export function isSubmodelOutputPort(value: unknown): value is SubmodelOutputPort {
  if (typeof value !== "object" || value === null) return false
  const port = value as Partial<SubmodelOutputPort>
  return isNonBlankText(port.portId)
    && isNonBlankText(port.label)
    && isSubmodelEndpoint(port.source)
}

export function isSubmodelDefinition(
  value: unknown,
  expectedDefinitionId?: string,
): value is SubmodelDefinition {
  if (typeof value !== "object" || value === null) return false
  const definition = value as Partial<SubmodelDefinition>
  if (!isNonBlankText(definition.definitionId)) return false
  if (expectedDefinitionId !== undefined && definition.definitionId !== expectedDefinitionId) return false
  if (!isNonBlankText(definition.file)) return false
  if (typeof definition.graph !== "object" || definition.graph === null) return false
  if (!Array.isArray(definition.graph.nodes) || !Array.isArray(definition.graph.edges)) return false
  if (!Array.isArray(definition.inputPorts) || !definition.inputPorts.every(isSubmodelInputPort)) return false
  if (!Array.isArray(definition.outputPorts) || !definition.outputPorts.every(isSubmodelOutputPort)) return false
  const portIds = [
    ...definition.inputPorts.map((port) => port.portId),
    ...definition.outputPorts.map((port) => port.portId),
  ]
  return new Set(portIds).size === portIds.length
}

export function isSubmodelInstanceConfig(value: unknown): value is SubmodelInstanceConfig {
  if (typeof value !== "object" || value === null) return false
  const config = value as Partial<SubmodelInstanceConfig>
  return isNonBlankText(config.definitionId)
    && isNonBlankText(config.alias)
    && (config.instanceOf === undefined || isNonBlankText(config.instanceOf))
}

/** Per-node occurrence data. Definitions live in the graph registry, not here. */
export interface SubmodelInstanceConfig extends Record<string, unknown> {
  definitionId: string
  alias: string
  /** Present only on a read-only occurrence; points directly to the editable owner. */
  instanceOf?: string
}

export interface SubmodelNodeData extends HauteNodeData {
  config: SubmodelInstanceConfig
}
export type SubmodelFlowNode = Node<SubmodelNodeData>

export interface SubmodelBoundaryPort {
  id: string
  label: string
  /** Every persisted parent edge represented by this logical input frame. */
  parentEdges: PipelineEdge[]
}

export type SubmodelBoundaryEdgeData = {
  submodelBoundary: {
    direction: "input"
    parentEdges: PipelineEdge[]
    portId: string
  } | {
    direction: "output"
    parentConsumerEdges: PipelineEdge[]
    portId: string
  }
}

export interface SubmodelPortData extends Record<string, unknown> {
  label: string
  instanceId: string
  definitionId: string
  portDirection: "input" | "output"
  ports: SubmodelBoundaryPort[]
  externalNodeIds: string[]
  _traceActive?: boolean
  _traceDimmed?: boolean
  _traceMotionDisabled?: boolean
}
export type SubmodelPortFlowNode = Node<SubmodelPortData>

export type HauteNode = PipelineFlowNode | SubmodelFlowNode | SubmodelPortFlowNode

/**
 * Central boundary for parsed/API graph nodes whose data arrives through
 * React Flow's broad Record<string, unknown> type.
 */
export function nodeData<T extends HauteNodeData>(node: { data: T }): T
export function nodeData(node: { data: Record<string, unknown> }): HauteNodeData
export function nodeData(node: { data: Record<string, unknown> }): HauteNodeData {
  return node.data as HauteNodeData
}

export function effectiveNodeType(node: { type?: string | null; data: Record<string, unknown> }): string {
  const dataNodeType = node.data.nodeType
  if (typeof dataNodeType === "string" && dataNodeType.length > 0) return dataNodeType
  return typeof node.type === "string" ? node.type : ""
}
