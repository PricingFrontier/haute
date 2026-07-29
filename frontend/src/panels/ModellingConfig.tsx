import { useCallback, useState } from "react"
import { cancelTrain, estimateTrainingRam, trainModel } from "../api/client"
import { runDispersionEstimate } from "../api/dispersion"
import {
  FAILED_JOB_STATUSES,
  type DispersionParam,
  type TrainEstimate,
} from "../api/types"
import { CommittedTextField } from "../components/form"
import {
  useStaleConfigEstimate,
  type UseStaleConfigEstimateResult,
} from "../hooks/useStaleConfigEstimate"
import useGraphStore from "../stores/useGraphStore"
import useNodeResultsStore, { type TrainProgress, type TrainResult } from "../stores/useNodeResultsStore"
import useSettingsStore from "../stores/useSettingsStore"
import useToastStore from "../stores/useToastStore"
import type { ModellingPane } from "../stores/useUIStore"
import { configField } from "../utils/configField"
import {
  executionErrorDetailMessage,
  executionJobStatusFromReason,
  executionMetricsFromError,
  executionTerminalReasonFromError,
} from "../utils/executionDiagnostics"
import { buildGraph } from "../utils/buildGraph"
import {
  trainingConfigurationIssues,
  type TrainingConfigurationIssue,
} from "../utils/trainingObjective"
import type { OnUpdateConfig } from "./editors"
import { useGraph } from "./useGraph"
import { CommonFeatureConfig } from "./modelling/CommonFeatureConfig"
import { GLMFactorConfig } from "./modelling/GLMFactorConfig"
import { GLMRegularizationConfig } from "./modelling/GLMRegularizationConfig"
import { GLMTargetConfig } from "./modelling/GLMTargetConfig"
import {
  HyperparametersConfig,
} from "./modelling/HyperparametersConfig"
import {
  formatHyperparameters,
  formatTuningSearchSpace,
  parseHyperparameters,
  parseTuningSearchSpace,
} from "./modelling/hyperparameters"
import { SplitAndMetricsConfig } from "./modelling/SplitAndMetricsConfig"
import { TargetAndTaskConfig } from "./modelling/TargetAndTaskConfig"
import { TrainingActionsAndResults } from "./modelling/TrainingActionsAndResults"
import type { ReactElement } from "react"

type Props = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  upstreamColumns?: { name: string; dtype: string }[]
  activePane?: ModellingPane
}

const CATBOOST_DEFAULT_PARAMS: Record<string, unknown> = {
  iterations: 1000,
  learning_rate: 0.05,
  depth: 6,
  l2_leaf_reg: 3,
  early_stopping_rounds: 50,
}

const CATBOOST_RESERVED_PARAM_KEYS = ["task_type"] as const
const CATBOOST_RESERVED_PARAM_HELP =
  "GPU training is configured in the Train pane."
const DEFAULT_EVALUATION: Record<string, unknown> = {
  schema_version: 1,
  strategy: "random",
  seed: 42,
  validation: { method: "single", size: 0.2 },
}

const TRAIN_INPUT_STYLE = {
  background: "var(--bg-input)",
  border: "1px solid var(--border)",
  color: "var(--text-primary)",
} as const

function errorMessage(error: unknown) {
  return executionErrorDetailMessage(error) ?? String(error)
}

function failureStatus(error: unknown, message: string): TrainProgress | undefined {
  const execution_metrics = executionMetricsFromError(error)
  if (!execution_metrics) return undefined

  const terminal_reason = executionTerminalReasonFromError(error)
  return {
    status: executionJobStatusFromReason(terminal_reason),
    progress: 1,
    message,
    iteration: 0,
    total_iterations: 0,
    train_loss: {},
    elapsed_seconds: 0,
    terminal_reason,
    execution_metrics,
  }
}

