/** Shared node data shape used across hooks and components. */

import type { Node } from "@xyflow/react"
import type { NodeTypeValue } from "../utils/nodeTypes"

export interface ColumnInfo {
  name: string
  dtype: string
}

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
  /** Node execution status — set by useTracing */
  _status?: "ok" | "error" | "running"
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

export interface SubmodelNodeData extends HauteNodeData {
  config?: {
    file?: string
    childNodeIds?: string[]
    inputPorts?: string[]
    outputPorts?: string[]
  }
}
export type SubmodelFlowNode = Node<SubmodelNodeData>

export interface SubmodelPortData extends Record<string, unknown> {
  label: string
  portDirection: "input" | "output"
  portName: string
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
