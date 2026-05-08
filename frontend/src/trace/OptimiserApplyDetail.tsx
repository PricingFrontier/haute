import type {
  OptimiserApplyNodeDetail,
  OptimiserApplyRatebookFactorDetail,
} from "../types/trace"
import { formatValue as _formatValue } from "../utils/formatValue"
import { CHART_COLORS } from "../theme/colors"
import {
  TraceDetailAlert,
  TraceDetailCallout,
  TraceDetailChip,
  TraceDetailPanel,
  TraceDetailSection,
  TraceDetailTable,
  TraceDetailTableRow,
} from "./TraceDetail"
import {
  finiteRecordEntries,
  formatOptimiserRecordCell,
  formatSignedValue,
  isFiniteNumber,
  optimiserCandidateGridClass,
  optimiserCandidateIsSelected,
  optimiserChartPath,
  optimiserConstraintNames,
  optimiserDisplayCandidates,
  optimiserScoreComparison,
  optimiserScoreFormulaText,
  optimiserSelectedCandidate,
} from "./optimiserApplyHelpers"

const formatValue = (v: unknown) => _formatValue(v, 2)

export function OptimiserOnlineDetail({ detail }: {
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
  const candidateGridClass = optimiserCandidateGridClass(hasConstraintColumns)
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

export function OptimiserRatebookDetail({ detail }: {
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

export function OptimiserApplyErrorDetail({ detail }: {
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