function AlgorithmGateway({ onUpdate }: { onUpdate: OnUpdateConfig }) {
  const algorithms = [
    {
      id: "catboost",
      name: "CatBoost",
      description:
        "Gradient boosting — handles categoricals natively, fast GPU training",
    },
    {
      id: "glm",
      name: "GLM",
      description:
        "Generalised linear model — interpretable coefficients, regulatory-friendly",
    },
  ] as const

  return (
    <div className="px-4 py-3 space-y-3">
      <label className="text-[11px] font-bold uppercase tracking-[0.08em]">Select Algorithm</label>
      {algorithms.map((option) => (
        <button
          key={option.id}
          type="button"
          onClick={() => onUpdate({
            algorithm: option.id,
            evaluation: DEFAULT_EVALUATION,
          })}
          className="w-full rounded-lg px-3 py-3 text-left algorithm-gateway-btn"
        >
          <span className="block text-xs font-semibold">{option.name}</span>
          <span
            className="mt-0.5 block text-[11px]"
            style={{ color: "var(--text-muted)" }}
          >
            {option.description}
          </span>
        </button>
      ))}
    </div>
  )
}

type TrainPaneProps = {
  algorithm: "catboost" | "glm"
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  params: Record<string, unknown>
  validationMessages: readonly string[]
  trainJob: ReturnType<typeof useNodeResultsStore.getState>["trainJobs"][string] | undefined
  cachedResult: ReturnType<typeof useNodeResultsStore.getState>["trainResults"][string] | undefined
  estimate: UseStaleConfigEstimateResult<TrainEstimate>
  submitting: boolean
  cancelling: boolean
  onTrain: () => void
  onCancel: () => void
  tuningEnabled: boolean
}

function TrainPane({
  algorithm,
  config,
  onUpdate,
  params,
  validationMessages,
  trainJob,
  cachedResult,
  estimate,
  submitting,
  cancelling,
  onTrain,
  onCancel,
  tuningEnabled,
}: TrainPaneProps) {
  const rowLimit = typeof config.row_limit === "number" ? config.row_limit : null
  const [validationRevealed, setValidationRevealed] = useState(false)

  const toggleGpu = (enabled: boolean) => {
    const { task_type: _taskType, ...nonGpuParams } = params
    onUpdate("params", enabled ? { ...nonGpuParams, task_type: "GPU" } : nonGpuParams)
  }
  const requestTrain = () => {
    if (validationMessages.length > 0) {
      setValidationRevealed(true)
      return
    }
    onTrain()
  }

  return (
    <>
      {algorithm === "catboost" && (
        <label className="flex cursor-pointer select-none items-center gap-2">
          <input
            type="checkbox"
            checked={params.task_type === "GPU"}
            onChange={(event) => toggleGpu(event.target.checked)}
            className="accent-purple-500"
          />
          <span className="text-[11px]" style={{ color: "var(--text-primary)" }}>
            GPU training
          </span>
          <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
            (CUDA)
          </span>
        </label>
      )}
      <div className="flex items-center gap-2">
        <label
          htmlFor="model-row-limit"
          className="text-[11px]"
          style={{ color: "var(--text-muted)" }}
        >
          Row limit
        </label>
        <input
          id="model-row-limit"
          aria-label="Row limit"
          type="number"
          min={0}
          step={100000}
          value={rowLimit ?? ""}
          onChange={(event) => {
            onUpdate("row_limit", event.target.value === "" ? null : Math.max(0, Number(event.target.value)))
          }}
          placeholder="All rows"
          className="w-32 rounded px-2 py-1 text-xs font-mono"
          style={TRAIN_INPUT_STYLE}
        />
        {rowLimit !== null && rowLimit > 0 && (
          <span className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>
            {rowLimit.toLocaleString()} rows
          </span>
        )}
      </div>
      <section className="space-y-2" aria-labelledby="mlflow-logging-heading">
        <h3
          id="mlflow-logging-heading"
          className="text-[11px] font-bold uppercase tracking-[0.08em]"
          style={{ color: "var(--text-muted)" }}
        >
          MLflow Logging
        </h3>
        <label className="block text-[11px]" style={{ color: "var(--text-muted)" }}>
          Experiment path
          <CommittedTextField
            type="text"
            aria-label="MLflow experiment path"
            value={configField(config, "mlflow_experiment", "")}
            onCommit={(value) => onUpdate("mlflow_experiment", value)}
            placeholder="MLflow experiment"
            className="mt-0.5 w-full rounded-lg px-2.5 py-1.5 text-xs font-mono"
            style={TRAIN_INPUT_STYLE}
          />
        </label>
        <label className="block text-[11px]" style={{ color: "var(--text-muted)" }}>
          Model name
          <CommittedTextField
            type="text"
            aria-label="MLflow model name"
            value={configField(config, "model_name", "")}
            onCommit={(value) => onUpdate("model_name", value)}
            placeholder="MLflow model name"
            className="mt-0.5 w-full rounded-lg px-2.5 py-1.5 text-xs font-mono"
            style={TRAIN_INPUT_STYLE}
          />
        </label>
      </section>
      <TrainingActionsAndResults
        validationMessages={validationRevealed ? validationMessages : []}
        training={Boolean(trainJob)}
        trainProgress={trainJob?.progress ?? null}
        estimatedRemainingSeconds={trainJob?.estimatedRemainingSeconds ?? null}
        trainResult={cachedResult?.result ?? null}
        isStale={estimate.isStale}
        ramEstimate={estimate.estimate}
        ramEstimateLoading={estimate.loading}
        ramEstimateError={estimate.error}
        rowLimit={rowLimit}
        terminalMetrics={cachedResult?.terminalStatus?.execution_metrics ?? null}
        terminalStatus={cachedResult?.terminalStatus?.status ?? null}
        terminalReason={cachedResult?.terminalStatus?.terminal_reason ?? null}
        submitting={submitting}
        cancelling={cancelling}
        tuningEnabled={tuningEnabled}
        onTrain={requestTrain}
        onCancel={onCancel}
      />
    </>
  )
}

