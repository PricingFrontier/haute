import { useState, useMemo, useRef, useEffect, type CSSProperties } from "react"
import { X, ChevronDown, ChevronRight, Scan, Copy, Check } from "lucide-react"
import type {
  BandingNodeDetail,
  ModelScoreContributionDetail,
  ModelScoreExplanationDetail,
  ModelScoreNodeDetail,
  OptimiserApplyNodeDetail,
  OptimiserApplyOnlineCandidateDetail,
  OptimiserApplyRatebookFactorDetail,
  RatingStepCombinedOutputDetail,
  RatingStepTableDetail,
  TraceNodeDetail,
  TraceResult,
  TraceStep,
} from "../types/trace"
import {
  GENERATED_COLUMN_ORIGIN_TYPES,
  SOURCE_ONLY_TYPES,
  nodeTypeLabels,
  nodeTypeColors,
} from "../utils/nodeTypes"
import { formatValue as _formatValue } from "../utils/formatValue"
import { formatExpression } from "../utils/formatTrace"
import PanelShell from "./PanelShell"
import CalculationHero from "../trace/CalculationHero"
import { CHART_COLORS } from "../theme/colors"
import { findTargetStep, collapsePassthroughs } from "./trace/traceGrouping"
import { traceToMarkdown } from "./trace/traceToMarkdown"
import useToastStore from "../stores/useToastStore"

const formatValue = (v: unknown) => _formatValue(v, 2)

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

