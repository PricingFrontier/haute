import { useCallback, useEffect, useMemo, useState } from "react"
import { cancelTrain, estimateTrainingRam, trainModel } from "../api/client"
import { runDispersionEstimate } from "../api/dispersion"
import {
  FAILED_JOB_STATUSES,
  type DispersionParam,
  type TrainEstimate,
} from "../api/types"
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
import useSettingsStore from "../stores/useSettingsStore"
import useToastStore from "../stores/useToastStore"
import useUIStore, { type ModellingPane } from "../stores/useUIStore"
import { configField } from "../utils/configField"
import {
  executionErrorDetailMessage,
  executionJobStatusFromReason,
  executionMetricsFromError,
  executionTerminalReasonFromError,
} from "../utils/executionDiagnostics"
import { buildGraph } from "../utils/buildGraph"
import {
  effectiveMetrics,
  trainingConfigurationIssues,
  trainingIssuePane,
  type TrainingConfigurationIssue,
} from "../utils/trainingObjective"
import type { OnUpdateConfig } from "./editors"
import { useGraph } from "./useGraph"
import {
  ALGORITHM_CAPABILITIES,
  algorithmCapability,
  isKnownAlgorithm,
  usesSharedPanes,
} from "./modelling/algorithmCapabilities"
import { CommonFeatureConfig } from "./modelling/CommonFeatureConfig"
import { EBMInteractionsConfig } from "./modelling/EBMInteractionsConfig"
import { ExportPane } from "./modelling/ExportPane"
import { XGBoostGpuToggle } from "./modelling/GpuTrainingToggle"
import { GLMInteractionsConfig } from "./modelling/GLMInteractionsConfig"
import { GLMRegularizationConfig } from "./modelling/GLMRegularizationConfig"
import { GLMTargetConfig } from "./modelling/GLMTargetConfig"
import { GLMTermsConfig } from "./modelling/GLMTermsConfig"
import { resolveModellingPane } from "./modelling/modellingPanes"
import {
  HyperparametersConfig,
} from "./modelling/HyperparametersConfig"
import {
  formatHyperparameters,
  formatTuningSearchSpace,
  parseHyperparameters,
  parseTuningSearchSpace,
} from "./modelling/hyperparameters"
import { trainingIdentityConfig } from "../utils/modellingExportConfig"
import { trainingLineage } from "../utils/trainedJobHandles"
import { useTrainedJobRestore } from "./modelling/useTrainedJobRestore"
import { SplitAndMetricsConfig } from "./modelling/SplitAndMetricsConfig"
import { TrainingRunSummary } from "./modelling/TrainingRunSummary"
import { estimateAfterSupersededPreviews } from "./modelling/trainingEstimate"
import { TargetAndTaskConfig } from "./modelling/TargetAndTaskConfig"
import { TrainingActionsAndResults } from "./modelling/TrainingActionsAndResults"
import type { ReactElement } from "react"

type Props = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  upstreamColumns?: { name: string; dtype: string }[]
  activePane?: ModellingPane
  /** Reports the panes whose settings block training, for the host's tab badges. */
  onPaneIssuesChange?: (nodeId: string, panes: readonly ModellingPane[]) => void
}

const CATBOOST_DEFAULT_PARAMS: Record<string, unknown> = {
  iterations: 1000,
  learning_rate: 0.05,
  depth: 6,
  l2_leaf_reg: 3,
  early_stopping_rounds: 50,
}

const XGBOOST_DEFAULT_PARAMS: Record<string, unknown> = {
  num_boost_round: 1000,
  eta: 0.1,
  max_depth: 6,
  early_stopping_rounds: 50,
}

const LIGHTGBM_DEFAULT_PARAMS: Record<string, unknown> = {
  num_iterations: 1000,
  learning_rate: 0.05,
  num_leaves: 31,
  early_stopping_round: 50,
}

// EBM has no early stopping: max_rounds is the whole budget of every fit.
const EBM_DEFAULT_PARAMS: Record<string, unknown> = {
  max_rounds: 2000,
  learning_rate: 0.02,
  interactions: 10,
}

