/**
 * Bottom-panel chart view for the optimiser node's input data (pre-solve).
 *
 * Groups preview rows by quote_id and renders per-quote line charts of
 * objective / constraint columns against scenario_index.  The user can
 * navigate between quotes via prev/next arrows or a search input. After a
 * solve the result workspace offers the same panes (Curves, Statistics).
 */

import { useState } from "react"
import { NODE_TYPES } from "../utils/nodeTypes"
import type { PreviewData } from "./DataPreview"
import {
  ScenarioCurvesPane,
  ScenarioDataNotice,
  ScenarioQuoteNavigation,
  ScenarioStatisticsPane,
} from "./optimiser/OptimiserScenarioPanes"
import {
  scenarioDataUnavailable,
  useOptimiserScenarioData,
} from "./optimiser/useOptimiserScenarioData"
import PreviewPanelFrame from "./PreviewPanelFrame"
import PreviewPanelTabs from "./PreviewPanelTabs"

export { OPTIMISER_DATA_PREVIEW_ROW_LIMIT } from "./optimiser/useOptimiserScenarioData"

interface OptimiserDataPreviewProps {
  data: PreviewData
  config: Record<string, unknown>
  onRefresh?: () => void
}

export default function OptimiserDataPreview({
  data,
  config,
  onRefresh,
}: OptimiserDataPreviewProps) {
  const [tab, setTab] = useState<"chart" | "statistics">("chart")
  const scenario = useOptimiserScenarioData(data, config, tab === "statistics")

  const unavailable = scenarioDataUnavailable(scenario)
  if (unavailable) {
    return (
      <PreviewPanelFrame
        nodeLabel={data.nodeLabel}
        nodeType={NODE_TYPES.OPTIMISER}
        collapsedMeta={unavailable === "no_objective" ? "Configure an objective column" : "No scenario data"}
        onRefresh={onRefresh}
        data-testid="optimiser-data-preview-frame"
      >
        <ScenarioDataNotice scenario={scenario} reason={unavailable} />
      </PreviewPanelFrame>
    )
  }

  const tabs = [
    { key: "chart", label: "Chart" },
    { key: "statistics", label: "Statistics" },
  ] as const

  return (
    <PreviewPanelFrame
      nodeLabel={data.nodeLabel}
      nodeType={NODE_TYPES.OPTIMISER}
      subtitle={scenario.metadata}
      onRefresh={onRefresh}
      actions={tab === "chart" ? <ScenarioQuoteNavigation scenario={scenario} /> : null}
      collapsedMeta={scenario.metadata}
      data-testid="optimiser-data-preview-frame"
    >
      <PreviewPanelTabs
        tabs={tabs}
        activeTab={tab}
        onChange={setTab}
        ariaLabel="Optimiser data preview panes"
        accentColor="var(--warning-strong)"
        equalWidth
      />

      {/* Content */}
      <div className="flex-1 overflow-auto px-4 py-3">
        {tab === "chart" ? (
          <ScenarioCurvesPane scenario={scenario} />
        ) : (
          <ScenarioStatisticsPane scenario={scenario} />
        )}
      </div>
    </PreviewPanelFrame>
  )
}
