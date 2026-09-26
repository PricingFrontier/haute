/**
 * Bottom-panel visualisations for the optimiser node.
 *
 * Renders in the same slot as DataPreview when an optimiser solve has
 * completed.  Shows Frontier (default when data exists), Summary,
 * Rates (ratebook mode), Quotes (online mode) and Convergence tabs as
 * available. Publishing lives only in the node's Export pane, whose target
 * is the frontier point selected here.
 */

import { useId, useState, useMemo, useCallback, useEffect, useRef } from "react"
import { AlertCircle, ChevronLeft, ChevronRight, Loader2, RefreshCw } from "lucide-react"
import { selectFrontierPoint as selectFrontierPointApi } from "../api/client"
import { apiErrorMessage } from "../api/errors"
import { formatNumber } from "../utils/formatValue"
import useGraphStore from "../stores/useGraphStore"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import useSettingsStore from "../stores/useSettingsStore"
import useUIStore from "../stores/useUIStore"
import { OPTIMISER_COLORS } from "../theme/colors"
import { bandingLevelOrderForOptimiser } from "../utils/banding"
import { NODE_TYPES } from "../utils/nodeTypes"
import type {
  FrontierData,
  OptimiserSolveResult,
} from "../api/types"
import type { SimpleEdge, SimpleNode } from "./editors"
import FrontierChart from "./optimiser/FrontierChart"
import ConvergenceChart from "./optimiser/ConvergenceChart"
import SummaryTab from "./optimiser/SummaryTab"
import DetailCard from "./optimiser/DetailCard"
import RatebookRatesTab from "./optimiser/RatebookRatesTab"
import { hasFactorTables } from "./optimiser/ratebookFactorTables"
import { formatOptimiserIterationSummary } from "./optimiser/iterationSummary"
import QuotesTab from "./optimiser/QuotesTab"
import { isSolveResultStale, startOptimiserSolve } from "./optimiser/solveActions"
import { useOptimiserReadiness } from "./optimiser/useOptimiserReadiness"
import { optimiserResultProvenance } from "./optimiser/resultProvenance"
import {
  OPTIMISER_VIEW_INTRODUCTIONS,
  OPTIMISER_VIEW_LABELS,
  type OptimiserResultView,
} from "./optimiser/resultViews"
import ResultsWorkspace from "./ResultsWorkspace"

// ─── Types (shared with OptimiserConfig) ─────────────────────────
export type { FrontierData }

export type OptimiserPreviewData = {
  /** The displayed result: the selected frontier point's, else the solve's. */
  result: OptimiserSolveResult
  /** The as-solved result, whatever point is selected. */
  solvedResult: OptimiserSolveResult
  jobId: string
  constraints: Record<string, Record<string, number>>
  nodeLabel: string
  frontier: FrontierData | null
  selectedPointIndex: number | null
}

// ─── Component ───────────────────────────────────────────────────

interface OptimiserPreviewProps {
  data: OptimiserPreviewData
  nodeId: string
  onRefresh?: () => void
  allNodes: SimpleNode[]
  edges: SimpleEdge[]
  submodels?: Record<string, unknown>
}

type RatesDetailState =
  | { status: "idle" }
  | { status: "loading"; key: string }
  | { status: "error"; key: string; error: string }

const EMPTY_COLUMNS: { name: string; dtype: string }[] = []

const REQUEST_FAILED = "The request failed."

const OPTIMISER_ACCENT = {
  color: OPTIMISER_COLORS.accent,
  soft: OPTIMISER_COLORS.accentSoft,
}

function HeaderPointStepper({
  pointCount,
  selectedIdx,
  onStepPoint,
}: {
  pointCount: number
  selectedIdx: number | null
  onStepPoint: (delta: number) => void
}) {
  if (selectedIdx == null || selectedIdx < 0 || selectedIdx >= pointCount) return null
  const atStart = selectedIdx <= 0
  const atEnd = selectedIdx >= pointCount - 1

  return (
    <div
      className="flex items-center gap-1 rounded px-1.5 py-0.5"
      style={{ background: "var(--bg-input)", border: "1px solid var(--border)" }}
    >
      <button
        type="button"
        onClick={() => onStepPoint(-1)}
        disabled={atStart}
        aria-label="Previous frontier point"
        title="Previous frontier point"
        className="w-5 h-5 inline-flex items-center justify-center rounded transition-colors"
        style={{
          color: atStart ? "var(--text-muted)" : "var(--text-secondary)",
          opacity: atStart ? 0.4 : 1,
        }}
      >
        <ChevronLeft size={13} />
      </button>
      <span className="text-[10px] font-medium tabular-nums" style={{ color: "var(--text-secondary)" }}>
        Point {selectedIdx + 1} of {pointCount}
      </span>
      <button
        type="button"
        onClick={() => onStepPoint(1)}
        disabled={atEnd}
        aria-label="Next frontier point"
        title="Next frontier point"
        className="w-5 h-5 inline-flex items-center justify-center rounded transition-colors"
        style={{
          color: atEnd ? "var(--text-muted)" : "var(--text-secondary)",
          opacity: atEnd ? 0.4 : 1,
        }}
      >
        <ChevronRight size={13} />
      </button>
    </div>
  )
}

