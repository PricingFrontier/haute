/**
 * Bottom-panel visualisations for the optimiser node.
 *
 * Renders in the same slot as DataPreview when an optimiser solve has
 * completed.  Three tabs: Frontier (default when data exists), Summary,
 * Convergence.
 *
 * The Frontier tab shows an interactive scatter chart (left) and a
 * detail card (right) with metrics and Save/Log actions.
 */

import { useState, useMemo, useCallback, useEffect } from "react"
import { ChevronDown, ChevronUp, ChevronLeft, ChevronRight, Loader2, Target, Save, Upload } from "lucide-react"
import {
  selectFrontierPoint as selectFrontierPointAPI,
  saveOptimiser,
  logOptimiserToMlflow,
} from "../api/client"
import { formatNumber } from "../utils/formatValue"
import { formatAxisLabel, yTicks } from "../utils/chartHelpers"
import { useDragResize } from "../hooks/useDragResize"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import useSettingsStore from "../stores/useSettingsStore"
import type { FrontierData } from "../api/types"

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
    constraint_names: string[]
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

// ─── Chart constants ─────────────────────────────────────────────

const CHART_W = 380
const CHART_H = 220
const CHART_PX = 50
const CHART_PX_RIGHT = 16
const CHART_PY = 16
const CHART_PY_BOTTOM = 28
const INNER_W = CHART_W - CHART_PX - CHART_PX_RIGHT
const INNER_H = CHART_H - CHART_PY - CHART_PY_BOTTOM

// ─── Constraint-met helper ────────────────────────────────────────

function isConstraintMet(thresholdType: string, ratio: number, absValue: number, thresholdVal: number): boolean {
  if (thresholdType === "min") return ratio >= thresholdVal
  if (thresholdType === "max") return ratio <= thresholdVal
  if (thresholdType === "min_abs") return absValue >= thresholdVal
  if (thresholdType === "max_abs") return absValue <= thresholdVal
  return true
}

// ─── Component ───────────────────────────────────────────────────

interface OptimiserPreviewProps {
  data: OptimiserPreviewData
  nodeId: string
}

type TabKey = "frontier" | "summary" | "convergence" | "export"

