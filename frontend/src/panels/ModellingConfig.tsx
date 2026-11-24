import { useCallback, useEffect, useMemo, useRef, useState } from "react"
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
import useNodeResultsStore, { type CachedTrainResult, type TrainProgress } from "../stores/useNodeResultsStore"
import { hasTrainResult } from "../stores/nodeResultsSelectors"
import useDocumentStatusStore, {
  captureDocumentExecutionFence,
  documentExecutionKey,
  isDocumentExecutionFenceCurrent,
} from "../stores/useDocumentStatusStore"
import useSettingsStore from "../stores/useSettingsStore"
import useToastStore from "../stores/useToastStore"
import useUIStore, { type ModellingPane } from "../stores/useUIStore"
import { openModelInModellingView, type TrainAction } from "../stores/useModellingViewStore"
import { applyModelUpdate, hasModels, interactiveModels, modelResultKey } from "../modelling/interactiveModels"
import { ModelCheckpoints } from "../modelling/ModelCheckpoints"
import { TrainingPane } from "../modelling/TrainingPane"
import { appliedPath, EMPTY_RECIPE, stageConfiguration, stageMatchesDraft, STAGES, type ModelStage } from "../modelling/stageSettings"
import { selectedCandidate, selectedPathPoint } from "../modelling/pathSelection"
import { useStagedTraining, useAppliedStageRecipe } from "../modelling/useStagedTraining"
import { hashConfig } from "../utils/configHash"
import { configField } from "../utils/configField"
import {
  executionJobStatusFromReason,
  executionMetricsFromError,
  executionTerminalReasonFromError,
} from "../utils/executionDiagnostics"
import { apiErrorMessage } from "../api/errors"
import { prepareCanvasRequest, answerNodeNamer } from "../utils/requestGateway"
import {
  effectiveMetrics,
  trainingConfigurationIssues,
  trainingIssuePane,
  type TrainingConfigurationIssue,
} from "../utils/trainingObjective"
import type { OnUpdateConfig } from "./editors/_shared"
import { useGraph, useRequestScope, useResultKey } from "./useGraph"
import {
  ALGORITHM_CAPABILITIES,
  algorithmCapability,
  isKnownAlgorithm,
  usesSharedPanes,
} from "./modelling/algorithmCapabilities"
import { CommonFeatureConfig } from "./modelling/CommonFeatureConfig"
import { EBMInteractionsConfig } from "./modelling/EBMInteractionsConfig"
import { ExportPane } from "./modelling/ExportPane"
import { GpuTrainingCheckbox, XGBoostGpuToggle } from "./modelling/GpuTrainingToggle"
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
import { trainingIdentityConfig, trainingEstimateConfig } from "../utils/modellingExportConfig"
import { shallowNodeDataHash } from "../utils/shallowNodeHash"
import { trainingLineage } from "../utils/trainedJobHandles"
import { useTrainedJobRestore } from "./modelling/useTrainedJobRestore"
import { SplitAndMetricsConfig } from "./modelling/SplitAndMetricsConfig"
import { TrainingRunSummary } from "./modelling/TrainingRunSummary"
import { estimateAfterSupersededPreviews } from "./modelling/trainingEstimate"
import { EVALUATION_PREVIEW_FAILED } from "./modelling/evaluationPreview"
import { TargetAndTaskConfig } from "./modelling/TargetAndTaskConfig"
import { TrainingActionsAndResults } from "./modelling/TrainingActionsAndResults"
import type { ReactElement } from "react"

type Props = {
  /** The node being configured: its training job, results and remembered pane. */
  nodeId: string
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  upstreamColumns?: { name: string; dtype: string }[]
  activePane?: ModellingPane
  /** Reports the panes whose settings block training, for the host's tab badges. */
  onPaneIssuesChange?: (nodeId: string, panes: readonly ModellingPane[]) => void
  /**
   * The Modelling view's host: these panes of the one editor, stacked, in place of the
   * node panel's `activePane`. Absent in the node panel.
   */
  panes?: readonly ModellingPane[]
  /** Where a "Go to" link leads; the node panel's own pane when absent. */
  onReviewPane?: (pane: ModellingPane) => void
  /**
   * The panes the host can lead to; every pane when absent. A link to another pane is not
   * offered, and neither is parameter tuning where Split, which gives it its validation
   * rows, cannot be reached.
   */
  reviewablePanes?: readonly ModellingPane[]
  /** The Modelling view's chosen factor, marked and scrolled to in the Features pane's list. */
  chosenFactor?: string | null
  /**
   * The Modelling view's model of an interactive build, by id: `config` is its flat config,
   * its run is kept under its own key, and every request names it.
   */
  modelId?: string
  /**
   * The Modelling view's toolbar trains the chosen model: the editor reports how to train
   * or cancel it, why it cannot, and its last failure, each time one changes; null on leaving.
   */
  onTrainAction?: (action: TrainAction | null) => void
  stagedPane?: "params" | "train"
  stagedTraining?: boolean
  onAppliedStage?: (updates: Record<string, unknown>) => unknown
}

