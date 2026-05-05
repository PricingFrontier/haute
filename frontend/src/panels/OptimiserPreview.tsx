/**
 * Bottom-panel visualisations for the optimiser node.
 *
 * Renders in the same slot as DataPreview when an optimiser solve has
 * completed.  Shows Frontier (default when data exists), Summary,
 * Rates (ratebook mode), Convergence, and Export tabs as available.
 *
 * The Frontier tab shows an interactive scatter chart (left) and a
 * detail card (right) with metrics and Save/Log actions.
 */

import { useState, useMemo, useCallback, useEffect, useRef } from "react"
import { AlertCircle, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Loader2, Target, Save, Table2, Upload } from "lucide-react"
import {
  applyOptimiser,
  saveOptimiser,
  logOptimiserToMlflow,
  selectFrontierPoint as selectFrontierPointApi,
} from "../api/client"
import { formatNumber } from "../utils/formatValue"
import { useDragResize } from "../hooks/useDragResize"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import useSettingsStore from "../stores/useSettingsStore"
import { MODEL_COLORS } from "../theme/colors"
import type { ApplyOptimiserResponse, FrontierData } from "../api/types"
import FrontierChart from "./optimiser/FrontierChart"
import ConvergenceChart from "./optimiser/ConvergenceChart"
import SummaryTab from "./optimiser/SummaryTab"
import DetailCard from "./optimiser/DetailCard"
import RatebookRatesTab from "./optimiser/RatebookRatesTab"
import { hasFactorTables } from "./optimiser/ratebookFactorTables"

// ─── Types (shared with OptimiserConfig) ─────────────────────────

export type SolveResult = {
  mode?: string
  total_objective: number
  baseline_objective: number
  constraints: Record<string, number>
  baseline_constraints: Record<string, number>
  lambdas: Record<string, number>
  converged: boolean
  iterations?: number
  n_quotes?: number
  n_steps?: number
  cd_iterations?: number
  factor_tables?: Record<string, Record<string, unknown>[]>
  history?: {
    iteration: number
    total_objective: number
    max_lambda_change: number
    all_constraints_satisfied?: boolean
    lambdas?: Record<string, number>
    total_constraints?: Record<string, number>
  }[] | null
  warning?: string
  frontier_error?: string | null
  scenario_value_stats?: {
    mean: number; std: number; min: number; max: number
    p5: number; p25: number; p50: number; p75: number; p95: number
    pct_increase: number; pct_decrease: number
  }
  scenario_value_histogram?: { counts: number[]; edges: number[] }
  clamp_rate?: number | null
  frontier?: {
    status: string
    points: Record<string, unknown>[]
    n_points: number
    points_returned: number
    constraint_names: string[]
    points_limit: number | null
    points_truncated: boolean
  } | null
}

export type { FrontierData }

export type OptimiserPreviewData = {
  result: SolveResult
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
}

type TabKey = "frontier" | "summary" | "rates" | "convergence" | "export"
type ResultDetailState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "loaded"; data: ApplyOptimiserResponse }
  | { status: "error"; error: string }
type RatesDetailState =
  | { status: "idle" }
  | { status: "loading"; key: string }
  | { status: "error"; key: string; error: string }

const EMPTY_FRONTIER_POINTS: Record<string, unknown>[] = []

function errorDetail(error: unknown): string {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail?: unknown }).detail
    if (typeof detail === "string" && detail.trim()) return detail
  }
  return error instanceof Error ? error.message : String(error)
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