const STARTER_SEARCH_SPACES: Record<string, Record<string, unknown>> = {
  xgboost: {
    max_depth: [4, 6, 8],
    eta: [0.03, 0.1, 0.3],
    lambda: [1, 3, 10],
  },
  lightgbm: {
    num_leaves: [15, 31, 63],
    learning_rate: [0.03, 0.05, 0.1],
    min_data_in_leaf: [20, 50, 100],
  },
  ebm: {
    max_rounds: [1000, 2000, 4000],
    learning_rate: [0.01, 0.02, 0.04],
    interactions: [0, 5, 10],
  },
}

const DEFAULT_PARAMS: Record<string, Record<string, unknown>> = {
  catboost: CATBOOST_DEFAULT_PARAMS,
  xgboost: XGBOOST_DEFAULT_PARAMS,
  lightgbm: LIGHTGBM_DEFAULT_PARAMS,
  ebm: EBM_DEFAULT_PARAMS,
}

const CATBOOST_RESERVED_PARAM_KEYS = ["task_type"] as const
const CATBOOST_RESERVED_PARAM_HELP =
  "GPU training is configured in the Train pane."

/** Keys the params editor refuses for a tree family, with the reason shown. */
function reservedParamsFor(algorithm: string): { keys: readonly string[]; help: string } {
  if (algorithm === "catboost") {
    return { keys: CATBOOST_RESERVED_PARAM_KEYS, help: CATBOOST_RESERVED_PARAM_HELP }
  }
  return {
    keys: algorithmCapability(algorithm)?.reserved_params ?? [],
    help:
      "Haute sets the objective, threads, seed, offset and categorical handling; "
      + "choose the loss in the Target pane and monotonicity in the Features pane.",
  }
}
const DEFAULT_EVALUATION: Record<string, unknown> = {
  schema_version: 1,
  strategy: "random",
  seed: 42,
  validation: { method: "single", size: 0.2 },
}

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

const ALGORITHM_DESCRIPTIONS: Record<string, string> = {
  catboost: "Gradient boosting - handles categoricals natively, fast GPU training",
  glm: "Generalised linear model - interpretable coefficients, regulatory-friendly",
  xgboost:
    "Gradient boosting - histogram trees with native categoricals and early stopping on CPU",
  lightgbm:
    "Gradient boosting - fast leaf-wise trees with native categoricals and early stopping on CPU",
  ebm:
    "Explainable boosting - additive shape functions and pairwise interactions you can read directly",
}

