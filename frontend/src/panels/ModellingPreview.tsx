/**
 * Bottom-panel visualisations for model training results.
 *
 * Renders in the same shared preview shell as other lower-panel nodes.
 */

import { useEffect, useState } from "react"

import type { TrainProgress, TrainResult } from "../stores/useNodeResultsStore"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import useGraphStore from "../stores/useGraphStore"
import { MODEL_COLORS } from "../theme/colors"
import { nodeData } from "../types/node"
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

const VIEW_INTRODUCTIONS: Record<Exclude<TabKey, "summary">, { title: string; description: string }> = {
  coefficients: { title: "GLM coefficients", description: "Inspect each term's estimate, uncertainty and statistical significance." },
  relativities: { title: "GLM relativities", description: "Compare each term's effect relative to the baseline of 1." },
  loss: { title: "Training loss", description: "Follow model fit across iterations and compare training and validation loss where available." },
  lift: { title: "Lift and discrimination", description: "Explore how well predictions separate lower and higher outcomes." },
  residuals: { title: "Residual diagnostics", description: "Inspect prediction errors and how closely predictions match actual outcomes." },
  features: { title: "Feature importance", description: "Compare the contribution of each feature to the model's predictions." },
  ave: { title: "Actual vs expected", description: "Compare observed and predicted outcomes across each feature's groups, alongside exposure." },
  pdp: { title: "Partial dependence", description: "Explore how model predictions change as one feature varies." },
}

export function ModellingPreview({ data, nodeId }: ModellingPreviewProps) {
  const { result } = data
  const [tab, setTab] = useState<TabKey>("summary")

  // eslint-disable-next-line react-hooks/set-state-in-effect -- reset tab on new training result
  useEffect(() => setTab("summary"), [nodeId, result])

  const trainProgress: TrainProgress | null = useNodeResultsStore((s) => s.trainJobs[nodeId]?.progress ?? null)
  const modellingNode = useGraphStore((s) => s.nodes.find(node => node.id === nodeId))

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
  const collapsedMetrics = Object.keys(result.final_test_metrics).length > 0
    ? result.final_test_metrics
    : result.diagnostic_metrics
  const metricsSummary = Object.entries(collapsedMetrics)
    .slice(0, 2)
    .map(([k, v]) => `${k}: ${typeof v === "number" && Number.isFinite(v) ? v.toFixed(4) : String(v)}`)
    .join(" | ")
  const tabs = availableTabs.map((key) => ({ key, label: TAB_LABELS[key] }))
  const config = modellingNode ? nodeData(modellingNode).config ?? {} : {}
  const introduction = activeTab === "summary" ? null : VIEW_INTRODUCTIONS[activeTab]

  const useBestAsFixedParameters = (params: Record<string, unknown>) => {
    if (!window.confirm(
      "Use the winning parameters as fixed parameters and disable tuning?",
    )) {
      return
    }

    useGraphStore.getState().setNodes(nodes => nodes.map(node => {
      if (node.id !== nodeId) return node
      const data = nodeData(node)
      const { tuning: _tuning, ...configWithoutTuning } = data.config ?? {}
      void _tuning
      return {
        ...node,
        data: {
          ...data,
          config: {
            ...configWithoutTuning,
            params: { ...params },
          },
        },
      }
    }))
  }

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

      <div className="shrink-0 overflow-x-auto">
        <div style={{ minWidth: availableTabs.length * 112 }}>
          <PreviewPanelTabs
            tabs={tabs}
            activeTab={activeTab}
            onChange={setTab}
            ariaLabel="Model result panes"
            accentColor={MODEL_COLORS.accent}
            idPrefix="modelling-preview"
            equalWidth
          />
        </div>
      </div>

      <div
        key={activeTab}
        id={`modelling-preview-${activeTab}-pane`}
        role="tabpanel"
        aria-labelledby={`modelling-preview-${activeTab}-tab`}
        tabIndex={0}
        className="flex-1 min-h-0 overflow-auto p-3 focus-ring"
      >
        {introduction && (
          <div className="mb-3">
            <h3 className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
              {introduction.title}
            </h3>
            <p className="mt-1 text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
              {introduction.description}
            </p>
          </div>
        )}
        {activeTab === "summary" && (
          <SummaryTab
            result={result}
            jobId={data.jobId}
            config={config}
            onUseBestParameters={useBestAsFixedParameters}
            elapsedSeconds={trainProgress?.elapsed_seconds}
          />
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