function ratingStatusStyle(status: string) {
  if (status === "matched") {
    return { background: "var(--success-soft-mid)", color: "var(--color-added, var(--success-hover))" }
  }
  if (status === "default") {
    return { background: "var(--warning-bright-soft-strong)", color: "var(--warning)" }
  }
  return { background: "var(--danger-soft)", color: "var(--danger-text)" }
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

function isOptimiserApplyErrorDetail(
  detail: OptimiserApplyNodeDetail,
): detail is Extract<OptimiserApplyNodeDetail, { status: "error" }> {
  return detail.status === "error"
}

function hasBandingSecondaryDetail(detail: TraceNodeDetail | null | undefined): boolean {
  return detail?.detail_type === "banding" &&
    (detail.lower_bound != null || detail.upper_bound != null || detail.is_default === true)
}

function isComputedPlaceholder(value: string | undefined): boolean {
  return value?.trim().toLowerCase() === "computed"
}

function isSourceOnlyNodeType(nodeType: string | undefined): boolean {
  return Boolean(nodeType && SOURCE_ONLY_TYPES.has(nodeType))
}

function isTraceOriginStep(step: TraceStep, tracedColumn: string | null | undefined): boolean {
  if (isSourceOnlyNodeType(step.node_type)) return true
  return Boolean(
    tracedColumn &&
    GENERATED_COLUMN_ORIGIN_TYPES.has(step.node_type) &&
    step.schema_diff.columns_added.includes(tracedColumn),
  )
}

function isCalculationRoutineSparse(step: TraceStep | null | undefined): boolean {
  if (hasRichModelScoreDetail(step) || hasRichOptimiserApplyDetail(step)) return true
  return step != null &&
    step.expression == null &&
    step.calculation == null &&
    (hasRichRatingStepDetail(step) || hasRichBandingDetail(step))
}

function initialTraceTab(trace: TraceResult): TraceTab {
  const targetStep = findTargetStep(trace.steps, trace.column)
  return isCalculationRoutineSparse(targetStep) ? "nodes" : "calculation"
}

function traceTabKey(trace: TraceResult): string {
  return `${trace.target_node_id}\u0000${trace.row_index}\u0000${trace.column ?? ""}`
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

function OptimiserOnlineDetail({ detail, labelStyle, valueStyle }: {
  detail: Extract<OptimiserApplyNodeDetail, { mode: "online" }>
  labelStyle: CSSProperties
  valueStyle: CSSProperties
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

  return (
    <div className="my-2 space-y-2 text-[11px]" style={{ color: "var(--text-secondary)" }}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span style={labelStyle}>Optimiser Apply</span>
        <span className="px-1.5 py-0.5 rounded font-mono" style={{ ...valueStyle, background: "rgba(255,255,255,.06)" }}>
          {detail.output_column} = {formatValue(detail.output_value)}
        </span>
        {detail.quote_id_column && (
          <span className="px-1.5 py-0.5 rounded font-mono" style={{ ...valueStyle, background: "rgba(255,255,255,.04)" }}>
            {detail.quote_id_column}: {formatValue(detail.quote_id_value)}
          </span>
        )}
      </div>

      {selected && (
        <div className="rounded px-2 py-1.5" style={{ background: "var(--accent-soft)" }}>
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-semibold" style={{ color: "var(--accent)" }}>Selected scenario</span>
            <span className="rounded px-1.5 py-0.5 font-mono text-[10px]" style={{ color: "var(--text-secondary)", background: "rgba(255,255,255,.045)" }}>
              {scenarioLabel}: {formatValue(selected.scenario_value)}
            </span>
            <span className="rounded px-1.5 py-0.5 font-mono text-[10px]" style={{ color: "var(--text-secondary)", background: "rgba(255,255,255,.035)" }}>
              index: {formatValue(selected.scenario_index)}
            </span>
            {scoreComparison && isFiniteNumber(scoreComparison.gapToNextBest) && (
              <span className="rounded px-1.5 py-0.5 font-mono text-[10px]" style={{ color: "var(--text-muted)", background: "rgba(255,255,255,.03)" }}>
                gap: {formatSignedValue(scoreComparison.gapToNextBest)}
              </span>
            )}
          </div>
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
        </div>
      )}

      {points.length > 0 && (
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
      )}

      {displayCandidates.length > 0 && (
        <div className="space-y-1 overflow-x-auto" aria-label="Optimiser candidates">
          <div className={`${candidateGridClass} tabular-nums text-[10px] font-semibold uppercase`} style={labelStyle}>
            <span>Index</span>
            <span>{scenarioLabel}</span>
            <span className="text-center">{objectiveLabel}</span>
            {hasConstraintColumns && (
              <>
                <span className="text-center" style={{ overflowWrap: "anywhere" }}>{constraintHeader}</span>
                <span className="text-center">Lambda Term</span>
              </>
            )}
            <span className="text-center">Score</span>
          </div>
          {displayCandidates.map((candidate) => {
            const isSelected = optimiserCandidateIsSelected(candidate, selected)
            const candidateConstraints = formatOptimiserRecordCell(candidate.constraints, constraintNames)
            const candidateLambdaTerms = formatOptimiserRecordCell(candidate.lambda_terms, constraintNames, { signed: true })
            return (
              <div
                key={candidate.scenario_index}
                className={`${candidateGridClass} rounded border-l-2 px-1 py-0.5 font-mono text-[10px] tabular-nums`}
                style={{
                  background: isSelected ? "var(--accent-soft)" : "transparent",
                  borderColor: isSelected ? "var(--accent)" : "transparent",
                  color: isSelected ? "var(--text-primary)" : "var(--text-secondary)",
                }}
              >
                <span>{candidate.scenario_index}</span>
                <span className="inline-flex min-w-0 items-center gap-1" style={{ overflowWrap: "anywhere" }}>
                  <span className="min-w-0">{formatValue(candidate.scenario_value)}</span>
                  {isSelected && (
                    <span className="shrink-0 rounded px-1 font-sans text-[9px] font-semibold" style={{ color: "var(--accent)", background: "rgba(255,255,255,.06)" }}>
                      selected
                    </span>
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
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function OptimiserRatebookDetail({ detail, labelStyle, valueStyle }: {
  detail: Extract<OptimiserApplyNodeDetail, { mode: "ratebook" }>
  labelStyle: CSSProperties
  valueStyle: CSSProperties
}) {
  const factors = Array.isArray(detail.factors) ? detail.factors : []
  const ratebookGridClass = "grid grid-cols-[minmax(9rem,12rem)_minmax(7rem,9rem)_minmax(5rem,6rem)_minmax(5rem,6rem)] gap-1.5"

  return (
    <div className="my-2 space-y-2 text-[11px]" style={{ color: "var(--text-secondary)" }}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span style={labelStyle}>Optimiser Apply</span>
        <span className="px-1.5 py-0.5 rounded font-mono" style={{ ...valueStyle, background: "rgba(255,255,255,.06)" }}>
          {detail.output_column} = {formatValue(detail.output_value)}
        </span>
      </div>

      <div className="rounded px-2 py-1.5" style={{ background: "var(--accent-soft)" }}>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-semibold" style={{ color: "var(--accent)" }}>Selected ratebook</span>
          <span className="rounded px-1.5 py-0.5 font-mono text-[10px]" style={{ color: "var(--text-secondary)", background: "rgba(255,255,255,.045)" }}>
            base: {formatValue(detail.base_value)}
          </span>
          <span className="rounded px-1.5 py-0.5 font-mono text-[10px]" style={{ color: "var(--text-secondary)", background: "rgba(255,255,255,.035)" }}>
            final: {formatValue(detail.final_value)}
          </span>
          <span className="rounded px-1.5 py-0.5 font-mono text-[10px]" style={{ color: "var(--text-muted)", background: "rgba(255,255,255,.03)" }}>
            {factors.length} factor{factors.length === 1 ? "" : "s"}
          </span>
        </div>
      </div>

      {detail.message && (
        <div className="rounded px-2 py-1 font-mono text-[10px]" style={{ background: "rgba(255,255,255,.035)", color: "var(--text-muted)" }}>
          {detail.message}
        </div>
      )}

      {factors.length > 0 && (
      <div className="space-y-1 overflow-x-auto" aria-label="Optimiser ratebook ladder">
        <div className={`${ratebookGridClass} tabular-nums text-[10px] font-semibold uppercase`} style={labelStyle}>
          <span>Factor</span>
          <span className="text-center">Input</span>
          <span className="text-center">Value</span>
          <span className="text-center">Total</span>
        </div>
        {factors.map((factor: OptimiserApplyRatebookFactorDetail) => (
          <div key={factor.name} className={`${ratebookGridClass} rounded border-l-2 px-1 py-0.5 font-mono text-[10px] tabular-nums`} style={{ borderColor: "transparent" }}>
            <span style={{ overflowWrap: "anywhere", color: "var(--text-secondary)" }}>
              {factor.name}
              {factor.default_used && (
                <span className="ml-1 px-1 py-0.5 rounded font-sans text-[9px] font-bold" style={{ background: "var(--warning-bright-soft-strong)", color: "var(--warning)" }}>
                  default used
                </span>
              )}
            </span>
            <span className="text-center" style={{ color: "var(--text-muted)", overflowWrap: "anywhere" }}>{formatValue(factor.input_value)}</span>
            <span className="text-center" style={{ color: "var(--accent)" }}>{formatValue(factor.factor_value)}</span>
            <span className="text-center" style={{ color: "var(--text-primary)" }}>{formatValue(factor.running_total)}</span>
          </div>
        ))}
      </div>
      )}
    </div>
  )
}

function OptimiserApplyErrorDetail({ detail, labelStyle, valueStyle }: {
  detail: Extract<OptimiserApplyNodeDetail, { status: "error" }>
  labelStyle: CSSProperties
  valueStyle: CSSProperties
}) {
  return (
    <div className="my-2 space-y-2 text-[11px]" style={{ color: "var(--text-secondary)" }}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span style={labelStyle}>Optimiser Apply</span>
        <span className="px-1.5 py-0.5 rounded font-mono" style={{ ...valueStyle, background: "rgba(255,255,255,.06)" }}>
          {detail.mode}
        </span>
      </div>
      <div role="alert" className="rounded px-2 py-1" style={{ background: "var(--danger-soft)", color: "var(--danger-text)" }}>
        Trace failed: {detail.error}
        {detail.error_type ? ` (${detail.error_type})` : ""}
      </div>
    </div>
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

  const labelStyle = { color: "var(--text-muted)", fontSize: "10px" }
  const valueStyle = { color: "var(--text-secondary)", fontSize: "11px", fontFamily: "var(--font-mono, monospace)" }

  if (detailType === "rating_step" && (Array.isArray(detail.tables) || Array.isArray(detail.combined_outputs))) {
    const tables = asRatingStepTables(detail)
    const combinedOutputs = asRatingStepCombinedOutputs(detail)

    return (
      <div className="my-2 space-y-2 text-[11px]" style={{ color: "var(--text-secondary)" }}>
        {tables.length > 0 && (
          <div className="space-y-1.5">
            <div style={labelStyle}>Rating Tables</div>
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
                      <span className="px-1.5 py-0.5 rounded text-[10px] font-bold" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>
                        traced column
                      </span>
                    )}
                    {status && (
                      <span className="px-1.5 py-0.5 rounded text-[10px] font-bold" style={ratingStatusStyle(status)}>
                        status: {formatRatingStatus(status)}
                      </span>
                    )}
                    {table.selected_value !== undefined && (
                      <span className="font-mono" style={{ color: "var(--accent)" }}>
                        selected: {formatValue(table.selected_value)}
                      </span>
                    )}
                    {table.default_value !== undefined && (
                      <span className="font-mono" style={{ color: "var(--text-muted)" }}>
                        default: {formatValue(table.default_value)}
                      </span>
                    )}
                    {table.default_used && (
                      <span className="px-1.5 py-0.5 rounded text-[10px] font-bold" style={{ background: "var(--warning-bright-soft-strong)", color: "var(--warning)" }}>
                        default used
                      </span>
                    )}
                  </div>
                  {table.factors && table.factors.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {table.factors.map((factor) => (
                        <span key={`${factor.column}-${String(factor.value)}`} className="px-1 py-0.5 rounded font-mono" style={{ background: "rgba(255,255,255,.06)" }}>
                          {factor.column}: {formatValue(factor.value)}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}

        {combinedOutputs.length > 0 && (
          <div className="space-y-1.5">
            <div style={labelStyle}>Combined Outputs</div>
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
                      <span className="px-1.5 py-0.5 rounded text-[10px] font-bold" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>
                        traced column
                      </span>
                    )}
                  </div>
                  <div style={valueStyle}>
                    {combined.operation} from base {formatValue(combined.base_value)}
                  </div>
                  {Object.keys(combined.input_values).length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {Object.entries(combined.input_values).map(([column, value]) => (
                        <span key={column} className="px-1 py-0.5 rounded font-mono" style={{ background: "rgba(255,255,255,.06)" }}>
                          {column}: {formatValue(value)}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    )
  }

  if (detailType === "rate_table_lookup" || detailType === "rating_step") {
    const keys = detail.lookup_keys as Record<string, unknown> | undefined
    const matched = detail.matched_row
    const defaultUsed = detail.default_used as boolean | undefined
    return (
      <div className="my-2 space-y-1 text-[11px]" style={{ color: "var(--text-secondary)" }}>
        <div style={labelStyle}>Rate Table Lookup</div>
        {keys && (
          <div className="flex flex-wrap gap-1">
            {Object.entries(keys).map(([k, v]) => (
              <span key={k} className="px-1 py-0.5 rounded font-mono" style={{ background: "rgba(255,255,255,.06)" }}>
                {k}: {String(v)}
              </span>
            ))}
          </div>
        )}
        {matched != null && <div style={valueStyle}>Matched row: {String(matched)}</div>}
        {defaultUsed && (
          <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-bold" style={{ background: "var(--warning-bright-soft-strong)", color: "var(--warning)" }}>
            default used
          </span>
        )}
      </div>
    )
  }

  if (detailType === "banding") {
    const banding = asBandingDetail(detail)
    const inputColumn = banding.input_column ?? banding.column
    const matchedBand = banding.matched_band ?? banding.selected_band
    const hasBandingSummary = banding.input_value !== undefined || matchedBand !== undefined
    const factorCount = Array.isArray(banding.factors) ? banding.factors.length : 0
    const hasRange = banding.lower_bound != null || banding.upper_bound != null
    const lower = banding.lower_bound != null ? formatValue(banding.lower_bound) : ""
    const upper = banding.upper_bound != null ? formatValue(banding.upper_bound) : ""
    const lowerBracket = banding.lower_inclusive === false ? "(" : "["
    const upperBracket = banding.upper_inclusive === false ? ")" : "]"
    const bandingSummary = `${inputColumn ? `${inputColumn}=` : ""}${formatValue(banding.input_value)} -> ${formatValue(matchedBand)}`
    const rangeSummary = `${lowerBracket}${lower}, ${upper}${upperBracket}`
    return (
      <div className="my-2 flex flex-wrap items-center gap-1.5 text-[11px]" style={{ color: "var(--text-secondary)" }}>
        {showBandingSummary && <span style={labelStyle}>Banding</span>}
        {showBandingSummary && hasBandingSummary && (
          <span
            aria-label={`Banding: ${bandingSummary}`}
            className="px-1.5 py-0.5 rounded"
            style={{ ...valueStyle, background: "rgba(255,255,255,.06)" }}
          >
            {bandingSummary}
          </span>
        )}
        {showBandingSummary && !hasBandingSummary && factorCount > 0 && (
          <span className="px-1.5 py-0.5 rounded" style={{ ...valueStyle, background: "rgba(255,255,255,.06)" }}>
            {factorCount} factor{factorCount === 1 ? "" : "s"}
          </span>
        )}
        {hasRange && (
          <span className="px-1.5 py-0.5 rounded" style={{ ...valueStyle, background: "rgba(255,255,255,.04)" }}>
            {rangeSummary}
          </span>
        )}
        {banding.is_default && (
          <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-bold" style={{ background: "var(--warning-bright-soft-strong)", color: "var(--warning)" }}>
            default
          </span>
        )}
      </div>
    )
  }

  if (detailType === "optimiser_apply") {
    const optimiserDetail = asOptimiserApplyDetail(detail)
    if (isOptimiserApplyErrorDetail(optimiserDetail)) {
      return (
        <OptimiserApplyErrorDetail
          detail={optimiserDetail}
          labelStyle={labelStyle}
          valueStyle={valueStyle}
        />
      )
    }
    if (optimiserDetail.mode === "online") {
      return (
        <OptimiserOnlineDetail
          detail={optimiserDetail}
          labelStyle={labelStyle}
          valueStyle={valueStyle}
        />
      )
    }
    if (optimiserDetail.mode === "ratebook") {
      return (
        <OptimiserRatebookDetail
          detail={optimiserDetail}
          labelStyle={labelStyle}
          valueStyle={valueStyle}
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
      <div className="my-2 space-y-2 text-[11px]" style={{ color: "var(--text-secondary)" }}>
        <div style={labelStyle}>{modelScoreTitle(modelDetail)}</div>
        {prediction.hasPrediction && !showContributionLadder && (
          <div style={valueStyle}>
            Prediction{predictionColumn ? `: ${predictionColumn}` : ""} = {formatValue(prediction.value)}
          </div>
        )}
        {featureColumns.length > 0 && !showContributionLadder && (
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
        )}
        {explanation?.status === "error" && (
          <div role="alert" className="rounded px-2 py-1" style={{ background: "var(--danger-soft)", color: "var(--danger-text)" }}>
            Explanation failed: {String(explanation.error || "unknown error")}
          </div>
        )}
        {explanation?.status !== "error" && explanation?.base_value !== undefined && !showContributionLadder && (
          <div className="space-y-1">
            <div style={valueStyle}>Base value: {formatValue(explanation.base_value)}</div>
            {explanation.prediction_from_shap !== undefined && (
              <div style={labelStyle}>
                base + contributions = {formatValue(explanation.prediction_from_shap)}
                {outputSpace ? ` (${outputSpace})` : ""}
              </div>
            )}
          </div>
        )}
        {showContributionLadder && (
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
        )}
        {!showContributionLadder && contributions.length > 0 && (
          <div className="mt-1 space-y-1" aria-label="Model feature contributions">
            <div style={labelStyle}>Contributions</div>
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
        )}
      </div>
    )
  }

  if (detailType === "scenario_expander") {
    return (
      <div className="my-2 space-y-1 text-[11px]" style={{ color: "var(--text-secondary)" }}>
        <div style={labelStyle}>Scenario Expander</div>
        <div style={valueStyle}>Step: {String(detail.step)}</div>
        <div style={valueStyle}>Multiplier: {String(detail.multiplier)}</div>
      </div>
    )
  }

  if (detailType === "live_switch") {
    return (
      <div className="my-2 space-y-1 text-[11px]" style={{ color: "var(--text-secondary)" }}>
        <div style={labelStyle}>Branch Selection</div>
        <div style={valueStyle}>Selected: {String(detail.selected_branch)}</div>
      </div>
    )
  }

  // Default: render as JSON
  return (
    <div className="my-2 text-[10px] font-mono" style={{ color: "var(--text-muted)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
      <pre>{JSON.stringify(detail, null, 2)}</pre>
    </div>
  )
}

function StepCard({ step, index, tracedColumn, isTargetStep, defaultExpanded = false }: { step: TraceStep; index: number; tracedColumn: string | null; isTargetStep?: boolean; defaultExpanded?: boolean }) {
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
  const richModelDetail = hasRichModelScoreDetail(step)
  const richOptimiserDetail = hasRichOptimiserApplyDetail(step)
  const richNodeDetail = richModelDetail || richOptimiserDetail
  const isOriginStep = isTraceOriginStep(step, tracedColumn)
  const sourceCalculationIsPlaceholder = isComputedPlaceholder(step.calculation?.substituted_text)
  const showSourceOrigin = isOriginStep &&
    (step.expression?.expression_type === "opaque" || sourceCalculationIsPlaceholder)
  const showOpaqueComputed = step.expression?.expression_type === "opaque" && !richNodeDetail && !isOriginStep
  const rawCalculationBlockText = step.calculation != null &&
    !(richNodeDetail && isComputedPlaceholder(step.calculation.substituted_text)) &&
    !(isOriginStep && sourceCalculationIsPlaceholder)
    ? step.calculation.substituted_text
    : null
  const calculationBlockText = rawCalculationBlockText != null && rawCalculationBlockText.trim().length > 0
    ? rawCalculationBlockText
    : null

  return (
    <div
      className="rounded-lg overflow-hidden transition-opacity"
      style={{
        border: relevant ? `1px solid ${accent}40` : "1px solid var(--border)",
        background: "var(--bg-elevated)",
        opacity: relevant ? 1 : 0.55,
      }}
    >
      {/* Collapsed header — hover bg driven by Tailwind.  The inline
          `background: transparent` is intentionally omitted so the
          Tailwind `hover:` rule can apply (inline > class specificity). */}
      <button
        onClick={() => setExpanded(!expanded)}
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
        <div className="px-3 pb-3" style={{ borderTop: "1px solid var(--border)" }}>
          {/* Expression block */}
          {step.expression && step.expression.expression_type !== "opaque" && (
            <div
              className="my-2 px-2 py-1.5 rounded text-[11px] font-mono"
              style={{ background: "rgba(255,255,255,.04)", color: "var(--text-secondary)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}
            >
              {formatExpression(step.expression.expression_text, 200)}
            </div>
          )}
          {showOpaqueComputed && (
            <div className="my-2 text-[11px]" style={{ color: "var(--text-muted)", fontStyle: "italic" }}>
              computed
            </div>
          )}
          {showSourceOrigin && (
            <div className="my-2 flex items-baseline gap-1.5 text-[11px]" style={{ color: "var(--text-muted)" }}>
              <span>Source node</span>
              <span className="font-mono font-semibold" style={{ color: "var(--text-secondary)" }}>
                {step.node_name}
              </span>
            </div>
          )}

          {/* Calculation block */}
          {calculationBlockText != null && (
            <div
              className="my-2 px-2 py-1.5 rounded text-[12px] font-mono font-semibold"
              style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
            >
              {calculationBlockText}
            </div>
          )}

          {/* Node detail section */}
          {step.node_detail && (
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

          {/* Column values table (shown when no expression/calculation detail) */}
          {!step.expression && !step.calculation && <div className="space-y-0.5">
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

type TraceTab = "calculation" | "nodes"

interface TracePanelProps {
  trace: TraceResult
  onClose: () => void
}

type DetailLevel = "formula" | "sources" | "all"

export default function TracePanel({ trace, onClose }: TracePanelProps) {
  const currentTraceTabKey = traceTabKey(trace)
  const [activeTabState, setActiveTabState] = useState<{ traceKey: string; activeTab: TraceTab }>(() => ({
    traceKey: currentTraceTabKey,
    activeTab: initialTraceTab(trace),
  }))
  const activeTab = activeTabState.traceKey === currentTraceTabKey
    ? activeTabState.activeTab
    : initialTraceTab(trace)
  const setActiveTab = (activeTab: TraceTab) => {
    setActiveTabState({ traceKey: currentTraceTabKey, activeTab })
  }
  const [detailLevel, setDetailLevel] = useState<DetailLevel>("sources")
  const [copied, setCopied] = useState(false)
  const [showHidden, setShowHidden] = useState(false)
  const copyTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined)
  const addToast = useToastStore((s) => s.addToast)

  // Clear copy timer on unmount
  useEffect(() => () => clearTimeout(copyTimerRef.current), [])

  const targetStep = useMemo(() => findTargetStep(trace.steps, trace.column), [trace.steps, trace.column])
  const targetStepOpensAsNodeDetail = isCalculationRoutineSparse(targetStep)
  // Build a set of node IDs that are collapsed (pass-through)
  const collapsedIds = useMemo(() => {
    if (!trace.column) return new Set<string>()
    const entries = collapsePassthroughs(trace.steps, trace.column)
    const ids = new Set<string>()
    for (const entry of entries) {
      if ("collapsed" in entry) {
        for (const step of entry.collapsed) {
          ids.add(step.node_id)
        }
      }
    }
    return ids
  }, [trace.steps, trace.column])

  const handleCopy = async () => {
    const md = traceToMarkdown(trace, targetStep)
    let didCopy = false
    try {
      await navigator.clipboard.writeText(md)
      didCopy = true
    } catch (clipboardError) {
      // WHY console-only: the fallback may still complete the user action.
      console.warn("Clipboard API copy failed; trying document fallback", clipboardError)
      const ta = document.createElement("textarea")
      ta.value = md
      document.body.appendChild(ta)
      ta.select()
      try {
        didCopy = document.execCommand("copy")
        if (!didCopy) {
          throw new Error("document.execCommand('copy') returned false")
        }
      } catch (fallbackError) {
        // WHY console-only: the user-visible toast below reports the failed copy.
        console.warn("Clipboard fallback copy failed", fallbackError)
      } finally {
        document.body.removeChild(ta)
      }
    }
    if (!didCopy) {
      addToast("error", "Could not copy trace markdown")
      return
    }
    setCopied(true)
    clearTimeout(copyTimerRef.current)
    copyTimerRef.current = setTimeout(() => setCopied(false), 2000)
  }

  // Split steps into visible vs hidden
  const visibleSteps: TraceStep[] = []
  const hiddenSteps: TraceStep[] = []
  for (const step of trace.steps) {
    if (detailLevel === "all" || showHidden || !collapsedIds.has(step.node_id)) {
      visibleSteps.push(step)
    } else {
      hiddenSteps.push(step)
    }
  }

  const targetHasRichPrimaryDetail = hasRichModelScoreDetail(targetStep) || hasRichOptimiserApplyDetail(targetStep)

  return (
    <PanelShell>
      {/* Header */}
      <div
        className="px-4 py-3 flex items-center gap-2 shrink-0"
        style={{ borderBottom: "1px solid var(--border)" }}
      >
        <Scan size={14} style={{ color: "var(--accent)" }} />
        <div className="flex-1 min-w-0">
          <div className="text-xs font-bold" style={{ color: "var(--text-primary)" }}>
            Trace{trace.column ? `: ${trace.column}` : ""}
          </div>
          <div className="text-[11px]" style={{ color: "var(--text-muted)" }}>
            {trace.row_id_column && trace.row_id_value != null ? (
              <><span className="font-mono">{trace.row_id_column}</span> = <span className="font-mono font-medium" style={{ color: "var(--text-secondary)" }}>{formatValue(trace.row_id_value)}</span></>
            ) : (
              <>Row {trace.row_index}</>
            )}
            {" "}&middot; {trace.nodes_in_trace} of {trace.total_nodes_in_pipeline} nodes
          </div>
        </div>
        <button
          onClick={handleCopy}
          className="p-1 rounded transition-colors hover:bg-[var(--bg-hover)]"
          style={{ color: copied ? "var(--color-added, var(--success-hover))" : "var(--text-muted)" }}
          title={copied ? "Copied trace" : "Copy trace as markdown"}
        >
          {copied ? <Check size={14} /> : <Copy size={14} />}
        </button>
        <button
          onClick={onClose}
          className="p-1 rounded transition-colors hover:bg-[var(--bg-hover)]"
          style={{ color: "var(--text-muted)" }}
        >
          <X size={14} />
        </button>
      </div>

      {/* Tab selection: Calculation | Nodes */}
      <div className="shrink-0 px-3 py-1.5" style={{ borderBottom: "1px solid var(--border)" }}>
        <div style={{
          display: "flex", background: "rgba(0,0,0,.2)", borderRadius: 6,
          padding: 2, gap: 2, border: "1px solid rgba(255,255,255,.05)",
        }}>
          {(["calculation", "nodes"] as const).map((tab) => {
            const active = activeTab === tab
            return (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                style={{
                  flex: 1, padding: "6px 0", fontSize: 11, fontWeight: 600,
                  borderRadius: 4, border: "none", cursor: "pointer",
                  transition: "all 150ms ease",
                  background: active ? "var(--accent-soft)" : "transparent",
                  color: active ? "var(--accent-hover)" : "rgba(255,255,255,.35)",
                  boxShadow: active ? "0 1px 3px rgba(0,0,0,.2)" : "none",
                }}
              >
                {tab.charAt(0).toUpperCase() + tab.slice(1)}
              </button>
            )
          })}
        </div>
      </div>

      {/* Tab content */}
      {activeTab === "calculation" ? (
        /* Calculation tab — full derivation */
        <div className="flex-1 overflow-y-auto" style={{
          background: "linear-gradient(180deg, var(--accent-soft-faint) 0%, var(--accent-soft-whisper) 100%)",
        }}>
          {targetStep && trace.column ? (
            targetHasRichPrimaryDetail && targetStep.node_detail ? (
              <div className="px-3 py-3">
                <NodeDetailBlock detail={targetStep.node_detail} tracedColumn={trace.column} />
              </div>
            ) : (
              <>
                <CalculationHero
                  column={trace.column}
                  expression={targetStep.expression ?? null}
                  calculation={targetStep.calculation ?? null}
                  executionMs={trace.execution_ms}
                  stepCount={trace.steps.length}
                  nodeName={targetStep.node_name}
                  nodeType={targetStep.node_type}
                  isSourceOrigin={isTraceOriginStep(targetStep, trace.column)}
                  waterfall={trace.waterfall}
                />
                {targetStep.node_detail && (
                  hasRichRatingStepDetail(targetStep) ||
                  (
                    hasRichBandingDetail(targetStep) &&
                    (targetStep.calculation == null || hasBandingSecondaryDetail(targetStep.node_detail))
                  )
                ) && (
                  <div className="px-3 pb-3">
                    <NodeDetailBlock
                      detail={targetStep.node_detail}
                      tracedColumn={trace.column}
                      showBandingSummary={targetStep.calculation == null}
                    />
                  </div>
                )}
              </>
            )
          ) : (
            <div className="px-4 py-4">
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-medium" style={{ color: "var(--text-muted)" }}>Result</span>
                <span className="px-2 py-0.5 rounded text-[13px] font-mono font-bold" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>
                  {formatValue(trace.output_value)}
                </span>
              </div>
            </div>
          )}
        </div>
      ) : (
        /* Nodes tab — pipeline node list */
        <div className="flex-1 overflow-hidden flex flex-col">
          {/* Detail level toggle */}
          <div className="px-3 py-1.5 shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
            <div style={{
              display: "flex", background: "rgba(0,0,0,.2)", borderRadius: 6,
              padding: 2, gap: 2, border: "1px solid rgba(255,255,255,.05)",
            }}>
              {(["formula", "sources", "all"] as const).map((level) => {
                const active = detailLevel === level
                return (
                  <button
                    key={level}
                    onClick={() => { setDetailLevel(level); setShowHidden(false) }}
                    style={{
                      flex: 1, padding: "6px 0", fontSize: 11, fontWeight: 600,
                      borderRadius: 4, border: "none", cursor: "pointer",
                      transition: "all 150ms ease",
                      background: active ? "var(--accent-soft)" : "transparent",
                      color: active ? "var(--accent-hover)" : "rgba(255,255,255,.35)",
                      boxShadow: active ? "0 1px 3px rgba(0,0,0,.2)" : "none",
                    }}
                  >
                    {level.charAt(0).toUpperCase() + level.slice(1)}
                  </button>
                )
              })}
            </div>
          </div>
          {/* Steps list */}
          <div className="flex-1 overflow-y-auto p-3 space-y-2">
            {detailLevel === "formula" ? (
              targetStep && (
                <StepCard step={targetStep} index={trace.steps.indexOf(targetStep)} tracedColumn={trace.column} isTargetStep={true} defaultExpanded={targetStepOpensAsNodeDetail} />
              )
            ) : (
              <>
                {visibleSteps.map((step) => (
                  <StepCard
                    key={step.node_id}
                    step={step}
                    index={trace.steps.indexOf(step)}
                    tracedColumn={trace.column}
                    isTargetStep={targetStep?.node_id === step.node_id}
                    defaultExpanded={targetStepOpensAsNodeDetail && targetStep?.node_id === step.node_id}
                  />
                ))}
                {hiddenSteps.length > 0 && !showHidden && (
                  <button
                    onClick={() => setShowHidden(true)}
                    className="trace-hidden-toggle w-full py-1.5 rounded text-[11px] transition-colors"
                    style={{ color: "var(--text-muted)", border: "1px dashed var(--border)", fontStyle: "italic" }}
                  >
                    {hiddenSteps.length} pass-through node{hiddenSteps.length > 1 ? "s" : ""} hidden
                  </button>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </PanelShell>
  )
}
