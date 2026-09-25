import { RefreshCw, XCircle } from "lucide-react"
import { Suspense, lazy, useCallback, useMemo } from "react"

import ExecutionDiagnosticsSummary from "../components/ExecutionDiagnosticsSummary"
import useNodeDataCache from "../hooks/useNodeDataCache"
import useNodeDataProfile from "../hooks/useNodeDataProfile"
import useSettingsStore from "../stores/useSettingsStore"
import useUIStore, { type ExplorePreviewPane } from "../stores/useUIStore"
import { NODE_GROUP_COLORS } from "../theme/colors"
import DataPreview, { type PreviewData } from "./DataPreview"
import { PREVIEW_PANEL_ACTION_BUTTON_CLASS } from "./previewPanelLayout"
import type { SimpleEdge, SimpleNode } from "./editors"
import { exploreDataView } from "./explore/exploreDataView"
import PreviewPanelFrame from "./PreviewPanelFrame"
import PreviewPanelTabs from "./PreviewPanelTabs"

const ExploreOverviewPane = lazy(() => import("./explore/ExploreOverviewPane"))
const ExplorePivotsPane = lazy(() => import("./explore/ExplorePivotsPane"))
const ExploreChartsPane = lazy(() => import("./explore/ExploreChartsPane"))
const ExploreRelationshipsPane = lazy(() => import("./explore/ExploreRelationshipsPane"))

type ExplorePreviewProps = {
  node: SimpleNode
  allNodes: SimpleNode[]
  edges: SimpleEdge[]
  submodels?: Record<string, unknown>
  preamble?: string
  previewData?: PreviewData | null
  onRefresh?: () => void
  onCellClick?: (rowIndex: number, column: string, rowValues?: Record<string, unknown>) => void
  tracedCell?: { rowIndex: number; column: string } | null
}

const EXPLORE_PREVIEW_PANES = [
  { key: "preview", label: "Preview" },
  { key: "overview", label: "Overview" },
  { key: "pivots", label: "Pivots" },
  { key: "charts", label: "Charts" },
  { key: "relationships", label: "Relationships" },
] as const satisfies readonly { key: ExplorePreviewPane; label: string }[]

/**
 * The Explore preview over the shared data point this node reads.
 *
 * Explore has no cache of its own: its build comes from the shared data-cache
 * hook, and the statistics the Overview, Pivots and Charts
 * panes render come from the shared `profile` analysis of the point's current
 * data version. A Banding or Rating editor on the same input therefore shows
 * the same state and shares the same build.
 */
