import { useState, useMemo } from "react"
import { X, ChevronDown, ChevronRight, Scan } from "lucide-react"
import type {
  BandingFactorDetail,
  BandingNodeDetail,
  ModelScoreContributionDetail,
  ModelScoreExplanationDetail,
  ModelScoreNodeDetail,
  OptimiserApplyNodeDetail,
  OptimiserApplyOnlineCandidateDetail,
  OptimiserApplyRatebookFactorDetail,
  RatingStepCombinedOutputDetail,
  RatingStepTableDetail,
  ScenarioExpanderNodeDetail,
  LiveSwitchNodeDetail,
  TraceNodeDetail,
  TraceResult,
  TraceStep,
} from "../types/trace"
import {
  nodeTypeLabels,
  nodeTypeColors,
} from "../utils/nodeTypes"
import { formatValue as _formatValue } from "../utils/formatValue"
import { formatExpression } from "../utils/formatTrace"
import PanelShell from "./PanelShell"
import CalculationHero from "../trace/CalculationHero"
import {
  TraceDetailAlert,
  TraceDetailCallout,
  TraceDetailChip,
  TraceDetailPanel,
  TraceDetailSection,
  TraceDetailTable,
  TraceDetailTableRow,
} from "../trace/TraceDetail"
import { isTraceOriginStep } from "../trace/traceOrigins"
import { CHART_COLORS } from "../theme/colors"
import {
  findTargetStep,
  collapsePassthroughs,
  isSourceLikeTraceStep,
  type CollapsedEntry,
} from "./trace/traceGrouping"

const formatValue = (v: unknown) => _formatValue(v, 2)
const traceDetailLabelStyle = { color: "var(--text-muted)", fontSize: 10 }
const traceDetailValueStyle = {
  color: "var(--text-secondary)",
  fontSize: 11,
  fontFamily: "var(--font-mono, monospace)",
}

function asRatingStepTables(detail: TraceNodeDetail): RatingStepTableDetail[] {
  return Array.isArray(detail.tables) ? detail.tables as RatingStepTableDetail[] : []
}

function asRatingStepCombinedOutputs(detail: TraceNodeDetail): RatingStepCombinedOutputDetail[] {
  return Array.isArray(detail.combined_outputs) ? detail.combined_outputs as RatingStepCombinedOutputDetail[] : []
}

function asBandingDetail(detail: TraceNodeDetail): BandingNodeDetail {
  return detail as BandingNodeDetail
}

function asModelScoreDetail(detail: TraceNodeDetail): ModelScoreNodeDetail {
  return detail as ModelScoreNodeDetail
}

function asOptimiserApplyDetail(detail: TraceNodeDetail): OptimiserApplyNodeDetail {
  return detail as OptimiserApplyNodeDetail
}

function asScenarioExpanderDetail(detail: TraceNodeDetail): ScenarioExpanderNodeDetail {
  return detail as ScenarioExpanderNodeDetail
}

function asLiveSwitchDetail(detail: TraceNodeDetail): LiveSwitchNodeDetail {
  return detail as LiveSwitchNodeDetail
}

function ratingTableStatus(table: RatingStepTableDetail): string | undefined {
  if (typeof table.status === "string" && table.status.length > 0) return table.status
  if (table.default_used) return "default"
  if (table.matched === false) return "no_match"
  if (table.matched === true) return "matched"
  return undefined
}

function formatRatingStatus(status: string): string {
  return status.replace(/_/g, " ")
}

function hasRichRatingStepDetail(step: TraceStep | null | undefined): boolean {
  const detail = step?.node_detail
  return Boolean(
    detail?.detail_type === "rating_step" &&
    (Array.isArray(detail.tables) || Array.isArray(detail.combined_outputs)),
  )
}

function hasRichBandingDetail(step: TraceStep | null | undefined): boolean {
  const detail = step?.node_detail
  return detail?.detail_type === "banding"
}

