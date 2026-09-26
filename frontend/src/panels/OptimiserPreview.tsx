/**
 * Bottom-panel visualisations for the optimiser node.
 *
 * Renders in the same slot as DataPreview when an optimiser solve has
 * completed.  Shows Frontier (default when data exists), Summary,
 * Rates (ratebook mode), Adjustments, Segments and Quotes (both modes) and
 * Convergence tabs as available. Publishing lives only in the node's Export pane, whose target
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
  OptimiserAdjustmentReport,
  OptimiserSegmentIndexResponse,
  OptimiserSegmentsResponse,
  OptimiserSolveResult,
} from "../api/types"
import type { SimpleEdge, SimpleNode } from "./editors"
import FrontierChart, { type FrontierChartPoint } from "./optimiser/FrontierChart"
import ConvergenceChart from "./optimiser/ConvergenceChart"
import SummaryTab from "./optimiser/SummaryTab"
import DetailCard from "./optimiser/DetailCard"
import {
  assessFrontierPoint,
  asSolvedOnSlice,
  discreteTradeOff,
  displayedSlicePosition,
  frontierConstraintKinds,
  sliceFrontier,
  sliceNeighbours,
  type FrontierSliceChoice,
  type FrontierSlicing,
} from "./optimiser/frontierSlices"
import { effectiveConstraintBounds } from "../stores/useNodeResultsStore"
import { ChartValuesTable } from "./modelling/ChartScaffold"
import RatebookRatesTab from "./optimiser/RatebookRatesTab"
import { hasFactorTables } from "./optimiser/ratebookFactorTables"
import { formatOptimiserIterationSummary } from "./optimiser/iterationSummary"
import QuotesTab from "./optimiser/QuotesTab"
import AdjustmentsTab, { type PointAdjustmentReports } from "./optimiser/AdjustmentsTab"
import SegmentsTab, { type SegmentResults } from "./optimiser/SegmentsTab"
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

/** What one review of one node's solve job remembers across tabs and point steps. */
type OptimiserReview = {
  key: string
  tab: OptimiserResultView
  /** The x constraint, as an index into the frontier's `swept_axes`. */
  xConstraintIdx: number
  /** The frontier slice the user picked, which holds while the selection stays put. */
  sliceChoice: FrontierSliceChoice | null
  /** The key Rates and Segments share (a rating factor or segment key), and its search. */
  featureKey: string | null
  featureSearch: string
  /** Frontier point adjustment reports loaded in this review, by `pointAdjustmentKey`. */
  adjustmentReports: PointAdjustmentReports
  /** Segment breakdowns and indexes loaded in this review. */
  segmentResults: SegmentResults
}

const EMPTY_COLUMNS: { name: string; dtype: string }[] = []

const REQUEST_FAILED = "The request failed."

const OPTIMISER_ACCENT = {
  color: OPTIMISER_COLORS.accent,
  soft: OPTIMISER_COLORS.accentSoft,
}

/**
 * Steps the publish target through the selected point's slice in bound order
 * (global numbering), disabled at the slice's ends.
 */
