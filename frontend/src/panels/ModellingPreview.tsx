/**
 * Bottom-panel visualisations for model training results.
 *
 * Renders in the same shared preview shell as other lower-panel nodes.
 */

import { useEffect, useState } from "react"
import { Maximize2, Minimize2 } from "lucide-react"
import ModalShell from "../components/ModalShell"

import type { TrainProgress, TrainResult } from "../stores/useNodeResultsStore"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import useGraphStore from "../stores/useGraphStore"
import useUIStore from "../stores/useUIStore"
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
import "./modelling/validation.css"

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

const TAB_KEYS = [
  "summary",
  "coefficients",
  "relativities",
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

export function ModellingPreview({ data, nodeId }: ModellingPreviewProps) {
  const { result } = data
  const [tab, setTab] = useState<TabKey>("summary")
  const [focused, setFocused] = useState(false)
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
    <ModalShell
      active={focused}
      ariaLabel="Model validation"
      onClose={() => setFocused(false)}
      width="w-[calc(100vw-24px)] h-[calc(100dvh-24px)]"
    >
      <PreviewPanelFrame
        nodeLabel={data.nodeLabel}
        nodeType={NODE_TYPES.MODELLING}
        collapsedMeta={result.status === "error" ? "Error" : metricsSummary}
        data-testid="modelling-preview-frame"
        initialHeight={initialHeight}
        onHeightChange={rememberHeight}
        focused={focused}
        actions={
          <button
            type="button"
            onClick={() => setFocused(!focused)}
            className="inline-flex items-center gap-1.5 rounded px-2 py-1 text-xs hover:bg-[var(--bg-hover)] focus-ring"
            style={{ color: MODEL_COLORS.accent }}
          >
            {focused ? (
              <Minimize2 size={14} aria-hidden="true" />
            ) : (
              <Maximize2 size={14} aria-hidden="true" />
            )}
            {focused ? "Exit focus view" : "Focus view"}
          </button>
        }
      >
        {trainProgress && (
          <div className="h-1 w-full shrink-0" style={{ background: MODEL_COLORS.accentSoft }}>
            <div
              className="h-full transition-all duration-300"
              style={{
                width: `${Math.max(trainProgress.progress * 100, 2)}%`,
                background: MODEL_COLORS.accent,
              }}
            />
          </div>
        )}

        <div className="shrink-0 overflow-x-auto">
          <PreviewPanelTabs
            tabs={tabs}
            activeTab={activeTab}
            onChange={setTab}
            ariaLabel="Model result panes"
            accentColor={MODEL_COLORS.accent}
            idPrefix="modelling-preview"
            appearance="results"
          />
        </div>

        <div
          className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-2 text-xs"
          style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}
        >
          <span>
            Diagnostics: {result.diagnostics_set === "final_test" ? "Final test" : "Development"} ·{" "}
            {(result.diagnostics_set === "final_test"
              ? result.final_test_rows
              : result.development_rows
            ).toLocaleString()}{" "}
            rows
          </span>
          {result.diagnostics_set === "development" && (
            <span style={{ color: "var(--warning)" }}>
              Development diagnostics are not held-out performance.
            </span>
          )}
        </div>

        <div
          key={activeTab}
          id={`modelling-preview-${activeTab}-pane`}
          role="tabpanel"
          aria-labelledby={`modelling-preview-${activeTab}-tab`}
          tabIndex={0}
          className="validation-workspace flex-1 min-h-0 overflow-auto p-4 focus-ring"
        >
          {introduction && (
            <div className="mb-4">
              <h3 className="text-base font-semibold" style={{ color: "var(--text-primary)" }}>
                {introduction.title}
              </h3>
              <p className="mt-1 text-xs leading-relaxed" style={{ color: "var(--text-muted)" }}>
                {introduction.description}
              </p>
            </div>
          )}
          {activeTab === "summary" && (
            <SummaryTab
              result={result}
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
          {activeTab === "ave" && <AveTab result={result} featureBrowser={featureBrowser} />}
          {activeTab === "pdp" && <PdpTab result={result} featureBrowser={featureBrowser} />}
        </div>
      </PreviewPanelFrame>
    </ModalShell>
  )
}