function hasRichModelScoreDetail(step: TraceStep | null | undefined): boolean {
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

function hasRichOptimiserApplyDetail(step: TraceStep | null | undefined): boolean {
  const detail = step?.node_detail as OptimiserApplyNodeDetail | null | undefined
  return Boolean(
    detail?.detail_type === "optimiser_apply" &&
    (
      detail.mode === "online" ||
      detail.mode === "ratebook"
    ),
  )
}

function hasPrimaryNodeDetail(step: TraceStep | null | undefined): boolean {
  const detailType = step?.node_detail?.detail_type
  return hasRichModelScoreDetail(step) ||
    hasRichOptimiserApplyDetail(step) ||
    hasRichRatingStepDetail(step) ||
    hasRichBandingDetail(step) ||
    detailType === "scenario_expander" ||
    detailType === "live_switch" ||
    detailType === "rate_table_lookup"
}

function isOptimiserApplyErrorDetail(
  detail: OptimiserApplyNodeDetail,
): detail is Extract<OptimiserApplyNodeDetail, { status: "error" }> {
  return detail.status === "error"
}

function hasBandingSecondaryDetail(detail: TraceNodeDetail | null | undefined): boolean {
  return detail?.detail_type === "banding" &&
    (detail.lower_bound != null || detail.upper_bound != null || detail.is_default === true)
}

interface BandingTraceRow {
  key: string
  inputColumn?: string
  outputColumn?: string
  inputValue?: unknown
  matchedBand?: unknown
  lowerBound?: unknown
  upperBound?: unknown
  lowerInclusive?: boolean | null
  upperInclusive?: boolean | null
  isDefault?: boolean
  status?: string
}

function bandingRowFromFactor(factor: BandingFactorDetail, index: number): BandingTraceRow {
  const inputColumn = factor.input_column ?? factor.column
  const outputColumn = factor.output_column
  const matchedBand = factor.matched_band ?? factor.selected_band
  return {
    key: `${outputColumn ?? "output"}-${inputColumn ?? "input"}-${index}`,
    inputColumn,
    outputColumn,
    inputValue: factor.input_value,
    matchedBand,
    lowerBound: factor.lower_bound,
    upperBound: factor.upper_bound,
    lowerInclusive: factor.lower_inclusive,
    upperInclusive: factor.upper_inclusive,
    isDefault: factor.is_default,
    status: factor.status,
  }
}

function bandingRowFromDetail(detail: BandingNodeDetail): BandingTraceRow | null {
  const inputColumn = detail.input_column ?? detail.column
  const outputColumn = detail.output_column
  const matchedBand = detail.matched_band ?? detail.selected_band
  if (
    inputColumn == null &&
    outputColumn == null &&
    detail.input_value === undefined &&
    matchedBand === undefined
  ) {
    return null
  }

  return {
    key: `${outputColumn ?? "output"}-${inputColumn ?? "input"}-summary`,
    inputColumn,
    outputColumn,
    inputValue: detail.input_value,
    matchedBand,
    lowerBound: detail.lower_bound,
    upperBound: detail.upper_bound,
    lowerInclusive: detail.lower_inclusive,
    upperInclusive: detail.upper_inclusive,
    isDefault: detail.is_default,
    status: detail.status,
  }
}

function bandingRows(detail: BandingNodeDetail): BandingTraceRow[] {
  const rows = Array.isArray(detail.factors)
    ? detail.factors.map((factor, index) => bandingRowFromFactor(factor, index))
    : []
  const summaryRow = bandingRowFromDetail(detail)
  if (!summaryRow) return rows
  if (rows.some((row) => row.outputColumn === summaryRow.outputColumn && row.inputColumn === summaryRow.inputColumn)) {
    return rows
  }
  return [summaryRow, ...rows]
}

function bandingRowsForDisplay(detail: BandingNodeDetail, tracedColumn?: string | null): BandingTraceRow[] {
  const rows = bandingRows(detail)
  if (!tracedColumn) return rows
  const tracedRow = rows.find((row) => row.outputColumn === tracedColumn)
  return tracedRow ? [tracedRow] : rows
}

function hasRenderableBandingRows(detail: TraceNodeDetail | null | undefined): boolean {
  return detail?.detail_type === "banding" && bandingRows(asBandingDetail(detail)).length > 0
}

function formatBandingTransform(row: BandingTraceRow): string {
  const source = row.inputColumn
    ? `${row.inputColumn}=${formatValue(row.inputValue)}`
    : formatValue(row.inputValue)
  return `${source} -> ${formatValue(row.matchedBand)}`
}

function formatBandingRange(row: BandingTraceRow): string | null {
  if (row.lowerBound == null && row.upperBound == null) return null
  const lower = row.lowerBound != null ? formatValue(row.lowerBound) : ""
  const upper = row.upperBound != null ? formatValue(row.upperBound) : ""
  const lowerBracket = row.lowerInclusive === false ? "(" : "["
  const upperBracket = row.upperInclusive === false ? ")" : "]"
  return `${lowerBracket}${lower}, ${upper}${upperBracket}`
}

function isComputedPlaceholder(value: string | undefined): boolean {
  return value?.trim().toLowerCase() === "computed"
}

function traceStoryKey(trace: TraceResult): string {
  return `${trace.target_node_id}\u0000${trace.row_index}\u0000${trace.column ?? ""}`
}

function stepCreatesOrModifiesColumn(step: TraceStep, column: string): boolean {
  const diff = step.schema_diff
  return diff.columns_added.includes(column) || diff.columns_modified.includes(column)
}

function isBulkSourceOriginStep(step: TraceStep): boolean {
  const diff = step.schema_diff
  return isSourceLikeTraceStep(step) &&
    step.node_detail == null &&
    diff.columns_added.length > 0 &&
    diff.columns_modified.length === 0 &&
    diff.columns_removed.length === 0 &&
    diff.columns_passed.length === 0
}

function shouldDefaultExpandStep(step: TraceStep, targetStep: TraceStep | null): boolean {
  if (targetStep?.node_id === step.node_id) return true
  return !isBulkSourceOriginStep(step)
}

function directInputSourceNodeNames(step: TraceStep | null): Set<string> {
  const names = new Set<string>()
  if (!step?.calculation?.input_sources || hasStructuredDependencyDetail(step.node_detail)) return names

  for (const source of Object.values(step.calculation.input_sources)) {
    if (source?.node_name) {
      names.add(source.node_name)
    }
  }

  return names
}

function hasStructuredDependencyDetail(detail: TraceNodeDetail | null | undefined): boolean {
  return detail?.detail_type === "model_score" ||
    detail?.detail_type === "rating_step" ||
    detail?.detail_type === "banding" ||
    detail?.detail_type === "optimiser_apply"
}

function targetStepDependencyColumns(step: TraceStep, tracedColumn: string | null): Set<string> {
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

function targetDependencyStepIds(steps: TraceStep[], targetStep: TraceStep | null, column: string | null): Set<string> {
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

function directInputSourceStepIds(steps: TraceStep[], targetStep: TraceStep | null): Set<string> {
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

function defaultExpandedStepIds(steps: TraceStep[], targetStep: TraceStep | null, column: string | null): Set<string> {
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

function traceStoryPreserveStepIds(steps: TraceStep[], targetStep: TraceStep | null, column: string | null): Set<string> {
  const ids = targetDependencyStepIds(steps, targetStep, column)
  for (const sourceStepId of directInputSourceStepIds(steps, targetStep)) {
    ids.add(sourceStepId)
  }
  if (targetStep) {
    ids.add(targetStep.node_id)
  }
  return ids
}

function modelScoreTitle(detail: ModelScoreNodeDetail): string {
  const identity = detail.model_identity
  if (!identity) return "Model Score"
  if (identity.registered_model) {
    return identity.version
      ? `Model: ${identity.registered_model} v${identity.version}`
      : `Model: ${identity.registered_model}`
  }
  if (identity.run_id) return `Model run: ${identity.run_id}`
  return identity.source_type ? `Model source: ${identity.source_type}` : "Model Score"
}

function modelScorePrediction(detail: ModelScoreNodeDetail): { hasPrediction: boolean; value: unknown } {
  if ("prediction_value" in detail) return { hasPrediction: true, value: detail.prediction_value }
  return { hasPrediction: false, value: undefined }
}

function modelScoreFeatureColumns(detail: ModelScoreNodeDetail): string[] {
  if (Array.isArray(detail.feature_columns) && detail.feature_columns.length > 0) return detail.feature_columns
  return []
}

function formatSignedValue(value: number): string {
  return `${value >= 0 ? "+" : ""}${formatValue(value)}`
}

function nextRunningTotal(total: number, contribution: number): number {
  return Number((total + contribution).toPrecision(12))
}

function resolveContributionFeatureValue(
  detail: ModelScoreNodeDetail,
  explanation: ModelScoreExplanationDetail | undefined,
  contribution: ModelScoreContributionDetail,
): { hasValue: boolean; value: unknown } {
  if (Object.prototype.hasOwnProperty.call(contribution, "feature_value")) {
    return { hasValue: true, value: contribution.feature_value }
  }
  if (
    explanation?.feature_values &&
    Object.prototype.hasOwnProperty.call(explanation.feature_values, contribution.feature)
  ) {
    return { hasValue: true, value: explanation.feature_values[contribution.feature] }
  }
  if (
    detail.feature_values &&
    Object.prototype.hasOwnProperty.call(detail.feature_values, contribution.feature)
  ) {
    return { hasValue: true, value: detail.feature_values[contribution.feature] }
  }
  return { hasValue: false, value: undefined }
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value)
}

function optimiserDisplayCandidates(
  candidates: OptimiserApplyOnlineCandidateDetail[],
  selected: OptimiserApplyOnlineCandidateDetail | undefined,
): OptimiserApplyOnlineCandidateDetail[] {
  return candidates.filter((candidate) =>
    !candidate.is_baseline || selected?.scenario_index === candidate.scenario_index
  )
}

function finiteRecordEntries(values: Record<string, unknown> | undefined): Array<[string, number]> {
  if (!values) return []
  return Object.entries(values).filter((entry): entry is [string, number] => isFiniteNumber(entry[1]))
}

function optimiserConstraintNames(
  candidates: OptimiserApplyOnlineCandidateDetail[],
  selected: OptimiserApplyOnlineCandidateDetail | undefined,
  configuredConstraints: Record<string, unknown> | undefined,
): string[] {
  const names = new Set<string>()
  for (const name of Object.keys(configuredConstraints ?? {})) names.add(name)
  for (const name of Object.keys(selected?.constraints ?? {})) names.add(name)
  for (const candidate of candidates) {
    for (const name of Object.keys(candidate.constraints ?? {})) names.add(name)
    for (const name of Object.keys(candidate.lambda_terms ?? {})) names.add(name)
  }
  return [...names]
}

function formatOptimiserRecordCell(
  values: Record<string, unknown> | undefined,
  names: string[],
  options: { signed?: boolean } = {},
): string {
  if (names.length === 0) return ""
  if (names.length === 1) {
    const value = values?.[names[0]]
    return options.signed && isFiniteNumber(value) ? formatSignedValue(value) : formatValue(value)
  }
  return names
    .map((name) => {
      const value = values?.[name]
      const formatted = options.signed && isFiniteNumber(value) ? formatSignedValue(value) : formatValue(value)
      return `${name}: ${formatted}`
    })
    .join(", ")
}

function optimiserScoreFormulaText(
  candidate: OptimiserApplyOnlineCandidateDetail,
  objectiveLabel: string,
): string {
  const lambdaTerms = finiteRecordEntries(candidate.lambda_terms)
  if (lambdaTerms.length === 0) {
    return `score = ${objectiveLabel}`
  }
  const terms = lambdaTerms
    .map(([, value]) => formatSignedValue(value))
    .join(" ")
  return `score = ${objectiveLabel} ${terms}`
}

function optimiserSelectedCandidate(
  candidates: OptimiserApplyOnlineCandidateDetail[],
  selected: OptimiserApplyOnlineCandidateDetail | null | undefined,
): OptimiserApplyOnlineCandidateDetail | undefined {
  return selected ?? candidates.find((candidate) => candidate.selected)
}

function optimiserCandidateIsSelected(
  candidate: OptimiserApplyOnlineCandidateDetail,
  selected: OptimiserApplyOnlineCandidateDetail | undefined,
): boolean {
  return candidate.selected || selected?.scenario_index === candidate.scenario_index
}

function optimiserScoreComparison(
  candidates: OptimiserApplyOnlineCandidateDetail[],
  selected: OptimiserApplyOnlineCandidateDetail | undefined,
): { rank: number; gapToNextBest?: number } | undefined {
  if (!selected || !isFiniteNumber(selected.decision_score)) return undefined
  const ranked = candidates
    .filter((candidate) => isFiniteNumber(candidate.decision_score))
    .sort((a, b) => {
      const scoreDiff = b.decision_score - a.decision_score
      return scoreDiff !== 0 ? scoreDiff : a.scenario_index - b.scenario_index
    })
  const rankIndex = ranked.findIndex((candidate) => candidate.scenario_index === selected.scenario_index)
  if (rankIndex < 0) return undefined
  const nextBest = ranked.find((candidate) => candidate.scenario_index !== selected.scenario_index)
  return {
    rank: rankIndex + 1,
    gapToNextBest: nextBest ? selected.decision_score - nextBest.decision_score : undefined,
  }
}

function optimiserCandidateXValue(candidate: OptimiserApplyOnlineCandidateDetail): number {
  return isFiniteNumber(candidate.scenario_value) ? candidate.scenario_value : candidate.scenario_index
}

type OptimiserChartCandidatePoint = {
  candidate: OptimiserApplyOnlineCandidateDetail
  xValue: number
  objectiveValue?: number
  scoreValue?: number
}

function optimiserChartPath(candidates: OptimiserApplyOnlineCandidateDetail[]): {
  points: Array<{ candidate: OptimiserApplyOnlineCandidateDetail; x: number; y: number }>
  objectivePath: string
  scorePath: string
} {
  const numericCandidates: OptimiserChartCandidatePoint[] = candidates
    .filter((candidate) => isFiniteNumber(candidate.objective) || isFiniteNumber(candidate.decision_score))
    .map((candidate) => ({
      candidate,
      xValue: optimiserCandidateXValue(candidate),
      objectiveValue: isFiniteNumber(candidate.objective) ? candidate.objective : undefined,
      scoreValue: isFiniteNumber(candidate.decision_score) ? candidate.decision_score : undefined,
    }))
    .sort((a, b) => a.xValue - b.xValue)

  if (numericCandidates.length === 0) return { points: [], objectivePath: "", scorePath: "" }

  const width = 280
  const height = 104
  const padX = 18
  const padY = 14
  const xValues = numericCandidates.map((point) => point.xValue)
  const yValues = numericCandidates.flatMap((point) => [point.objectiveValue, point.scoreValue])
    .filter(isFiniteNumber)
  const minX = Math.min(...xValues)
  const maxX = Math.max(...xValues)
  const minY = Math.min(...yValues)
  const maxY = Math.max(...yValues)
  const xSpan = maxX - minX || 1
  const ySpan = maxY - minY || 1
  const xFor = (xValue: number) => padX + ((xValue - minX) / xSpan) * (width - padX * 2)
  const yFor = (yValue: number) => height - padY - ((yValue - minY) / ySpan) * (height - padY * 2)
  const scoreCandidates = numericCandidates.filter((point): point is OptimiserChartCandidatePoint & { scoreValue: number } =>
    isFiniteNumber(point.scoreValue)
  )
  const objectiveCandidates = numericCandidates.filter((point): point is OptimiserChartCandidatePoint & { objectiveValue: number } =>
    isFiniteNumber(point.objectiveValue)
  )
  const points = scoreCandidates.map(({ candidate, xValue, scoreValue }) => ({
    candidate,
    x: xFor(xValue),
    y: yFor(scoreValue),
  }))
  const objectivePath = objectiveCandidates
    .map((point, index) => `${index === 0 ? "M" : "L"} ${xFor(point.xValue).toFixed(1)} ${yFor(point.objectiveValue).toFixed(1)}`)
    .join(" ")
  const scorePath = points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x.toFixed(1)} ${point.y.toFixed(1)}`)
    .join(" ")
  return { points, objectivePath, scorePath }
}

function OptimiserOnlineDetail({ detail }: {
  detail: Extract<OptimiserApplyNodeDetail, { mode: "online" }>
}) {
  const candidates = Array.isArray(detail.candidates) ? detail.candidates : []
  const selected = optimiserSelectedCandidate(candidates, detail.selected)
  const displayCandidates = optimiserDisplayCandidates(candidates, selected)
  const scoreComparison = optimiserScoreComparison(candidates, selected)
  const { points, objectivePath, scorePath } = optimiserChartPath(displayCandidates)
  const selectedLambdaEntries = finiteRecordEntries(selected?.lambda_terms)
  const constraintNames = optimiserConstraintNames(displayCandidates, selected, detail.constraints)
  const hasConstraintColumns = constraintNames.length > 0
  const candidateGridClass = hasConstraintColumns
    ? "grid grid-cols-[3rem_minmax(8rem,10rem)_minmax(7rem,8.5rem)_minmax(8rem,11rem)_minmax(7rem,8.5rem)_minmax(5rem,6rem)] gap-1.5"
    : "grid grid-cols-[3rem_minmax(8rem,10rem)_minmax(7rem,8.5rem)_minmax(5rem,6rem)] gap-1.5"
  const scenarioLabel = detail.scenario_value_column ?? "scenario"
  const objectiveLabel = detail.objective_column ?? "objective"
  const constraintHeader = constraintNames.length === 1 ? constraintNames[0] : "constraints"

  const summary = (
    <>
      <TraceDetailChip>{detail.output_column} = {formatValue(detail.output_value)}</TraceDetailChip>
      {detail.quote_id_column && (
        <TraceDetailChip tone="muted">
          {detail.quote_id_column}: {formatValue(detail.quote_id_value)}
        </TraceDetailChip>
      )}
    </>
  )

  return (
    <TraceDetailPanel title="Optimiser Apply" summary={summary}>
      {selected && (
        <TraceDetailCallout
          title="Selected scenario"
          summary={(
            <>
              <TraceDetailChip>{scenarioLabel}: {formatValue(selected.scenario_value)}</TraceDetailChip>
              <TraceDetailChip tone="muted">index: {formatValue(selected.scenario_index)}</TraceDetailChip>
              {scoreComparison && isFiniteNumber(scoreComparison.gapToNextBest) && (
                <TraceDetailChip tone="muted">gap: {formatSignedValue(scoreComparison.gapToNextBest)}</TraceDetailChip>
              )}
            </>
          )}
        >
          <div
            className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-1 font-mono text-[10px]"
            style={{ color: "var(--text-secondary)" }}
            aria-label="Optimiser score calculation"
          >
            <span style={{ color: "var(--text-muted)" }}>{objectiveLabel}</span>
            <span className="font-semibold">{formatValue(selected.objective)}</span>
            {selectedLambdaEntries.map(([name, value]) => (
              <span key={name} className="inline-flex min-w-0 items-center gap-1">
                <span style={{ color: "var(--text-muted)" }}>+</span>
                <span style={{ color: "var(--text-muted)", overflowWrap: "anywhere" }}>lambda {name}</span>
                <span style={{ color: value >= 0 ? "var(--color-added, var(--success-hover))" : "var(--danger-text)" }}>
                  {formatSignedValue(value)}
                </span>
              </span>
            ))}
            <span style={{ color: "var(--text-muted)" }}>=</span>
            <span className="font-semibold" style={{ color: "var(--text-primary)" }}>score {formatValue(selected.decision_score)}</span>
          </div>
        </TraceDetailCallout>
      )}

      {points.length > 0 && (
        <TraceDetailSection title="Candidate Curve">
          <div className="rounded px-2 py-2" style={{ background: "rgba(255,255,255,.035)", border: "1px solid var(--border)" }}>
            <svg
              aria-label="Optimiser candidate curve"
              viewBox="0 0 280 104"
              role="img"
              className="w-full"
              style={{ display: "block", maxHeight: 136 }}
            >
              <line x1="18" y1="90" x2="262" y2="90" stroke="var(--border)" />
              <line x1="18" y1="14" x2="18" y2="90" stroke="var(--border)" />
              {objectivePath && (
                <path d={objectivePath} fill="none" stroke={CHART_COLORS.neutral} strokeWidth="1.5" strokeDasharray="4 3" strokeLinecap="round" strokeLinejoin="round" />
              )}
              {scorePath && (
                <path d={scorePath} fill="none" stroke={CHART_COLORS.objective} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              )}
              {points.map(({ candidate, x, y }) => {
                const isSelected = optimiserCandidateIsSelected(candidate, selected)
                const radius = isSelected ? 4.5 : 2.5
                const fill = isSelected ? "var(--accent)" : "var(--bg-panel)"
                return (
                  <g key={candidate.scenario_index}>
                    <circle cx={x} cy={y} r={radius} fill={fill} stroke={CHART_COLORS.objective} strokeWidth={isSelected ? 2 : 1.5} />
                    {isSelected && <title>selected scenario {candidate.scenario_index}</title>}
                  </g>
                )
              })}
            </svg>
            <div className="mt-1 flex items-center gap-3 text-[10px]">
              <span className="inline-flex items-center gap-1" style={{ color: "var(--text-muted)" }}>
                <span className="inline-block size-2 rounded-full" style={{ background: CHART_COLORS.objective }} />score
              </span>
              <span className="inline-flex items-center gap-1" style={{ color: "var(--text-muted)" }}>
                <span className="inline-block h-0 w-3 border-t border-dashed" style={{ borderColor: CHART_COLORS.neutral }} />{objectiveLabel}
              </span>
              <span className="inline-flex items-center gap-1" style={{ color: "var(--text-muted)" }}>
                <span className="inline-block size-2 rounded-full" style={{ background: "var(--accent)" }} />selected
              </span>
              <span className="ml-auto font-mono" style={{ color: "var(--text-muted)" }}>
                {displayCandidates.length} candidate{displayCandidates.length === 1 ? "" : "s"}
              </span>
            </div>
          </div>
        </TraceDetailSection>
      )}

      {displayCandidates.length > 0 && (
        <TraceDetailTable
          ariaLabel="Optimiser candidates"
          gridClass={candidateGridClass}
          headers={hasConstraintColumns
            ? ["Index", scenarioLabel, objectiveLabel, constraintHeader, "Lambda Term", "Score"]
            : ["Index", scenarioLabel, objectiveLabel, "Score"]}
        >
          {displayCandidates.map((candidate) => {
            const isSelected = optimiserCandidateIsSelected(candidate, selected)
            const candidateConstraints = formatOptimiserRecordCell(candidate.constraints, constraintNames)
            const candidateLambdaTerms = formatOptimiserRecordCell(candidate.lambda_terms, constraintNames, { signed: true })
            return (
              <TraceDetailTableRow
                key={candidate.scenario_index}
                gridClass={candidateGridClass}
                selected={isSelected}
              >
                <span>{candidate.scenario_index}</span>
                <span className="inline-flex min-w-0 items-center justify-center gap-1" style={{ overflowWrap: "anywhere" }}>
                  <span className="min-w-0">{formatValue(candidate.scenario_value)}</span>
                  {isSelected && (
                    <TraceDetailChip tone="accent" mono={false}>selected</TraceDetailChip>
                  )}
                </span>
                <span className="text-center">{formatValue(candidate.objective)}</span>
                {hasConstraintColumns && (
                  <>
                    <span className="text-center" style={{ overflowWrap: "anywhere" }}>{candidateConstraints}</span>
                    <span className="text-center" style={{ color: "var(--text-muted)", overflowWrap: "anywhere" }}>
                      {candidateLambdaTerms}
                    </span>
                  </>
                )}
                <span className="text-center" title={optimiserScoreFormulaText(candidate, objectiveLabel)}>
                  {formatValue(candidate.decision_score)}
                </span>
              </TraceDetailTableRow>
            )
          })}
        </TraceDetailTable>
      )}
    </TraceDetailPanel>
  )
}
function OptimiserRatebookDetail({ detail }: {
  detail: Extract<OptimiserApplyNodeDetail, { mode: "ratebook" }>
}) {
  const factors = Array.isArray(detail.factors) ? detail.factors : []
  const ratebookGridClass = "grid grid-cols-[minmax(9rem,12rem)_minmax(7rem,9rem)_minmax(5rem,6rem)_minmax(5rem,6rem)] gap-1.5"

  return (
    <TraceDetailPanel
      title="Optimiser Apply"
      summary={<TraceDetailChip>{detail.output_column} = {formatValue(detail.output_value)}</TraceDetailChip>}
    >
      <TraceDetailCallout
        title="Selected ratebook"
        summary={(
          <>
            <TraceDetailChip>base: {formatValue(detail.base_value)}</TraceDetailChip>
            <TraceDetailChip tone="muted">final: {formatValue(detail.final_value)}</TraceDetailChip>
            <TraceDetailChip tone="muted">{factors.length} factor{factors.length === 1 ? "" : "s"}</TraceDetailChip>
          </>
        )}
      />

      {detail.message && (
        <div className="rounded px-2 py-1 font-mono text-[10px]" style={{ background: "rgba(255,255,255,.035)", color: "var(--text-muted)" }}>
          {detail.message}
        </div>
      )}

      {factors.length > 0 && (
        <TraceDetailTable
          ariaLabel="Optimiser ratebook ladder"
          gridClass={ratebookGridClass}
          headers={["Factor", "Input", "Value", "Total"]}
        >
          {factors.map((factor: OptimiserApplyRatebookFactorDetail) => (
            <TraceDetailTableRow key={factor.name} gridClass={ratebookGridClass}>
              <span style={{ overflowWrap: "anywhere", color: "var(--text-secondary)" }}>
                {factor.name}
                {factor.default_used && (
                  <span className="ml-1">
                    <TraceDetailChip tone="warning" mono={false}>default used</TraceDetailChip>
                  </span>
                )}
              </span>
              <span className="text-center" style={{ color: "var(--text-muted)", overflowWrap: "anywhere" }}>{formatValue(factor.input_value)}</span>
              <span className="text-center" style={{ color: "var(--accent)" }}>{formatValue(factor.factor_value)}</span>
              <span className="text-center" style={{ color: "var(--text-primary)" }}>{formatValue(factor.running_total)}</span>
            </TraceDetailTableRow>
          ))}
        </TraceDetailTable>
      )}
    </TraceDetailPanel>
  )
}

function OptimiserApplyErrorDetail({ detail }: {
  detail: Extract<OptimiserApplyNodeDetail, { status: "error" }>
}) {
  return (
    <TraceDetailPanel title="Optimiser Apply" summary={<TraceDetailChip>{detail.mode}</TraceDetailChip>}>
      <TraceDetailAlert>
        Trace failed: {detail.error}
        {detail.error_type ? ` (${detail.error_type})` : ""}
      </TraceDetailAlert>
    </TraceDetailPanel>
  )
}

function NodeDetailBlock({
  detail,
  tracedColumn,
  showBandingSummary = true,
}: {
  detail: TraceNodeDetail
  tracedColumn?: string | null
  showBandingSummary?: boolean
}) {
  const detailType = detail.detail_type as string | undefined

  const labelStyle = traceDetailLabelStyle
  const valueStyle = traceDetailValueStyle

  if (detailType === "rating_step" && (Array.isArray(detail.tables) || Array.isArray(detail.combined_outputs))) {
    const tables = asRatingStepTables(detail)
    const combinedOutputs = asRatingStepCombinedOutputs(detail)

    return (
      <TraceDetailPanel title="Rating Step">
        {tables.length > 0 && (
          <TraceDetailSection title="Rating Tables">
            {tables.map((table, tableIndex) => {
              const title = table.name || table.output_column || `table ${tableIndex + 1}`
              const status = ratingTableStatus(table)
              const isTracedTable = tracedColumn != null &&
                (table.output_column === tracedColumn || table.name === tracedColumn)
              return (
                <div
                  key={`${title}-${tableIndex}`}
                  className="space-y-1 py-1.5"
                  style={{
                    borderTop: tableIndex === 0 ? "none" : "1px solid var(--border)",
                    borderLeft: isTracedTable ? "2px solid var(--accent)" : "2px solid transparent",
                    paddingLeft: isTracedTable ? 8 : 0,
                  }}
                >
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-mono font-semibold" style={{ color: "var(--text-primary)" }}>
                      {title}
                    </span>
                    {isTracedTable && (
                      <TraceDetailChip tone="accent" mono={false}>traced column</TraceDetailChip>
                    )}
                    {status && (
                      <TraceDetailChip
                        tone={status === "matched" ? "success" : status === "default" ? "warning" : "danger"}
                        mono={false}
                      >
                        status: {formatRatingStatus(status)}
                      </TraceDetailChip>
                    )}
                    {table.selected_value !== undefined && (
                      <TraceDetailChip tone="accent">selected: {formatValue(table.selected_value)}</TraceDetailChip>
                    )}
                    {table.default_value !== undefined && (
                      <TraceDetailChip tone="muted">default: {formatValue(table.default_value)}</TraceDetailChip>
                    )}
                    {table.default_used && (
                      <TraceDetailChip tone="warning" mono={false}>default used</TraceDetailChip>
                    )}
                  </div>
                  {table.factors && table.factors.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {table.factors.map((factor) => (
                        <TraceDetailChip key={`${factor.column}-${String(factor.value)}`}>
                          {factor.column}: {formatValue(factor.value)}
                        </TraceDetailChip>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </TraceDetailSection>
        )}

        {combinedOutputs.length > 0 && (
          <TraceDetailSection title="Combined Outputs">
            {combinedOutputs.map((combined) => {
              const isTracedCombined = tracedColumn != null && combined.column === tracedColumn
              return (
                <div
                  key={combined.column}
                  className="space-y-1 py-1.5"
                  style={{
                    borderTop: "1px solid var(--border)",
                    borderLeft: isTracedCombined ? "2px solid var(--accent)" : "2px solid transparent",
                    paddingLeft: isTracedCombined ? 8 : 0,
                  }}
                >
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-mono font-semibold" style={{ color: "var(--text-primary)" }}>
                      {combined.column} = {formatValue(combined.value)}
                    </span>
                    {isTracedCombined && (
                      <TraceDetailChip tone="accent" mono={false}>traced column</TraceDetailChip>
                    )}
                  </div>
                  <div style={valueStyle}>
                    {combined.operation} from base {formatValue(combined.base_value)}
                  </div>
                  {Object.keys(combined.input_values).length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {Object.entries(combined.input_values).map(([column, value]) => (
                        <TraceDetailChip key={column}>{column}: {formatValue(value)}</TraceDetailChip>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </TraceDetailSection>
        )}
      </TraceDetailPanel>
    )
  }

  if (detailType === "rate_table_lookup" || detailType === "rating_step") {
    const keys = detail.lookup_keys as Record<string, unknown> | undefined
    const matched = detail.matched_row
    const defaultUsed = detail.default_used as boolean | undefined
    return (
      <TraceDetailPanel title="Rate Table Lookup">
        {keys && (
          <div className="flex flex-wrap gap-1">
            {Object.entries(keys).map(([k, v]) => (
              <TraceDetailChip key={k}>{k}: {String(v)}</TraceDetailChip>
            ))}
          </div>
        )}
        {matched != null && <TraceDetailChip tone="muted">matched row: {String(matched)}</TraceDetailChip>}
        {defaultUsed && (
          <TraceDetailChip tone="warning" mono={false}>default used</TraceDetailChip>
        )}
      </TraceDetailPanel>
    )
  }

  if (detailType === "banding") {
    const banding = asBandingDetail(detail)
    const rows = bandingRowsForDisplay(banding, tracedColumn)
    const singleRow = rows.length === 1 ? rows[0] : null
    const rangeSummary = singleRow ? formatBandingRange(singleRow) : null
    const bandingGridClass = "grid grid-cols-[minmax(8rem,1fr)_minmax(8rem,1fr)_minmax(5rem,.65fr)_minmax(5rem,.65fr)] gap-1.5"
    return (
      <TraceDetailPanel
        title="Banding"
        summary={(
          <>
            {singleRow?.outputColumn && (
              <TraceDetailChip tone="accent">{singleRow.outputColumn}</TraceDetailChip>
            )}
            {showBandingSummary && singleRow && (
              <TraceDetailChip>{formatBandingTransform(singleRow)}</TraceDetailChip>
            )}
            {showBandingSummary && !singleRow && rows.length > 0 && (
              <TraceDetailChip>{rows.length} banded output{rows.length === 1 ? "" : "s"}</TraceDetailChip>
            )}
            {rangeSummary && <TraceDetailChip tone="muted">{rangeSummary}</TraceDetailChip>}
            {singleRow?.isDefault && <TraceDetailChip tone="warning" mono={false}>default</TraceDetailChip>}
          </>
        )}
      >
        {!singleRow && rows.length > 0 && (
          <TraceDetailTable
            ariaLabel="Banding outputs"
            gridClass={bandingGridClass}
            headers={["Output", "Source", "Band", "Rule"]}
          >
            {rows.map((row) => {
              const range = formatBandingRange(row)
              return (
                <TraceDetailTableRow key={row.key} gridClass={bandingGridClass}>
                  <span style={{ overflowWrap: "anywhere", color: "var(--accent)" }}>
                    {row.outputColumn ?? ""}
                  </span>
                  <span className="text-center" style={{ overflowWrap: "anywhere", color: "var(--text-secondary)" }}>
                    {row.inputColumn ? `${row.inputColumn}=${formatValue(row.inputValue)}` : formatValue(row.inputValue)}
                  </span>
                  <span className="text-center" style={{ color: "var(--text-primary)" }}>
                    {formatValue(row.matchedBand)}
                  </span>
                  <span className="text-center" style={{ color: "var(--text-muted)" }}>
                    {row.isDefault ? "default" : range ?? row.status ?? ""}
                  </span>
                </TraceDetailTableRow>
              )
            })}
          </TraceDetailTable>
        )}
      </TraceDetailPanel>
    )
  }

  if (detailType === "optimiser_apply") {
    const optimiserDetail = asOptimiserApplyDetail(detail)
    if (isOptimiserApplyErrorDetail(optimiserDetail)) {
      return (
        <OptimiserApplyErrorDetail
          detail={optimiserDetail}
        />
      )
    }
    if (optimiserDetail.mode === "online") {
      return (
        <OptimiserOnlineDetail
          detail={optimiserDetail}
        />
      )
    }
    if (optimiserDetail.mode === "ratebook") {
      return (
        <OptimiserRatebookDetail
          detail={optimiserDetail}
        />
      )
    }
  }

  if (detailType === "model_score") {
    const modelDetail = asModelScoreDetail(detail)
    const prediction = modelScorePrediction(modelDetail)
    const featureColumns = modelScoreFeatureColumns(modelDetail)
    const featureValues = modelDetail.feature_values ?? {}
    const explanation = modelDetail.explanation
    const contributions = explanation?.contributions ?? []
    const predictionColumn = modelDetail.prediction_column
    const outputSpace = explanation?.output_space ?? explanation?.prediction_space
    const showContributionLadder = explanation?.status !== "error" &&
      explanation?.base_value !== undefined &&
      contributions.length > 0
    const contributionLadderRows = (() => {
      if (!showContributionLadder || explanation?.base_value === undefined) return []
      let total = explanation.base_value
      return contributions.map((contribution, index) => {
        const value = contribution.contribution ?? contribution.contribution_value ?? contribution.shap_value
        total = nextRunningTotal(total, value)
        return {
          contribution,
          featureValue: resolveContributionFeatureValue(modelDetail, explanation, contribution),
          index,
          runningTotal: total,
          value,
        }
      })
    })()
    const predictionFromLadder = explanation?.prediction_from_shap ?? prediction.value
    const omittedContributionCount = explanation?.truncated ? explanation.omitted_count ?? 0 : 0
    return (
      <TraceDetailPanel
        title={modelScoreTitle(modelDetail)}
        summary={prediction.hasPrediction && !showContributionLadder
          ? (
              <TraceDetailChip tone="accent">
                Prediction{predictionColumn ? `: ${predictionColumn}` : ""} = {formatValue(prediction.value)}
              </TraceDetailChip>
            )
          : outputSpace
            ? <TraceDetailChip tone="muted">{outputSpace}</TraceDetailChip>
            : undefined}
      >
        {featureColumns.length > 0 && !showContributionLadder && (
          <TraceDetailSection title="Feature Values">
            <div className="grid gap-1" aria-label="Model feature values">
              {featureColumns.map((feature) => {
                const hasFeatureValue = Object.prototype.hasOwnProperty.call(featureValues, feature)
                return (
                  <div key={feature} className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 font-mono text-[10px]">
                    <span style={{ overflowWrap: "anywhere", color: "var(--text-secondary)" }}>{feature}</span>
                    <span style={{ color: "var(--text-muted)" }}>
                      {hasFeatureValue ? formatValue(featureValues[feature]) : ""}
                    </span>
                  </div>
                )
              })}
            </div>
          </TraceDetailSection>
        )}
        {explanation?.status === "error" && (
          <TraceDetailAlert>
            Explanation failed: {String(explanation.error || "unknown error")}
          </TraceDetailAlert>
        )}
        {explanation?.status !== "error" && explanation?.base_value !== undefined && !showContributionLadder && (
          <TraceDetailSection title="Contribution Summary">
            <TraceDetailChip>Base value: {formatValue(explanation.base_value)}</TraceDetailChip>
            {explanation.prediction_from_shap !== undefined && (
              <div style={labelStyle}>
                base + contributions = {formatValue(explanation.prediction_from_shap)}
                {outputSpace ? ` (${outputSpace})` : ""}
              </div>
            )}
          </TraceDetailSection>
        )}
        {showContributionLadder && (
          <TraceDetailSection title="Contribution Ladder">
            <div className="mt-1 space-y-1" aria-label="Model score contribution ladder">
            <div className="grid grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)_minmax(4.5rem,auto)_minmax(4.5rem,auto)] gap-2 text-[10px] font-semibold uppercase" style={labelStyle}>
              <span>Factor</span>
              <span>Value</span>
              <span className="text-right">Contribution</span>
              <span className="text-right">Total</span>
            </div>
            <div
              className="grid grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)_minmax(4.5rem,auto)_minmax(4.5rem,auto)] gap-2 font-mono text-[10px]"
              data-testid="model-score-ladder-row"
            >
              <span style={{ color: "var(--text-secondary)" }}>Base</span>
              <span />
              <span />
              <span className="text-right" style={{ color: "var(--text-primary)" }}>
                {formatValue(explanation.base_value)}
              </span>
            </div>
            {contributionLadderRows.map(({ contribution, featureValue, index, runningTotal, value }) => {
              return (
                <div
                  key={`${contribution.feature}-${index}`}
                  className="grid grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)_minmax(4.5rem,auto)_minmax(4.5rem,auto)] gap-2 font-mono text-[10px]"
                  data-testid="model-score-ladder-row"
                >
                  <span style={{ overflowWrap: "anywhere", color: "var(--text-secondary)" }}>
                    {contribution.rank ? `${contribution.rank}. ` : ""}{contribution.feature}
                  </span>
                  <span style={{ overflowWrap: "anywhere", color: "var(--text-muted)" }}>
                    {featureValue.hasValue ? formatValue(featureValue.value) : "not provided"}
                  </span>
                  <span className="text-right" style={{ color: value >= 0 ? "var(--success-hover)" : "var(--danger-text)" }}>
                    {formatSignedValue(value)}
                  </span>
                  <span className="text-right" style={{ color: "var(--text-primary)" }}>
                    {formatValue(runningTotal)}
                  </span>
                </div>
              )
            })}
            {explanation?.truncated && (
              <div style={labelStyle}>
                Prediction includes {omittedContributionCount} omitted contribution{omittedContributionCount === 1 ? "" : "s"}.
              </div>
            )}
            <div
              className="grid grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)_minmax(4.5rem,auto)_minmax(4.5rem,auto)] gap-2 border-t pt-1 font-mono text-[10px] font-semibold"
              style={{ borderColor: "var(--border)" }}
              data-testid="model-score-ladder-row"
            >
              <span style={{ color: "var(--text-primary)" }}>Prediction</span>
              <span style={{ overflowWrap: "anywhere", color: "var(--text-muted)" }}>
                {predictionColumn ?? ""}
              </span>
              <span className="text-right" style={{ color: "var(--text-muted)" }}>
                {outputSpace ? `(${outputSpace})` : ""}
              </span>
              <span className="text-right" style={{ color: "var(--accent)" }}>{formatValue(predictionFromLadder)}</span>
            </div>
            </div>
          </TraceDetailSection>
        )}
        {!showContributionLadder && contributions.length > 0 && (
          <TraceDetailSection title="Contributions">
            <div className="mt-1 space-y-1" aria-label="Model feature contributions">
            {contributions.map((contribution, index) => {
              const value = contribution.shap_value
              const signedValue = `${value >= 0 ? "+" : ""}${formatValue(value)}`
              return (
                <div
                  key={`${contribution.feature}-${index}`}
                  className="grid grid-cols-[minmax(0,1fr)_auto_auto] gap-2 font-mono text-[10px]"
                >
                  <span style={{ overflowWrap: "anywhere", color: "var(--text-secondary)" }}>
                    {contribution.rank ? `${contribution.rank}. ` : ""}{contribution.feature}
                  </span>
                  <span style={{ color: "var(--text-muted)" }}>
                    {contribution.feature_value !== undefined ? formatValue(contribution.feature_value) : ""}
                  </span>
                  <span style={{ color: value == null || value >= 0 ? "var(--success-hover)" : "var(--danger-text)" }}>
                    {signedValue}
                  </span>
                </div>
              )
            })}
            {explanation?.truncated && (
              <div style={labelStyle}>{explanation.omitted_count ?? 0} contribution(s) omitted</div>
            )}
            </div>
          </TraceDetailSection>
        )}
      </TraceDetailPanel>
    )
  }

  if (detailType === "scenario_expander") {
    const expander = asScenarioExpanderDetail(detail)
    const scenarioColumn = expander.scenario_column ??
      (typeof expander.step === "string" && expander.step.length > 0 ? expander.step : "scenario")
    const scenarioValue = expander.scenario_value ?? expander.multiplier
    const scenarioIndex = expander.scenario_index
    const minValue = expander.parameters?.min_value ?? expander.range?.min
    const maxValue = expander.parameters?.max_value ?? expander.range?.max
    const stepCount = expander.parameters?.steps
    const hasGridSettings = minValue !== undefined || maxValue !== undefined || stepCount !== undefined
    return (
      <TraceDetailPanel
        title="Scenario Expander"
        summary={(
          <>
          {scenarioValue !== undefined && (
            <TraceDetailChip>{scenarioColumn}: {formatValue(scenarioValue)}</TraceDetailChip>
          )}
          {scenarioIndex !== undefined && (
            <TraceDetailChip tone="muted">index: {formatValue(scenarioIndex)}</TraceDetailChip>
          )}
          </>
        )}
      >
        {hasGridSettings && (
          <div className="flex flex-wrap gap-1">
            {minValue !== undefined && (
              <TraceDetailChip tone="muted">min: {formatValue(minValue)}</TraceDetailChip>
            )}
            {maxValue !== undefined && (
              <TraceDetailChip tone="muted">max: {formatValue(maxValue)}</TraceDetailChip>
            )}
            {stepCount !== undefined && (
              <TraceDetailChip tone="muted">steps: {formatValue(stepCount)}</TraceDetailChip>
            )}
          </div>
        )}
        {expander.error && (
          <TraceDetailAlert>
            Trace detail failed: {expander.error}
          </TraceDetailAlert>
        )}
      </TraceDetailPanel>
    )
  }

  if (detailType === "live_switch") {
    const liveSwitch = asLiveSwitchDetail(detail)
    const activeBranch = liveSwitch.active_branch ?? liveSwitch.selected_branch
    const activeScenario = liveSwitch.active_scenario
    const prunedBranches = Array.isArray(liveSwitch.pruned_branches) ? liveSwitch.pruned_branches : []
    const availableBranches = Array.isArray(liveSwitch.available_branches) ? liveSwitch.available_branches : []
    return (
      <TraceDetailPanel
        title="Live Switch"
        summary={(
          <>
          {activeBranch && (
            <TraceDetailChip>active branch: {activeBranch}</TraceDetailChip>
          )}
          {activeScenario && (
            <TraceDetailChip tone="muted">scenario: {activeScenario}</TraceDetailChip>
          )}
          </>
        )}
      >
        {prunedBranches.length > 0 && (
          <div style={valueStyle}>Pruned branches: {prunedBranches.join(", ")}</div>
        )}
        {availableBranches.length > 0 && prunedBranches.length === 0 && (
          <div style={valueStyle}>Available branches: {availableBranches.join(", ")}</div>
        )}
        {liveSwitch.error && (
          <TraceDetailAlert>
            Trace detail failed: {liveSwitch.error}
          </TraceDetailAlert>
        )}
      </TraceDetailPanel>
    )
  }

  // Default: render as JSON
  return (
    <TraceDetailPanel title={detailType ? detailType.replace(/_/g, " ") : "Trace Detail"}>
      <pre className="rounded px-2 py-1.5 text-[10px] font-mono" style={{ color: "var(--text-muted)", background: "rgba(255,255,255,.035)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
        {JSON.stringify(detail, null, 2)}
      </pre>
    </TraceDetailPanel>
  )
}

function StepCard({
  step,
  index,
  tracedColumn,
  isTargetStep,
  defaultExpanded = false,
  waterfall,
}: {
  step: TraceStep
  index: number
  tracedColumn: string | null
  isTargetStep?: boolean
  defaultExpanded?: boolean
  waterfall?: TraceResult["waterfall"]
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const accent = nodeTypeColors[step.node_type] || CHART_COLORS.cyan
  const typeLabel = nodeTypeLabels[step.node_type] || "NODE"
  const relevant = step.column_relevant

  const { columns_added, columns_modified, columns_removed } = step.schema_diff

  // Key values to always show (collapsed): traced column or first added/modified
  const keyEntries: { col: string; val: unknown; tag: "added" | "modified" | "value" }[] = []
  if (tracedColumn && step.output_values[tracedColumn] !== undefined) {
    const tag = columns_added.includes(tracedColumn)
      ? "added"
      : columns_modified.includes(tracedColumn)
        ? "modified"
        : "value"
    keyEntries.push({ col: tracedColumn, val: step.output_values[tracedColumn], tag })
  } else {
    for (const col of columns_added.slice(0, 2)) {
      keyEntries.push({ col, val: step.output_values[col], tag: "added" })
    }
    for (const col of columns_modified.slice(0, 2)) {
      keyEntries.push({ col, val: step.output_values[col], tag: "modified" })
    }
  }

  const tagColors = {
    added: { bg: "var(--success-soft-mid)", color: "var(--color-added, var(--success-hover))", label: "+" },
    modified: { bg: "var(--warning-bright-soft)", color: "var(--color-modified, var(--warning))", label: "~" },
    value: { bg: "rgba(255,255,255,.06)", color: "var(--text-secondary)", label: "=" },
  }

  // All output columns for expanded view
  const allOutputCols = Object.keys(step.output_values)
  const richNodeDetail = hasPrimaryNodeDetail(step)
  const isOriginStep = isTraceOriginStep(step, tracedColumn)
  const sourceCalculationIsPlaceholder = isComputedPlaceholder(step.calculation?.substituted_text)
  const showSourceOrigin = isOriginStep &&
    (step.expression?.expression_type === "opaque" || sourceCalculationIsPlaceholder)
  const showOpaqueComputed = step.expression?.expression_type === "opaque" && !richNodeDetail && !isOriginStep
  const rawCalculationBlockText = step.calculation != null &&
    !richNodeDetail &&
    !(isOriginStep && sourceCalculationIsPlaceholder)
    ? step.calculation.substituted_text
    : null
  const calculationBlockText = rawCalculationBlockText != null && rawCalculationBlockText.trim().length > 0
    ? rawCalculationBlockText
    : null
  const showCalculationHero = Boolean(
    isTargetStep &&
    !richNodeDetail &&
    (step.expression != null || step.calculation != null) &&
    tracedColumn,
  )
  const showSecondaryDetail = Boolean(
    step.node_detail &&
    (
      !showCalculationHero ||
      hasRichRatingStepDetail(step) ||
      (
        hasRichBandingDetail(step) &&
        (step.calculation == null || hasBandingSecondaryDetail(step.node_detail))
      )
    ),
  )
  const showColumnValuesTable = !step.expression &&
    !step.calculation &&
    !richNodeDetail &&
    !(hasRichBandingDetail(step) && hasRenderableBandingRows(step.node_detail))

  return (
    <div
      className="rounded-lg overflow-hidden transition-opacity"
      data-testid={`trace-step-card-${step.node_id}`}
      data-target-step={isTargetStep || undefined}
      data-relevance={relevant ? "relevant" : "irrelevant"}
      style={{
        border: relevant ? `1px solid ${accent}40` : "1px solid var(--border)",
        background: "var(--bg-elevated)",
        opacity: relevant ? 1 : 0.55,
      }}
    >
      {/* Collapsed header - hover bg driven by Tailwind.  The inline
          `background: transparent` is intentionally omitted so the
          Tailwind `hover:` rule can apply (inline > class specificity). */}
      <button
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        aria-controls={`trace-step-body-${step.node_id}`}
        className="w-full flex items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-[var(--bg-hover)]"
      >
        {expanded ? (
          <ChevronDown size={12} style={{ color: "var(--text-muted)" }} />
        ) : (
          <ChevronRight size={12} style={{ color: "var(--text-muted)" }} />
        )}
        <span
          className="text-[11px] font-mono font-bold shrink-0"
          style={{ color: "var(--text-muted)", minWidth: "1.2em" }}
        >
          {index + 1}
        </span>
        <span className="text-[13px] font-semibold truncate" style={{ color: "var(--text-primary)" }}>
          {step.node_name}
        </span>
        <span
          className="text-[9px] font-bold uppercase tracking-wider shrink-0 px-1.5 py-0.5 rounded"
          style={{ color: accent, background: `${accent}15` }}
        >
          {typeLabel}
        </span>
        {(() => {
          const badge = (() => {
            if (tracedColumn) {
              const diff = step.schema_diff
              if (diff.columns_added.includes(tracedColumn)) return "creates"
              if (diff.columns_modified.includes(tracedColumn)) return "modifies"
              if (diff.columns_passed.includes(tracedColumn)) return "passes"
              return null
            }
            return step.row_lineage_type || null
          })()
          return badge ? (
            <span
              className="text-[9px] font-medium shrink-0 px-1 py-0.5 rounded"
              style={{ color: "var(--text-muted)", background: "rgba(255,255,255,.06)" }}
            >
              {badge}
            </span>
          ) : null
        })()}
        <span className="ml-auto text-[10px] font-mono shrink-0" style={{ color: "var(--text-muted)" }}>
          {step.execution_ms.toFixed(1)}ms
        </span>
      </button>

      {/* Key values (always visible when there are entries) */}
      {keyEntries.length > 0 && !expanded && (
        <div className="px-3 pb-2 flex flex-wrap gap-1.5" style={{ paddingLeft: "2.8rem" }}>
          {keyEntries.map(({ col, val, tag }) => {
            const tc = tagColors[tag]
            return (
              <span
                key={col}
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-mono"
                style={{ background: tc.bg, color: tc.color }}
              >
                <span className="font-bold">{tc.label}</span>
                {col}: {formatValue(val)}
              </span>
            )
          })}
          {calculationBlockText != null && !isTargetStep && (
            <span
              className="inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-mono"
              style={{ background: "rgba(255,255,255,.06)", color: "var(--text-secondary)" }}
            >
              {calculationBlockText}
            </span>
          )}
        </div>
      )}

      {/* Expanded: full column list */}
      {expanded && (
        <div
          id={`trace-step-body-${step.node_id}`}
          data-testid={`trace-step-body-${step.node_id}`}
          className="px-3 pb-3"
          style={{ borderTop: "1px solid var(--border)" }}
        >
          {showCalculationHero && tracedColumn && (
            <div className="pt-2">
              <CalculationHero
                column={tracedColumn}
                expression={step.expression ?? null}
                calculation={step.calculation ?? null}
                nodeName={step.node_name}
                nodeType={step.node_type}
                isSourceOrigin={isOriginStep}
                waterfall={waterfall}
                frame={false}
              />
            </div>
          )}

          {/* Expression block */}
          {!showCalculationHero && !richNodeDetail && step.expression && step.expression.expression_type !== "opaque" && (
            <div
              className="my-2 px-2 py-1.5 rounded text-[11px] font-mono"
              style={{ background: "rgba(255,255,255,.04)", color: "var(--text-secondary)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}
            >
              {formatExpression(step.expression.expression_text, 200)}
            </div>
          )}
          {!showCalculationHero && showOpaqueComputed && (
            <div className="my-2 text-[11px]" style={{ color: "var(--text-muted)", fontStyle: "italic" }}>
              computed
            </div>
          )}
          {!showCalculationHero && showSourceOrigin && (
            <div className="my-2 flex items-baseline gap-1.5 text-[11px]" style={{ color: "var(--text-muted)" }}>
              <span>Source node</span>
              <span className="font-mono font-semibold" style={{ color: "var(--text-secondary)" }}>
                {step.node_name}
              </span>
            </div>
          )}

          {/* Calculation block */}
          {!showCalculationHero && calculationBlockText != null && (
            <div
              className="my-2 px-2 py-1.5 rounded text-[12px] font-mono font-semibold"
              style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
            >
              {calculationBlockText}
            </div>
          )}

          {/* Node detail section */}
          {showSecondaryDetail && step.node_detail && (
            <NodeDetailBlock detail={step.node_detail} tracedColumn={tracedColumn} />
          )}

          {/* Schema changes summary */}
          <div className="flex flex-wrap gap-2 py-2 text-[10px]">
            {columns_added.length > 0 && (
              <span style={{ color: "var(--color-added, var(--success-hover))" }}>+{columns_added.length} added</span>
            )}
            {columns_modified.length > 0 && (
              <span style={{ color: "var(--color-modified, var(--warning))" }}>~{columns_modified.length} modified</span>
            )}
            {columns_removed.length > 0 && (
              <span style={{ color: "var(--color-removed, var(--danger-text))" }}>-{columns_removed.length} removed</span>
            )}
            <span style={{ color: "var(--text-muted)" }}>
              {step.schema_diff.columns_passed.length} passed through
            </span>
          </div>

          {/* Column values table (shown when no richer node-specific detail exists) */}
          {showColumnValuesTable && <div className="space-y-0.5">
            {allOutputCols.map((col) => {
              const isAdded = columns_added.includes(col)
              const isModified = columns_modified.includes(col)
              const isRemoved = columns_removed.includes(col)
              const inputVal = step.input_values[col]
              const outputVal = step.output_values[col]
              const isTraced = col === tracedColumn

              let rowColor = "var(--text-secondary)"
              let prefix = ""
              if (isAdded) {
                rowColor = "var(--color-added, var(--success-hover))"
                prefix = "+"
              } else if (isModified) {
                rowColor = "var(--color-modified, var(--warning))"
                prefix = "~"
              } else if (isRemoved) {
                rowColor = "var(--color-removed, var(--danger-text))"
                prefix = "-"
              }

              return (
                <div
                  key={col}
                  className="flex items-center gap-2 px-2 py-0.5 rounded text-[11px] font-mono"
                  style={{
                    background: isTraced ? "var(--accent-soft)" : "transparent",
                    borderLeft: isTraced ? "2px solid var(--accent)" : "2px solid transparent",
                  }}
                >
                  <span className="font-bold w-3" style={{ color: rowColor }}>
                    {prefix}
                  </span>
                  <span className="truncate" style={{ color: rowColor, minWidth: "6em", maxWidth: "10em" }}>
                    {col}
                  </span>
                  {isModified && inputVal !== undefined && (
                    <>
                      <span style={{ color: "var(--text-muted)" }}>{formatValue(inputVal)}</span>
                      <span style={{ color: "var(--text-muted)" }}>&rarr;</span>
                    </>
                  )}
                  <span style={{ color: isAdded || isModified ? rowColor : "var(--text-secondary)" }}>
                    {formatValue(outputVal)}
                  </span>
                </div>
              )
            })}
          </div>}
        </div>
      )}
    </div>
  )
}

interface TracePanelProps {
  trace: TraceResult
  onClose: () => void
}

export default function TracePanel({ trace, onClose }: TracePanelProps) {
  const storyKey = traceStoryKey(trace)
  const [showHidden, setShowHidden] = useState(false)

  const targetStep = useMemo(() => findTargetStep(trace.steps, trace.column), [trace.steps, trace.column])
  const preserveStepIds = useMemo(
    () => traceStoryPreserveStepIds(trace.steps, targetStep, trace.column),
    [trace.steps, targetStep, trace.column],
  )
  const expandedStepIds = useMemo(
    () => defaultExpandedStepIds(trace.steps, targetStep, trace.column),
    [trace.steps, targetStep, trace.column],
  )
  const focusedStoryEntries = useMemo<CollapsedEntry[]>(() => {
    if (!trace.column) return trace.steps
    return collapsePassthroughs(
      trace.steps,
      trace.column,
      preserveStepIds,
      { collapseUnpreserved: targetStep != null },
    )
  }, [trace.steps, trace.column, preserveStepIds, targetStep])
  const hiddenStepCount = useMemo(
    () => focusedStoryEntries.reduce((count, entry) => count + ("collapsed" in entry ? entry.collapsed.length : 0), 0),
    [focusedStoryEntries],
  )
  const storyEntries = useMemo<CollapsedEntry[]>(() => {
    if (showHidden) return trace.steps
    if (targetStep) {
      return focusedStoryEntries.filter((entry) => !("collapsed" in entry))
    }
    return focusedStoryEntries
  }, [focusedStoryEntries, showHidden, targetStep, trace.steps])

  return (
    <PanelShell>
      {/* Header */}
      <div
        className="px-4 py-3 flex items-center gap-2 shrink-0"
        style={{ borderBottom: "1px solid var(--border)" }}
      >
        <Scan size={14} style={{ color: "var(--accent)" }} />
        <div className="flex-1 min-w-0">
          <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1 text-xs font-bold" style={{ color: "var(--text-primary)" }}>
            <span className="truncate">Trace{trace.column ? `: ${trace.column}` : ""}</span>
            {trace.column && (
              <span
                className="font-mono text-[11px] font-semibold"
                data-testid="trace-target-summary"
                style={{ color: "var(--accent)", fontVariantNumeric: "tabular-nums" }}
              >
                = {formatValue(trace.output_value)}
              </span>
            )}
          </div>
          <div className="text-[11px]" style={{ color: "var(--text-muted)" }}>
            {trace.row_id_column && trace.row_id_value != null ? (
              <><span className="font-mono">{trace.row_id_column}</span> = <span className="font-mono font-medium" style={{ color: "var(--text-secondary)" }}>{formatValue(trace.row_id_value)}</span></>
            ) : (
              <>Row {trace.row_index}</>
            )}
            {" "}&middot; {trace.nodes_in_trace} of {trace.total_nodes_in_pipeline} nodes
            {targetStep && (
              <>
                {" "}&middot; created by <span className="font-mono" style={{ color: "var(--text-secondary)" }}>{targetStep.node_name}</span>
              </>
            )}
            {hiddenStepCount > 0 && (
              <>
                {" "}&middot;{" "}
                <button
                  type="button"
                  data-testid="trace-show-full"
                  onClick={() => setShowHidden((value) => !value)}
                  className="underline-offset-2 hover:underline"
                  style={{ color: "var(--accent)" }}
                >
                  {showHidden ? "show focused trace" : "show full trace"}
                </button>
              </>
            )}
          </div>
        </div>
        <button
          onClick={onClose}
          aria-label="Close trace"
          className="p-1 rounded transition-colors hover:bg-[var(--bg-hover)]"
          style={{ color: "var(--text-muted)" }}
        >
          <X size={14} />
        </button>
      </div>

      <div
        className="flex-1 overflow-y-auto p-3 space-y-2"
        data-testid="trace-story"
        style={{ background: "var(--bg-panel)" }}
      >
        {!trace.column && (
          <div className="flex items-center gap-2 rounded px-2 py-1.5 text-[11px]" style={{ background: "var(--bg-elevated)", color: "var(--text-muted)" }}>
            <span>Result</span>
            <span className="font-mono font-semibold" style={{ color: "var(--accent)" }}>
              {formatValue(trace.output_value)}
            </span>
          </div>
        )}

        {storyEntries.map((entry, entryIndex) => {
          if ("collapsed" in entry) {
            const hiddenCount = entry.collapsed.length
            return (
              <button
                key={`collapsed-${entryIndex}-${hiddenCount}`}
                data-testid="trace-hidden-toggle"
                onClick={() => setShowHidden(true)}
                className="trace-hidden-toggle w-full py-1.5 rounded text-[11px] transition-colors"
                style={{ color: "var(--text-muted)", border: "1px dashed var(--border)", fontStyle: "italic" }}
              >
                {hiddenCount} pass-through node{hiddenCount > 1 ? "s" : ""} hidden
              </button>
            )
          }

          const isTargetStep = targetStep?.node_id === entry.node_id
          return (
            <StepCard
              key={`${storyKey}-${entry.node_id}`}
              step={entry}
              index={trace.steps.indexOf(entry)}
              tracedColumn={trace.column}
              isTargetStep={isTargetStep}
              defaultExpanded={expandedStepIds.has(entry.node_id)}
              waterfall={isTargetStep ? trace.waterfall : undefined}
            />
          )
        })}
      </div>
    </PanelShell>
  )
}
