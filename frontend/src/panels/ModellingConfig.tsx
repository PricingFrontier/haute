import { useCallback, useRef, useState } from "react"
import { Info } from "lucide-react"
import { cancelTrain, estimateTrainingRam, getExperiments, trainModel } from "../api/client"
import { runDispersionEstimate } from "../api/dispersion"
import {
  FAILED_JOB_STATUSES,
  type DispersionParam,
  type TrainEstimate,
} from "../api/types"
import { CommittedTextField } from "../components/form"
import MlflowDestinationSelector from "../components/MlflowDestinationSelector"
import Tooltip from "../components/Tooltip"
import {
  useStaleConfigEstimate,
  type UseStaleConfigEstimateResult,
} from "../hooks/useStaleConfigEstimate"
import useGraphStore from "../stores/useGraphStore"
import useNodeResultsStore, { type TrainProgress, type TrainResult } from "../stores/useNodeResultsStore"
import {
  captureDocumentExecutionFence,
  isDocumentExecutionFenceCurrent,
} from "../stores/useDocumentStatusStore"
import useSettingsStore, { useMlflowDestinations } from "../stores/useSettingsStore"
import useToastStore from "../stores/useToastStore"
import { type ModellingPane } from "../stores/useUIStore"
import { configField } from "../utils/configField"
import {
  defaultExperimentName,
  effectiveMlflowDestination,
  mlflowDestinationEntry,
  mlflowLogAvailability,
} from "../utils/mlflowDestinations"
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
  nodeLabel: string
}

/**
 * The manual-only note. It used to sit in the pane as always-visible prose;
 * it is help, not state, so it now lives behind the section's Info icon and
 * nowhere else.
 */
const MLFLOW_MANUAL_HELP =
  "Used only when you press “Log run to MLflow” after training completes. " +
  "Nothing is logged automatically."

const MLFLOW_MODEL_NAME_HELP =
  "Optional: also register the logged model under this name."

/** The offset-field help pattern: a hover-only Info icon beside a label. */
function MlflowHelpIcon({ label, ariaLabel }: { label: string; ariaLabel: string }) {
  return (
    <Tooltip label={label}>
      <span className="inline-flex cursor-help" aria-label={ariaLabel}>
        <Info size={11} style={{ color: "var(--text-muted)" }} />
      </span>
    </Tooltip>
  )
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
  nodeLabel,
}: TrainPaneProps) {
  const rowLimit = typeof config.row_limit === "number" ? config.row_limit : null
  const [validationRevealed, setValidationRevealed] = useState(false)

  // Where this node logs is its own config, so every derived value below —
  // the default experiment path, whether logging is possible, and which
  // destination the datalist browses — follows `mlflow_destination`.
  const mlflowDestination = configField(config, "mlflow_destination", "")
  const mlflowInventory = useMlflowDestinations()
  const mlflowAvailability = mlflowLogAvailability(mlflowInventory, mlflowDestination)
  const effectiveDestination = effectiveMlflowDestination(mlflowDestination, mlflowInventory.auto)
  const experimentDefault = defaultExperimentName(nodeLabel, effectiveDestination)

  // Experiment suggestions belong to one backend. The scope key names that
  // backend (the resolved destination, not just the key, so a repointed
  // server counts as a change), and every change discards what was fetched
  // for the previous one — including an in-flight response.
  // TODO(C7): once `useMlflowBrowser` takes a destination, this becomes
  // `useMlflowBrowser({ destination: mlflowDestination })`.
  const mlflowScope = `${effectiveDestination}|${
    mlflowDestinationEntry(mlflowInventory.destinations, effectiveDestination)?.destination ?? ""
  }`
  const [loadedExperiments, setLoadedExperiments] = useState<{
    scope: string
    items: { experiment_id: string; name: string }[]
  }>({ scope: "", items: [] })
  // Suggestions are stamped with the scope they came from, so a scope change
  // hides them by derivation — no effect, and a response that lands after the
  // change can never be shown against the new backend.
  const experiments = loadedExperiments.scope === mlflowScope ? loadedExperiments.items : []
  const experimentsRequest = useRef({ scope: "", generation: 0 })
  const loadExperimentOptions = () => {
    if (!mlflowAvailability.available) return
    const scope = mlflowScope
    const request = experimentsRequest.current
    if (request.scope === scope) return
    request.scope = scope
    const generation = ++request.generation
    getExperiments(mlflowDestination)
      .then((data) => {
        if (generation !== request.generation) return
        setLoadedExperiments({ scope, items: Array.isArray(data) ? data : [] })
      })
      .catch(() => {
        if (generation !== request.generation) return
        // Let the next focus retry this scope.
        request.scope = ""
        setLoadedExperiments({ scope, items: [] })
      })
  }

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
        <div className="flex items-center gap-1">
          <h3
            id="mlflow-logging-heading"
            className="text-[11px] font-bold uppercase tracking-[0.08em]"
            style={{ color: "var(--text-muted)" }}
          >
            MLflow logging
          </h3>
          <MlflowHelpIcon label={MLFLOW_MANUAL_HELP} ariaLabel="About MLflow logging" />
        </div>
        <MlflowDestinationSelector
          value={mlflowDestination}
          onChange={(value) => onUpdate("mlflow_destination", value)}
        />
        <label className="block text-[11px]" style={{ color: "var(--text-muted)" }}>
          <span className="inline-flex items-center gap-1">
            Experiment path
            <MlflowHelpIcon
              label={`Leave blank to use the default: ${experimentDefault}`}
              ariaLabel="About the experiment path"
            />
          </span>
          <CommittedTextField
            type="text"
            aria-label="MLflow experiment path"
            value={configField(config, "mlflow_experiment", "")}
            onCommit={(value) => onUpdate("mlflow_experiment", value)}
            placeholder={experimentDefault}
            list="mlflow-experiment-options"
            onFocus={loadExperimentOptions}
            className="mt-0.5 w-full rounded-lg px-2.5 py-1.5 text-xs font-mono"
            style={TRAIN_INPUT_STYLE}
          />
          <datalist id="mlflow-experiment-options">
            {experiments.map((experiment) => (
              <option key={experiment.experiment_id} value={experiment.name} />
            ))}
          </datalist>
        </label>
        <label className="block text-[11px]" style={{ color: "var(--text-muted)" }}>
          <span className="inline-flex items-center gap-1">
            Model name
            <MlflowHelpIcon label={MLFLOW_MODEL_NAME_HELP} ariaLabel="About the model name" />
          </span>
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
    const documentFence = captureDocumentExecutionFence()
    if (!isDocumentExecutionFenceCurrent(documentFence)) return
    setSubmitting(true)
    try {
      const result = await trainModel({
        graph: graph(),
        node_id: nodeId,
        source: useSettingsStore.getState().activeSource,
        streamingChunkSize: useSettingsStore.getState().streamingChunkSize,
      })
      if (!isDocumentExecutionFenceCurrent(documentFence)) return
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
      if (!isDocumentExecutionFenceCurrent(documentFence)) return
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
      nodeLabel={allNodes.find((node) => node.id === nodeId)?.data.label ?? "model"}
    />
  )

  let paneBody: ReactElement | null = null
  if (algorithm === "catboost") {
    if (activePane === "target") {
      paneBody = <TargetAndTaskConfig config={config} onUpdate={onUpdate} columns={upstreamColumns} target={target} weight={weight} metrics={metrics} />
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
