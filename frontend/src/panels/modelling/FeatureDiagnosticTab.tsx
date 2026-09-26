import type { ReactNode } from "react"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { ChartEmptyState } from "./ChartScaffold"
import { FeatureBrowser, type FeatureRanking } from "./FeatureBrowser"
import { useDiagnosticFeature, type SharedFeatureBrowser } from "./useDiagnosticFeature"

const IMPORTANCE_RANKING: FeatureRanking = {
  label: "Importance",
  description: "How much each feature contributes to the model's predictions.",
}

/**
 * The per-feature diagnostic tab layout AvE and PDP share: a feature browser
 * ranked by importance beside the selected feature's chart.
 */
export function FeatureDiagnosticTab<TRow extends { feature: string }>({
  result,
  rows,
  featureBrowser,
  noun,
  renderChart,
}: {
  result: TrainResult
  rows: TRow[]
  featureBrowser?: SharedFeatureBrowser
  /** What the tab shows, for its empty states ("AvE", "PDP"). */
  noun: string
  renderChart: (row: TRow) => ReactNode
}) {
  const importance = new Map(
    result.feature_importance.map((item) => [item.feature, item.importance]),
  )
  const browser = useDiagnosticFeature(
    rows.map((item) => ({
      feature: item.feature,
      importance: importance.get(item.feature) ?? 0,
    })),
    featureBrowser,
  )
  return (
    <FeatureDiagnosticLayout
      rows={rows}
      browser={browser}
      rankedBy={IMPORTANCE_RANKING}
      itemNoun="feature"
      noun={noun}
      renderChart={renderChart}
    />
  )
}

/**
 * The per-item diagnostic layout: a ranked, searchable browser beside the
 * selected item's chart, with explicit empty states for no rows at all and
 * for a selection without a row. `rankedBy` names the browser's ranking.
 */
export function FeatureDiagnosticLayout<TRow extends { feature: string }>({
  rows,
  browser,
  rankedBy,
  itemNoun,
  noun,
  renderChart,
}: {
  rows: TRow[]
  browser: SharedFeatureBrowser
  rankedBy: FeatureRanking
  /** What the browser lists, singular ("feature", "factor"). */
  itemNoun: string
  /** What the tab shows, for its empty states ("AvE", "PDP", "rate"). */
  noun: string
  renderChart: (row: TRow) => ReactNode
}) {
  const row = rows.find((item) => item.feature === browser.selected)
  if (!browser.features.length) return <ChartEmptyState>No {noun} data available</ChartEmptyState>
  return (
    <div className="validation-feature-layout">
      <FeatureBrowser {...browser} itemNoun={itemNoun} rankedBy={rankedBy} />
      <div className="min-w-0">
        {row ? (
          renderChart(row)
        ) : (
          <ChartEmptyState>No {noun} data for {browser.selected}</ChartEmptyState>
        )}
      </div>
    </div>
  )
}
