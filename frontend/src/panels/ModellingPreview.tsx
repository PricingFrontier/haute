/**
 * Bottom-panel visualisations for model training results.
 *
 * Renders in the same shared preview shell as other lower-panel nodes.
 */

import { useEffect, useState } from "react"

import type { TrainProgress, TrainResult } from "../stores/useNodeResultsStore"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import useGraphStore from "../stores/useGraphStore"
import useUIStore from "../stores/useUIStore"
import { nodeData } from "../types/node"
import { NODE_TYPES } from "../utils/nodeTypes"
import { AveTab } from "./modelling/AveTab"
import { FeaturesTab } from "./modelling/FeaturesTab"
import { GLMCoefficientsTab } from "./modelling/GLMCoefficientsTab"
import { GLMRelativitiesTab } from "./modelling/GLMRelativitiesTab"
import { EBMTermsTab } from "./modelling/EBMTermsTab"
import { LiftTab } from "./modelling/LiftTab"
import { LossTab } from "./modelling/LossTab"
import { PdpTab } from "./modelling/PdpTab"
import { ResidualsTab } from "./modelling/ResidualsTab"
import { SummaryTab } from "./modelling/SummaryTab"
import { diagnosticsRowCount, diagnosticsSetLabel } from "./modelling/diagnosticsSet"
import ResultsWorkspace from "./ResultsWorkspace"

export type ModellingPreviewData = {
  result: TrainResult
  jobId: string
  nodeLabel: string
  configHash: string
}

interface ModellingPreviewProps {
  data: ModellingPreviewData
  nodeId: string
  onRefresh?: () => void
}

const TAB_KEYS = [
  "summary",
  "coefficients",
  "relativities",
  "terms",
  "loss",
  "lift",
  "residuals",
  "features",
  "ave",
  "pdp",
] as const
type TabKey = (typeof TAB_KEYS)[number]

const TAB_LABELS: Record<TabKey, string> = {
  summary: "Summary",
  coefficients: "Coefficients",
  relativities: "Relativities",
  terms: "Terms",
  loss: "Loss",
  lift: "Lift",
  residuals: "Residuals",
  features: "Features",
  ave: "AvE",
  pdp: "PDP",
}

const VIEW_INTRODUCTIONS: Record<
  Exclude<TabKey, "summary">,
  { title: string; description: string }
> = {
  coefficients: {
    title: "GLM coefficients",
    description: "Inspect each term's estimate, uncertainty and statistical significance.",
  },
  relativities: {
    title: "GLM relativities",
    description: "Compare each term's effect relative to the baseline of 1.",
  },
  terms: {
    title: "EBM terms",
    description:
      "Read each main effect's shape and each pairwise interaction's surface: the model is their sum.",
  },
  loss: {
    title: "Training loss",
    description:
      "Follow model fit across iterations and compare training and validation loss where available.",
  },
  lift: {
    title: "Lift and discrimination",
    description: "Explore how well predictions separate lower and higher outcomes.",
  },
  residuals: {
    title: "Residual diagnostics",
    description: "Inspect prediction errors and how closely predictions match actual outcomes.",
  },
  features: {
    title: "Feature importance",
    description: "Compare the contribution of each feature to the model's predictions.",
  },
  ave: {
    title: "Actual vs expected",
    description:
      "Compare observed and predicted outcomes across each feature's groups, alongside exposure.",
  },
  pdp: {
    title: "Partial dependence",
    description: "Explore how model predictions change as one feature varies.",
  },
}

