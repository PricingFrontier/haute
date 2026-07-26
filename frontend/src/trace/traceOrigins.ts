import type { TraceStep } from "../types/trace"
import { GENERATED_COLUMN_ORIGIN_TYPES, SOURCE_ONLY_TYPES } from "../utils/nodeTypes"

export function isTraceSourceNodeType(nodeType: string | undefined): boolean {
  return Boolean(
    nodeType &&
    SOURCE_ONLY_TYPES.has(nodeType),
  )
}

export function isTraceGeneratedColumnOriginType(nodeType: string | undefined): boolean {
  return Boolean(
    nodeType &&
    GENERATED_COLUMN_ORIGIN_TYPES.has(nodeType),
  )
}

export function isTraceOriginStep(
  step: TraceStep | null | undefined,
  tracedColumn: string | null | undefined,
): step is TraceStep {
  if (!step) return false
  if (isTraceSourceNodeType(step.node_type)) return true
  return Boolean(
    tracedColumn &&
    isTraceGeneratedColumnOriginType(step.node_type) &&
    step.schema_diff.columns_added.includes(tracedColumn),
  )
}