export default function OptimiserPreview({ data, nodeId }: OptimiserPreviewProps) {
  const { result, jobId, constraints } = data

  const [collapsed, setCollapsed] = useState(false)
  const { height, containerRef, onDragStart } = useDragResize({ initialHeight: 320, minHeight: 160, maxHeight: 600 })

  // Default tab: frontier when frontier data exists, otherwise summary
  const [tab, setTab] = useState<TabKey>(() =>
    data.frontier && data.frontier.points.length > 0 ? "frontier" : "summary",
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

  // ── Frontier point selection ──
  const frontier = data.frontier
  const selectedIdx = data.selectedPointIndex

  // Clear action feedback when the selected point changes (M8)
  useEffect(() => { setActionMsg(null) }, [selectedIdx])

  const handlePointClick = useCallback(
    async (index: number) => {
      // Toggle off if clicking selected point
      if (index === selectedIdx) {
        storeSelectPoint(nodeId, null)
        return
      }
      storeSelectPoint(nodeId, index)
      try {
        const res = await selectFrontierPointAPI({ job_id: jobId, point_index: index })
        storeUpdateAfterSelect(nodeId, index, res)
      } catch (err) {
        console.warn("frontier point select failed", err)
        // Revert to the previous selection so the UI doesn't show stale data
        storeSelectPoint(nodeId, selectedIdx)
      }
    },
    [selectedIdx, nodeId, jobId, storeSelectPoint, storeUpdateAfterSelect],
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
      // If a frontier point is selected, ensure it's applied on the backend first
      if (selectedIdx != null && frontier) {
        await selectFrontierPointAPI({ job_id: jobId, point_index: selectedIdx })
      }
      const outputPath = `output/optimiser_${data.nodeLabel.toLowerCase().replace(/ /g, "_")}.json`
      const res = await saveOptimiser({ job_id: jobId, output_path: outputPath })
      setActionMsg(res.message ?? `Saved to ${res.path ?? outputPath}`)
    } catch (e) {
      setActionMsg(`Save failed: ${e}`)
    } finally {
      setSaving(false)
    }
  }, [selectedIdx, frontier, jobId, data.nodeLabel])

  const handleLogMlflow = useCallback(async () => {
    setLogging(true)
    setActionMsg(null)
    try {
      // If a frontier point is selected, ensure it's applied on the backend first
      if (selectedIdx != null && frontier) {
        await selectFrontierPointAPI({ job_id: jobId, point_index: selectedIdx })
      }
      const res = await logOptimiserToMlflow({ job_id: jobId })
      const target = res.experiment_name ? ` to ${res.experiment_name}` : ""
      setActionMsg(res.run_url ? `Logged${target}: ${res.run_url}` : `Logged${target} (run ${res.run_id ?? "ok"})`)
    } catch (e) {
      setActionMsg(`MLflow log failed: ${e}`)
    } finally {
      setLogging(false)
    }
  }, [selectedIdx, frontier, jobId])

  // ── Collapsed ──
  if (collapsed) {
    return (
      <div className="h-8 flex items-center px-4 shrink-0" style={{ borderTop: "1px solid var(--border)", background: "var(--bg-panel)" }}>
        <button onClick={() => setCollapsed(false)} aria-label="Expand panel" className="flex items-center gap-2 text-xs" style={{ color: "var(--text-secondary)" }}>
          <ChevronUp size={14} />
          <Target size={14} />
          <span className="font-medium">{data.nodeLabel}</span>
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
  const availableTabs: TabKey[] = hasFrontier ? ["frontier", "summary"] : ["summary"]
  if (result.history && result.history.length > 0) availableTabs.push("convergence")
  availableTabs.push("export")

  const TAB_LABELS: Record<TabKey, string> = {
    frontier: "Frontier",
    summary: "Summary",
    convergence: "Convergence",
    export: "Export",
  }

  // ── Expanded ──
  return (
    <div ref={containerRef} style={{ height, borderTop: "1px solid var(--border)", background: "var(--bg-panel)" }} className="flex flex-col shrink-0 relative">
      {/* Drag handle */}
      <div
        onMouseDown={onDragStart}
        className="absolute top-0 left-0 right-0 h-1 cursor-ns-resize z-10 transition-colors"
        style={{ background: "var(--chrome-border)" }}
        onMouseEnter={e => { e.currentTarget.style.background = "var(--accent)" }}
        onMouseLeave={e => { e.currentTarget.style.background = "var(--chrome-border)" }}
      />

      {/* Header */}
      <div className="min-h-9 flex items-center flex-wrap px-4 shrink-0 gap-x-2 gap-y-1 py-1.5" style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-elevated)" }}>
        <Target size={14} style={{ color: "#f59e0b" }} />
        <span className="text-xs font-bold" style={{ color: "var(--text-primary)" }}>{data.nodeLabel}</span>
        <span className="text-[11px]" style={{ color: result.converged ? "#22c55e" : "#f59e0b" }}>
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
                background: tab === t ? "var(--accent-soft)" : "var(--chrome-hover)",
                color: tab === t ? "var(--accent)" : "var(--text-muted)",
              }}
            >
              {TAB_LABELS[t]}
            </button>
          ))}
        </div>

        <div className="ml-auto flex items-center gap-1">
          <button onClick={() => setCollapsed(true)} aria-label="Collapse panel" className="p-1 rounded transition-colors" style={{ color: "var(--text-muted)" }}
            onMouseEnter={e => { e.currentTarget.style.background = "var(--bg-hover)" }}
            onMouseLeave={e => { e.currentTarget.style.background = "transparent" }}
          >
            <ChevronDown size={14} />
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto px-4 py-3">
        {/* ── Frontier Tab ── */}
        {tab === "frontier" && (
          <FrontierTab
            frontier={frontier}
            result={result}
            constraints={constraints}
            constraintNames={constraintNames}
            selectedIdx={selectedIdx}
            xConstraintIdx={xConstraintIdx}
            onXConstraintChange={setXConstraintIdx}
            onPointClick={handlePointClick}
            onStepPoint={handleStepPoint}
            onSave={handleSave}
            onLogMlflow={handleLogMlflow}
            saving={saving}
            logging={logging}
            mlflowAvailable={mlflowAvailable}
            actionMsg={actionMsg}
          />
        )}

        {/* ── Summary Tab ── */}
        {tab === "summary" && (
          <SummaryTab result={result} constraints={constraints} />
        )}

        {/* ── Convergence Tab ── */}
        {tab === "convergence" && result.history && result.history.length > 0 && (
          <ConvergenceTab result={result} />
        )}

        {/* ── Export Tab ── */}
        {tab === "export" && (
          <ExportTab
            result={result}
            onSave={handleSave}
            onLogMlflow={handleLogMlflow}
            saving={saving}
            logging={logging}
            mlflowAvailable={mlflowAvailable}
            actionMsg={actionMsg}
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
  onStepPoint: (delta: number) => void
  onSave: () => void
  onLogMlflow: () => void
  saving: boolean
  logging: boolean
  mlflowAvailable: boolean
  actionMsg: string | null
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
  onStepPoint,
  onSave,
  onLogMlflow,
  saving,
  logging,
  mlflowAvailable,
  actionMsg,
}: FrontierTabProps) {
  if (!frontier || frontier.points.length === 0) {
    return (
      <div className="text-xs py-4" style={{ color: "var(--text-muted)" }}>
        No frontier data available. Frontier is computed automatically during the solve.
      </div>
    )
  }

  const points = frontier.points
  const xConstraintName = constraintNames[xConstraintIdx] ?? constraintNames[0]
  const xKey = xConstraintName ? `total_${xConstraintName}` : null
  const yKey = "total_objective"

  // Build scales
  const xVals = xKey ? points.map(p => p[xKey] as number).filter(v => typeof v === "number" && Number.isFinite(v)) : []
  const yVals = points.map(p => p[yKey] as number).filter(v => typeof v === "number" && Number.isFinite(v))

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
            points={points}
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
          {points.length} frontier points. Click a point for details.
        </div>
      </div>

      {/* RIGHT: Detail card */}
      {selectedIdx != null && points[selectedIdx] && (
        <div className="flex-[45] min-w-[200px] max-w-[320px]">
          <FrontierDetailCard
            points={points}
            selectedIdx={selectedIdx}
            result={result}
            constraints={constraints}
            constraintNames={constraintNames}
            onStepPoint={onStepPoint}
            onSave={onSave}
            onLogMlflow={onLogMlflow}
            saving={saving}
            logging={logging}
            mlflowAvailable={mlflowAvailable}
            actionMsg={actionMsg}
          />
        </div>
      )}
    </div>
  )
}

