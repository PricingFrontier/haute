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
import { ChevronDown, ChevronUp, Loader2, Target, Save, Upload } from "lucide-react"
import {
  selectFrontierPoint as selectFrontierPointAPI,
  saveOptimiser,
  logOptimiserToMlflow,
} from "../api/client"
import { formatNumber } from "../utils/formatValue"
import { useDragResize } from "../hooks/useDragResize"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import useSettingsStore from "../stores/useSettingsStore"
import useToastStore from "../stores/useToastStore"
import type { FrontierData } from "../api/types"
import FrontierChart from "./optimiser/FrontierChart"
import ConvergenceChart from "./optimiser/ConvergenceChart"
import SummaryTab from "./optimiser/SummaryTab"
import DetailCard from "./optimiser/DetailCard"

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
  const addToast = useToastStore((s) => s.addToast)

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
        const detail = err instanceof Error ? err.message : "unknown error"
        addToast("error", `Failed to select frontier point: ${detail}`)
        // Revert to the previous selection so the UI doesn't show stale data
        storeSelectPoint(nodeId, selectedIdx)
      }
    },
    [selectedIdx, nodeId, jobId, storeSelectPoint, storeUpdateAfterSelect, addToast],
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
          <ConvergenceChart result={result} />
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
          <DetailCard
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
