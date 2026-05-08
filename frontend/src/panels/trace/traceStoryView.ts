import type {
  ModelScoreNodeDetail,
  OptimiserApplyNodeDetail,
  TraceNodeDetail,
  TraceResult,
  TraceStep,
} from "../../types/trace"
import { isSourceLikeTraceStep } from "./traceGrouping"
import { asBandingDetail, bandingRows } from "../../trace/bandingRows"
import { asModelScoreDetail, modelScoreFeatureColumns } from "../../trace/modelScoreHelpers"
import {
  asRatingStepCombinedOutputs,
  asRatingStepTables,
  hasRichRatingStepDetail,
} from "../../trace/ratingStepHelpers"

export { hasRichRatingStepDetail }

function asOptimiserApplyDetail(detail: TraceNodeDetail): OptimiserApplyNodeDetail {
  return detail as OptimiserApplyNodeDetail
}

export function hasRichBandingDetail(step: TraceStep | null | undefined): boolean {
  const detail = step?.node_detail
  return detail?.detail_type === "banding"
}

export function hasRichModelScoreDetail(step: TraceStep | null | undefined): boolean {
  const detail = step?.node_detail as ModelScoreNodeDetail | null | undefined
  return Boolean(
    detail?.detail_type === "model_score" &&
    (
      "prediction_value" in detail ||
      Array.isArray(detail.feature_columns) ||
      detail.feature_values != null ||
      detail.explanation != null
    ),
  )
}

export function hasRichOptimiserApplyDetail(step: TraceStep | null | undefined): boolean {
  const detail = step?.node_detail as OptimiserApplyNodeDetail | null | undefined
  return Boolean(
    detail?.detail_type === "optimiser_apply" &&
    (
      detail.mode === "online" ||
      detail.mode === "ratebook"
    ),
  )
}

export function hasPrimaryNodeDetail(step: TraceStep | null | undefined): boolean {
  const detailType = step?.node_detail?.detail_type
  return hasRichModelScoreDetail(step) ||
    hasRichOptimiserApplyDetail(step) ||
    hasRichRatingStepDetail(step) ||
    hasRichBandingDetail(step) ||
    detailType === "scenario_expander" ||
    detailType === "live_switch" ||
    detailType === "rate_table_lookup"
}

export function isOptimiserApplyErrorDetail(
  detail: OptimiserApplyNodeDetail,
): detail is Extract<OptimiserApplyNodeDetail, { status: "error" }> {
  return detail.status === "error"
}

export function traceStoryKey(trace: TraceResult): string {
  return `${trace.target_node_id}\u0000${trace.row_index}\u0000${trace.column ?? ""}`
}

export function stepCreatesOrModifiesColumn(step: TraceStep, column: string): boolean {
  const diff = step.schema_diff
  return diff.columns_added.includes(column) || diff.columns_modified.includes(column)
}

export function isBulkSourceOriginStep(step: TraceStep): boolean {
  const diff = step.schema_diff
  return isSourceLikeTraceStep(step) &&
    step.node_detail == null &&
    diff.columns_added.length > 0 &&
    diff.columns_modified.length === 0 &&
    diff.columns_removed.length === 0 &&
    diff.columns_passed.length === 0
}

export function shouldDefaultExpandStep(step: TraceStep, targetStep: TraceStep | null): boolean {
  if (targetStep?.node_id === step.node_id) return true
  return !isBulkSourceOriginStep(step)
}

export function hasStructuredDependencyDetail(detail: TraceNodeDetail | null | undefined): boolean {
  return detail?.detail_type === "model_score" ||
    detail?.detail_type === "rating_step" ||
    detail?.detail_type === "banding" ||
    detail?.detail_type === "optimiser_apply"
}

export function directInputSourceNodeNames(step: TraceStep | null): Set<string> {
  const names = new Set<string>()
  if (!step?.calculation?.input_sources || hasStructuredDependencyDetail(step.node_detail)) return names

  for (const source of Object.values(step.calculation.input_sources)) {
    if (source?.node_name) {
      names.add(source.node_name)
    }
  }

  return names
}