export default function OptimiserPreview({ data, nodeId }: OptimiserPreviewProps) {
  const liveData = useNodeResultsStore((s) => s.getOptimiserPreview(nodeId))
  const displayData = liveData ?? data
  const { result, jobId, constraints } = displayData

  const [collapsed, setCollapsed] = useState(false)
  const { height, containerRef, onDragStart } = useDragResize({ initialHeight: 320, minHeight: 160, maxHeight: 600 })

  // Default tab: frontier when frontier data exists, otherwise summary
  const [tab, setTab] = useState<TabKey>(() =>
    displayData.frontier && displayData.frontier.points.length > 0 ? "frontier" : "summary",
  )

  // X-axis constraint picker for multi-constraint frontiers
  const constraintNames = useMemo(() => Object.keys(constraints), [constraints])
  const [xConstraintIdx, setXConstraintIdx] = useState(0)

  // Store actions
  const storeSelectPoint = useNodeResultsStore((s) => s.selectFrontierPoint)
  const storeUpdateAfterSelect = useNodeResultsStore((s) => s.updateFrontierAfterSelect)

  // MLflow availability
  const mlflowAvailable = useSettingsStore((s) => s.mlflow.status === "connected")

  // Detail card action state
  const [saving, setSaving] = useState(false)
  const [logging, setLogging] = useState(false)
  const [actionMsg, setActionMsg] = useState<string | null>(null)
  const [resultDetail, setResultDetail] = useState<ResultDetailState>({ status: "idle" })
  const [ratesDetail, setRatesDetail] = useState<RatesDetailState>({ status: "idle" })
  const resultDetailAbortRef = useRef<AbortController | null>(null)
  const requestedRatesRef = useRef<Map<string, number>>(new Map())
  const ratesRequestSeqRef = useRef(0)
  const terminalDetailBlocksActions = resultDetail.status === "loading" || resultDetail.status === "loaded"

  // ── Frontier point selection ──
  const frontier = displayData.frontier
  const selectedIdx = displayData.selectedPointIndex

  // Clear action feedback when the selected point changes (M8)
  useEffect(() => { setActionMsg(null) }, [selectedIdx])
  const abortResultDetailRequest = useCallback(() => {
    resultDetailAbortRef.current?.abort()
    resultDetailAbortRef.current = null
  }, [])
  useEffect(() => {
    abortResultDetailRequest()
    setResultDetail({ status: "idle" })
  }, [abortResultDetailRequest, jobId])
  useEffect(() => abortResultDetailRequest, [abortResultDetailRequest])

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
        requestedRates.delete(key)
        storeUpdateAfterSelect(nodeId, selectedIdx, res)
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
        setRatesDetail({ status: "error", key, error: errorDetail(e) })
      })

    return () => {
      if (requestedRates.get(key) === requestId) {
        requestedRates.delete(key)
      }
      controller.abort()
    }
  }, [shouldMaterialiseSelectedRates, selectedIdx, jobId, nodeId, storeUpdateAfterSelect])

  const handlePointClick = useCallback(
    (index: number) => {
      // Toggle off if clicking selected point
      if (index === selectedIdx) {
        storeSelectPoint(nodeId, null)
        return
      }
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

  // ── Save / Log actions ──
  const handleSave = useCallback(async () => {
    setSaving(true)
    setActionMsg(null)
    try {
      const outputPath = `output/optimiser_${displayData.nodeLabel.toLowerCase().replace(/ /g, "_")}.json`
      const res = await saveOptimiser({
        job_id: jobId,
        output_path: outputPath,
        ...(selectedIdx != null && frontier ? { point_index: selectedIdx } : {}),
      })
      setActionMsg(res.message ?? `Saved to ${res.path ?? outputPath}`)
    } catch (e) {
      setActionMsg(`Save failed: ${e}`)
    } finally {
      setSaving(false)
    }
  }, [selectedIdx, frontier, jobId, displayData.nodeLabel])

  const handleLogMlflow = useCallback(async () => {
    setLogging(true)
    setActionMsg(null)
    try {
      const res = await logOptimiserToMlflow({
        job_id: jobId,
        ...(selectedIdx != null && frontier ? { point_index: selectedIdx } : {}),
      })
      const target = res.experiment_name ? ` to ${res.experiment_name}` : ""
      setActionMsg(res.run_url ? `Logged${target}: ${res.run_url}` : `Logged${target} (run ${res.run_id ?? "ok"})`)
    } catch (e) {
      setActionMsg(`MLflow log failed: ${e}`)
    } finally {
      setLogging(false)
    }
  }, [selectedIdx, frontier, jobId])

  const handleLoadResultDetail = useCallback(async () => {
    abortResultDetailRequest()
    const controller = new AbortController()
    resultDetailAbortRef.current = controller
    setResultDetail({ status: "loading" })
    try {
      const res = await applyOptimiser(
        {
          job_id: jobId,
          ...(selectedIdx != null && frontier ? { point_index: selectedIdx } : {}),
        },
        { signal: controller.signal },
      )
      if (resultDetailAbortRef.current !== controller) return
      setResultDetail({ status: "loaded", data: res })
    } catch (e) {
      if (controller.signal.aborted) return
      const detail = e instanceof Error ? e.message : String(e)
      setResultDetail({ status: "error", error: detail })
    } finally {
      if (resultDetailAbortRef.current === controller) {
        resultDetailAbortRef.current = null
      }
    }
  }, [abortResultDetailRequest, selectedIdx, frontier, jobId])

  // ── Collapsed ──
  if (collapsed) {
    return (
      <div className="h-8 flex items-center px-4 shrink-0" style={{ borderTop: "1px solid var(--border)", background: "var(--bg-panel)" }}>
        <button onClick={() => setCollapsed(false)} aria-label="Expand panel" className="flex items-center gap-2 text-xs" style={{ color: "var(--text-secondary)" }}>
          <ChevronUp size={14} />
          <Target size={14} />
          <span className="font-medium">{displayData.nodeLabel}</span>
          <span style={{ color: "var(--text-muted)" }}>
            {result.converged ? "Converged" : "Not converged"}
            {" — "}Objective: {formatNumber(result.total_objective)}
          </span>
        </button>
      </div>
    )
  }

  // ── Tabs available ──
  const hasFrontier = frontier && frontier.points.length > 0
  const ratebookFactorTables = result.mode === "ratebook" && hasFactorTables(result.factor_tables)
    ? result.factor_tables
    : null
  const hasRates = ratebookFactorTables != null
  const canMaterialiseSelectedRates = result.mode === "ratebook" && frontier != null && selectedIdx != null
  const headerPointCount = frontier?.points.length ?? 0
  const availableTabs: TabKey[] = hasFrontier ? ["frontier", "summary"] : ["summary"]
  if (hasRates || canMaterialiseSelectedRates) availableTabs.push("rates")
  if (result.history && result.history.length > 0) availableTabs.push("convergence")
  availableTabs.push("export")
  const activeTab = availableTabs.includes(tab) ? tab : availableTabs[0]

  const TAB_LABELS: Record<TabKey, string> = {
    frontier: "Frontier",
    summary: "Summary",
    rates: "Rates",
    convergence: "Convergence",
    export: "Export",
  }

  // ── Expanded ──
  return (
    <div ref={containerRef} style={{ height, borderTop: "1px solid var(--border)", background: "var(--bg-panel)" }} className="flex flex-col shrink-0 relative">
      {/* Drag handle */}
      <div
        onMouseDown={onDragStart}
        className="drag-handle-hover absolute top-0 left-0 right-0 h-1 cursor-ns-resize z-10"
      />

      {/* Header */}
      <div className="min-h-9 flex items-center flex-wrap px-4 shrink-0 gap-x-2 gap-y-1 py-1.5" style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-elevated)" }}>
        <Target size={14} style={{ color: "var(--warning-strong)" }} />
        <span className="text-xs font-bold" style={{ color: "var(--text-primary)" }}>{displayData.nodeLabel}</span>
        <HeaderPointStepper
          pointCount={headerPointCount}
          selectedIdx={selectedIdx}
          onStepPoint={handleStepPoint}
        />
        <span className="text-[11px]" style={{ color: result.converged ? "var(--success)" : "var(--warning-strong)" }}>
          {result.converged ? "Converged" : "Not converged"}
          {result.mode === "ratebook"
            ? ` · ${result.cd_iterations ?? "?"} CD iters`
            : ` · ${result.iterations ?? "?"} iters`}
          {result.n_quotes != null && <> · {result.n_quotes.toLocaleString()} quotes</>}
        </span>

        {/* Tab selector */}
        <div className="flex gap-1 ml-3">
          {availableTabs.map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className="px-2 py-0.5 rounded text-[10px] font-medium"
              style={{
                background: activeTab === t ? "var(--accent-soft)" : "var(--chrome-hover)",
                color: activeTab === t ? "var(--accent)" : "var(--text-muted)",
              }}
            >
              {TAB_LABELS[t]}
            </button>
          ))}
        </div>

        <div className="ml-auto flex items-center gap-1">
          <button onClick={() => setCollapsed(true)} aria-label="Collapse panel" className="p-1 rounded transition-colors hover:bg-[var(--bg-hover)]" style={{ color: "var(--text-muted)" }}>
            <ChevronDown size={14} />
          </button>
        </div>
      </div>

      {result.frontier_error && (
        <div
          className="flex items-start gap-2 px-4 py-2 text-xs"
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

      {/* Content */}
      <div className="flex-1 overflow-auto px-4 py-3">
        {/* ── Frontier Tab ── */}
        {activeTab === "frontier" && (
          <FrontierTab
            frontier={frontier}
            result={result}
            constraints={constraints}
            constraintNames={constraintNames}
            selectedIdx={selectedIdx}
            xConstraintIdx={xConstraintIdx}
            onXConstraintChange={setXConstraintIdx}
            onPointClick={handlePointClick}
            onSave={handleSave}
            onLogMlflow={handleLogMlflow}
            saving={saving}
            logging={logging}
            terminalActionsDisabled={terminalDetailBlocksActions}
            mlflowAvailable={mlflowAvailable}
            actionMsg={actionMsg}
          />
        )}

        {/* ── Summary Tab ── */}
        {activeTab === "summary" && (
          <SummaryTab
            result={result}
            constraints={constraints}
            canMaterialiseRatebookRates={selectedRatebookRatesMissing}
            ratebookRatesDetail={ratesDetail}
          />
        )}

        {activeTab === "rates" && (
          ratebookFactorTables ? (
            <RatebookRatesTab factorTables={ratebookFactorTables} />
          ) : (
            <RatebookRatesPending detail={ratesDetail} />
          )
        )}

        {/* ── Convergence Tab ── */}
        {activeTab === "convergence" && result.history && result.history.length > 0 && (
          <ConvergenceChart result={result} />
        )}

        {/* ── Export Tab ── */}
        {activeTab === "export" && (
          <ExportTab
            result={result}
            onSave={handleSave}
            onLogMlflow={handleLogMlflow}
            onLoadResultDetail={handleLoadResultDetail}
            saving={saving}
            logging={logging}
            terminalActionsDisabled={terminalDetailBlocksActions}
            mlflowAvailable={mlflowAvailable}
            actionMsg={actionMsg}
            resultDetail={resultDetail}
          />
        )}
      </div>
    </div>
  )
}