// one_hot_max_size is a visible starter value, not a hidden default: CatBoost's
// own default encodes most categorical columns with target statistics, which
// train many times slower than one-hot encoding on small categorical columns.
const CATBOOST_DEFAULT_PARAMS: Record<string, unknown> = {
  iterations: 1000,
  learning_rate: 0.05,
  depth: 6,
  l2_leaf_reg: 3,
  early_stopping_rounds: 50,
  one_hot_max_size: 10,
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

// t-boost's own defaults are its recommended recipe; n_trees is the ceiling every
// fit stops early within, and the order caps how many features a table couples.
// The bag count, interaction hurdle, pruning, banding and graduation are t-boost's defaults
// too, written out so the node shows they are on and where to change them. Pruning main
// effects is not t-boost's default; a new node opts in.
const TBOOST_DEFAULT_PARAMS: Record<string, unknown> = {
  n_trees: 4000,
  max_interaction_order: 3,
  n_bags: 8,
  interaction_gain_hurdle: 2,
  prune: true,
  prune_main_effects: true,
  band_tolerance: 0.75,
  graduate: true,
}

// An interactive build is boosted first; pruning, banding and smoothing are later steps of
// the Modelling view, so its starting recipe switches each of them off. `band_tolerance`
// is null because t-boost bands at 0.75 when the key is absent.
const INTERACTIVE_BUILD_PARAMS: Record<string, unknown> = {
  n_trees: 4000,
  max_interaction_order: 3,
  n_bags: 8,
  interaction_gain_hurdle: 2,
  prune: false,
  band_tolerance: null,
  graduate: false,
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
  tboost: {
    learning_rate: [0.03, 0.05, 0.1],
    max_interaction_order: [2, 3],
    lambda_: [0.5, 1, 2],
  },
}

const DEFAULT_PARAMS: Record<string, Record<string, unknown>> = {
  catboost: CATBOOST_DEFAULT_PARAMS,
  xgboost: XGBOOST_DEFAULT_PARAMS,
  lightgbm: LIGHTGBM_DEFAULT_PARAMS,
  ebm: EBM_DEFAULT_PARAMS,
  tboost: TBOOST_DEFAULT_PARAMS,
}

const TREE_RESERVED_PARAM_HELP =
  "Haute sets the objective, threads, seed, offset and categorical handling; "
  + "choose the loss in the Target pane and monotonicity in the Features pane."
// CatBoost's GPU setting (task_type) lives in the Train pane; the rest Haute sets.
const CATBOOST_RESERVED_PARAM_KEYS: readonly string[] = [
  "task_type",
  ...(algorithmCapability("catboost")?.reserved_params ?? []),
]
const CATBOOST_RESERVED_PARAM_HELP =
  `${TREE_RESERVED_PARAM_HELP} GPU training is configured in the Train pane.`

/** Keys the params editor refuses for a tree family, with the reason shown. */
function reservedParamsFor(algorithm: string): { keys: readonly string[]; help: string } {
  if (algorithm === "catboost") {
    return { keys: CATBOOST_RESERVED_PARAM_KEYS, help: CATBOOST_RESERVED_PARAM_HELP }
  }
  return {
    keys: algorithmCapability(algorithm)?.reserved_params ?? [],
    help: TREE_RESERVED_PARAM_HELP,
  }
}
// Issues a pane does not repeat above its body: the GLM Parameters pane shows its
// conflicts beside the settings involved, and a missing L1 ratio only in Train's list.
const PANE_BODY_ISSUE_CODES: ReadonlySet<TrainingConfigurationIssue["code"]> = new Set([
  "glm-elastic-net-l1-ratio",
  "glm-smooth-regularization",
  "glm-robust-standard-errors",
])
const DEFAULT_EVALUATION: Record<string, unknown> = {
  schema_version: 1,
  strategy: "random",
  seed: 42,
  validation: { method: "single", size: 0.2 },
}

/**
 * A new node's split. A t-boost fit takes no validation rows (every bag stops on rows it
 * holds back itself), so a t-boost node starts with none set aside.
 */
function defaultEvaluation(algorithm: string): Record<string, unknown> {
  return algorithm === "tboost"
    ? { ...DEFAULT_EVALUATION, validation: { method: "none" } }
    : DEFAULT_EVALUATION
}

/** Why a node's last training failed: the failed job's error, or a result the server marked an error. */
function trainFailure(cached: CachedTrainResult): string | null {
  if (cached.result === null) return cached.error ?? null
  return cached.result.status === "error" ? cached.result.error ?? null : null
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
  tboost:
    "Rating-table boosting - gradient boosting whose model is exactly a set of rating tables",
}

function AlgorithmGateway({ onUpdate, columns }: {
  onUpdate: OnUpdateConfig
  columns: readonly { name: string; dtype: string }[]
}) {
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
            evaluation: defaultEvaluation(option.id),
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
      <button
        type="button"
        // Every factor starts in the model. The columns are written here, in the click,
        // and only once they are known: a node whose columns have not arrived keeps no
        // feature list, and the Modelling view's Include all fills it.
        onClick={() => onUpdate({
          interactive_build: true,
          evaluation: defaultEvaluation("tboost"),
          models: [{
            id: "m1",
            name: "t-boost",
            algorithm: "tboost",
            params: { ...INTERACTIVE_BUILD_PARAMS },
            ...(columns.length > 0 ? { feature_columns: columns.map((column) => column.name) } : {}),
          }],
        })}
        className="w-full rounded-lg px-3 py-3 text-left algorithm-gateway-btn"
      >
        <span className="block text-xs font-semibold">Interactive build</span>
        <span
          className="mt-0.5 block text-[11px]"
          style={{ color: "var(--text-muted)" }}
        >
          t-boost, built step by step in the Modelling view
        </span>
      </button>
    </div>
  )
}