// ─── Frontier Chart (SVG scatter) ────────────────────────────────

interface FrontierChartProps {
  points: Record<string, unknown>[]
  xKey: string
  yKey: string
  xLabel: string
  selectedIdx: number | null
  currentX: number | null
  currentY: number
  onPointClick: (index: number) => void
}

function FrontierChart({
  points,
  xKey,
  yKey,
  xLabel,
  selectedIdx,
  currentX,
  currentY,
  onPointClick,
}: FrontierChartProps) {
  const { xScale, yScale, xTicks, yTickVals } = useMemo(() => {
    const xs = points.map(p => p[xKey] as number).filter(v => typeof v === "number" && Number.isFinite(v))
    const ys = points.map(p => p[yKey] as number).filter(v => typeof v === "number" && Number.isFinite(v))

    // Include current solve point in domain calculation
    if (currentX != null && Number.isFinite(currentX)) xs.push(currentX)
    if (Number.isFinite(currentY)) ys.push(currentY)

    let xMin = Math.min(...xs), xMax = Math.max(...xs)
    let yMin = Math.min(...ys), yMax = Math.max(...ys)

    // Add 5% padding
    const xPad = (xMax - xMin) * 0.05 || 0.01
    const yPad = (yMax - yMin) * 0.05 || 0.01
    xMin -= xPad; xMax += xPad
    yMin -= yPad; yMax += yPad

    const xRange = xMax - xMin || 1
    const yRange = yMax - yMin || 1

    return {
      xScale: (v: number) => CHART_PX + ((v - xMin) / xRange) * INNER_W,
      yScale: (v: number) => CHART_PY + INNER_H - ((v - yMin) / yRange) * INNER_H,
      xTicks: yTicks(xMin + xPad, xMax - xPad, 4),
      yTickVals: yTicks(yMin + yPad, yMax - yPad, 4),
    }
  }, [points, xKey, yKey, currentX, currentY])

  return (
    <svg width={CHART_W} height={CHART_H} style={{ background: "var(--bg-input)", borderRadius: 6, border: "1px solid var(--border)" }}>
      {/* Grid lines + Y axis labels */}
      {yTickVals.map(t => (
        <g key={`y-${t}`}>
          <line x1={CHART_PX} y1={yScale(t)} x2={CHART_PX + INNER_W} y2={yScale(t)} stroke="var(--border)" strokeWidth={0.5} />
          <text x={CHART_PX - 4} y={yScale(t) + 3} textAnchor="end" fontSize={9} fill="var(--text-muted)">{formatAxisLabel(t)}</text>
        </g>
      ))}
      {/* X axis labels */}
      {xTicks.map(t => (
        <text key={`x-${t}`} x={xScale(t)} y={CHART_H - CHART_PY_BOTTOM + 14} textAnchor="middle" fontSize={9} fill="var(--text-muted)">{formatAxisLabel(t)}</text>
      ))}
      {/* Axis labels */}
      <text x={CHART_PX + INNER_W / 2} y={CHART_H - 3} textAnchor="middle" fontSize={9} fill="var(--text-muted)">{xLabel}</text>
      <text x={6} y={CHART_PY + INNER_H / 2} textAnchor="middle" fontSize={9} fill="var(--text-muted)" transform={`rotate(-90,6,${CHART_PY + INNER_H / 2})`}>objective</text>

      {/* Frontier points */}
      {points.map((p, i) => {
        const x = p[xKey] as number
        const y = p[yKey] as number
        if (typeof x !== "number" || typeof y !== "number") return null
        const isSel = selectedIdx === i
        return (
          <circle
            key={i}
            cx={xScale(x)}
            cy={yScale(y)}
            r={isSel ? 6 : 4}
            fill={isSel ? "#f59e0b" : "var(--accent)"}
            stroke={isSel ? "#fff" : "none"}
            strokeWidth={isSel ? 2 : 0}
            opacity={isSel ? 1 : 0.7}
            style={{ cursor: "pointer" }}
            onClick={() => onPointClick(i)}
            tabIndex={0}
            role="button"
            aria-label={`Select frontier point ${i + 1}`}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPointClick(i) } }}
          />
        )
      })}

      {/* Current solve result marker (diamond ring) */}
      {currentX != null && Number.isFinite(currentX) && Number.isFinite(currentY) && (
        <g>
          <circle
            cx={xScale(currentX)}
            cy={yScale(currentY)}
            r={6}
            fill="none"
            stroke="#f59e0b"
            strokeWidth={2}
          />
          <circle
            cx={xScale(currentX)}
            cy={yScale(currentY)}
            r={2.5}
            fill="#f59e0b"
          />
        </g>
      )}
    </svg>
  )
}