export default function ModellingConfig({
  config,
  onUpdate,
  upstreamColumns = [],
  activePane = "target",
}: Props) {
  const { allNodes, edges, submodels, preamble } = useGraph()
  const nodeId = String(config._nodeId ?? "")
  const trainJob = useNodeResultsStore((state) => state.trainJobs[nodeId])
  const cachedResult = useNodeResultsStore((state) => state.trainResults[nodeId])
  const startTrainJob = useNodeResultsStore((state) => state.startTrainJob)
  const updateTrainProgress = useNodeResultsStore((state) => state.updateTrainProgress)
  const completeTrainJob = useNodeResultsStore((state) => state.completeTrainJob)
  const failTrainJob = useNodeResultsStore((state) => state.failTrainJob)
  const addToast = useToastStore((state) => state.addToast)
  const activeSource = useSettingsStore((state) => state.activeSource)
  const structuralVersion = useGraphStore((state) => state.structuralVersion)
  const [submitting, setSubmitting] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  // Drafts live above the Params pane.  They deliberately survive a Params
  // unmount for this node, while a newly seen node starts from its own params.
  const [paramDrafts, setParamDrafts] = useState<Record<string, string>>({})
  const [searchSpaceDrafts, setSearchSpaceDrafts] = useState<Record<string, string>>({})

  const algorithm = String(config.algorithm ?? "").toLowerCase()
  const params = configField<Record<string, unknown>>(config, "params", {})
  const target = configField(config, "target", "")
  const weight = configField(config, "weight", "")
  const exclude = configField<string[]>(config, "exclude", [])
  const evaluation = configField<Record<string, unknown>>(
    config,
    "evaluation",
    DEFAULT_EVALUATION,
  )
  const tuning = (
    config.tuning !== null
    && typeof config.tuning === "object"
    && !Array.isArray(config.tuning)
  )
    ? config.tuning as Record<string, unknown>
    : null
  const task = configField(config, "task", "regression")
  const metrics = configField<string[]>(config, "metrics", task === "regression" ? ["gini", "rmse"] : ["auc", "logloss"])
  const paramsProjection = formatHyperparameters(
    params,
    CATBOOST_DEFAULT_PARAMS,
    CATBOOST_RESERVED_PARAM_KEYS,
  )
  const paramDraft = paramDrafts[nodeId] ?? paramsProjection
  const tuningSearchSpace = (
    tuning?.search_space !== null
    && typeof tuning?.search_space === "object"
    && !Array.isArray(tuning.search_space)
  )
    ? tuning.search_space
    : {}
  const searchSpaceDraft = searchSpaceDrafts[nodeId]
    ?? formatTuningSearchSpace(tuningSearchSpace as Record<string, unknown>)
  let paramDraftIssue: TrainingConfigurationIssue | null = null
  if (algorithm === "catboost" && !tuning) {
    try {
      parseHyperparameters(
        paramDraft,
        CATBOOST_RESERVED_PARAM_KEYS,
        CATBOOST_RESERVED_PARAM_HELP,
      )
    } catch (cause) {
      const detail = cause instanceof Error ? cause.message : "Invalid JSON"
      paramDraftIssue = {
        code: "catboost-params",
        message: `Parameters JSON is invalid: ${detail}`,
      }
    }
  }
  let searchSpaceDraftIssue: TrainingConfigurationIssue | null = null
  if (tuning) {
    try {
      parseTuningSearchSpace(searchSpaceDraft)
    } catch (cause) {
      const detail = cause instanceof Error ? cause.message : "Invalid JSON"
      searchSpaceDraftIssue = {
        code: "tuning-config",
        message: `Search space JSON is invalid: ${detail}`,
      }
    }
  }
  const configuredValidationIssues = trainingConfigurationIssues(config)
  const validationIssues = [
    ...configuredValidationIssues,
    ...(paramDraftIssue ? [paramDraftIssue] : []),
    ...(searchSpaceDraftIssue ? [searchSpaceDraftIssue] : []),
  ]
  const hasTrainingConfigurationIssues = validationIssues.length > 0
  const validationMessages = validationIssues.map((issue) => issue.message)

  const graph = useCallback(
    () => buildGraph(allNodes, edges, submodels, preamble),
    [allNodes, edges, submodels, preamble],
  )
  const estimateEndpoint = useCallback(
    (_payload: void, context: { signal: AbortSignal }) => (
      estimateTrainingRam({ graph: graph(), node_id: nodeId, source: activeSource }, context)
    ),
    [activeSource, graph, nodeId],
  )
  const estimate = useStaleConfigEstimate<TrainEstimate>(
    nodeId,
    config,
    cachedResult,
    estimateEndpoint,
    { source: activeSource, structuralVersion },
    { toastLabel: "RAM estimate failed" },
  )
  const onEvaluationChange = useCallback(
    (nextEvaluation: Record<string, unknown>) => (
      onUpdate("evaluation", nextEvaluation)
    ),
    [onUpdate],
  )
  const onEstimateDispersion = useCallback(
    (param: DispersionParam) => runDispersionEstimate({
      graph: graph(),
      node_id: nodeId,
      param,
      source: useSettingsStore.getState().activeSource,
    }),
    [graph, nodeId],
  )
  const onTrain = useCallback(async () => {
    if (hasTrainingConfigurationIssues) return
    setSubmitting(true)
    try {
      const result = await trainModel({
        graph: graph(),
        node_id: nodeId,
        source: useSettingsStore.getState().activeSource,
        streamingChunkSize: useSettingsStore.getState().streamingChunkSize,
      })
      if (result.status === "started" && result.job_id) {
        startTrainJob(
          nodeId,
          result.job_id,
          allNodes.find((node) => node.id === nodeId)?.data.label ?? "Model Training",
          estimate.configHash,
          activeSource,
          structuralVersion,
        )
      } else {
        completeTrainJob(nodeId, result as unknown as TrainResult)
      }
    } catch (error) {
      const message = errorMessage(error)
      completeTrainJob(
        nodeId,
        {
          status: "error",
          job_id: null,
          diagnostic_metrics: {},
          final_test_metrics: {},
          feature_importance: [],
          model_path: "",
          development_rows: 0,
          final_test_rows: 0,
          diagnostics_set: "development",
          features: [],
          cat_features: [],
          error: message,
          best_iteration: null,
          loss_history: [],
          loss_history_truncated: false,
          double_lift: [],
          shap_summary: [],
          feature_importance_loss: [],
          ave_per_feature: [],
          residuals_histogram: [],
          residuals_stats: {},
          actual_vs_predicted: [],
          lorenz_curve: [],
          lorenz_curve_perfect: [],
          pdp_data: [],
          warning: null,
          total_source_rows: null,
          glm_coefficients: [],
          glm_relativities: [],
          glm_fit_statistics: {},
          glm_regularization_path: null,
          diagnostics_errors: [],
          feature_selection: null,
        },
        failureStatus(error, message),
      )
    } finally {
      setSubmitting(false)
    }
  }, [activeSource, allNodes, completeTrainJob, estimate.configHash, graph, hasTrainingConfigurationIssues, nodeId, startTrainJob, structuralVersion])
  const onCancel = useCallback(async () => {
    const job = useNodeResultsStore.getState().trainJobs[nodeId]
    if (!job || cancelling) return

    setCancelling(true)
    try {
      const status = await cancelTrain<TrainProgress>(job.jobId)
      if (status.status === "completed" && status.result) completeTrainJob(nodeId, status.result, status)
      else if (FAILED_JOB_STATUSES.has(status.status)) failTrainJob(nodeId, status.message || "Training stopped", status)
      else updateTrainProgress(nodeId, status)
    } catch (error) {
      addToast("error", `Could not cancel training: ${errorMessage(error)}`)
    } finally {
      setCancelling(false)
    }
  }, [addToast, cancelling, completeTrainJob, failTrainJob, nodeId, updateTrainProgress])

  if (!algorithm) return <AlgorithmGateway onUpdate={onUpdate} />
  if (algorithm !== "catboost" && algorithm !== "glm") {
    return <div className="px-4 py-3" role="alert">Unsupported modelling algorithm: {algorithm}.</div>
  }

  const splitPane = (
    <SplitAndMetricsConfig
      columns={upstreamColumns}
      evaluation={evaluation}
      onEvaluationChange={onEvaluationChange}
      preview={estimate.estimate?.evaluation_preview ?? null}
    />
  )
  const trainPane = (
    <TrainPane
      key={`${nodeId}:${hasTrainingConfigurationIssues ? "incomplete" : "complete"}`}
      algorithm={algorithm}
      config={config}
      onUpdate={onUpdate}
      params={params}
      validationMessages={validationMessages}
      trainJob={trainJob}
      cachedResult={cachedResult}
      estimate={estimate}
      submitting={submitting}
      cancelling={cancelling}
      onTrain={onTrain}
      onCancel={onCancel}
      tuningEnabled={tuning !== null}
    />
  )

  let paneBody: ReactElement | null = null
  if (algorithm === "catboost") {
    if (activePane === "target") {
      paneBody = <TargetAndTaskConfig config={config} onUpdate={onUpdate} columns={upstreamColumns} target={target} weight={weight} task={task} metrics={metrics} />
    } else if (activePane === "features") {
      paneBody = <CommonFeatureConfig config={config} onUpdate={onUpdate} columns={upstreamColumns} algorithm="catboost" />
    } else if (activePane === "params") {
      paneBody = (
        <HyperparametersConfig
          algorithmLabel="CatBoost"
          params={params}
          defaultParams={CATBOOST_DEFAULT_PARAMS}
          reservedKeys={CATBOOST_RESERVED_PARAM_KEYS}
          reservedKeysHelp={CATBOOST_RESERVED_PARAM_HELP}
          onUpdate={onUpdate}
          draft={paramDraft}
          setDraft={(value) => setParamDrafts((current) => ({ ...current, [nodeId]: value }))}
          tuning={tuning}
          evaluation={evaluation}
          metrics={metrics}
          searchSpaceDraft={searchSpaceDraft}
          setSearchSpaceDraft={(value) => setSearchSpaceDrafts(
            (current) => ({ ...current, [nodeId]: value }),
          )}
        />
      )
    } else if (activePane === "split") {
      paneBody = splitPane
    } else if (activePane === "train") {
      paneBody = trainPane
    }
  } else if (activePane === "target") {
    paneBody = <GLMTargetConfig config={config} onUpdate={onUpdate} columns={upstreamColumns} onEstimateDispersion={onEstimateDispersion} />
  } else if (activePane === "features") {
    paneBody = <><CommonFeatureConfig config={config} onUpdate={onUpdate} columns={upstreamColumns} algorithm="glm" /><GLMFactorConfig config={config} onUpdate={onUpdate} columns={upstreamColumns} target={target} weight={weight} exclude={exclude} /></>
  } else if (activePane === "params") {
    paneBody = <GLMRegularizationConfig config={config} onUpdate={onUpdate} />
  } else if (activePane === "split") {
    paneBody = splitPane
  } else if (activePane === "train") {
    paneBody = trainPane
  }

  return (
    <div id={`modelling-${activePane}-pane`} role="tabpanel" aria-labelledby={`modelling-${activePane}-tab`} className="px-4 py-3 space-y-4">
      {paneBody}
    </div>
  )
}
