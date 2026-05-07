import { useState, useMemo, useRef, useEffect } from "react"
import { X, ChevronDown, ChevronRight, Scan, Copy, Check } from "lucide-react"
import type {
  BandingNodeDetail,
  ModelScoreContributionDetail,
  ModelScoreExplanationDetail,
  ModelScoreNodeDetail,
  RatingStepCombinedOutputDetail,
  RatingStepTableDetail,
  TraceNodeDetail,
  TraceResult,
  TraceStep,
} from "../types/trace"
import { nodeTypeLabels, nodeTypeColors } from "../utils/nodeTypes"
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

function hasBandingSecondaryDetail(detail: TraceNodeDetail | null | undefined): boolean {
  return detail?.detail_type === "banding" &&
    (detail.lower_bound != null || detail.upper_bound != null || detail.is_default === true)
}

function isComputedPlaceholder(value: string | undefined): boolean {
  return value?.trim().toLowerCase() === "computed"
}

function isCalculationRoutineSparse(step: TraceStep | null | undefined): boolean {
  if (hasRichModelScoreDetail(step)) return true
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
        const value = contribution.shap_value
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
  const showOpaqueComputed = step.expression?.expression_type === "opaque" && !richModelDetail
  const calculationBlockText = step.calculation != null &&
    !(richModelDetail && isComputedPlaceholder(step.calculation.substituted_text))
    ? step.calculation.substituted_text
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

  const targetHasRichModelDetail = hasRichModelScoreDetail(targetStep)

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
            targetHasRichModelDetail && targetStep.node_detail ? (
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