export function ModellingPreview({ data, nodeId, onRefresh }: ModellingPreviewProps) {
  const { result } = data
  const [tab, setTab] = useState<TabKey>("summary")
  const [selectedFeature, setSelectedFeature] = useState<string | null>(null)
  const [featureSearch, setFeatureSearch] = useState("")
  const initialHeight = useUIStore((s) => s.modellingPreviewHeight)
  const rememberHeight = useUIStore((s) => s.setModellingPreviewHeight)

  useEffect(() => {
    /* eslint-disable react-hooks/set-state-in-effect -- a new result starts a new diagnostic review */
    setTab("summary")
    setSelectedFeature(null)
    setFeatureSearch("")
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [nodeId, result])

  const importance = new Map(
    result.feature_importance.map((item) => [item.feature, item.importance]),
  )
  const featureNames = [
    ...new Set([
      ...result.ave_per_feature.map((item) => item.feature),
      ...result.pdp_data.map((item) => item.feature),
    ]),
  ]
  const featureBrowser = {
    features: featureNames.map((feature) => ({
      feature,
      importance: importance.get(feature) ?? 0,
    })),
    selected: selectedFeature ?? featureNames[0] ?? null,
    onSelect: setSelectedFeature,
    search: featureSearch,
    onSearch: setFeatureSearch,
  }

  const trainProgress: TrainProgress | null = useNodeResultsStore(
    (s) => s.trainJobs[nodeId]?.progress ?? null,
  )

  const availableTabs = TAB_KEYS.filter((t) => {
    switch (t) {
      case "summary":
        return true
      case "coefficients":
        return result.glm_coefficients && result.glm_coefficients.length > 0
      case "relativities":
        return result.glm_relativities && result.glm_relativities.length > 0
      case "terms":
        return (result.ebm_terms ?? []).length > 0
      case "loss":
        return result.loss_history && result.loss_history.length > 1
      case "lift":
        return (
          (result.double_lift && result.double_lift.length > 0) ||
          (result.lorenz_curve && result.lorenz_curve.length > 0)
        )
      case "residuals":
        return (
          (result.residuals_histogram && result.residuals_histogram.length > 0) ||
          (result.actual_vs_predicted && result.actual_vs_predicted.length > 0)
        )
      case "features":
        return result.feature_importance.length > 0
      case "ave":
        return result.ave_per_feature && result.ave_per_feature.length > 0
      case "pdp":
        return result.pdp_data && result.pdp_data.length > 0
      default:
        return false
    }
  })
  const activeTab = availableTabs.includes(tab) ? tab : "summary"
  const collapsedMetrics =
    Object.keys(result.final_test_metrics).length > 0
      ? result.final_test_metrics
      : result.diagnostic_metrics
  const metricsSummary = Object.entries(collapsedMetrics)
    .slice(0, 2)
    .map(
      ([k, v]) => `${k}: ${typeof v === "number" && Number.isFinite(v) ? v.toFixed(4) : String(v)}`,
    )
    .join(" | ")
  const tabs = availableTabs.map((key) => ({ key, label: TAB_LABELS[key] }))
  const introduction = activeTab === "summary" ? null : VIEW_INTRODUCTIONS[activeTab]

  const useBestAsFixedParameters = (params: Record<string, unknown>) => {
    if (!window.confirm("Use the winning parameters as fixed parameters and disable tuning?")) {
      return
    }

    useGraphStore.getState().setNodes((nodes) =>
      nodes.map((node) => {
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
      }),
    )
  }

  return (
    <ResultsWorkspace
      ariaLabel="Model validation"
      idPrefix="modelling-preview"
      tabsAriaLabel="Model result panes"
      tabs={tabs}
      activeTab={activeTab}
      onTabChange={setTab}
      nodeLabel={data.nodeLabel}
      nodeType={NODE_TYPES.MODELLING}
      onRefresh={onRefresh}
      collapsedMeta={result.status === "error" ? "Error" : metricsSummary}
      data-testid="modelling-preview-frame"
      height={initialHeight}
      onHeightChange={rememberHeight}
      progress={trainProgress ? trainProgress.progress : null}
      provenance={
        <>
          <span>
            Diagnostics: {diagnosticsSetLabel(result.diagnostics_set)} ·{" "}
            {diagnosticsRowCount(result).toLocaleString()} rows
          </span>
          {result.diagnostics_set === "development" && (
            <span style={{ color: "var(--warning)" }}>
              Training diagnostics are in-sample performance.
            </span>
          )}
        </>
      }
      intro={introduction}
    >
      {activeTab === "summary" && (
        <SummaryTab
          result={result}
          onUseBestParameters={useBestAsFixedParameters}
          elapsedSeconds={trainProgress?.elapsed_seconds}
        />
      )}
      {activeTab === "coefficients" && <GLMCoefficientsTab result={result} />}
      {activeTab === "relativities" && <GLMRelativitiesTab result={result} />}
      {activeTab === "terms" && <EBMTermsTab result={result} />}
      {activeTab === "loss" && <LossTab result={result} />}
      {activeTab === "lift" && <LiftTab result={result} />}
      {activeTab === "residuals" && <ResidualsTab result={result} />}
      {activeTab === "features" && <FeaturesTab result={result} />}
      {activeTab === "ave" && <AveTab result={result} featureBrowser={featureBrowser} />}
      {activeTab === "pdp" && <PdpTab result={result} featureBrowser={featureBrowser} />}
    </ResultsWorkspace>
  )
}