export default function OptimiserPreview({ data, nodeId, allNodes, edges, submodels, onRefresh }: OptimiserPreviewProps) {
  const liveData = useNodeResultsStore((s) => s.getOptimiserPreview(nodeId))
  const displayData = liveData ?? data
  const { result, solvedResult, jobId } = displayData

  // The tab and X axis belong to one review: one node's one solve job. Stepping
  // through points builds a new result each press, so they must not follow
  // `result`; a new job or another optimiser node starts at the default tab.
  // Adjusted during render so a stale tab never paints.
  const reviewKey = JSON.stringify([nodeId, jobId])
  const defaultTab: OptimiserResultView = (
    displayData.frontier && displayData.frontier.points.length > 0 ? "frontier" : "summary"
  )
  const [review, setReview] = useState<{ key: string; tab: OptimiserResultView; xConstraintIdx: number }>(
    { key: reviewKey, tab: defaultTab, xConstraintIdx: 0 },
  )
  if (review.key !== reviewKey) {
    setReview({ key: reviewKey, tab: defaultTab, xConstraintIdx: 0 })
  }
  const { tab, xConstraintIdx } = review.key === reviewKey ? review : { tab: defaultTab, xConstraintIdx: 0 }
  const setTab = useCallback((next: OptimiserResultView) => {
    setReview((current) => ({ ...current, tab: next }))
  }, [])
  const setXConstraintIdx = useCallback((next: number) => {
    setReview((current) => ({ ...current, xConstraintIdx: next }))
  }, [])

  const height = useUIStore((s) => s.optimiserPreviewHeight)
  const rememberHeight = useUIStore((s) => s.setOptimiserPreviewHeight)

  // X-axis constraint picker for multi-constraint frontiers

  // Store actions
  const storeSelectPoint = useNodeResultsStore((s) => s.selectFrontierPoint)
  const storeUpdateAfterSelect = useNodeResultsStore((s) => s.updateFrontierAfterSelect)

  const nodeConfig = allNodes.find((node) => node.id === nodeId)?.data.config ?? {}

  // The result is stale once the node's solve inputs no longer match it.
  const cachedSolve = useNodeResultsStore((s) => s.solveResults[nodeId])
  const activeSource = useSettingsStore((s) => s.activeSource)
  const structuralVersion = useGraphStore((s) => s.structuralVersion)
  const solveRunning = useNodeResultsStore((s) => Boolean(s.solveJobs[nodeId]))
  const isStale = isSolveResultStale(cachedSolve, nodeConfig, activeSource, structuralVersion)
  // Re-run obeys the Solve pane's readiness, judged by the same hook and the
  // same column path (known columns, else the source-aware cache and fetch).
  // Columns are fetched only while Re-run is on offer.
  const soleInputColumns = useMemo(() => {
    const edgesIn = edges.filter((edge) => edge.target === nodeId)
    const source = edgesIn.length === 1 ? allNodes.find((node) => node.id === edgesIn[0].source) : undefined
    return Array.isArray(source?.data._columns) ? (source.data._columns as { name: string; dtype: string }[]) : EMPTY_COLUMNS
  }, [allNodes, edges, nodeId])
  const rerunReadiness = useOptimiserReadiness({
    nodeId,
    config: nodeConfig,
    allNodes,
    edges,
    submodels,
    fallbackColumns: soleInputColumns,
    fetchColumns: isStale,
  })
  const [rerunning, setRerunning] = useState(false)
  const handleRerun = useCallback(async () => {
    if (!rerunReadiness.canSolve) return
    setRerunning(true)
    try {
      await startOptimiserSolve({ nodeId, config: nodeConfig, allNodes, edges, submodels })
    } finally {
      setRerunning(false)
    }
  }, [allNodes, edges, nodeConfig, nodeId, rerunReadiness.canSolve, submodels])

  const [ratesDetail, setRatesDetail] = useState<RatesDetailState>({ status: "idle" })
  // Retry reissues a failed materialisation: the failed key is already released.
  const [ratesAttempt, setRatesAttempt] = useState(0)
  const retryRates = useCallback(() => {
    setRatesDetail({ status: "idle" })
    setRatesAttempt((current) => current + 1)
  }, [])
  const requestedRatesRef = useRef<Map<string, number>>(new Map())
  const ratesRequestSeqRef = useRef(0)
  const factorLevelOrder = useMemo(
    () => bandingLevelOrderForOptimiser(nodeId, allNodes, edges),
    [nodeId, allNodes, edges],
  )

  // ── Frontier point selection ──
  const frontier = displayData.frontier
  const selectedIdx = displayData.selectedPointIndex

  // Per-effect cleanup at L271 already deletes the in-flight key when deps
  // change or on unmount, but we additionally clear the entire map on jobId
  // change so any orphan keys keyed under the previous job (defensive — the
  // map's keys embed jobId, so a stale entry can never match a new request)
  // do not accumulate across long-lived sessions.
  useEffect(() => {
    requestedRatesRef.current.clear()
    setRatesDetail({ status: "idle" })
  }, [jobId])

  const selectedRatebookRatesMissing = (
    result.mode === "ratebook"
    && frontier != null
    && selectedIdx != null
    && !hasFactorTables(result.factor_tables)
  )
  const shouldMaterialiseSelectedRates = (
    selectedRatebookRatesMissing
    && (tab === "rates" || tab === "summary")
  )
  useEffect(() => {
    if (!shouldMaterialiseSelectedRates || selectedIdx == null) return
    const key = `${jobId}:${selectedIdx}`
    const requestedRates = requestedRatesRef.current
    if (requestedRates.has(key)) return
    const requestId = ratesRequestSeqRef.current + 1
    ratesRequestSeqRef.current = requestId
    requestedRates.set(key, requestId)

    const controller = new AbortController()
    setRatesDetail({ status: "loading", key })
    selectFrontierPointApi(
      {
        job_id: jobId,
        point_index: selectedIdx,
        include_ratebook_tables: true,
      },
      { signal: controller.signal },
    )
      .then((res) => {
        if (requestedRates.get(key) !== requestId) return
        storeUpdateAfterSelect(nodeId, selectedIdx, res)
        requestedRates.delete(key)
        setRatesDetail((current) => {
          if (current.status !== "loading" || current.key !== key) return current
          return hasFactorTables(res.factor_tables)
            ? { status: "idle" }
            : {
                status: "error",
                key,
                error: "No rate tables were returned for this selected point.",
              }
        })
      })
      .catch((e) => {
        if (requestedRates.get(key) !== requestId) return
        requestedRates.delete(key)
        if (controller.signal.aborted) return
        setRatesDetail({ status: "error", key, error: apiErrorMessage(e, REQUEST_FAILED) })
      })

    return () => {
      if (requestedRates.get(key) === requestId) {
        requestedRates.delete(key)
      }
      controller.abort()
    }
  }, [shouldMaterialiseSelectedRates, selectedIdx, jobId, nodeId, storeUpdateAfterSelect, ratesAttempt])

  // A click selects a point as the publish target; the selected point stays
  // selected (the Export pane's target choice returns to the solved result).
  const handlePointClick = useCallback(
    (index: number) => {
      if (index === selectedIdx) return
      storeSelectPoint(nodeId, index)
    },
    [selectedIdx, nodeId, storeSelectPoint],
  )

  const handleStepPoint = useCallback(
    (delta: number) => {
      if (!frontier) return
      const next = (selectedIdx ?? 0) + delta
      if (next < 0 || next >= frontier.points.length) return
      handlePointClick(next)
    },
    [frontier, selectedIdx, handlePointClick],
  )

  // Tabs available
  const frontierWithPoints = frontier && frontier.points.length > 0 ? frontier : null
  const ratebookFactorTables = result.mode === "ratebook" && hasFactorTables(result.factor_tables)
    ? result.factor_tables
    : null
  const hasRates = ratebookFactorTables != null
  const canMaterialiseSelectedRates = result.mode === "ratebook" && frontier != null && selectedIdx != null
  const headerPointCount = frontier?.points.length ?? 0
  const iterationSummary = formatOptimiserIterationSummary(result)
  const availableTabs: OptimiserResultView[] = frontierWithPoints ? ["frontier", "summary"] : ["summary"]
  if (hasRates || canMaterialiseSelectedRates) availableTabs.push("rates")
  if (result.mode !== "ratebook") availableTabs.push("quotes")
  // Only the solve records a history, so a selected point keeps Convergence.
  const solvedHistoryRecorded = solvedResult.history != null && solvedResult.history.length > 0
  if (solvedHistoryRecorded) availableTabs.push("convergence")
  const activeTab = availableTabs.includes(tab) ? tab : availableTabs[0]

  const tabs = availableTabs.map((key) => ({ key, label: OPTIMISER_VIEW_LABELS[key] }))
  const statusSummary = [
    result.converged ? "Converged" : "Not converged",
    iterationSummary?.compact,
    result.n_quotes != null ? `${result.n_quotes.toLocaleString()} quotes` : null,
  ].filter(Boolean).join(" | ")

  const provenance = optimiserResultProvenance(result, selectedIdx, headerPointCount)
  return (
    <ResultsWorkspace
      ariaLabel="Optimiser validation"
      idPrefix="optimiser-preview"
      tabsAriaLabel="Optimiser result panes"
      tabs={tabs}
      activeTab={activeTab}
      onTabChange={setTab}
      nodeLabel={displayData.nodeLabel}
      nodeType={NODE_TYPES.OPTIMISER}
      onRefresh={onRefresh}
      subtitle={statusSummary}
      collapsedMeta={`${result.converged ? "Converged" : "Not converged"} | Objective: ${formatNumber(result.total_objective)}`}
      data-testid="optimiser-preview-frame"
      height={height}
      onHeightChange={rememberHeight}
      accent={OPTIMISER_ACCENT}
      headerActions={(
        <HeaderPointStepper
          pointCount={headerPointCount}
          selectedIdx={selectedIdx}
          onStepPoint={handleStepPoint}
        />
      )}
      notices={(
        <>
          {isStale && (
            <div
              role="status"
              className="flex shrink-0 items-center gap-2 px-4 py-1.5 text-xs"
              style={{ background: "var(--warning-soft)", borderBottom: "1px solid var(--warning-border)", color: "var(--warning)" }}
            >
              <RefreshCw size={12} className="shrink-0" style={{ color: "var(--warning-strong)" }} />
              <span>
                The configuration has changed since this result was solved.
                {!rerunReadiness.canSolve && ` It cannot be re-run yet: ${rerunReadiness.issues[0].message}`}
              </span>
              <button
                type="button"
                onClick={() => void handleRerun()}
                disabled={rerunning || solveRunning || !rerunReadiness.canSolve}
                className="ml-auto px-2 py-0.5 rounded text-[11px] font-medium disabled:opacity-60"
                style={{ background: "var(--warning-soft-emphasis)", color: "var(--warning-strong)" }}
              >
                {solveRunning ? "Re-running" : "Re-run"}
              </button>
            </div>
          )}
          {result.warning && (
            <div
              role="status"
              aria-label="Result warning"
              className="flex shrink-0 items-start gap-2 px-4 py-1.5 text-xs"
              style={{ background: "var(--warning-soft)", borderBottom: "1px solid var(--warning-border)", color: "var(--warning-strong)" }}
            >
              <AlertCircle size={14} className="mt-0.5 shrink-0" />
              <span>{result.warning}</span>
            </div>
          )}
          {result.frontier_error && (
            <div
              className="flex shrink-0 items-start gap-2 px-4 py-2 text-xs"
              style={{
                color: "var(--warning-strong)",
                background: "var(--warning-soft-emphasis)",
                borderBottom: "1px solid var(--warning-border-strong)",
              }}
            >
              <AlertCircle size={14} className="mt-0.5 shrink-0" />
              <span>{result.frontier_error}</span>
            </div>
          )}
        </>
      )}
      provenance={(
        <p data-testid="optimiser-provenance" className="m-0">
          {provenance.join(" · ")}
        </p>
      )}
      intro={OPTIMISER_VIEW_INTRODUCTIONS[activeTab]}
    >
      {activeTab === "frontier" && frontierWithPoints && (
        <FrontierTab
          frontier={frontierWithPoints}
          result={result}
          solvedResult={solvedResult}
          selectedIdx={selectedIdx}
          xConstraintIdx={xConstraintIdx}
          onXConstraintChange={setXConstraintIdx}
          onPointClick={handlePointClick}
        />
      )}

      {activeTab === "summary" && (
        <SummaryTab
          result={result}
          selectedPointIndex={selectedIdx}
          canMaterialiseRatebookRates={selectedRatebookRatesMissing}
          ratebookRatesDetail={ratesDetail}
        />
      )}

      {activeTab === "rates" && (
        ratebookFactorTables ? (
          <RatebookRatesTab factorTables={ratebookFactorTables} factorLevelOrder={factorLevelOrder} />
        ) : (
          <RatebookRatesPending detail={ratesDetail} onRetry={retryRates} />
        )
      )}

      {activeTab === "convergence" && (
        <ConvergenceChart
          solvedResult={solvedResult}
          selectedPoint={selectedIdx == null ? null : { index: selectedIdx, result }}
        />
      )}

      {activeTab === "quotes" && (
        <QuotesTab
          nodeId={nodeId}
          jobId={jobId}
          frontierGeneration={solvedResult.frontier_generation}
          pointIndex={selectedIdx}
        />
      )}
    </ResultsWorkspace>
  )
}