/** An interactive build's node panel: what the model is, and the way into the Modelling view. */
function InteractiveBuildSummary({ nodeId, config }: { nodeId: string; config: Record<string, unknown> }) {
  const target = typeof config.target === "string" && config.target !== "" ? config.target : null
  return (
    <div className="px-4 py-3 space-y-3" data-testid="interactive-build-summary">
      <p className="text-[13px] font-semibold">Interactive build</p>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-[12px]">
        <dt style={{ color: "var(--text-muted)" }}>Models</dt>
        <dd className="truncate">{hasModels(config) ? interactiveModels(config).map((model) => model.name).join(", ") : "None"}</dd>
        <dt style={{ color: "var(--text-muted)" }}>Target</dt>
        <dd className="truncate">{target ?? "Not set"}</dd>
      </dl>
      <button
        type="button"
        className="rounded-lg px-3 py-1.5 text-[12px] font-medium"
        style={{
          background: "var(--model-accent-soft)",
          border: "1px solid var(--model-accent-border)",
          color: "var(--model-accent)",
        }}
        // Another view hides an open utility panel, so it waits for the panel's unsaved edit.
        onClick={() => useUIStore.getState().afterUtilitySaved(() => openModelInModellingView(nodeId))}
      >
        Open in Modelling
      </button>
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
  /** Whether the host can lead to a pane: an issue on one it cannot is listed without its "Go to". */
  canReviewPane: (pane: ModellingPane) => boolean
  trainJob: ReturnType<typeof useNodeResultsStore.getState>["trainJobs"][string] | undefined
  cachedResult: ReturnType<typeof useNodeResultsStore.getState>["trainResults"][string] | undefined
  estimate: UseStaleConfigEstimateResult<TrainEstimate>
  estimateWaiting: boolean
  nodeLabel: (nodeId: string) => string
  nodeOpener: (nodeId: string) => (() => void) | null
  submitting: boolean
  cancelling: boolean
  onTrain: () => void
  onCancel: () => void
  tuningEnabled: boolean
  /** False in the Modelling view, which has no preview panel below to point a finished model's results at. */
  resultsInPreviewPanel: boolean
}

function TrainPane({
  algorithm,
  config,
  onUpdate,
  params,
  validationIssues,
  columns,
  onReviewPane,
  canReviewPane,
  trainJob,
  cachedResult,
  estimate,
  estimateWaiting,
  nodeLabel,
  nodeOpener,
  submitting,
  cancelling,
  onTrain,
  onCancel,
  tuningEnabled,
  resultsInPreviewPanel,
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
        <GpuTrainingCheckbox checked={params.task_type === "GPU"} onToggle={toggleGpu} />
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
          if (!canReviewPane(pane)) return null
          if (pane === "params") return "Parameters"
          // An interactive build's features are the Modelling view's factors.
          if (pane === "features" && config.interactive_build === true) return "Factors"
          return pane.charAt(0).toUpperCase() + pane.slice(1)
        })}
        training={Boolean(trainJob)}
        trainProgress={trainJob?.progress ?? null}
        estimatedRemainingSeconds={trainJob?.estimatedRemainingSeconds ?? null}
        trainResult={cachedResult?.result ?? null}
        trainError={cachedResult ? trainFailure(cachedResult) : null}
        isStale={estimate.isStale}
        ramEstimate={estimate.estimate}
        ramEstimateLoading={estimate.loading}
        ramEstimateError={estimate.error}
        ramEstimateErrorCode={estimate.errorCode}
        estimateWaiting={estimateWaiting}
        rowLimit={rowLimit}
        nodeLabel={nodeLabel}
        nodeOpener={nodeOpener}
        terminalMetrics={cachedResult?.terminalStatus?.execution_metrics ?? null}
        terminalStatus={cachedResult?.terminalStatus?.status ?? null}
        terminalReason={cachedResult?.terminalStatus?.terminal_reason ?? null}
        terminalTraceback={cachedResult?.terminalStatus?.worker_remote_traceback ?? null}
        submitting={submitting}
        cancelling={cancelling}
        tuningEnabled={tuningEnabled}
        resultsInPreviewPanel={resultsInPreviewPanel}
        onTrain={requestTrain}
        onCancel={onCancel}
      />
    </>
  )
}