export function targetStepDependencyColumns(step: TraceStep, tracedColumn: string | null): Set<string> {
  const dependencyColumns = new Set<string>()
  const detail = step.node_detail

  if (!hasStructuredDependencyDetail(detail)) {
    for (const col of step.expression?.referenced_columns ?? []) {
      dependencyColumns.add(col)
    }
    for (const col of Object.keys(step.calculation?.input_values ?? {})) {
      if (col !== tracedColumn) {
        dependencyColumns.add(col)
      }
    }
  }

  if (detail?.detail_type === "model_score") {
    const modelDetail = asModelScoreDetail(detail)
    for (const feature of modelScoreFeatureColumns(modelDetail)) {
      dependencyColumns.add(feature)
    }
    for (const feature of Object.keys(modelDetail.feature_values ?? {})) {
      dependencyColumns.add(feature)
    }
  }

  if (detail?.detail_type === "rating_step") {
    for (const table of asRatingStepTables(detail)) {
      for (const factor of table.factors ?? []) {
        dependencyColumns.add(factor.column)
      }
      for (const lookupColumn of Object.keys(table.lookup_keys ?? {})) {
        dependencyColumns.add(lookupColumn)
      }
    }
    for (const combined of asRatingStepCombinedOutputs(detail)) {
      for (const column of Object.keys(combined.input_values ?? {})) {
        dependencyColumns.add(column)
      }
    }
    for (const lookupColumn of Object.keys(detail.lookup_keys ?? {})) {
      dependencyColumns.add(lookupColumn)
    }
  }

  if (detail?.detail_type === "banding") {
    for (const row of bandingRows(asBandingDetail(detail))) {
      if (row.inputColumn) {
        dependencyColumns.add(row.inputColumn)
      }
    }
  }

  if (detail?.detail_type === "optimiser_apply") {
    const optimiserDetail = asOptimiserApplyDetail(detail)
    if (!isOptimiserApplyErrorDetail(optimiserDetail)) {
      if (optimiserDetail.mode === "ratebook" && Array.isArray(optimiserDetail.factors)) {
        for (const factor of optimiserDetail.factors) {
          if (factor.name) {
            dependencyColumns.add(factor.name)
          }
        }
      }
      if (optimiserDetail.mode === "online") {
        if (optimiserDetail.objective_column) {
          dependencyColumns.add(optimiserDetail.objective_column)
        }
        if (optimiserDetail.quote_id_column) {
          dependencyColumns.add(optimiserDetail.quote_id_column)
        }
        if (optimiserDetail.scenario_index_column) {
          dependencyColumns.add(optimiserDetail.scenario_index_column)
        }
        if (optimiserDetail.scenario_value_column) {
          dependencyColumns.add(optimiserDetail.scenario_value_column)
        }
        for (const constraint of Object.keys(optimiserDetail.constraints ?? {})) {
          dependencyColumns.add(constraint)
        }
      }
    }
  }

  if (dependencyColumns.size === 0 && !detail && !step.expression && !step.calculation) {
    if (tracedColumn && step.schema_diff.columns_modified.includes(tracedColumn)) {
      dependencyColumns.add(tracedColumn)
    }
    for (const passedColumn of step.schema_diff.columns_passed) {
      dependencyColumns.add(passedColumn)
    }
  }

  return dependencyColumns
}

export function targetDependencyStepIds(steps: TraceStep[], targetStep: TraceStep | null, column: string | null): Set<string> {
  const ids = new Set<string>()
  if (!targetStep) return ids

  const targetIndex = steps.findIndex((step) => step.node_id === targetStep.node_id)
  const dependencyColumns = targetStepDependencyColumns(targetStep, column)
  if (dependencyColumns.size === 0) return ids

  steps.forEach((step, index) => {
    if (step.node_id === targetStep.node_id) return
    if (targetIndex >= 0 && index > targetIndex) return
    for (const dependencyColumn of dependencyColumns) {
      if (stepCreatesOrModifiesColumn(step, dependencyColumn)) {
        ids.add(step.node_id)
        return
      }
    }
  })

  return ids
}

export function directInputSourceStepIds(steps: TraceStep[], targetStep: TraceStep | null): Set<string> {
  const ids = new Set<string>()
  const sourceNodeNames = directInputSourceNodeNames(targetStep)
  if (sourceNodeNames.size === 0) return ids

  for (const step of steps) {
    if (sourceNodeNames.has(step.node_name)) {
      ids.add(step.node_id)
    }
  }

  return ids
}

export function defaultExpandedStepIds(steps: TraceStep[], targetStep: TraceStep | null, column: string | null): Set<string> {
  const ids = new Set<string>()
  if (!targetStep) return ids

  if (shouldDefaultExpandStep(targetStep, targetStep)) {
    ids.add(targetStep.node_id)
  }

  const targetIndex = steps.findIndex((step) => step.node_id === targetStep.node_id)
  const sourceNodeNames = directInputSourceNodeNames(targetStep)
  const dependencyColumns = targetStepDependencyColumns(targetStep, column)

  steps.forEach((step, index) => {
    if (step.node_id === targetStep.node_id) return
    if (targetIndex >= 0 && index > targetIndex) return
    if (sourceNodeNames.has(step.node_name)) {
      if (shouldDefaultExpandStep(step, targetStep)) {
        ids.add(step.node_id)
      }
      return
    }
    for (const dependencyColumn of dependencyColumns) {
      if (stepCreatesOrModifiesColumn(step, dependencyColumn)) {
        if (shouldDefaultExpandStep(step, targetStep)) {
          ids.add(step.node_id)
        }
        return
      }
    }
  })

  return ids
}

export function traceStoryPreserveStepIds(steps: TraceStep[], targetStep: TraceStep | null, column: string | null): Set<string> {
  const ids = targetDependencyStepIds(steps, targetStep, column)
  for (const sourceStepId of directInputSourceStepIds(steps, targetStep)) {
    ids.add(sourceStepId)
  }
  if (targetStep) {
    ids.add(targetStep.node_id)
  }
  return ids
}
