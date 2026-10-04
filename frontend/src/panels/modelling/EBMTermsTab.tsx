/**
 * EBM terms: the model itself, not a post-hoc explanation.
 *
 * An EBM predicts intercept + one additive score per term on the link scale.
 * Main effects are shape functions (a score per category or per value bin,
 * with the missing-value bin first); a pairwise interaction is one surface
 * over two features. Scores are additive term scores, never SHAP values.
 */
import { useState } from "react"
import type { EbmTerm, EbmTermAxis } from "../../api/types"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { ChartEmptyState } from "./ChartScaffold"
import { formatChartNumber } from "../../utils/chartHelpers"
import { FeatureBrowser } from "./FeatureBrowser"
import { LevelBars, StepShape, SurfaceTable } from "./termCharts"

export function EBMTermsTab({ result }: { result: TrainResult }) {
  const terms = result.ebm_terms ?? []
  const [selected, setSelected] = useState<string | null>(null)
  const [search, setSearch] = useState("")
  if (!terms.length) return <ChartEmptyState>No EBM terms available</ChartEmptyState>
  const active = terms.find((term) => term.term === selected) ?? terms[0]
  return (
    <div className="validation-feature-layout">
      <FeatureBrowser
        features={terms.map((term) => ({ feature: term.term, importance: term.importance }))}
        selected={active.term}
        onSelect={setSelected}
        search={search}
        onSearch={setSearch}
      />
      <div className="min-w-0">
        <div className="validation-chart-title">
          <div>
            <h4 className="validation-feature-heading">{active.term}</h4>
            <p className="validation-chart-description">
              {active.kind === "interaction" ? "Pairwise interaction" : "Main effect"} · importance{" "}
              {formatChartNumber(active.importance)}
            </p>
          </div>
        </div>
        {active.kind === "interaction" ? (
          <InteractionSurface key={active.term} term={active} />
        ) : active.axes[0].type === "nominal" ? (
          <NominalShape key={active.term} term={active} />
        ) : (
          <ContinuousShape key={active.term} term={active} />
        )}
      </div>
    </div>
  )
}

function mainScores(term: EbmTerm): number[] {
  return (term.scores as number[]).map(Number)
}

function NominalShape({ term }: { term: EbmTerm }) {
  return (
    <LevelBars
      title={`Shape function for ${term.term}`}
      labels={term.axes[0].labels}
      values={mainScores(term)}
      reference={0}
      valueLabel="Score"
    />
  )
}

function ContinuousShape({ term }: { term: EbmTerm }) {
  return (
    <StepShape
      title={`Shape function for ${term.term}`}
      labels={term.axes[0].labels}
      values={mainScores(term)}
      reference={0}
      valueLabel="Score"
    />
  )
}

function InteractionSurface({ term }: { term: EbmTerm }) {
  const [first, second] = term.axes as [EbmTermAxis, EbmTermAxis]
  return (
    <SurfaceTable
      title={`Interaction surface for ${term.term}`}
      first={first}
      second={second}
      grid={(term.scores as number[][]).map((row) => row.map(Number))}
      reference={0}
    />
  )
}