export default function ExplorePreview({
  node,
  allNodes,
  edges,
  submodels,
  preamble,
  previewData,
  onRefresh,
  onCellClick,
  tracedCell,
}: ExplorePreviewProps) {
  const nodeId = node.id
  const nodeLabel = String(node.data.label || node.id)
  const nodeType = node.data.nodeType
  const activeSource = useSettingsStore((s) => s.activeSource)
  const rememberedPane = useUIStore((s) => s.explorePreviewPanes[nodeId])
  const setExplorePreviewPane = useUIStore((s) => s.setExplorePreviewPane)
  const setExplorePane = useUIStore((s) => s.setExplorePane)

  const cache = useNodeDataCache({ node, allNodes, edges, submodels, preamble })
  const {
    profile,
    executionMetrics,
    profiling,
    message: profileMessage,
    error: profileError,
    refresh: retryProfile,
    cancel: cancelProfile,
  } = useNodeDataProfile({ node, allNodes, edges, submodels, preamble, cache })
  const view = useMemo(
    () =>
      exploreDataView(
        profile,
        cache.point?.data_version,
        cache.point?.point.producer_node_id,
        activeSource,
      ),
    [activeSource, cache.point, profile],
  )

  // Selecting Pivots or Charts here aligns the settings pane to the matching
  // editor pane (one-directional; Preview/Overview leave the editor alone,
  // and editor-side pane changes never touch this preview).
  const selectPreviewPane = useCallback(
    (pane: ExplorePreviewPane) => {
      setExplorePreviewPane(nodeId, pane)
      if (pane === "pivots" || pane === "charts") {
        setExplorePane(nodeId, pane)
      }
    },
    [nodeId, setExplorePane, setExplorePreviewPane],
  )

  const activePane =
    rememberedPane === "overview" ||
    rememberedPane === "pivots" ||
    rememberedPane === "charts" ||
    rememberedPane === "relationships"
      ? rememberedPane
      : "preview"
  const activePaneMeta =
    EXPLORE_PREVIEW_PANES.find((pane) => pane.key === activePane) ?? EXPLORE_PREVIEW_PANES[0]
  const busy = cache.busy || profiling
  const progressPercent = Math.min(Math.max(cache.progress * 100, 0), 100)
  const statusText = profiling
    ? profileMessage || "Profiling data"
    : profileError
      ? `Profiling failed: ${profileError}`
      : null

  return (
    <PreviewPanelFrame
      nodeLabel={nodeLabel}
      nodeType={nodeType}
      onRefresh={onRefresh}
      refreshTitle="Refresh Explore outputs"
      subtitle={statusText ? `${activeSource} | ${statusText}` : activeSource}
      actions={
        <span className="inline-flex items-center gap-1">
          {/* The profile runs after the data is cached, so its own progress and
              its retry are actions of their own. */}
          {profiling ? (
            <button
              type="button"
              onClick={() => void cancelProfile()}
              className={PREVIEW_PANEL_ACTION_BUTTON_CLASS}
              style={{
                color: "var(--danger)",
                background: "var(--danger-soft)",
                border: "1px solid var(--danger-border)",
              }}
              title={profileMessage || "Profiling data"}
              data-testid="explore-profile-cancel"
            >
              <XCircle size={12} className="shrink-0" />
              <span className="truncate">Cancel profile</span>
            </button>
          ) : profileError ? (
            <button
              type="button"
              onClick={() => void retryProfile()}
              className={PREVIEW_PANEL_ACTION_BUTTON_CLASS}
              style={{ color: "var(--text-on-accent)", background: "var(--danger-solid)" }}
              title={`Profiling this data failed: ${profileError}`}
              data-testid="explore-profile-retry"
            >
              <RefreshCw size={12} className="shrink-0" />
              <span className="truncate">Retry profile</span>
            </button>
          ) : null}
        </span>
      }
      data-testid="explore-preview-frame"
    >
      {busy && (
        <div
          role="progressbar"
          aria-label="Explore data progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progressPercent}
          className="h-1 w-full shrink-0"
          style={{ background: "var(--accent-soft)" }}
        >
          <div
            className="h-full transition-all duration-300"
            style={{
              width: `${Math.max(progressPercent, 2)}%`,
              background: NODE_GROUP_COLORS.explore,
            }}
          />
        </div>
      )}

      <ExecutionDiagnosticsSummary metrics={executionMetrics} />

      <PreviewPanelTabs
        tabs={EXPLORE_PREVIEW_PANES}
        activeTab={activePane}
        onChange={selectPreviewPane}
        ariaLabel="Explore result panes"
        accentColor={NODE_GROUP_COLORS.explore}
        idPrefix="explore-preview"
        equalWidth
      />

      <div className="flex-1 min-h-0 flex flex-col" data-testid="explore-preview-body">
        <div
          id={`explore-preview-${activePaneMeta.key}-pane`}
          role="tabpanel"
          aria-labelledby={`explore-preview-${activePaneMeta.key}-tab`}
          className="flex-1 min-h-0 flex flex-col"
          data-testid={`explore-preview-${activePaneMeta.key}-pane`}
        >
          {activePane === "preview" ? (
            <DataPreview
              data={previewData ?? null}
              onCellClick={onCellClick}
              tracedCell={tracedCell}
              embedded
            />
          ) : (
            <Suspense
              fallback={
                <div
                  role="status"
                  className="flex flex-1 items-center justify-center text-xs"
                  style={{ color: "var(--text-muted)" }}
                >
                  Loading {activePane}…
                </div>
              }
            >
              {activePane === "overview" ? (
                <ExploreOverviewPane node={node} report={view} />
              ) : activePane === "pivots" ? (
                <ExplorePivotsPane
                  node={node}
                  allNodes={allNodes}
                  edges={edges}
                  submodels={submodels}
                  preamble={preamble}
                  report={view}
                />
              ) : activePane === "charts" ? (
                <ExploreChartsPane
                  node={node}
                  allNodes={allNodes}
                  edges={edges}
                  submodels={submodels}
                  preamble={preamble}
                  report={view}
                />
              ) : (
                <ExploreRelationshipsPane
                  node={node}
                  allNodes={allNodes}
                  edges={edges}
                  submodels={submodels}
                  preamble={preamble}
                  report={view}
                />
              )}
            </Suspense>
          )}
        </div>
      </div>
    </PreviewPanelFrame>
  )
}