/**
 * A Model Training node's editor. An interactive build is edited in the Modelling view,
 * which passes `panes`; in the node panel it shows what it is and the way there.
 */
export default function ModellingConfig(props: Props) {
  if (props.config.interactive_build === true && props.panes === undefined) {
    return <InteractiveBuildSummary nodeId={props.nodeId} config={props.config} />
  }
  return <ModellingEditor {...props} />
}

function ModellingEditor({
  nodeId,
  config,
  onUpdate,
  upstreamColumns = [],
  activePane = "target",
  onPaneIssuesChange,
  panes,
  onReviewPane,
  reviewablePanes,
  chosenFactor = null,
  modelId,
  onTrainAction,
  stagedTraining = false,
  stagedPane = "train",
  onAppliedStage,
}: Props) {
  const requestScope = useRequestScope()
  const { allNodes, edges, submodels, preamble, openNode } = useGraph()
  const setModellingPane = useUIStore((state) => state.setModellingPane)
  const reviewPane = onReviewPane ?? ((pane: ModellingPane) => setModellingPane(nodeId, pane))
  const canReviewPane = (pane: ModellingPane) => reviewablePanes === undefined || reviewablePanes.includes(pane)
  // The node's training is kept under its result key (an occurrence's own inside a
  // submodel); its id still names it on the canvas, and its panes and drafts.
  const nodeResultKey = useResultKey(nodeId)
  const resultKey = modelId === undefined ? nodeResultKey : modelResultKey(nodeResultKey, modelId)
  const trainJob = useNodeResultsStore((state) => state.trainJobs[resultKey])
  const cachedResult = useNodeResultsStore((state) => state.trainResults[resultKey])
  const startTrainJob = useNodeResultsStore((state) => state.startTrainJob)
  const updateTrainProgress = useNodeResultsStore((state) => state.updateTrainProgress)
  const completeTrainJob = useNodeResultsStore((state) => state.completeTrainJob)
  const failTrainJob = useNodeResultsStore((state) => state.failTrainJob)
  const failTrainStart = useNodeResultsStore((state) => state.failTrainStart)
  const addToast = useToastStore((state) => state.addToast)
  const activeSource = useSettingsStore((state) => state.activeSource)
  const structuralVersion = useGraphStore((state) => state.structuralVersion)
  const [submitting, setSubmitting] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  // Drafts live above the Params pane.  They deliberately survive a Params
  // unmount for this node, while a newly seen node starts from its own params.
  const [restoreVersion, setRestoreVersion] = useState(0)
  const [paramDrafts, setParamDrafts] = useState<Record<string, string>>({})
  const [searchSpaceDrafts, setSearchSpaceDrafts] = useState<Record<string, string>>({})

  const algorithm = String(config.algorithm ?? "").toLowerCase()
  const rawParams = configField<Record<string, unknown>>(config, "params", {})
  const stagedEnabled = stagedTraining && algorithm === "tboost" && modelId !== undefined
  const stagedDraft = useMemo(() => {
    try { return { ...stageConfiguration(config), error: null } }
    catch (error) { return { params: rawParams, stages: stageConfiguration({}).stages, error: apiErrorMessage(error) } }
  }, [config, rawParams])
  const params = stagedEnabled ? stagedDraft.params : rawParams
  const target = configField(config, "target", "")
  const weight = configField(config, "weight", "")
  const evaluation = configField<Record<string, unknown>>(
    config,
    "evaluation",
    defaultEvaluation(algorithm),
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
  const configuredValidationIssues = trainingConfigurationIssues(config, {
    targetDtype: upstreamColumns.find((column) => column.name === config.target)?.dtype ?? "",
  })
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
  // The graph reads the preamble and constants from the store; listing them
  // renews the callback, so an edit made after mount reaches the next request.
  const globalConstants = useGraphStore((state) => state.globalConstants)
  const estimateInputs = useMemo(() => trainingEstimateConfig(config), [config])
  const estimateStructureKey = useMemo(() => hashConfig({
    nodes: allNodes.filter((entry) => entry.id !== nodeId).map((entry) => [entry.id, shallowNodeDataHash(entry.data)]),
    edges: edges.map((edge) => [edge.source, edge.target, edge.sourceHandle, edge.targetHandle]),
    submodels: submodels ?? null, preamble, globalConstants,
  }), [allNodes, nodeId, edges, submodels, preamble, globalConstants])
  const requestTarget = useCallback(
    () => {
      void allNodes
      void edges
      void submodels
      void globalConstants
      void preamble
      const prepared = prepareCanvasRequest(requestScope, nodeId)
      return modelId === undefined
        ? prepared
        : { ...prepared, resultKey: modelResultKey(prepared.resultKey, modelId) }
    },
    [requestScope, allNodes, edges, submodels, nodeId, preamble, globalConstants, modelId],
  )
  const graph = useCallback(() => requestTarget().graph, [requestTarget])
  const stageRequest = useCallback(() => {
    const target = requestTarget()
    return { graph: target.graph, node_id: target.nodeId, source: activeSource, model: modelId }
  }, [requestTarget, activeSource, modelId])
  const stageBuild = useStagedTraining({ enabled: stagedEnabled && requestScope !== null,
    resultKey, request: stageRequest, jobId: trainJob?.jobId })
  const [requestedStage, setRequestedStage] = useState<ModelStage | "finalise" | null>(null)
  const otherTraining = useNodeResultsStore((state) => Object.keys(state.trainJobs).some((key) => key !== resultKey))

  // The estimate names a node by its id in the graph sent. One inside another
  // submodel is off this canvas; its id still says which.
  const nodeNamed = useMemo(() => {
    // Named in the view the panel shows; its graph renews it.
    void allNodes
    return answerNodeNamer()
  }, [allNodes])
  const canvasNodeLabel = useCallback(
    (id: string) => nodeNamed(id)?.data.label || id,
    [nodeNamed],
  )
  // Only a node on this canvas opens: matched by id, since the panel's graph holds copies.
  const canvasNodeOpener = useCallback(
    (id: string) => {
      const node = nodeNamed(id)
      return openNode && node && allNodes.some((candidate) => candidate.id === node.id) ? () => openNode(node.id) : null
    },
    [allNodes, nodeNamed, openNode],
  )
  const [estimateWaiting, setEstimateWaiting] = useState(false)
  const latestEstimate = useRef(0)
  const estimateEndpoint = useCallback(
    async (_payload: void, context: { signal: AbortSignal }) => {
      // A superseded estimate settles after its replacement starts, so only the
      // latest may show or clear the waiting notice; the replacement starts
      // without it, whatever the estimate it replaces was waiting for.
      const run = ++latestEstimate.current
      setEstimateWaiting(false)
      try {
        return await estimateAfterSupersededPreviews(
          () => {
            const target = requestTarget()
            return estimateTrainingRam({ graph: target.graph, node_id: target.nodeId, source: activeSource, model: modelId }, context)
          },
          context.signal,
          undefined,
          { onWaiting: () => { if (run === latestEstimate.current) setEstimateWaiting(true) } },
        )
      } finally {
        if (run === latestEstimate.current) setEstimateWaiting(false)
      }
    },
    [activeSource, modelId, requestTarget],
  )
  const estimate = useStaleConfigEstimate<TrainEstimate>(
    nodeId,
    trainingIdentity,
    cachedResult,
    estimateEndpoint,
    { source: activeSource, structuralVersion },
    // A saved version (the comparison view) runs nothing, so it is not estimated.
    { toastLabel: "Training estimate failed", enabled: requestScope !== null, estimateInputs, estimateStructureKey },
  )
  // A completed result is remembered per document so a browser reload can put
  // it back from the server; a result the server no longer holds is recorded
  // in the results store, which the results panel reads too. A saved version
  // runs nothing, so it restores nothing.
  useTrainedJobRestore(resultKey, cachedResult, Boolean(trainJob), graph, requestScope !== null)
  const trainedResultExpired = useNodeResultsStore(
    (state) => Object.hasOwn(state.expiredTrainJobs, resultKey),
  )
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
  // A dispersion estimate reads the document, this node's training settings,
  // the graph feeding it and the data source; a change to any stops it.
  const documentKey = useDocumentStatusStore(documentExecutionKey)
  // The estimate hook hashes the training identity in this same render.
  const trainingHash = estimate.configHash
  const dispersionEstimate = useMemo(() => ({
    scope: JSON.stringify([documentKey, resultKey, activeSource, structuralVersion, trainingHash]),
    run: (param: DispersionParam, signal: AbortSignal) => {
      const target = requestTarget()
      return runDispersionEstimate({
        graph: target.graph,
        node_id: target.nodeId,
        param,
        source: activeSource,
      }, { signal })
    },
  }), [activeSource, documentKey, resultKey, requestTarget, structuralVersion, trainingHash])
  const onTrain = useCallback(async (operation?: ModelStage | "finalise") => {
    if (hasTrainingConfigurationIssues || (stagedEnabled && stagedDraft.error)) return
    const documentFence = captureDocumentExecutionFence()
    if (!isDocumentExecutionFenceCurrent(documentFence)) return
    setSubmitting(true)
    // The lineage is taken from the exact payload submitted, before awaiting, so
    // an edit made while the request is pending never relabels this job; the
    // config hash is this render's training identity for the same reason. The
    // job is kept under the key prepared with the request, so a start answered
    // after the view changed stays with the occurrence it was started in.
    const target = { ...requestTarget() }
    if (operation !== undefined && modelId !== undefined) {
      const updates = { params: stagedDraft.params, stages: stagedDraft.stages }
      const submitted = operation === "full" ? { ...updates, stages: { ...updates.stages, applied_recipe: EMPTY_RECIPE } } : updates
      const updated = onUpdate(updates)
      if (updated?.ok === false) { setSubmitting(false); return }
      target.graph = { ...target.graph, nodes: target.graph.nodes.map((node) => node.id === target.nodeId
        ? { ...node, data: { ...node.data, config: applyModelUpdate(node.data.config as Record<string, unknown>, modelId, submitted) } } : node) }
      setRequestedStage(operation)
    }
    const key = target.resultKey
    const nodeLabel = allNodes.find((node) => node.id === nodeId)?.data.label ?? "Model Training"
    const lineage = trainingLineage(target.graph)
    const configHash = operation === undefined ? trainingHash : hashConfig(trainingIdentityConfig({
      ...config, params: stagedDraft.params, stages: { ...stagedDraft.stages,
        applied_recipe: operation === "full" ? EMPTY_RECIPE : stagedDraft.stages.applied_recipe },
    }))
    const attempt = { configHash, source: activeSource, structuralVersion }
    try {
      const result = await trainModel({
        graph: target.graph,
        node_id: target.nodeId,
        source: useSettingsStore.getState().activeSource,
        model: modelId,
        ...(operation === undefined ? {} : { stage: { operation,
          ...(operation === "full" ? { prepare_next: true } : { parent_revision: stageBuild.state?.current?.revision }) } }),
      })
      if (!isDocumentExecutionFenceCurrent(documentFence)) return
      if (result.status === "started" && result.job_id) {
        startTrainJob(key, result.job_id, nodeLabel, configHash, activeSource, structuralVersion, lineage)
      } else {
        // The server only ever answers a started job; anything else refused the start.
        failTrainStart(key, attempt, result.error || `Training did not start (status ${result.status}).`)
      }
    } catch (error) {
      if (!isDocumentExecutionFenceCurrent(documentFence)) return
      const message = apiErrorMessage(error)
      failTrainStart(key, attempt, message, failureStatus(error, message))
    } finally {
      setSubmitting(false)
    }
  }, [activeSource, allNodes, failTrainStart, hasTrainingConfigurationIssues, modelId, nodeId, requestTarget, startTrainJob, structuralVersion, trainingHash, config, stagedEnabled, stagedDraft, stageBuild.state, onUpdate])
  const restoredStageJob = useRef<string | null>(null)
  useEffect(() => {
    const savedJob = stageBuild.state?.job
    if (!stagedEnabled || trainJob || savedJob?.status !== "running"
      || savedJob.id === cachedResult?.jobId || savedJob.id === restoredStageJob.current) return
    restoredStageJob.current = savedJob.id
    setRequestedStage(savedJob.operation)
    startTrainJob(resultKey, savedJob.id, "Model training", trainingHash, activeSource, structuralVersion)
  }, [stagedEnabled, stageBuild.state?.job, trainJob, cachedResult?.jobId, resultKey, trainingHash, activeSource, structuralVersion, startTrainJob])

  useAppliedStageRecipe(stagedEnabled ? stageBuild.state : undefined, config, onAppliedStage ?? onUpdate, stageBuild.key)

  const stageJob = stageBuild.state?.job
  const refreshStages = stageBuild.refresh
  const onCancel = useCallback(async () => {
    const key = resultKey
    const job = useNodeResultsStore.getState().trainJobs[key]
    const durableJob = stageJob
    const jobId = durableJob?.status === "running" ? durableJob.id : job?.jobId
    if (!jobId || cancelling) return

    setCancelling(true)
    const documentFence = captureDocumentExecutionFence()
    try {
      const status = await cancelTrain(jobId)
      // The reply belongs to the job it cancelled; the store ignores it once
      // polling has retired that job, and a changed document drops it here.
      if (!isDocumentExecutionFenceCurrent(documentFence)) return
      if (job?.jobId === jobId) {
        if (status.status === "completed" && status.result) completeTrainJob(key, jobId, status.result, status)
        else if (FAILED_JOB_STATUSES.has(status.status)) failTrainJob(key, jobId, status.message || "Training stopped", status)
        else updateTrainProgress(key, jobId, status)
      }
      refreshStages()
    } catch (error) {
      if (
        isDocumentExecutionFenceCurrent(documentFence)
        && (durableJob?.id === jobId || useNodeResultsStore.getState().trainJobs[key]?.jobId === jobId)
      ) {
        addToast("error", `Could not cancel training: ${apiErrorMessage(error)}`)
      }
    } finally {
      setCancelling(false)
    }
  }, [addToast, cancelling, completeTrainJob, failTrainJob, resultKey, updateTrainProgress, stageJob, refreshStages])

  // The view's toolbar trains this model, so it hears how, and what stands in the way.
  const trainFailureMessage = cachedResult ? trainFailure(cachedResult) : null
  const trainIssueKey = validationIssues.map((issue) => issue.message).join("\n")
  const actionRef = useRef({ onTrain, onCancel })
  useEffect(() => {
    actionRef.current = { onTrain, onCancel }
  }, [onTrain, onCancel])
  const training = Boolean(trainJob)
  useEffect(() => {
    if (onTrainAction === undefined) return
    onTrainAction({
      training,
      busy: submitting || cancelling,
      issues: trainIssueKey === "" ? [] : trainIssueKey.split("\n"),
      error: trainFailureMessage,
      train: () => void actionRef.current.onTrain(),
      cancel: () => void actionRef.current.onCancel(),
    })
  }, [onTrainAction, training, submitting, cancelling, trainIssueKey, trainFailureMessage])
  useEffect(() => () => onTrainAction?.(null), [onTrainAction])

  if (!algorithm) return <AlgorithmGateway onUpdate={onUpdate} columns={upstreamColumns} />
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
      previewError={estimate.errorCode === EVALUATION_PREVIEW_FAILED ? estimate.error : null}
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
      canReviewPane={canReviewPane}
      trainJob={trainJob}
      cachedResult={cachedResult}
      estimate={estimate}
      estimateWaiting={estimateWaiting}
      nodeLabel={canvasNodeLabel}
      nodeOpener={canvasNodeOpener}
      submitting={submitting}
      cancelling={cancelling}
      onTrain={onTrain}
      onCancel={onCancel}
      tuningEnabled={tuning !== null}
      resultsInPreviewPanel={panes === undefined}
    />
  )
  const exportPane = (
    <ExportPane
      algorithm={algorithm}
      config={config}
      onUpdate={onUpdate}
      nodeLabel={allNodes.find((node) => node.id === nodeId)?.data.label ?? "model"}
      trainedJobId={stagedEnabled ? stageBuild.finalJobId ?? null :
        cachedResult && hasTrainResult(cachedResult) && cachedResult.jobId
          ? cachedResult.jobId
          : null
      }
      training={Boolean(trainJob)}
      trainedResultStale={estimate.isStale}
      trainedResultExpired={trainedResultExpired}
    />
  )

  const bodyFor = (pane: ModellingPane): ReactElement | null => {
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
            <CommonFeatureConfig
              config={config}
              onUpdate={onUpdate}
              columns={upstreamColumns}
              noun={config.interactive_build === true ? "factor" : "feature"}
              chosen={chosenFactor}
            />
            {algorithm === "ebm" && (
              <EBMInteractionsConfig config={config} onUpdate={onUpdate} columns={upstreamColumns} />
            )}
          </>
        )
      } else if (pane === "params") {
        paneBody = (
          <HyperparametersConfig
            onReviewSplit={canReviewPane("split") ? () => reviewPane("split") : undefined}
            tuningOffered={canReviewPane("split")}
            algorithmLabel={algorithmCapability(algorithm)?.label ?? algorithm}
            starterSearchSpace={STARTER_SEARCH_SPACES[algorithm]}
            refitOnDevelopment={config.refit_on_development !== false}
            params={params}
            reservedKeys={reservedParams.keys}
            reservedKeysHelp={reservedParams.help}
            onUpdate={stagedEnabled ? ((key, value) => onUpdate({
              stages: stagedDraft.stages,
              ...(typeof key === "string" ? { [key]: value } : key),
            })) : onUpdate}
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
        if (stagedEnabled) {
          const current = stageBuild.state?.current?.result.response.staged
          const pending: ModelStage[] = []
          for (const name of ["pruned", "banded", "smoothed"] as const) {
            const snapshot = current?.stages[name]
            if (!snapshot) continue
            if (!stagedDraft.stages.enabled[name] || pending.includes(snapshot.parent as ModelStage)
              || !stageMatchesDraft(stagedDraft.stages, name, snapshot)) pending.push(name)
          }
          const savedJob = stageBuild.state?.job
          const queued = stageBuild.state?.next_preview?.status === "queued"
          const active = queued || (savedJob?.automatic && (savedJob.status === "running" || trainJob?.jobId === savedJob.id))
            ? current?.selected_stage ?? "full"
            : savedJob?.status === "running" ? savedJob.operation
            : trainJob ? requestedStage ?? "full" : null
          paneBody = <>
            <TrainingPane key={restoreVersion} view={stagedPane} settings={stagedDraft.stages} gbm={paneBody}
              currentResult={stageBuild.state?.current?.result.response}
              previewResult={selectedCandidate(stageBuild)}
              previewStage={selectedPathPoint(stageBuild)?.available.path.stage ?? stageBuild.state?.preview?.result.response.staged?.selected_stage}
              candidatePath={selectedPathPoint(stageBuild)?.available.path}
              candidatePoint={selectedPathPoint(stageBuild)?.pointId}
              inspectionPending={stageBuild.inspection?.loading || Boolean(stageBuild.inspection?.error)}
              evaluationMode={(evaluation.validation as { method?: string } | undefined)?.method === "cross_validation" ? "OOF validation" : (evaluation.validation as { method?: string } | undefined)?.method === "single" ? "Validation" : "Development"}
              evaluationKey={hashConfig({ evaluation, target, modelId })}
              checkpoints={stageBuild.state?.current && <ModelCheckpoints checkpoints={stageBuild.state.checkpoints}
                disabled={active !== null || stageBuild.busy || submitting || cancelling || otherTraining || stageBuild.state.preview != null}
                onAction={(action, value) => stageBuild.checkpointAction(action, value, (updates) => {
                  const result = (onAppliedStage ?? onUpdate)(updates)
                  if (!(result && typeof result === "object" && "ok" in result && result.ok === false)) {
                    setParamDrafts({})
                    setSearchSpaceDrafts({})
                    setRestoreVersion((version) => version + 1)
                  }
                  return result
                })} />}
              pending={pending}
              stages={STAGES.filter((name) => current?.stages[name] !== undefined)}
              pathAvailable={current?.pruning_path != null}
              pathChanged={current !== undefined && hashConfig({ path: appliedPath(current.configuration) }) !== hashConfig({ path: stagedDraft.stages.path })}
              active={active} busy={submitting || cancelling || stageBuild.busy || queued}
              blocked={hasTrainingConfigurationIssues || Boolean(stagedDraft.error) || otherTraining}
              blockingReasons={[...validationIssues.map((issue) => issue.message),
                ...(stagedDraft.error ? [stagedDraft.error] : []),
                ...(otherTraining ? ["Another model is training. Wait for it to finish or cancel that run."] : []),
                ...(queued ? ["Finishing the current run. Cancellation will be available shortly."] : [])]}
              postfitBlocked={Boolean(stageBuild.state?.stale_reason)}
              preview={selectedPathPoint(stageBuild) !== undefined || stageBuild.state?.preview != null}
              onRun={(stage) => void onTrain(stage)} onCancel={() => void onCancel()}
              onUpdate={(stages) => onUpdate({ params: stagedDraft.params, stages })}
              onApply={() => void stageBuild.previewAction("apply")}
              onDiscard={() => void stageBuild.previewAction("discard")}
              finalAvailable={stageBuild.state?.final != null}
              onExport={() => void stageBuild.prepareExport()}
              exportPanel={stageBuild.finalJobId ? exportPane : undefined} />
            {(stagedDraft.error || stageBuild.error || (stageBuild.state?.next_preview?.status !== "queued" ? stageBuild.state?.next_preview?.message : undefined) || stageBuild.state?.stale_reason || current?.path_unavailable_reason) &&
              <p className="px-4 pb-4 text-[11px] leading-relaxed" role="status" style={{ color: "var(--text-muted)" }}>
                {stagedDraft.error || stageBuild.error || (stageBuild.state?.next_preview?.status !== "queued" ? stageBuild.state?.next_preview?.message : undefined) || stageBuild.state?.stale_reason || current?.path_unavailable_reason}
              </p>}
          </>
        }
      }
    } else if (algorithm === "glm" && pane === "target") {
      paneBody = (
        <>
          <GLMTargetConfig config={config} onUpdate={onUpdate} columns={upstreamColumns} dispersionEstimate={dispersionEstimate} />
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

    return paneBody
  }
  const issueAlerts = (pane: ModellingPane) => (
    pane === "train" || pane === "split"
      ? []
      : validationIssues.filter((issue) => trainingIssuePane(issue) === pane && !PANE_BODY_ISSUE_CODES.has(issue.code) && issue !== paramDraftIssue && issue !== searchSpaceDraftIssue)
  ).map((issue) => (
    <p key={issue.code} role="alert" className="rounded-lg border px-3 py-2 text-xs leading-5" style={{ borderColor: "var(--warning-border)", color: "var(--warning-strong)", background: "var(--warning-soft-subtle)" }}>{issue.message}</p>
  ))

  // The Modelling view keeps readiness warnings below the controls in each pane.
  if (panes !== undefined) {
    return (
      <div key={nodeId} className="px-4 py-3 space-y-6">
        {panes.map((stacked) => (
          <section key={stacked} data-pane={stacked} className="space-y-4">
            {bodyFor(stacked)}
            {issueAlerts(stacked)}
          </section>
        ))}
      </div>
    )
  }

  // The same pane list as the tabs: a pane this algorithm lacks shows Target.
  const pane = resolveModellingPane(algorithm, activePane)
  return (
    <div key={nodeId} id={`modelling-${pane}-pane`} role="tabpanel" aria-labelledby={`modelling-${pane}-tab`} className="px-4 py-3 space-y-4">
      {issueAlerts(pane)}
      {bodyFor(pane)}
    </div>
  )
}