// ─── Frontier Detail Card ────────────────────────────────────────

interface FrontierDetailCardProps {
  points: Record<string, unknown>[]
  selectedIdx: number
  result: SolveResult
  constraints: Record<string, Record<string, number>>
  constraintNames: string[]
  onStepPoint: (delta: number) => void
  onSave: () => void
  onLogMlflow: () => void
  saving: boolean
  logging: boolean
  mlflowAvailable: boolean
  actionMsg: string | null
}

function FrontierDetailCard({
  points,
  selectedIdx,
  result,
  constraints,
  constraintNames,
  onStepPoint,
  onSave,
  onLogMlflow,
  saving,
  logging,
  mlflowAvailable,
  actionMsg,
}: FrontierDetailCardProps) {
  const point = points[selectedIdx]
  if (!point) return null

  const objValue = Number(point.total_objective ?? 0)
  const baselineObj = result.baseline_objective
  const objVsBaseline = baselineObj !== 0 ? ((objValue / baselineObj - 1) * 100) : null

  return (
    <div className="rounded-lg p-3 space-y-3" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}>
      {/* Header with stepper */}
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-bold" style={{ color: "var(--text-primary)" }}>
          Point {selectedIdx + 1} of {points.length}
        </span>
        <div className="flex items-center gap-0.5">
          <button
            onClick={() => onStepPoint(-1)}
            disabled={selectedIdx <= 0}
            aria-label="Previous point"
            className="p-0.5 rounded transition-colors"
            style={{ color: selectedIdx <= 0 ? "var(--text-muted)" : "var(--text-secondary)", opacity: selectedIdx <= 0 ? 0.4 : 1 }}
          >
            <ChevronLeft size={14} />
          </button>
          <button
            onClick={() => onStepPoint(1)}
            disabled={selectedIdx >= points.length - 1}
            aria-label="Next point"
            className="p-0.5 rounded transition-colors"
            style={{ color: selectedIdx >= points.length - 1 ? "var(--text-muted)" : "var(--text-secondary)", opacity: selectedIdx >= points.length - 1 ? 0.4 : 1 }}
          >
            <ChevronRight size={14} />
          </button>
        </div>
      </div>

      {/* Objective */}
      <div>
        <label className="text-[10px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Objective</label>
        <div className="mt-0.5 flex items-baseline justify-between text-xs font-mono gap-2">
          <span style={{ color: "var(--text-primary)" }}>{formatNumber(objValue)}</span>
          {objVsBaseline != null && (
            <span style={{ color: "#f59e0b" }}>{objVsBaseline >= 0 ? "+" : ""}{objVsBaseline.toFixed(2)}% vs baseline</span>
          )}
        </div>
      </div>

      {/* Constraints */}
      {constraintNames.length > 0 && (
        <div>
          <label className="text-[10px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Constraints</label>
          <div className="mt-0.5 space-y-0.5">
            {constraintNames.map(name => {
              const totalKey = `total_${name}`
              const value = Number(point[totalKey] ?? 0)
              const baseline = result.baseline_constraints[name]
              const ratio = baseline ? value / baseline : 0
              const spec = constraints[name] || {}
              const thresholdType = Object.keys(spec)[0]
              const thresholdVal = spec[thresholdType] ?? 0
              const met = isConstraintMet(thresholdType, ratio, value, thresholdVal)
              return (
                <div key={name} className="flex items-center justify-between text-xs font-mono gap-2">
                  <span className="flex items-center gap-1.5">
                    <span className="inline-block w-1.5 h-1.5 rounded-full shrink-0" style={{ background: met ? "#22c55e" : "#ef4444" }} />
                    <span style={{ color: "var(--text-secondary)" }}>{name}</span>
                  </span>
                  <span>
                    <span style={{ color: "var(--text-primary)" }}>{formatNumber(value)}</span>
                    {baseline != null && baseline !== 0 && (
                      <span style={{ color: "var(--text-muted)" }}> ({(ratio * 100).toFixed(1)}%)</span>
                    )}
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Lambdas */}
      {(() => {
        const lambdaKeys = Object.keys(point).filter(k => k.startsWith("lambda_"))
        if (lambdaKeys.length === 0) return null
        return (
          <div>
            <label className="text-[10px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Lambdas</label>
            <div className="mt-0.5 space-y-0.5">
              {lambdaKeys.map(k => {
                const displayName = k.replace(/^lambda_/, "")
                const v = point[k] as number
                return (
                  <div key={k} className="flex justify-between text-xs font-mono gap-2">
                    <span style={{ color: "var(--text-secondary)" }}>{displayName}</span>
                    <span style={{ color: "var(--text-primary)" }}>{typeof v === "number" ? v.toFixed(6) : String(v)}</span>
                  </div>
                )
              })}
            </div>
          </div>
        )
      })()}

      {/* Action buttons */}
      <div className="flex gap-2 pt-1" style={{ borderTop: "1px solid var(--border)" }}>
        <button
          onClick={onSave}
          disabled={saving || logging}
          className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors"
          style={{
            background: saving || logging ? "var(--chrome-hover)" : "rgba(245,158,11,.12)",
            color: saving || logging ? "var(--text-muted)" : "#f59e0b",
            border: "1px solid rgba(245,158,11,.25)",
          }}
        >
          {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
          Save Result
        </button>
        {mlflowAvailable && (
          <button
            onClick={onLogMlflow}
            disabled={saving || logging}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors"
            style={{
              background: saving || logging ? "var(--chrome-hover)" : "rgba(168,85,247,.12)",
              color: saving || logging ? "var(--text-muted)" : "#a855f7",
              border: "1px solid rgba(168,85,247,.25)",
            }}
          >
            {logging ? <Loader2 size={12} className="animate-spin" /> : <Upload size={12} />}
            Log to MLflow
          </button>
        )}
      </div>

      {/* Action feedback */}
      {actionMsg && (
        <div className="text-[10px] font-mono px-1" style={{ color: "var(--text-muted)", wordBreak: "break-all" }}>
          {actionMsg}
        </div>
      )}
    </div>
  )
}

// ─── Summary Tab (inline, unchanged) ─────────────────────────────

function SummaryTab({
  result,
  constraints,
}: {
  result: SolveResult
  constraints: Record<string, Record<string, number>>
}) {
  return (
    <div className="flex gap-6 flex-wrap">
      {/* Left column: objective + constraints */}
      <div className="space-y-3 min-w-[200px]">
        <div>
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Objective</label>
          <div className="mt-1 space-y-0.5">
            <div className="flex justify-between text-xs font-mono gap-4">
              <span style={{ color: "var(--text-secondary)" }}>Optimised</span>
              <span style={{ color: "var(--text-primary)" }}>{formatNumber(result.total_objective)}</span>
            </div>
            <div className="flex justify-between text-xs font-mono gap-4">
              <span style={{ color: "var(--text-secondary)" }}>Baseline</span>
              <span style={{ color: "var(--text-muted)" }}>{formatNumber(result.baseline_objective)}</span>
            </div>
            {result.baseline_objective !== 0 && (
              <div className="flex justify-between text-xs font-mono gap-4">
                <span style={{ color: "var(--text-secondary)" }}>Uplift</span>
                <span style={{ color: "#f59e0b" }}>
                  {((result.total_objective / result.baseline_objective - 1) * 100).toFixed(2)}%
                </span>
              </div>
            )}
          </div>
        </div>

        {/* Constraints with binding indicators */}
        {Object.keys(result.constraints).length > 0 && (
          <div>
            <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Constraints</label>
            <div className="mt-1 space-y-0.5">
              {Object.entries(result.constraints).map(([name, value]) => {
                const baseline = result.baseline_constraints[name]
                const ratio = baseline ? value / baseline : 0
                const spec = constraints[name] || {}
                const thresholdType = Object.keys(spec)[0]
                const thresholdVal = spec[thresholdType] ?? 0
                const met = isConstraintMet(thresholdType, ratio, value, thresholdVal)
                return (
                  <div key={name} className="flex items-center justify-between text-xs font-mono gap-4">
                    <span className="flex items-center gap-1.5">
                      <span className="inline-block w-1.5 h-1.5 rounded-full shrink-0" style={{ background: met ? "#22c55e" : "#ef4444" }} />
                      <span style={{ color: "var(--text-secondary)" }}>{name}</span>
                    </span>
                    <span>
                      <span style={{ color: "var(--text-primary)" }}>{formatNumber(value)}</span>
                      {baseline !== undefined && (
                        <span style={{ color: "var(--text-muted)" }}> ({(ratio * 100).toFixed(1)}%)</span>
                      )}
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* Lambdas (online) / Factor tables (ratebook) */}
        {result.mode !== "ratebook" && Object.keys(result.lambdas).length > 0 && (
          <div>
            <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Lambdas</label>
            <div className="mt-1 space-y-0.5">
              {Object.entries(result.lambdas).map(([name, value]) => (
                <div key={name} className="flex justify-between text-xs font-mono gap-4">
                  <span style={{ color: "var(--text-secondary)" }}>{name}</span>
                  <span style={{ color: "var(--text-primary)" }}>{value.toFixed(6)}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {result.mode === "ratebook" && result.clamp_rate != null && (
          <div className="flex justify-between text-xs font-mono">
            <span style={{ color: "var(--text-muted)" }}>Clamp rate</span>
            <span style={{ color: "#f59e0b" }}>{(result.clamp_rate * 100).toFixed(1)}%</span>
          </div>
        )}

      </div>

      {/* Middle column: histogram + stats */}
      {result.scenario_value_histogram && (() => {
        const { counts, edges } = result.scenario_value_histogram
        if (!counts || counts.length === 0) return null
        const maxCount = Math.max(...counts)
        const w = 320, h = 100, px = 2, py = 2
        const chartW = w - px * 2, chartH = h - py * 2
        const barW = chartW / counts.length
        const eMin = edges[0], eMax = edges[edges.length - 1]
        const oneX = eMax > eMin ? px + ((1.0 - eMin) / (eMax - eMin)) * chartW : null
        return (
          <div className="min-w-[200px]">
            <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Scenario Value Distribution</label>
            <svg width={w} height={h} className="mt-1" style={{ background: "var(--bg-input)", borderRadius: 6, border: "1px solid var(--border)" }}>
              {counts.map((c, i) => {
                const barH = maxCount > 0 ? (c / maxCount) * chartH : 0
                return (
                  <rect key={i} x={px + i * barW + 0.5} y={py + chartH - barH} width={Math.max(barW - 1, 1)} height={barH} fill="#f59e0b" opacity={0.7} />
                )
              })}
              {oneX != null && oneX >= px && oneX <= px + chartW && (
                <line x1={oneX} y1={py} x2={oneX} y2={py + chartH} stroke="#ef4444" strokeWidth={1} strokeDasharray="3,2" />
              )}
            </svg>
            <div className="flex gap-3 mt-0.5 text-[10px]" style={{ color: "var(--text-muted)" }}>
              <span>{eMin.toFixed(2)}</span>
              <span className="flex-1" />
              {oneX != null && <span><span style={{ color: "#ef4444" }}>|</span> 1.0</span>}
              <span className="flex-1" />
              <span>{eMax.toFixed(2)}</span>
            </div>

            {/* Stats grid */}
            {result.scenario_value_stats && (
              <div className="mt-2 grid grid-cols-2 gap-x-6 gap-y-0.5 text-xs font-mono">
                <div className="flex justify-between"><span style={{ color: "var(--text-muted)" }}>Mean</span><span style={{ color: "var(--text-primary)" }}>{result.scenario_value_stats.mean.toFixed(4)}</span></div>
                <div className="flex justify-between"><span style={{ color: "var(--text-muted)" }}>Std</span><span style={{ color: "var(--text-primary)" }}>{result.scenario_value_stats.std.toFixed(4)}</span></div>
                <div className="flex justify-between"><span style={{ color: "var(--text-muted)" }}>P5-P95</span><span style={{ color: "var(--text-primary)" }}>{result.scenario_value_stats.p5.toFixed(3)}-{result.scenario_value_stats.p95.toFixed(3)}</span></div>
                <div className="flex justify-between"><span style={{ color: "var(--text-muted)" }}>Min-Max</span><span style={{ color: "var(--text-primary)" }}>{result.scenario_value_stats.min.toFixed(3)}-{result.scenario_value_stats.max.toFixed(3)}</span></div>
                <div className="flex justify-between"><span style={{ color: "#22c55e" }}>Increase</span><span style={{ color: "#22c55e" }}>{(result.scenario_value_stats.pct_increase * 100).toFixed(1)}%</span></div>
                <div className="flex justify-between"><span style={{ color: "#ef4444" }}>Decrease</span><span style={{ color: "#ef4444" }}>{(result.scenario_value_stats.pct_decrease * 100).toFixed(1)}%</span></div>
              </div>
            )}
          </div>
        )
      })()}

      {/* Factor tables (ratebook) */}
      {result.mode === "ratebook" && result.factor_tables && (
        <div className="min-w-[180px]">
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Factor Tables</label>
          {Object.entries(result.factor_tables).map(([factorName, rows]) => (
            <div key={factorName} className="mt-1.5">
              <div className="text-[11px] font-medium mb-1" style={{ color: "var(--text-secondary)" }}>{factorName}</div>
              <div className="space-y-0.5">
                {rows.map((row, i) => {
                  const levelName = row.__factor_group__ as string ?? row[Object.keys(row)[0]] as string ?? `Level ${i}`
                  const mult = row.optimal_scenario_value as number
                  return (
                    <div key={i} className="flex justify-between text-xs font-mono gap-4">
                      <span style={{ color: "var(--text-secondary)" }}>{levelName}</span>
                      <span style={{ color: "var(--text-primary)" }}>{typeof mult === "number" ? mult.toFixed(2) : "?"}</span>
                    </div>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Export Tab ──────────────────────────────────────────────────

function ExportTab({
  result,
  onSave,
  onLogMlflow,
  saving,
  logging,
  mlflowAvailable,
  actionMsg,
}: {
  result: SolveResult
  onSave: () => void
  onLogMlflow: () => void
  saving: boolean
  logging: boolean
  mlflowAvailable: boolean
  actionMsg: string | null
}) {
  return (
    <div className="space-y-4 max-w-md">
      <div className="space-y-1">
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
          Save to file
        </label>
        <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
          Save the optimisation result as a JSON artifact. This can be loaded by an Apply Optimisation node.
        </p>
        <button
          onClick={onSave}
          disabled={saving || logging}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors mt-1.5"
          style={{
            background: saving || logging ? "var(--chrome-hover)" : "rgba(245,158,11,.12)",
            color: saving || logging ? "var(--text-muted)" : "#f59e0b",
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
            disabled={saving || logging}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors mt-1.5"
            style={{
              background: saving || logging ? "var(--chrome-hover)" : "rgba(168,85,247,.12)",
              color: saving || logging ? "var(--text-muted)" : "#a855f7",
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
            <span style={{ color: result.converged ? "#22c55e" : "#f59e0b" }}>
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

// ─── Convergence Tab (inline, unchanged) ─────────────────────────

function ConvergenceTab({ result }: { result: SolveResult }) {
  const hist = result.history!
  const w = 400, h = 140, px = 6, py = 6
  const chartW = w - px * 2, chartH = h - py * 2

  const objVals = hist.map(e => e.total_objective)
  const lcVals = hist.map(e => e.max_lambda_change)
  const objMin = Math.min(...objVals), objMax = Math.max(...objVals)
  const lcMin = Math.min(...lcVals), lcMax = Math.max(...lcVals)
  const objRange = objMax - objMin || 1, lcRange = lcMax - lcMin || 1

  const xScale = (i: number) => px + (i / Math.max(hist.length - 1, 1)) * chartW
  const yObj = (v: number) => py + chartH - ((v - objMin) / objRange) * chartH
  const yLc = (v: number) => py + chartH - ((v - lcMin) / lcRange) * chartH

  const objPath = hist.map((e, i) => `${i === 0 ? "M" : "L"}${xScale(i).toFixed(1)},${yObj(e.total_objective).toFixed(1)}`).join(" ")
  const lcPath = hist.map((e, i) => `${i === 0 ? "M" : "L"}${xScale(i).toFixed(1)},${yLc(e.max_lambda_change).toFixed(1)}`).join(" ")

  return (
    <div className="flex gap-6 flex-wrap">
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Convergence</label>
        <svg width={w} height={h} className="mt-1" style={{ background: "var(--bg-input)", borderRadius: 6, border: "1px solid var(--border)" }}>
          <path d={objPath} fill="none" stroke="#f59e0b" strokeWidth={1.5} />
          <path d={lcPath} fill="none" stroke="#3b82f6" strokeWidth={1.5} />
        </svg>
        <div className="flex gap-3 mt-0.5 text-[10px]" style={{ color: "var(--text-muted)" }}>
          <span><span style={{ color: "#f59e0b" }}>--</span> Objective</span>
          <span><span style={{ color: "#3b82f6" }}>--</span> Lambda change</span>
        </div>
      </div>

      {/* Iteration table */}
      <div className="min-w-[280px]">
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Iterations</label>
        <div className="mt-1 max-h-48 overflow-y-auto">
          <div className="flex text-[10px] font-bold py-0.5 sticky top-0" style={{ color: "var(--text-muted)", background: "var(--bg-panel)" }}>
            <span className="w-8 text-center">#</span>
            <span className="flex-1 text-right">Objective</span>
            <span className="flex-1 text-right">Max dLambda</span>
            <span className="w-10 text-center">OK</span>
          </div>
          {hist.map(e => (
            <div key={e.iteration} className="flex text-[10px] font-mono py-0.5" style={{ color: "var(--text-secondary)" }}>
              <span className="w-8 text-center">{e.iteration}</span>
              <span className="flex-1 text-right">{formatNumber(e.total_objective)}</span>
              <span className="flex-1 text-right">{e.max_lambda_change.toExponential(2)}</span>
              <span className="w-10 text-center">{e.all_constraints_satisfied ? "Y" : "N"}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
