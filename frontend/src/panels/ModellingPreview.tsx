/**
 * Bottom-panel visualisations for model training results.
 *
 * Renders in the same slot as DataPreview when a training job has
 * completed. Seven tabs: Summary, Loss, Lift, Residuals, Features, AvE, PDP.
 */

import { useState, useEffect } from "react"
import { ChevronDown, ChevronUp, BrainCircuit } from "lucide-react"
import { useDragResize } from "../hooks/useDragResize"
import type { TrainResult, TrainProgress } from "../stores/useNodeResultsStore"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import useSettingsStore from "../stores/useSettingsStore"
import { SummaryTab } from "./modelling/SummaryTab"
import { LossTab } from "./modelling/LossTab"
import { LiftTab } from "./modelling/LiftTab"
import { ResidualsTab } from "./modelling/ResidualsTab"
import { FeaturesTab } from "./modelling/FeaturesTab"
import { AveTab } from "./modelling/AveTab"
import { PdpTab } from "./modelling/PdpTab"
import { GLMCoefficientsTab } from "./modelling/GLMCoefficientsTab"
import { GLMRelativitiesTab } from "./modelling/GLMRelativitiesTab"

// ─── Types ───────────────────────────────────────────────────────

export type ModellingPreviewData = {
  result: TrainResult
  jobId: string
  nodeLabel: string
  configHash: string
}

interface ModellingPreviewProps {
  data: ModellingPreviewData
  nodeId: string
}

// ─── Tab definitions ─────────────────────────────────────────────

const TAB_KEYS = ["summary", "coefficients", "relativities", "loss", "lift", "residuals", "features", "ave", "pdp"] as const
type TabKey = (typeof TAB_KEYS)[number]

const TAB_LABELS: Record<TabKey, string> = {
  summary: "Summary",
  coefficients: "Coefficients",
  relativities: "Relativities",
  loss: "Loss",
  lift: "Lift",
  residuals: "Residuals",
  features: "Features",
  ave: "AvE",
  pdp: "PDP",
}

// ─── Component ───────────────────────────────────────────────────

export function ModellingPreview({
  data,
  nodeId,
}: ModellingPreviewProps) {
  const { result } = data
  const [collapsed, setCollapsed] = useState(false)
  const { height, containerRef, onDragStart } = useDragResize({ initialHeight: 360, minHeight: 180, maxHeight: 700 })
  const [tab, setTab] = useState<TabKey>("summary")
  // eslint-disable-next-line react-hooks/set-state-in-effect -- reset tab on new training result
  useEffect(() => setTab("summary"), [result])

  // Source training progress from store
  const trainProgress: TrainProgress | null = useNodeResultsStore((s) => s.trainJobs[nodeId]?.progress ?? null)

  // Source MLflow status from settings store
  const mlflow = useSettingsStore((s) => s.mlflow)
  const mlflowBackend = mlflow.status === "connected" ? { installed: true, backend: mlflow.backend, host: mlflow.host } : null

  // Determine which tabs are available based on data
  const availableTabs = TAB_KEYS.filter(t => {
    switch (t) {
      case "summary": return true
      case "coefficients": return result.glm_coefficients && result.glm_coefficients.length > 0
      case "relativities": return result.glm_relativities && result.glm_relativities.length > 0
      case "loss": return result.loss_history && result.loss_history.length > 1
      case "lift": return (result.double_lift && result.double_lift.length > 0) || (result.lorenz_curve && result.lorenz_curve.length > 0)
      case "residuals": return (result.residuals_histogram && result.residuals_histogram.length > 0) || (result.actual_vs_predicted && result.actual_vs_predicted.length > 0)
      case "features": return result.feature_importance.length > 0
      case "ave": return result.ave_per_feature && result.ave_per_feature.length > 0
      case "pdp": return result.pdp_data && result.pdp_data.length > 0
      default: return false
    }
  })

  // Status summary for collapsed bar
  const metricsSummary = Object.entries(result.metrics).slice(0, 2).map(([k, v]) => `${k}: ${typeof v === 'number' && Number.isFinite(v) ? v.toFixed(4) : String(v)}`).join(" | ")

  // ── Collapsed ──
  if (collapsed) {
    return (
      <div className="h-8 flex items-center px-4 shrink-0" style={{ borderTop: "1px solid var(--border)", background: "var(--bg-panel)" }}>
        <button onClick={() => setCollapsed(false)} className="flex items-center gap-2 text-xs" style={{ color: "var(--text-secondary)" }}>
          <ChevronUp size={14} />
          <BrainCircuit size={14} />
          <span className="font-medium">{data.nodeLabel}</span>
          <span style={{ color: "var(--text-muted)" }}>
            {result.status === "error" ? "Error" : metricsSummary}
          </span>
        </button>
      </div>
    )
  }

  // ── Expanded ──
  return (
    <div ref={containerRef} style={{ height, borderTop: "1px solid var(--border)", background: "var(--bg-panel)" }} className="flex flex-col shrink-0 relative">
      {/* Drag handle */}
      <div
        onMouseDown={onDragStart}
        className="drag-handle-hover absolute top-0 left-0 right-0 h-1 cursor-ns-resize z-10"
      />

      {/* Training progress bar */}
      {trainProgress && (
        <div className="h-1 w-full shrink-0" style={{ background: "rgba(168,85,247,.15)" }}>
          <div
            className="h-full transition-all duration-300"
            style={{ width: `${Math.max(trainProgress.progress * 100, 2)}%`, background: "#a855f7" }}
          />
        </div>
      )}

      {/* Header */}
      <div className="min-h-9 flex items-center flex-wrap px-4 shrink-0 gap-x-2 gap-y-1 py-1.5" style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-elevated)" }}>
        <BrainCircuit size={14} style={{ color: "#a855f7" }} />
        <span className="text-xs font-bold" style={{ color: "var(--text-primary)" }}>{data.nodeLabel}</span>
        {/* Tab bar */}
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

        {/* Collapse + Close */}
        <div className="ml-auto flex items-center gap-1">
          <button onClick={() => setCollapsed(true)} className="p-1 rounded transition-colors hover:bg-[var(--bg-hover)]" style={{ color: "var(--text-muted)" }}>
            <ChevronDown size={14} />
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto px-4 py-3">
        {tab === "summary" && (
          <SummaryTab result={result} jobId={data.jobId} mlflowBackend={mlflowBackend} config={{}} />
        )}
        {tab === "coefficients" && <GLMCoefficientsTab result={result} />}
        {tab === "relativities" && <GLMRelativitiesTab result={result} />}
        {tab === "loss" && <LossTab result={result} />}
        {tab === "lift" && <LiftTab result={result} />}
        {tab === "residuals" && <ResidualsTab result={result} />}
        {tab === "features" && <FeaturesTab result={result} />}
        {tab === "ave" && <AveTab result={result} />}
        {tab === "pdp" && <PdpTab result={result} />}
      </div>
    </div>
  )
}
