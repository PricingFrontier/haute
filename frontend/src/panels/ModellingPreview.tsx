/**
 * Bottom-panel visualisations for model training results.
 *
 * Renders in the same shared preview shell as other lower-panel nodes.
 */

import { useEffect, useState } from "react"

import type { TrainProgress, TrainResult } from "../stores/useNodeResultsStore"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import useSettingsStore from "../stores/useSettingsStore"
import { MODEL_COLORS } from "../theme/colors"
import { NODE_TYPES } from "../utils/nodeTypes"
import { AveTab } from "./modelling/AveTab"
import { FeaturesTab } from "./modelling/FeaturesTab"
import { GLMCoefficientsTab } from "./modelling/GLMCoefficientsTab"
import { GLMRelativitiesTab } from "./modelling/GLMRelativitiesTab"
import { LiftTab } from "./modelling/LiftTab"
import { LossTab } from "./modelling/LossTab"
import { PdpTab } from "./modelling/PdpTab"
import { ResidualsTab } from "./modelling/ResidualsTab"
import { SummaryTab } from "./modelling/SummaryTab"
import PreviewPanelFrame from "./PreviewPanelFrame"
import PreviewPanelTabs from "./PreviewPanelTabs"

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

export function ModellingPreview({ data, nodeId }: ModellingPreviewProps) {
  const { result } = data
  const [tab, setTab] = useState<TabKey>("summary")

  // eslint-disable-next-line react-hooks/set-state-in-effect -- reset tab on new training result
  useEffect(() => setTab("summary"), [result])

  const trainProgress: TrainProgress | null = useNodeResultsStore((s) => s.trainJobs[nodeId]?.progress ?? null)
  const mlflow = useSettingsStore((s) => s.mlflow)
  const mlflowBackend = mlflow.status === "connected" ? { installed: true, backend: mlflow.backend, host: mlflow.host } : null

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
  const activeTab = availableTabs.includes(tab) ? tab : "summary"
  const metricsSummary = Object.entries(result.metrics)
    .slice(0, 2)
    .map(([k, v]) => `${k}: ${typeof v === "number" && Number.isFinite(v) ? v.toFixed(4) : String(v)}`)
    .join(" | ")
  const tabs = availableTabs.map((key) => ({ key, label: TAB_LABELS[key] }))

  return (
    <PreviewPanelFrame
      nodeLabel={data.nodeLabel}
      nodeType={NODE_TYPES.MODELLING}
      collapsedMeta={result.status === "error" ? "Error" : metricsSummary}
      data-testid="modelling-preview-frame"
    >
      {trainProgress && (
        <div className="h-1 w-full shrink-0" style={{ background: MODEL_COLORS.accentSoft }}>
          <div
            className="h-full transition-all duration-300"
            style={{ width: `${Math.max(trainProgress.progress * 100, 2)}%`, background: MODEL_COLORS.accent }}
          />
        </div>
      )}

      <PreviewPanelTabs
        tabs={tabs}
        activeTab={activeTab}
        onChange={setTab}
        ariaLabel="Model result panes"
        accentColor={MODEL_COLORS.accent}
      />

      <div className="flex-1 overflow-auto px-4 py-3">
        {activeTab === "summary" && (
          <SummaryTab result={result} jobId={data.jobId} mlflowBackend={mlflowBackend} config={{}} />
        )}
        {activeTab === "coefficients" && <GLMCoefficientsTab result={result} />}
        {activeTab === "relativities" && <GLMRelativitiesTab result={result} />}
        {activeTab === "loss" && <LossTab result={result} />}
        {activeTab === "lift" && <LiftTab result={result} />}
        {activeTab === "residuals" && <ResidualsTab result={result} />}
        {activeTab === "features" && <FeaturesTab result={result} />}
        {activeTab === "ave" && <AveTab result={result} />}
        {activeTab === "pdp" && <PdpTab result={result} />}
      </div>
    </PreviewPanelFrame>
  )
}
