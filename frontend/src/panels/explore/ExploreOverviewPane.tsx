/**
 * Overview pane for the Explore preview's bottom panel.
 *
 * Drives a small card registry from the node's `config.overview` toggles:
 *
 *   1. No cards enabled         -> hint pointing at the right-panel toggle.
 *   2. >=1 toggle on, no report -> hint pointing at the "Process & cache full data" button.
 *   3. >=1 toggle on, report set -> each enabled card stacked top-to-bottom.
 *
 * Adding a new card means updating the shared definition list and adding one
 * renderer below; empty-state and report-gating logic stays untouched.
 *
 * Purely presentational - no state, no effects, all inputs via props.
 */

import type { JSX } from "react"
import type { ExploreCacheReport } from "../../api/types"
import type { SimpleNode } from "../editors"
import {
  CategoricalSummaryCard,
  DataQualityCard,
  DatasetSnapshotCard,
  NumericSummaryCard,
} from "./ExploreSummaryCards"
import SchemaTableCard from "./SchemaTableCard"
import {
  OVERVIEW_CARD_DEFINITIONS,
  isOverviewCardEnabled,
  type OverviewCardKey,
} from "./overviewCardDefinitions"
import { readOverview } from "./overviewConfig"

interface ExploreOverviewPaneProps {
  node: SimpleNode
  report: ExploreCacheReport | null
}

const CARD_RENDERERS: Record<OverviewCardKey, (report: ExploreCacheReport) => JSX.Element> = {
  dataset_snapshot: (report) => <DatasetSnapshotCard report={report} />,
  schema: (report) => <SchemaTableCard report={report} />,
  numeric_summary: (report) => <NumericSummaryCard report={report} />,
  categorical_summary: (report) => <CategoricalSummaryCard report={report} />,
  data_quality: (report) => <DataQualityCard report={report} />,
}

function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="flex-1 flex items-center justify-center p-4">
      <div className="text-center max-w-md">
        <div className="text-xs font-semibold" style={{ color: "var(--text-secondary)" }}>
          {title}
        </div>
        <div className="mt-1 text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
          {body}
        </div>
      </div>
    </div>
  )
}

export default function ExploreOverviewPane({ node, report }: ExploreOverviewPaneProps) {
  const overview = readOverview(node.data.config ?? {})
  const enabledCards = OVERVIEW_CARD_DEFINITIONS.filter((definition) =>
    isOverviewCardEnabled(overview, definition),
  )

  if (enabledCards.length === 0) {
    return (
      <div
        data-testid="explore-overview-pane"
        className="flex-1 min-h-0 flex flex-col"
      >
        <EmptyState
          title="No cards enabled"
          body="Use the Overview tab in the config panel on the right to enable cards."
        />
      </div>
    )
  }

  if (report === null) {
    return (
      <div
        data-testid="explore-overview-pane"
        className="flex-1 min-h-0 flex flex-col"
      >
        <EmptyState
          title="No cached data yet"
          body="Click 'Process & cache full data' above to populate the enabled cards."
        />
      </div>
    )
  }

  return (
    <div
      data-testid="explore-overview-pane"
      className="flex-1 min-h-0 flex flex-col overflow-y-auto"
    >
      <div className="p-3 space-y-3">
        {enabledCards.map((card) => (
          <div key={card.key}>{CARD_RENDERERS[card.key](report)}</div>
        ))}
      </div>
    </div>
  )
}
