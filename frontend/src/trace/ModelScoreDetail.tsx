import type { ModelScoreNodeDetail } from "../types/trace"
import { formatTraceValue } from "./traceFormatting"
import {
  TraceDetailAlert,
  TraceDetailChip,
  TraceDetailPanel,
  TraceDetailSection,
} from "./TraceDetail"
import { formatSignedValue, nextRunningTotal } from "./optimiserApplyHelpers"
import {
  modelScoreFeatureColumns,
  modelScorePrediction,
  modelScoreTitle,
  resolveContributionFeatureValue,
} from "./modelScoreHelpers"

const formatValue = formatTraceValue
const traceDetailLabelStyle = { color: "var(--text-muted)", fontSize: 10 }

export function ModelScoreDetailBlock({ detail }: {
  detail: ModelScoreNodeDetail
}) {
  const labelStyle = traceDetailLabelStyle
  const modelDetail = detail
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
  const additivePrediction = explanation?.prediction_from_contributions ?? explanation?.prediction_from_shap
  const predictionFromLadder = additivePrediction ?? prediction.value
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
      {featureColumns.length === 0 && modelDetail.feature_metadata_unavailable && (
        <TraceDetailAlert>
          Feature details unavailable: {modelDetail.feature_metadata_unavailable}
        </TraceDetailAlert>
      )}
      {explanation?.status === "error" && (
        <TraceDetailAlert>
          Explanation failed: {String(explanation.error || "unknown error")}
        </TraceDetailAlert>
      )}
      {explanation?.status !== "error" && explanation?.base_value !== undefined && !showContributionLadder && (
        <TraceDetailSection title="Contribution Summary">
          <TraceDetailChip>Base value: {formatValue(explanation.base_value)}</TraceDetailChip>
          {additivePrediction !== undefined && (
            <div style={labelStyle}>
              base + contributions = {formatValue(additivePrediction)}
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