function HeaderPointStepper({
  pointCount,
  selectedIdx,
  previous,
  next,
  onSelect,
}: {
  pointCount: number
  selectedIdx: number | null
  previous: number | null
  next: number | null
  onSelect: (index: number) => void
}) {
  if (selectedIdx == null) return null
  const atStart = previous === null
  const atEnd = next === null

  return (
    <div
      className="flex items-center gap-1 rounded px-1.5 py-0.5"
      style={{ background: "var(--bg-input)", border: "1px solid var(--border)" }}
    >
      <button
        type="button"
        onClick={() => { if (previous !== null) onSelect(previous) }}
        disabled={atStart}
        aria-label="Previous frontier point"
        title="Previous frontier point in this slice"
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
        onClick={() => { if (next !== null) onSelect(next) }}
        disabled={atEnd}
        aria-label="Next frontier point"
        title="Next frontier point in this slice"
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
  // The Rates factor (the Segments key too) and its search belong to the review,
  // so they survive tab switches and point steps.
  const freshReview: OptimiserReview = {
    key: reviewKey,
    tab: defaultTab,
    xConstraintIdx: 0,
    sliceChoice: null,
    featureKey: null,
    featureSearch: "",
    adjustmentReports: {},
    segmentResults: { breakdowns: {}, indexes: {} },
  }
  const [review, setReview] = useState(freshReview)
  if (review.key !== reviewKey) {
    setReview(freshReview)
  }
  const {
    tab,
    xConstraintIdx,
    sliceChoice,
    featureKey,
    featureSearch,
    adjustmentReports,
    segmentResults,
  } = review.key === reviewKey ? review : freshReview
  const setTab = useCallback((next: OptimiserResultView) => {
    setReview((current) => ({ ...current, tab: next }))
  }, [])
  const setXConstraintIdx = useCallback((next: number) => {
    setReview((current) => ({ ...current, xConstraintIdx: next }))
  }, [])
  const setSliceChoice = useCallback((next: FrontierSliceChoice) => {
    setReview((current) => ({ ...current, sliceChoice: next }))
  }, [])
  const setFeatureKey = useCallback((next: string) => {
    setReview((current) => ({ ...current, featureKey: next }))
  }, [])
  const setFeatureSearch = useCallback((next: string) => {
    setReview((current) => ({ ...current, featureSearch: next }))
  }, [])
  const recordSegmentBreakdown = useCallback((key: string, response: OptimiserSegmentsResponse) => {
    setReview((current) => ({
      ...current,
      segmentResults: {
        ...current.segmentResults,
        breakdowns: { ...current.segmentResults.breakdowns, [key]: response },
      },
    }))
  }, [])
  const recordSegmentIndex = useCallback((key: string, response: OptimiserSegmentIndexResponse) => {
    setReview((current) => ({
      ...current,
      segmentResults: {
        ...current.segmentResults,
        indexes: { ...current.segmentResults.indexes, [key]: response },
      },
    }))
  }, [])
  const recordAdjustmentReport = useCallback((key: string, report: OptimiserAdjustmentReport) => {
    setReview((current) => ({
      ...current,
      adjustmentReports: { ...current.adjustmentReports, [key]: report },
    }))
  }, [])
  const openAdjustments = useCallback(() => setTab("adjustments"), [setTab])

  const height = useUIStore((s) => s.optimiserPreviewHeight)
  const rememberHeight = useUIStore((s) => s.setOptimiserPreviewHeight)

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

  // Tabs available
  const frontierWithPoints = frontier && frontier.points.length > 0 ? frontier : null

  // The frontier is shown one slice at a time along a swept x constraint; the
  // stepper and the chart share the slicing. Every index is global.
  const xConstraintName = frontierWithPoints ? frontierWithPoints.swept_axes[xConstraintIdx] : null
  if (frontierWithPoints && xConstraintName === undefined) {
    throw new Error(
      `Frontier x constraint ${xConstraintIdx} is not one of its swept constraints `
      + `[${frontierWithPoints.swept_axes.join(", ")}]`,
    )
  }
  const slicing = useMemo(
    () => (frontierWithPoints && xConstraintName
      ? sliceFrontier(
        frontierWithPoints.points,
        frontierWithPoints.constraint_names,
        frontierWithPoints.swept_axes,
        xConstraintName,
      )
      : null),
    [frontierWithPoints, xConstraintName],
  )
  const stepNeighbours = slicing && selectedIdx != null
    ? sliceNeighbours(slicing, selectedIdx)
    : { previous: null, next: null }
  const handleSliceChange = useCallback(
    (key: string) => {
      if (!slicing || !frontierWithPoints) return
      setSliceChoice({
        xName: slicing.xName,
        generation: frontierWithPoints.frontier_generation,
        key,
        selection: selectedIdx,
      })
    },
    [slicing, frontierWithPoints, selectedIdx, setSliceChoice],
  )
  // The y axis names the objective column only while the node's config is the
  // one the result was solved with; a stale result's column is not known.
  const objectiveName = !isStale && typeof nodeConfig.objective === "string" && nodeConfig.objective !== ""
    ? nodeConfig.objective
    : "Objective"
  const ratebookFactorTables = result.mode === "ratebook" && hasFactorTables(result.factor_tables)
    ? result.factor_tables
    : null
  const hasRates = ratebookFactorTables != null
  const canMaterialiseSelectedRates = result.mode === "ratebook" && frontier != null && selectedIdx != null
  const headerPointCount = frontier?.points.length ?? 0
  const iterationSummary = formatOptimiserIterationSummary(result)
  const availableTabs: OptimiserResultView[] = frontierWithPoints ? ["frontier", "summary"] : ["summary"]
  if (hasRates || canMaterialiseSelectedRates) availableTabs.push("rates")
  // Every result describes its adjustments: online choices, or the ratebook's evaluated steps.
  availableTabs.push("adjustments")
  // Segments too: analysis columns, a ratebook result's factors, or the empty state.
  availableTabs.push("segments")
  // Quotes pages any result's chosen scenarios (a ratebook's evaluated steps).
  availableTabs.push("quotes")
  // Convergence draws the solve's history or CD trace, so every result offers it.
  availableTabs.push("convergence")
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
          previous={stepNeighbours.previous}
          next={stepNeighbours.next}
          onSelect={handlePointClick}
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
      {activeTab === "frontier" && frontierWithPoints && slicing && (
        <FrontierTab
          frontier={frontierWithPoints}
          result={result}
          solvedResult={solvedResult}
          selectedIdx={selectedIdx}
          xConstraintIdx={xConstraintIdx}
          onXConstraintChange={setXConstraintIdx}
          slicing={slicing}
          slicePosition={displayedSlicePosition(
            slicing,
            selectedIdx,
            sliceChoice,
            frontierWithPoints.frontier_generation,
          )}
          onSliceChange={handleSliceChange}
          objectiveName={objectiveName}
          onPointClick={handlePointClick}
        />
      )}

      {activeTab === "summary" && (
        <SummaryTab
          result={result}
          selectedPointIndex={selectedIdx}
          canMaterialiseRatebookRates={selectedRatebookRatesMissing}
          ratebookRatesDetail={ratesDetail}
          onOpenAdjustments={openAdjustments}
        />
      )}

      {activeTab === "rates" && (
        ratebookFactorTables ? (
          <RatebookRatesTab
            factorTables={ratebookFactorTables}
            factorLevelOrder={factorLevelOrder}
            selection={{
              selected: featureKey,
              onSelect: setFeatureKey,
              search: featureSearch,
              onSearch: setFeatureSearch,
            }}
          />
        ) : (
          <RatebookRatesPending detail={ratesDetail} onRetry={retryRates} />
        )
      )}

      {activeTab === "adjustments" && (
        <AdjustmentsTab
          jobId={jobId}
          frontierGeneration={solvedResult.frontier_generation}
          pointIndex={selectedIdx}
          solvedResult={solvedResult}
          pointReports={adjustmentReports}
          onPointReport={recordAdjustmentReport}
        />
      )}

      {activeTab === "segments" && (
        <SegmentsTab
          jobId={jobId}
          frontierGeneration={solvedResult.frontier_generation}
          pointIndex={selectedIdx}
          solvedResult={solvedResult}
          selection={{
            selected: featureKey,
            onSelect: setFeatureKey,
            search: featureSearch,
            onSearch: setFeatureSearch,
          }}
          results={segmentResults}
          onBreakdown={recordSegmentBreakdown}
          onIndex={recordSegmentIndex}
        />
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
          mode={result.mode}
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
  /** The x constraint, as an index into `frontier.swept_axes`. */
  xConstraintIdx: number
  onXConstraintChange: (idx: number) => void
  slicing: FrontierSlicing
  /** The displayed slice's position in `slicing.slices`. */
  slicePosition: number
  onSliceChange: (key: string) => void
  /** The y axis name: the objective column, or "Objective" when it is not known. */
  objectiveName: string
  onPointClick: (index: number) => void
}

const STATUS_LABELS = {
  feasible: "Feasible",
  not_converged: "Not converged",
  breached: "Breached",
} as const

function formatFrontierValue(value: number): string {
  return value.toLocaleString("en-US", { maximumFractionDigits: 6 })
}

function FrontierTab({
  frontier,
  result,
  solvedResult,
  selectedIdx,
  xConstraintIdx,
  onXConstraintChange,
  slicing,
  slicePosition,
  onSliceChange,
  objectiveName,
  onPointClick,
}: FrontierTabProps) {
  const xPickerId = useId()
  const slicePickerId = useId()
  const { points, constraint_names: constraintNames, swept_axes: sweptAxes } = frontier
  const xName = slicing.xName

  // Kinds come from the solve's bounds; feasibility is judged on each point's own.
  const kinds = useMemo(() => frontierConstraintKinds(constraintNames, solvedResult), [constraintNames, solvedResult])
  const assessments = useMemo(
    () => points.map((point, index) => assessFrontierPoint(point, constraintNames, kinds, index)),
    [points, constraintNames, kinds],
  )
  const slice = slicing.slices[slicePosition]
  const chartPoints: FrontierChartPoint[] = slice.indices.map((index) => ({
    index,
    x: points[index].totals[xName],
    y: points[index].total_objective,
    status: assessments[index].status,
  }))
  const solvedBounds = Object.fromEntries(
    Object.entries(effectiveConstraintBounds(solvedResult)).map(([name, { bound }]) => [name, bound]),
  )
  const asSolved = {
    x: solvedResult.constraints[xName],
    y: solvedResult.total_objective,
    onSlice: asSolvedOnSlice(points, slicing, slice, solvedBounds),
  }

  const shownPointCount = frontier.points_returned || points.length
  const totalPointCount = frontier.n_points || points.length
  const iterationsLabel = points[0].mode === "ratebook" ? "CD passes" : "Iterations"

  const selectedPoint = selectedIdx != null ? points[selectedIdx] : undefined

  return (
    <div className="optimiser-frontier-layout">
      <div className="optimiser-frontier-chart">
        {(sweptAxes.length > 1 || slicing.slices.length > 1) && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 mb-3">
            {sweptAxes.length > 1 && (
              <div className="flex items-center gap-2">
                <label htmlFor={xPickerId} className="text-xs font-medium" style={{ color: "var(--text-secondary)" }}>X axis:</label>
                <select
                  id={xPickerId}
                  value={xConstraintIdx}
                  onChange={(e) => onXConstraintChange(Number(e.target.value))}
                  className="validation-control"
                >
                  {sweptAxes.map((name, i) => (
                    <option key={name} value={i}>{name}</option>
                  ))}
                </select>
              </div>
            )}
            {slicing.slices.length > 1 && (
              <div className="flex items-center gap-2">
                <label htmlFor={slicePickerId} className="text-xs font-medium" style={{ color: "var(--text-secondary)" }}>
                  Holding {slicing.heldNames.join(" and ")} at
                </label>
                <select
                  id={slicePickerId}
                  value={slicePosition}
                  onChange={(e) => onSliceChange(slicing.slices[Number(e.target.value)].key)}
                  className="validation-control"
                >
                  {slicing.slices.map((option, i) => (
                    <option key={option.key} value={i}>
                      {slicing.heldNames.map((name) => formatFrontierValue(option.held[name])).join(", ")}
                    </option>
                  ))}
                </select>
              </div>
            )}
          </div>
        )}

        <FrontierChart
          points={chartPoints}
          xLabel={xName}
          yLabel={objectiveName}
          selectedIdx={selectedIdx}
          asSolved={asSolved}
          onPointClick={onPointClick}
        />

        <p className="validation-chart-description mt-2">
          {slicing.slices.length > 1 && (
            <>This slice holds {slice.indices.length.toLocaleString()} of the {shownPointCount.toLocaleString()} frontier points. </>
          )}
          {frontier.points_truncated ? (
            <>
              Showing {shownPointCount.toLocaleString()} of {totalPointCount.toLocaleString()} frontier points;
              response cap is {(frontier.points_limit ?? shownPointCount).toLocaleString()}
              {slicing.slices.length > 1 ? ", so a slice may be incomplete" : ""}. Click a point for details.
            </>
          ) : slicing.slices.length > 1 ? (
            <>Click a point for details.</>
          ) : (
            <>
              {shownPointCount.toLocaleString()} frontier points. Click a point for details.
            </>
          )}
        </p>

        <ChartValuesTable
          summary="View slice values"
          ariaLabel="Frontier slice values"
          headers={["Point", `${xName} bound`, `${xName} achieved`, objectiveName, "Converged", iterationsLabel, "Status"]}
          rows={slice.indices.map((index) => {
            const point = points[index]
            return [
              `Point ${index + 1}`,
              formatFrontierValue(point.bounds[xName]),
              formatFrontierValue(point.totals[xName]),
              formatFrontierValue(point.total_objective),
              point.converged ? "Yes" : "No",
              point.iterations.toLocaleString(),
              STATUS_LABELS[assessments[index].status],
            ]
          })}
        />
      </div>

      {selectedIdx != null && selectedPoint && (
        <div className="optimiser-frontier-detail">
          <DetailCard
            result={result}
            frontierPoint={{
              index: selectedIdx,
              point: selectedPoint,
              kinds,
              xName,
              assessment: assessments[selectedIdx],
              tradeOff: discreteTradeOff({ points, slicing, assessments, kinds, index: selectedIdx }),
            }}
          />
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