function AlgorithmGateway({ onUpdate }: { onUpdate: OnUpdateConfig }) {
  // A family appears here once its descriptor ships, so the gateway can never
  // offer a model type the backend cannot train.
  const algorithms = Object.entries(ALGORITHM_CAPABILITIES).map(([id, capability]) => ({
    id,
    name: capability.label,
    description: ALGORITHM_DESCRIPTIONS[id] ?? "",
  }))

  return (
    <div className="px-4 py-3 space-y-3">
      <label className="text-sm font-semibold">Select algorithm</label>
      {algorithms.map((option) => (
        <button
          key={option.id}
          type="button"
          onClick={() => onUpdate({
            algorithm: option.id,
            ...(DEFAULT_PARAMS[option.id] ? { params: { ...DEFAULT_PARAMS[option.id] } } : {}),
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
  algorithm: string
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  params: Record<string, unknown>
  validationIssues: readonly TrainingConfigurationIssue[]
  columns: { name: string; dtype: string }[]
  onReviewPane: (pane: ModellingPane) => void
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
  validationIssues,
  columns,
  onReviewPane,
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
  const validationMessages = validationIssues.map((issue) => issue.message)

  const toggleGpu = (enabled: boolean) => {
    const { task_type: _taskType, ...nonGpuParams } = params
    onUpdate("params", enabled ? { ...nonGpuParams, task_type: "GPU" } : nonGpuParams)
  }
  const requestTrain = () => {
    if (validationMessages.length > 0) return
    onTrain()
  }

  return (
    <>
      <TrainingRunSummary config={config} columns={columns} preview={estimate.estimate?.evaluation_preview ?? null} />
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
      {algorithmCapability(algorithm)?.gpu_device === true && (
        <XGBoostGpuToggle
          checked={config.device === "gpu"}
          onToggle={(enabled) => onUpdate("device", enabled ? "gpu" : undefined)}
        />
      )}
      <TrainingActionsAndResults
        validationMessages={validationMessages}
        onValidationMessageClick={(index) => onReviewPane(trainingIssuePane(validationIssues[index]))}
        validationDestinations={validationIssues.map((issue) => {
          const pane = trainingIssuePane(issue)
          return pane === "params" ? "Parameters" : pane.charAt(0).toUpperCase() + pane.slice(1)
        })}
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
  onPaneIssuesChange,
}: Props) {
  const { allNodes, edges, submodels, preamble } = useGraph()
  const nodeId = String(config._nodeId ?? "")
  const setModellingPane = useUIStore((state) => state.setModellingPane)
  const reviewPane = (pane: ModellingPane) => setModellingPane(nodeId, pane)
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
  const metrics = effectiveMetrics(config)
  const reservedParams = reservedParamsFor(algorithm)
  const paramsProjection = formatHyperparameters(
    params,
    reservedParams.keys,
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
  // Tuning hides the fixed-parameter editor, so only a visible draft can block training.
  let paramDraftIssue: TrainingConfigurationIssue | null = null
  if (usesSharedPanes(algorithm) && !tuning) {
    try {
      parseHyperparameters(
        paramDraft,
        reservedParams.keys,
        reservedParams.help,
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
  // A string key keeps the host update to real changes in the flagged panes.
  const panesNeedingAttention = [...new Set(validationIssues.map(trainingIssuePane))].sort().join(",")
  useEffect(() => {
    onPaneIssuesChange?.(
      nodeId,
      panesNeedingAttention ? (panesNeedingAttention.split(",") as ModellingPane[]) : [],
    )
  }, [nodeId, onPaneIssuesChange, panesNeedingAttention])

  // Export settings do not change the trained model, so the stale check and
  // the RAM estimate follow the config without them.
  const trainingIdentity = useMemo(() => trainingIdentityConfig(config), [config])
  const graph = useCallback(
    () => buildGraph(allNodes, edges, submodels, preamble),
    [allNodes, edges, submodels, preamble],
  )
  const estimateEndpoint = useCallback(
    (_payload: void, context: { signal: AbortSignal }) => estimateAfterSupersededPreviews(
      () => estimateTrainingRam({ graph: graph(), node_id: nodeId, source: activeSource }, context),
      context.signal,
    ),
    [activeSource, graph, nodeId],
  )
  const estimate = useStaleConfigEstimate<TrainEstimate>(
    nodeId,
    trainingIdentity,
    cachedResult,
    estimateEndpoint,
    { source: activeSource, structuralVersion },
    { toastLabel: "Training estimate failed" },
  )
  // A completed result is remembered per document so a browser reload can put
  // it back from the server; a result the server no longer holds is reported.
  const trainedResultExpired = useTrainedJobRestore(nodeId, cachedResult, Boolean(trainJob), graph)
  const onEvaluationChange = useCallback(
    (nextEvaluation: Record<string, unknown>) => {
      const method = (nextEvaluation.validation as Record<string, unknown> | undefined)?.method
      if (config.refit_on_development === false && method !== "single") {
        onUpdate({ evaluation: nextEvaluation, refit_on_development: true })
      } else {
        onUpdate("evaluation", nextEvaluation)
      }
    },
    [config.refit_on_development, onUpdate],
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
    // The lineage is taken from the exact payload submitted, before awaiting, so
    // an edit made while the request is pending never relabels this job.
    const submittedGraph = graph()
    const lineage = trainingLineage(submittedGraph)
    try {
      const result = await trainModel({
        graph: submittedGraph,
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
          lineage,
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
          glm_inference: null,
          glm_smooth_terms: [],
          glm_regularization: null,
          ebm_terms: [],
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
  if (!isKnownAlgorithm(algorithm)) {
    return <div className="px-4 py-3" role="alert">Unsupported modelling algorithm: {algorithm}.</div>
  }

  const splitPane = (
    <SplitAndMetricsConfig
      columns={upstreamColumns}
      rowLimit={typeof config.row_limit === "number" ? config.row_limit : null}
      onRowLimitChange={(value) => onUpdate("row_limit", value)}
      evaluation={evaluation}
      onEvaluationChange={onEvaluationChange}
      refitOnDevelopment={config.refit_on_development !== false}
      onRefitOnDevelopmentChange={(value) => onUpdate("refit_on_development", value)}
      tuningEnabled={Boolean(tuning)}
      preview={estimate.estimate?.evaluation_preview ?? null}
      previewError={estimate.error?.startsWith("Evaluation preview failed:") ? estimate.error : null}
    />
  )
  const trainPane = (
    <TrainPane
      key={`${nodeId}:${hasTrainingConfigurationIssues ? "incomplete" : "complete"}`}
      algorithm={algorithm}
      config={config}
      onUpdate={onUpdate}
      params={params}
      validationIssues={validationIssues}
      columns={upstreamColumns}
      onReviewPane={reviewPane}
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
  const exportPane = (
    <ExportPane
      algorithm={algorithm}
      config={config}
      onUpdate={onUpdate}
      nodeLabel={allNodes.find((node) => node.id === nodeId)?.data.label ?? "model"}
      trainedJobId={
        cachedResult && cachedResult.result.status !== "error" && cachedResult.jobId
          ? cachedResult.jobId
          : null
      }
      training={Boolean(trainJob)}
      trainedResultStale={estimate.isStale}
      trainedResultExpired={trainedResultExpired}
    />
  )

  // The same pane list as the tabs: a pane this algorithm lacks shows Target.
  const pane = resolveModellingPane(algorithm, activePane)
  let paneBody: ReactElement | null = null
  if (pane === "split") {
    paneBody = splitPane
  } else if (pane === "train") {
    paneBody = trainPane
  } else if (pane === "export") {
    paneBody = exportPane
  } else if (usesSharedPanes(algorithm)) {
    if (pane === "target") {
      paneBody = <TargetAndTaskConfig algorithm={algorithm} config={config} onUpdate={onUpdate} columns={upstreamColumns} target={target} weight={weight} metrics={metrics} />
    } else if (pane === "features") {
      paneBody = (
        <>
          <CommonFeatureConfig config={config} onUpdate={onUpdate} columns={upstreamColumns} />
          {algorithm === "ebm" && (
            <EBMInteractionsConfig config={config} onUpdate={onUpdate} columns={upstreamColumns} />
          )}
        </>
      )
    } else if (pane === "params") {
      paneBody = (
        <HyperparametersConfig
          onReviewSplit={() => reviewPane("split")}
          algorithmLabel={algorithmCapability(algorithm)?.label ?? algorithm}
          starterSearchSpace={STARTER_SEARCH_SPACES[algorithm]}
          params={params}
          reservedKeys={reservedParams.keys}
          reservedKeysHelp={reservedParams.help}
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
    }
  } else if (algorithm === "glm" && pane === "target") {
    paneBody = (
      <>
        <GLMTargetConfig config={config} onUpdate={onUpdate} columns={upstreamColumns} onEstimateDispersion={onEstimateDispersion} />
      </>
    )
  } else if (algorithm === "glm" && pane === "params") {
    paneBody = <GLMRegularizationConfig config={config} onUpdate={onUpdate} />
  } else if (algorithm === "glm" && pane === "features") {
    paneBody = (
      <>
        <GLMTermsConfig config={config} onUpdate={onUpdate} columns={upstreamColumns} />
        <GLMInteractionsConfig config={config} onUpdate={onUpdate} columns={upstreamColumns} />
      </>
    )
  }

  return (
    <div key={nodeId} id={`modelling-${pane}-pane`} role="tabpanel" aria-labelledby={`modelling-${pane}-tab`} className="px-4 py-3 space-y-4">
      {pane !== "train" && pane !== "split" && validationIssues.filter((issue) => trainingIssuePane(issue) === pane && issue.code !== "glm-elastic-net-l1-ratio" && issue !== paramDraftIssue && issue !== searchSpaceDraftIssue).map((issue) => (
        <p key={issue.code} role="alert" className="rounded-lg border px-3 py-2 text-xs leading-5" style={{ borderColor: "var(--warning-border)", color: "var(--warning-strong)", background: "var(--warning-soft-subtle)" }}>{issue.message}</p>
      ))}
      {paneBody}
    </div>
  )
}
