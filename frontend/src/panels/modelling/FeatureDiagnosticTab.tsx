import type { ReactNode } from "react"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { ChartEmptyState } from "./ChartScaffold"
import { FeatureBrowser } from "./FeatureBrowser"
import { useDiagnosticFeature, type SharedFeatureBrowser } from "./useDiagnosticFeature"

/**
 * The per-feature diagnostic tab layout AvE and PDP share: a feature browser
 * ranked by importance beside the selected feature's chart, with explicit
 * empty states for no rows at all and for a selection without a row.
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
  const row = rows.find((item) => item.feature === browser.selected)
  if (!browser.features.length) return <ChartEmptyState>No {noun} data available</ChartEmptyState>
  return (
    <div className="validation-feature-layout">
      <FeatureBrowser {...browser} />
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