// ─── Frontier Tab ────────────────────────────────────────────────

interface FrontierTabProps {
  frontier: FrontierData | null
  result: SolveResult
  constraints: Record<string, Record<string, number>>
  constraintNames: string[]
  selectedIdx: number | null
  xConstraintIdx: number
  onXConstraintChange: (idx: number) => void
  onPointClick: (index: number) => void
  onSave: () => void
  onLogMlflow: () => void
  saving: boolean
  logging: boolean
  terminalActionsDisabled: boolean
  mlflowAvailable: boolean
  actionMsg: string | null
}

function finitePointNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

function frontierConstraintPointValue(point: Record<string, unknown>, name: string): number | null {
  const totalValue = finitePointNumber(point[`total_${name}`])
  if (totalValue !== null) return totalValue
  const nested = point.constraints
  if (nested && typeof nested === "object" && !Array.isArray(nested)) {
    const nestedValue = finitePointNumber((nested as Record<string, unknown>)[name])
    if (nestedValue !== null) return nestedValue
  }
  return finitePointNumber(point[name])
}

function FrontierTab({
  frontier,
  result,
  constraints,
  constraintNames,
  selectedIdx,
  xConstraintIdx,
  onXConstraintChange,
  onPointClick,
  onSave,
  onLogMlflow,
  saving,
  logging,
  terminalActionsDisabled,
  mlflowAvailable,
  actionMsg,
}: FrontierTabProps) {
  const points = frontier?.points ?? EMPTY_FRONTIER_POINTS
  const xConstraintName = constraintNames[xConstraintIdx] ?? constraintNames[0]
  const xKey = xConstraintName ? `total_${xConstraintName}` : null
  const yKey = "total_objective"
  const chartPoints = useMemo(() => {
    if (!xKey || !xConstraintName) return points
    return points.map((point) => {
      if (finitePointNumber(point[xKey]) !== null) return point
      const value = frontierConstraintPointValue(point, xConstraintName)
      return value === null ? point : { ...point, [xKey]: value }
    })
  }, [points, xConstraintName, xKey])

  if (!frontier || points.length === 0) {
    return (
      <div className="text-xs py-4" style={{ color: "var(--text-muted)" }}>
        No frontier data available. Enable efficient frontier in the constraint settings and run the optimiser.
      </div>
    )
  }

  const shownPointCount = frontier.points_returned || points.length
  const totalPointCount = frontier.n_points || points.length

  // Build scales
  const xVals = xKey ? chartPoints.map(p => p[xKey] as number).filter(v => typeof v === "number" && Number.isFinite(v)) : []
  const yVals = chartPoints.map(p => p[yKey] as number).filter(v => typeof v === "number" && Number.isFinite(v))

  const hasChartData = xKey && xVals.length >= 2 && yVals.length >= 2

  // Current solve result marker position
  const currentX = xConstraintName ? result.constraints[xConstraintName] : null
  const currentY = result.total_objective

  return (
    <div className="flex gap-4 h-full">
      {/* LEFT: Chart area */}
      <div className="flex-[55] min-w-0">
        {constraintNames.length > 1 && (
          <div className="flex items-center gap-2 mb-2">
            <label className="text-[10px] font-medium" style={{ color: "var(--text-muted)" }}>X axis:</label>
            <select
              value={xConstraintIdx}
              onChange={e => onXConstraintChange(Number(e.target.value))}
              className="text-[11px] font-mono rounded px-1.5 py-0.5"
              style={{
                background: "var(--bg-input)",
                border: "1px solid var(--border)",
                color: "var(--text-primary)",
              }}
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
            xKey={xKey!}
            yKey={yKey}
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

        <div className="text-[10px] mt-1" style={{ color: "var(--text-muted)" }}>
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
        </div>
      </div>

      {/* RIGHT: Detail card */}
      {selectedIdx != null && points[selectedIdx] && (
        <div className="flex-[45] min-w-[200px] max-w-[320px]">
          <DetailCard
            points={points}
            selectedIdx={selectedIdx}
            constraints={constraints}
            constraintNames={constraintNames}
            onSave={onSave}
            onLogMlflow={onLogMlflow}
            saving={saving}
            logging={logging}
            terminalActionsDisabled={terminalActionsDisabled}
            mlflowAvailable={mlflowAvailable}
            actionMsg={actionMsg}
          />
        </div>
      )}
    </div>
  )
}

// ─── Export Tab ──────────────────────────────────────────────────

function RatebookRatesPending({ detail }: { detail: RatesDetailState }) {
  if (detail.status === "error") {
    return (
      <div
        className="flex items-start gap-2 text-xs px-3 py-2 rounded"
        style={{ background: "var(--danger-soft)", color: "var(--danger)" }}
      >
        <AlertCircle size={14} className="mt-0.5 shrink-0" />
        <span>Rate table load failed: {detail.error}</span>
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

function ExportTab({
  result,
  onSave,
  onLogMlflow,
  onLoadResultDetail,
  saving,
  logging,
  terminalActionsDisabled,
  mlflowAvailable,
  actionMsg,
  resultDetail,
}: {
  result: SolveResult
  onSave: () => void
  onLogMlflow: () => void
  onLoadResultDetail: () => void
  saving: boolean
  logging: boolean
  terminalActionsDisabled: boolean
  mlflowAvailable: boolean
  actionMsg: string | null
  resultDetail: ResultDetailState
}) {
  const detailBusy = resultDetail.status === "loading"
  const detailButtonLabel = resultDetail.status === "loaded" ? "Refresh detail" : "Load detail"
  const terminalActionBusy = saving || logging || terminalActionsDisabled

  return (
    <div className="space-y-4 max-w-md">
      <div className="space-y-1">
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
          Result detail
        </label>
        <button
          onClick={onLoadResultDetail}
          disabled={saving || logging || detailBusy}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors mt-1"
          style={{
            background: saving || logging || detailBusy ? "var(--chrome-hover)" : "var(--accent-soft)",
            color: saving || logging || detailBusy ? "var(--text-muted)" : "var(--accent)",
          }}
        >
          {detailBusy ? <Loader2 size={12} className="animate-spin" /> : <Table2 size={12} />}
          {detailBusy ? "Loading detail" : detailButtonLabel}
        </button>
        <ResultDetailStatus detail={resultDetail} />
      </div>

      <div className="space-y-1">
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
          Save to file
        </label>
        <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
          Save the optimisation result as a JSON artifact. This can be loaded by an Apply Optimisation node.
        </p>
        <button
          onClick={onSave}
          disabled={terminalActionBusy}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors mt-1.5"
          style={{
            background: terminalActionBusy ? "var(--chrome-hover)" : "var(--warning-soft-emphasis)",
            color: terminalActionBusy ? "var(--text-muted)" : "var(--warning-strong)",
          }}
        >
          {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
          Save result
        </button>
      </div>

      {mlflowAvailable && (
        <div className="space-y-1">
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
            Log to MLflow
          </label>
          <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
            Log the optimisation result, convergence history, and metadata to MLflow for tracking and comparison.
          </p>
          <button
            onClick={onLogMlflow}
            disabled={terminalActionBusy}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors mt-1.5"
            style={{
              background: terminalActionBusy ? "var(--chrome-hover)" : MODEL_COLORS.accentSoft,
              color: terminalActionBusy ? "var(--text-muted)" : MODEL_COLORS.accent,
            }}
          >
            {logging ? <Loader2 size={12} className="animate-spin" /> : <Upload size={12} />}
            Log to MLflow
          </button>
        </div>
      )}

      {actionMsg && (
        <div className="text-xs px-2 py-1.5 rounded" style={{ background: "var(--bg-input)", color: "var(--text-secondary)" }}>
          {actionMsg}
        </div>
      )}

      {/* Quick summary for context */}
      <div className="pt-2" style={{ borderTop: "1px solid var(--border)" }}>
        <div className="grid grid-cols-2 gap-x-6 gap-y-0.5 text-xs font-mono">
          <div className="flex justify-between">
            <span style={{ color: "var(--text-muted)" }}>Objective</span>
            <span style={{ color: "var(--text-primary)" }}>{formatNumber(result.total_objective)}</span>
          </div>
          <div className="flex justify-between">
            <span style={{ color: "var(--text-muted)" }}>Baseline</span>
            <span style={{ color: "var(--text-primary)" }}>{formatNumber(result.baseline_objective)}</span>
          </div>
          <div className="flex justify-between">
            <span style={{ color: "var(--text-muted)" }}>Status</span>
            <span style={{ color: result.converged ? "var(--success)" : "var(--warning-strong)" }}>
              {result.converged ? "Converged" : "Not converged"}
            </span>
          </div>
          {result.n_quotes != null && (
            <div className="flex justify-between">
              <span style={{ color: "var(--text-muted)" }}>Quotes</span>
              <span style={{ color: "var(--text-primary)" }}>{result.n_quotes.toLocaleString()}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function ResultDetailStatus({ detail }: { detail: ResultDetailState }) {
  if (detail.status === "idle") return null

  if (detail.status === "loading") {
    return (
      <div className="flex items-center gap-1.5 text-[11px]" style={{ color: "var(--text-muted)" }}>
        <Loader2 size={12} className="animate-spin" />
        Loading result detail...
      </div>
    )
  }

  if (detail.status === "error") {
    return (
      <div className="flex items-start gap-1.5 text-[11px] px-2 py-1.5 rounded" style={{ background: "var(--danger-soft)", color: "var(--danger)" }}>
        <AlertCircle size={12} className="mt-0.5 shrink-0" />
        <span>Detail load failed: {detail.error}</span>
      </div>
    )
  }

  const returnedRows = detail.data.preview_row_count ?? detail.data.preview.length
  const totalRows = detail.data.row_count ?? returnedRows
  const limit = detail.data.preview_row_limit ?? returnedRows

  return (
    <div className="text-[11px] px-2 py-1.5 rounded" style={{ background: "var(--bg-input)", color: "var(--text-secondary)" }}>
      <span style={{ color: "var(--text-primary)", fontWeight: 600 }}>
        {returnedRows.toLocaleString()} of {totalRows.toLocaleString()} rows loaded
      </span>
      {detail.data.preview_truncated && (
        <span> · capped at {limit.toLocaleString()}</span>
      )}
    </div>
  )
}
