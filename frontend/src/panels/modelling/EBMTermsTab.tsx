/**
 * EBM terms: the model itself, not a post-hoc explanation.
 *
 * An EBM predicts intercept + one additive score per term on the link scale.
 * Main effects are shape functions (a score per category or per value bin,
 * with the missing-value bin first); a pairwise interaction is one surface
 * over two features. Scores are additive term scores, never SHAP values.
 * Each term shows as a table by default, with a chart of it a click away.
 */
import { useState } from "react"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { ChartEmptyState } from "./ChartScaffold"
import { formatChartNumber } from "../../utils/chartHelpers"
import { FeatureBrowser } from "./FeatureBrowser"
import { TERM_DISPLAY_OPTIONS, TermTableView, ViewSwitch, type TermDisplay } from "./TermTableView"

export function EBMTermsTab({ result }: { result: TrainResult }) {
  const terms = result.ebm_terms ?? []
  const [selected, setSelected] = useState<string | null>(null)
  const [search, setSearch] = useState("")
  const [display, setDisplay] = useState<TermDisplay>("table")
  if (!terms.length) return <ChartEmptyState>No EBM terms available</ChartEmptyState>
  const active = terms.find((term) => term.term === selected) ?? terms[0]
  const interaction = active.kind === "interaction"
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
              {interaction ? "Pairwise interaction" : "Main effect"} · importance{" "}
              {formatChartNumber(active.importance)}
            </p>
          </div>
          <ViewSwitch label="Term display" options={TERM_DISPLAY_OPTIONS} value={display} onChange={setDisplay} />
        </div>
        <TermTableView
          key={active.term}
          title={`${interaction ? "Interaction surface" : "Shape function"} for ${active.term}`}
          axes={active.axes}
          values={active.scores}
          reference={0}
          valueLabel="Score"
          display={display}
        />
      </div>
    </div>
  )
}