// Frontier Tab

interface FrontierTabProps {
  frontier: FrontierData
  /** The displayed result, for the selected point's detail card. */
  result: OptimiserSolveResult
  /** The as-solved result, which anchors the chart's as-solved marker. */
  solvedResult: OptimiserSolveResult
  selectedIdx: number | null
  xConstraintIdx: number
  onXConstraintChange: (idx: number) => void
  onPointClick: (index: number) => void
}

function FrontierTab({
  frontier,
  result,
  solvedResult,
  selectedIdx,
  xConstraintIdx,
  onXConstraintChange,
  onPointClick,
}: FrontierTabProps) {
  const xPickerId = useId()
  const points = frontier.points
  // The frontier's own constraints: the guard checked every point names each one.
  const constraintNames = frontier.constraint_names
  const xConstraintName = constraintNames[xConstraintIdx] ?? constraintNames[0]
  // Each typed point holds every constraint's total, so the x value is read by name.
  const chartPoints = useMemo(
    () => (xConstraintName
      ? points.map((point) => ({ x: point.totals[xConstraintName], y: point.total_objective }))
      : []),
    [points, xConstraintName],
  )

  const shownPointCount = frontier.points_returned || points.length
  const totalPointCount = frontier.n_points || points.length

  const hasChartData = chartPoints.length >= 2

  // The as-solved marker stays where the solve is whichever point is selected.
  const currentX = xConstraintName ? solvedResult.constraints[xConstraintName] : null
  const currentY = solvedResult.total_objective

  return (
    <div className="optimiser-frontier-layout">
      <div className="optimiser-frontier-chart">
        {constraintNames.length > 1 && (
          <div className="flex items-center gap-2 mb-3">
            <label htmlFor={xPickerId} className="text-xs font-medium" style={{ color: "var(--text-secondary)" }}>X axis:</label>
            <select
              id={xPickerId}
              value={xConstraintIdx}
              onChange={e => onXConstraintChange(Number(e.target.value))}
              className="validation-control"
            >
              {constraintNames.map((name, i) => (
                <option key={name} value={i}>{name}</option>
              ))}
            </select>
          </div>
        )}

        {hasChartData ? (
          <FrontierChart
            points={chartPoints}
            xKey="x"
            yKey="y"
            xLabel={xConstraintName ?? "constraint"}
            selectedIdx={selectedIdx}
            currentX={currentX}
            currentY={currentY}
            onPointClick={onPointClick}
          />
        ) : (
          <div className="text-xs py-4" style={{ color: "var(--text-muted)" }}>
            Insufficient data to plot frontier chart.
          </div>
        )}

        <p className="validation-chart-description mt-2">
          {frontier.points_truncated ? (
            <>
              Showing {shownPointCount.toLocaleString()} of {totalPointCount.toLocaleString()} frontier points;
              response cap is {(frontier.points_limit ?? shownPointCount).toLocaleString()}. Click a point for details.
            </>
          ) : (
            <>
              {shownPointCount.toLocaleString()} frontier points. Click a point for details.
            </>
          )}
        </p>
      </div>

      {selectedIdx != null && points[selectedIdx] && (
        <div className="optimiser-frontier-detail">
          <DetailCard result={result} />
        </div>
      )}
    </div>
  )
}

function RatebookRatesPending({ detail, onRetry }: { detail: RatesDetailState; onRetry: () => void }) {
  if (detail.status === "error") {
    return (
      <div
        role="alert"
        className="flex items-start gap-2 text-xs px-3 py-2 rounded"
        style={{ background: "var(--danger-soft)", color: "var(--danger)" }}
      >
        <AlertCircle size={14} className="mt-0.5 shrink-0" />
        <span className="flex-1">Rate table load failed: {detail.error}</span>
        <button
          type="button"
          onClick={onRetry}
          className="shrink-0 rounded px-2 py-0.5 text-[11px] font-medium"
          style={{ border: "1px solid var(--danger-border-strong)", color: "var(--danger)" }}
        >
          Retry
        </button>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-2 text-xs" style={{ color: "var(--text-muted)" }}>
      <Loader2 size={14} className="animate-spin" />
      Materialising selected point rates...
    </div>
  )
}
