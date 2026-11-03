/** Shared API response/request types for the Haute backend. */

import type { toCanonicalGraphPayload } from "../utils/graphSnapshot"

// Re-export canonical types from their source locations
import type {
  NodeTypeValue,
  PipelineEdge,
  PipelineGraph,
} from "../types/node"
import type {
  EvaluationPreviewPayload as EvaluationPreview,
  ExplorePivotMemberOption as GeneratedExplorePivotMemberOption,
  ExplorePivotMembersResponse as GeneratedExplorePivotMembersResponse,
  ExplorePivotPath as GeneratedExplorePivotPath,
  ExplorePivotResult as GeneratedExplorePivotResult,
  ExplorePivotRunResponse as GeneratedExplorePivotRunResponse,
  ExplorePivotStatusResponse as GeneratedExplorePivotStatusResponse,
  GitStorageBind as GeneratedGitStorageBind,
  GitStorageSync as GeneratedGitStorageSync,
  GitWorkingBranchResponse as GeneratedGitWorkingBranchResponse,
  MlflowDestinationEntry as GeneratedMlflowDestinationEntry,
  MlflowTestConnectionResponse as GeneratedMlflowTestConnectionResponse,
  NodeDataStatusResponse as GeneratedNodeDataStatusResponse,
  PreviewNodeResponse as GeneratedPreviewNodeResponse,
  TraceResponse as GeneratedTraceResponse,
  WriteOutputResponse as GeneratedWriteOutputResponse,
  OptimiserFrontierAutoRangeStatusResponse as GeneratedOptimiserFrontierAutoRangeStatusResponse,
  OptimiserFrontierResponse as GeneratedOptimiserFrontierResponse,
  OptimiserSolveResponse as GeneratedOptimiserSolveResponse,
  OptimiserSolveResult as GeneratedOptimiserSolveResult,
  OptimiserStatusResponse as GeneratedOptimiserStatusResponse,
  TrainEstimateResponse as GeneratedTrainEstimateResponse,
  TrainResponse as GeneratedTrainResponse,
  TrainStatusResponse as GeneratedTrainStatusResponse,
} from "../generated/api-contracts.generated"
import type {
  ExecutionMemoryPressureEventPayload,
  ExecutionMetricsPayload,
  ExecutionStrategyBoundaryCollectionPayload as GeneratedExecutionStrategyCollection,
  ExecutionStrategyBoundaryPayload as GeneratedExecutionStrategyBoundary,
  ExecutionStrategyDiagnosticPayload as GeneratedExecutionStrategyDiagnostic,
  ExecutionStrategyProjectionCausePayload as GeneratedExecutionStrategyProjectionCause,
  ExecutionStrategyProvenancePayload as GeneratedExecutionStrategyProvenance,
  ExecutionStrategyReasonPayload as GeneratedExecutionStrategyReason,
} from "../generated/api-contracts.generated"
export type { BackendNodeStatus, ColumnInfo, NodeStatus, PipelineGraph } from "../types/node"
export type { TraceResult, TraceStep, TraceSchemaDiff } from "../types/trace"

export interface EditorIdentityRequestNode {
  node_id: string
  label: string
  node_type: NodeTypeValue
  source_handles: string[]
  alias?: string
  instance_of?: string
  /** The submodel definition the node belongs to in the naming context; absent at the root. */
  submodel?: string
}

export interface EditorIdentityBatchRequest {
  nodes: EditorIdentityRequestNode[]
  /** The document's naming context, as save receives it; the response then lists its violations. */
  graph?: ReturnType<typeof toCanonicalGraphPayload> & { preserved_blocks?: string[] }
  /** Give each node, in order, the first free name instead of reporting a collision. */
  allocate?: boolean
}

// Editor identities, Polars step rendering and pipeline settings are generated.
export type {
  EditorIdentitiesResponse as EditorIdentityBatchResponse,
  EditorIdentityResponseNode as EditorNodeIdentity,
  PipelineSettingsAutomatic,
  PipelineSettingsResponse,
  PipelineSettingsValues,
  PolarsFreeCodeColumnsResponse,
  PolarsStepsRenderResponse,
} from "../generated/api-contracts.generated"
export type ExecutionMemoryPressureEvent = ExecutionMemoryPressureEventPayload

export type ExecutionStrategyStatus = GeneratedExecutionStrategyDiagnostic["status"]
export type ExecutionStrategy = GeneratedExecutionStrategyDiagnostic["strategy"]
export type ExecutionStrategyProfile = GeneratedExecutionStrategyDiagnostic["profile"]
export type ExecutionStrategyBoundedness = GeneratedExecutionStrategyDiagnostic["boundedness"]
export type ExecutionStrategyDetailState = GeneratedExecutionStrategyDiagnostic["detail_state"]

export type ExecutionStrategyBoundary = GeneratedExecutionStrategyBoundary

export type ExecutionStrategyReason = GeneratedExecutionStrategyReason & {
  topological_rank: number | null
  node_id: string | null
  operator: string | null
}

export type ExecutionStrategyProvenance = GeneratedExecutionStrategyProvenance

export type ExecutionStrategyProjectionCause = GeneratedExecutionStrategyProjectionCause

export type ExecutionStrategyBoundedCollection<T> = {
  state: GeneratedExecutionStrategyCollection["state"]
  total_count: GeneratedExecutionStrategyCollection["total_count"]
  items: T[]
  [key: string]: unknown
}

export type ExecutionStrategyDiagnostic = GeneratedExecutionStrategyDiagnostic & {
  boundaries: ExecutionStrategyBoundedCollection<ExecutionStrategyBoundary>
  reasons: ExecutionStrategyBoundedCollection<ExecutionStrategyReason>
  provenance: ExecutionStrategyBoundedCollection<ExecutionStrategyProvenance>
}

/**
 * The execution metrics a response carries, checked through the shared execution-metrics
 * validator, with the strategy diagnostic in the shape `parseExecutionStrategyDiagnostic` gives it.
 */
export type ExecutionMetrics = Omit<ExecutionMetricsPayload, "execution_strategy"> & {
  execution_strategy: ExecutionStrategyDiagnostic | null
}

// The preview, the inputs it reads and its progress are generated; its execution metrics
// are checked through the shared module.
export type PreviewNodeResponse = Omit<GeneratedPreviewNodeResponse, "execution_metrics"> & {
  execution_metrics: ExecutionMetrics | null
}

export type {
  NodeMemoryInfo as NodeMemory,
  NodeTimingInfo as NodeTiming,
  JsonCacheInferResponse,
  PortPreview,
  PreviewInputsResponse,
  PreviewProgressResponse,
  PreviewSeedPlanEntry,
  SchemaResponse as SchemaResult,
  SchemaWarning,
} from "../generated/api-contracts.generated"

export interface SavePipelineResponse {
  status?: string
  file: string
  pipeline_name: string
  warnings?: string[]
  /** SHA of the ledger commit this save produced, or null when no working
   *  branch is configured. Updates the saved comparison anchor. */
  git_sha?: string | null
  source_revision: string
  /** The saved document's fingerprint, as live sync names it. */
  document_fingerprint: string
  /** True when version capture was skipped only because git has no commit
   *  identity. The app prompts for a name/email, then captures what was skipped. */
  identity_required?: boolean
  /** The files, relative to the project, the save's capture skipped or failed on. */
  uncaptured?: string[]
}

/** One generation a trace reads: an entry of the preview's `seed_plan`. */
export interface TraceSeedPlanEntry {
  node_id: string
  port_label: null
  identity_digest: string
  generation_id: string
}

export interface SubmodelCreateResponse {
  status: string
  submodel_file: string
  parent_file: string
  graph: PipelineGraph
  source_revision: string
}

export interface NodeInstanceCreateResponse {
  status: string
  graph: PipelineGraph
  owner_name: string
  instance_name: string
  definition_id: string
  source_revision: string
}

export interface SubmodelGraphResponse {
  status: string
  submodel_name: string
  /** Canonical transport graph; it does not contain editor identity metadata. */
  graph: PipelineGraph
  submodel_file: string
  definition_id: string
}

export interface DissolveSubmodelResponse {
  status: string
  graph: PipelineGraph
  source_revision: string
  instance_id: string
  definition_id: string
}

export type TraceResponse = Omit<GeneratedTraceResponse, "trace"> & {
  trace: import("../types/trace").TraceResult
}

// The output write, its destination and the OUTPUT dry run are generated.
export type WriteOutputResponse = Omit<GeneratedWriteOutputResponse, "execution_metrics"> & {
  execution_metrics: ExecutionMetrics | null
}

export type {
  OutputAssembleDryRunResponse,
  OutputDestinationResponse,
} from "../generated/api-contracts.generated"

// ---------------------------------------------------------------------------
// Data In/Out capabilities (GET /api/io-capabilities)
// ---------------------------------------------------------------------------

/**
 * One format's capabilities from the dataInput/dataOutput registry.
 * Mirrors `IoFormatCapability` in `src/haute/schemas.py` so the frontend
 * never hard-codes registry knowledge: its grouping, input/output modes,
 * accepted arguments, execution guarantees, and unavailable engines.
 */
export type {
  IoCapabilitiesResponse,
  IoCapabilityGroup,
  IoFieldCapability,
  IoFormatCapability,
  IoInputCapability,
  IoOutputCapability,
} from "../generated/api-contracts.generated"

// ---------------------------------------------------------------------------
// The workbench (GET /api/workbench, GET /api/workbench/tables, GET and PUT
// /api/workbench/form, whose form is the file's canonical shape, and GET
// /api/workbench/params, a params file's header and a column's values)
// ---------------------------------------------------------------------------

export type {
  ChartWidget,
  CollectionWidget,
  ColumnLook,
  ColumnName,
  Condition,
  ConditionGroup,
  CsvExport,
  CsvImport,
  ExcelExport,
  ExcelImport,
  ExportWidget,
  FieldRef,
  FormSchema,
  FormSpec,
  HeadingWidget,
  ImportWidget,
  OptionsSource,
  Page,
  QueryParameter,
  RowCount,
  RowsCondition,
  RowTest,
  RowTotal,
  SchemaColumn,
  SchemaTable,
  Section,
  ShowWhen,
  SqlExport,
  SqlImport,
  TableInputWidget,
  TableName,
  Theme,
  WorkbenchAppFormResponse,
  WorkbenchAppPeopleResponse,
  WorkbenchAppPriceRequest,
  WorkbenchAppPriceResponse,
  WorkbenchAppQuote,
  WorkbenchAppQuoteActivityEntry,
  WorkbenchAppQuoteActivityResponse,
  WorkbenchAppQuoteAmend,
  WorkbenchAppQuoteAssign,
  WorkbenchAppQuoteChange,
  WorkbenchAppQuoteChangeResponse,
  WorkbenchAppQuoteCreateRequest,
  WorkbenchAppQuoteNote,
  WorkbenchAppQuoteResponse,
  WorkbenchAppQuoteSaveRequest,
  WorkbenchAppQuoteSource,
  WorkbenchAppQuoteStore,
  WorkbenchAppQuotesResponse,
  WorkbenchAppRelatedQuote,
  WorkbenchAppReleaseResponse,
  WorkbenchCatalogueColumn,
  WorkbenchCatalogueTable,
  WorkbenchColumn,
  WorkbenchFormResponse,
  WorkbenchFormSaveResponse,
  WorkbenchParamFileResponse,
  WorkbenchStatusResponse,
  WorkbenchStoreCatalogueResponse,
  WorkbenchStoreColumn,
  WorkbenchStoreCompareResponse,
  WorkbenchStoreDifference,
  WorkbenchStoreExtra,
  WorkbenchStoreRename,
  WorkbenchStoreSqlEnvironment,
  WorkbenchStoreSqlResponse,
  WorkbenchTable,
  WorkbenchTablesResponse,
  WorkbenchTestQuoteMove,
  WorkbenchTestQuoteOpenResponse,
  WorkbenchTestQuoteRefusal,
  WorkbenchTestQuoteRunResponse,
  WorkbenchTestQuotesAcceptResponse,
  WorkbenchTestQuotesResponse,
  WorkbenchTestQuoteSummary,
  WorkbenchTestQuoteTarget,
  WorkbenchTestQuoteWriteResponse,
} from "../generated/api-contracts.generated"

// ---------------------------------------------------------------------------
// Cache-inventory contracts (/api/cache)
// ---------------------------------------------------------------------------

// The cache-inventory responses are generated.
export type {
  CacheClearResponse,
  CacheNodeEntry,
  CacheNodesResponse,
  CacheOwnerEntry,
  CacheUsageResponse,
} from "../generated/api-contracts.generated"

// ---------------------------------------------------------------------------
// Input-cache contracts (/api/input-cache)
// ---------------------------------------------------------------------------

export interface InputCacheSourceRequest {
  schema_version: 1
  /** A structured API Input acts on every emitting table of the node together. */
  node_type?: "dataInput" | "apiInput"
  config: Record<string, unknown>
}

/** Start or join a snapshot build; the server chooses how it is built. */
export interface InputCacheBuildRequest extends InputCacheSourceRequest {
  refresh: boolean
}

// The input-cache responses are generated.
export type {
  InputCacheBuildResponse,
  InputCacheCancelResponse,
  InputCacheGenerationPayload as InputCacheGeneration,
  InputCacheJobStatusResponse,
  InputCacheProgress,
  InputCacheSnapshotStatusResponse as InputCacheSnapshotResponse,
  InputCacheTableStatus,
} from "../generated/api-contracts.generated"

// ---------------------------------------------------------------------------
// Graph payload — internal to the API client layer
// ---------------------------------------------------------------------------

import type { Node } from "@xyflow/react"
import type { GlobalConstant } from "../utils/globalConstants"

/** Graph payload accepted by most pipeline endpoints. */
export type GraphPayload = {
  nodes: Node[]
  edges: PipelineEdge[]
  submodels?: Record<string, unknown>
  preamble?: string
  global_constants?: GlobalConstant[]
  global_constants_error?: string | null
}

// ---------------------------------------------------------------------------
// OUTPUT assemble dry-run (/api/output-assemble/dry-run)
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Modelling types
// ---------------------------------------------------------------------------

// Generated from the backend response models (scripts/generate_api_contracts.py).
export type {
  DispersionEstimateResponse as DispersionEstimateStart,
  DispersionEstimateStatusResponse as DispersionEstimateStatus,
  EvaluationDateRangePayload as EvaluationDateRange,
  EvaluationPreviewPayload as EvaluationPreview,
  GpuFamilyStatus,
  LogExperimentResponse,
  ModellingGpuStatusResponse,
  ModelSaveDestinationResponse,
  SaveModelResponse,
} from "../generated/api-contracts.generated"
export type {
  MlflowDestinationEntry,
  MlflowDestinationsResponse,
  MlflowExperimentSummary as MlflowExperiment,
  MlflowModelSummary as MlflowModel,
  MlflowModelVersionSummary as MlflowModelVersion,
  MlflowRunSummary as MlflowRun,
  MlflowSettingsResponse,
  MlflowTestConnectionResponse,
} from "../generated/api-contracts.generated"

/** The three tracking destinations; a request's `""` means the local folder. */
export type MlflowDestinationKey = GeneratedMlflowDestinationEntry["key"]

export type MlflowProbeCategory = GeneratedMlflowTestConnectionResponse["category"]

export interface MlflowSettingsUpdateRequest {
  tracking_uri: string
  folder: string
}

export interface MlflowTestConnectionRequest {
  /** Empty destination probes the local folder. */
  destination: "" | MlflowDestinationKey
  tracking_uri?: string
  folder?: string
}

/** Why a training estimate cannot size its input: one reason from a closed set. */
export type TrainEstimateUnavailable =
  | { reason: "row_count_unprovable"; blocking_node_id: string }
  | { reason: "schema_unresolvable"; blocking_node_id: null }

/**
 * The generated estimate with its cross-field rules applied by
 * `parseTrainEstimateResponse`: the reason is the discriminated union above,
 * and an omitted evaluation preview is null. Memory figures are null exactly
 * when `unavailable` is set; `total_rows` only for `row_count_unprovable`.
 */
export type TrainEstimate = Omit<GeneratedTrainEstimateResponse, "unavailable" | "evaluation_preview"> & {
  unavailable: TrainEstimateUnavailable | null
  evaluation_preview: EvaluationPreview | null
}

export type DispersionParam = "theta" | "var_power"

export interface TrainFeatureImportanceRow {
  feature: string
  importance: number
}

export interface TrainDoubleLiftRow {
  decile: number
  actual: number
  predicted: number
  count: number
}

export interface TrainShapSummaryRow {
  feature: string
  mean_abs_shap: number
}

export interface TrainAveBin {
  label: string
  exposure: number
  avg_actual: number
  avg_predicted: number
}

export interface TrainAvePerFeatureRow {
  feature: string
  type: string
  bins: TrainAveBin[]
}

export interface TrainResidualHistogramRow {
  bin_center: number
  count: number
  weighted_count: number
}

export interface ActualVsPredictedRow {
  actual: number
  predicted: number
  weight: number
}

export interface LorenzCurvePoint {
  cum_weight_frac: number
  cum_actual_frac: number
}

export interface PdpGridPoint {
  value: number | string | null
  avg_prediction: number
}

export interface PdpFeatureRow {
  feature: string
  type: string
  grid: PdpGridPoint[]
  error?: string
  error_type?: string
}

/** Inference fields are null when `glm_inference.valid` is false. */
export interface GlmCoefficientRow {
  feature: string
  coefficient: number
  std_error: number | null
  z_value: number | null
  p_value: number | null
  significance: string | null
}

/** Bounds are null when inference is not valid. */
export interface GlmRelativityRow {
  feature: string
  relativity: number
  ci_lower: number | null
  ci_upper: number | null
}

export interface GlmInference {
  /** RustyStats' inference status, or `singular_design`. */
  status: string
  valid: boolean
  /** `model` or the robust type (HC0 to HC3) when valid; null otherwise. */
  standard_errors: string | null
  /** Why statistics are unavailable; null when valid. */
  reason: string | null
}

export interface GlmSmoothTerm {
  term: string
  k: number
  edf: number
  lambda: number
}

export interface GlmRegularization {
  penalty: "ridge" | "lasso" | "elastic_net"
  mode: "cross_validation" | "fixed"
  /** The penalty RustyStats applied. */
  alpha: number
  l1_ratio: number | null
  n_nonzero: number
  cv_folds: number | null
  cv_selection: string | null
  cv_seed: number | null
}

export interface TrainDiagnosticsError {
  diagnostic: string
  error: string
  error_type: string
}

export interface TrainFeatureSelectionExcludedColumn {
  column: string
  reason: "target" | "weight" | "offset" | "fold" | "identifier" | "evaluation" | "not_selected" | "not_in_formula"
}

export interface TrainFeatureSelectionCollection<T> {
  state: "available" | "truncated"
  total_count: number
  items: T[]
}

export interface TrainFeatureSelection {
  schema_version: 1
  mode: "explicit" | "glm_terms"
  feature_count: number
  detail_state: "available" | "truncated"
  features: TrainFeatureSelectionCollection<string>
  retained_metadata: TrainFeatureSelectionCollection<TrainFeatureSelectionExcludedColumn>
  excluded_columns: TrainFeatureSelectionCollection<TrainFeatureSelectionExcludedColumn>
}

/** One axis of an EBM term: the missing bin first, then categories or value bins. */
export interface EbmTermAxis {
  feature: string
  type: "nominal" | "continuous"
  labels: string[]
  cuts?: number[]
}

/** An EBM main effect (scores per bin) or pairwise interaction (a score grid). */
export interface EbmTerm {
  term: string
  features: string[]
  kind: "main" | "interaction"
  importance: number
  axes: EbmTermAxis[]
  scores: number[] | number[][]
}

/** A table's values nested one array level per axis, in axis order. */
export type NestedNumbers = number | NestedNumbers[]

/** One rating table of a t-boost model; ``scores`` are link-scale. */
export interface TBoostTable {
  term: string
  features: string[]
  order: number
  importance: number
  axes: EbmTermAxis[]
  scores: NestedNumbers[]
  /** ``exp`` of the scores under a log link, else null. */
  relativities: NestedNumbers[] | null
  /** Training mass per cell: the sum of weight times offset. */
  support: NestedNumbers[]
}

/** An effect over t-boost's dense-cell budget, which has no table. */
export interface TBoostFactoredEffect {
  term: string
  features: string[]
  importance: number
}

/** A t-boost model's rating tables, ranked by importance. */
export interface TBoostTables {
  link: "identity" | "log" | "logit"
  base_value: number
  tables: TBoostTable[]
  factored: TBoostFactoredEffect[]
}

// The training contracts are generated (scripts/generate_api_contracts.py).
export type {
  EvaluationFitPayload as EvaluationFit,
  EvaluationMetricSummaryPayload as EvaluationMetricSummary,
  EvaluationReportPayload as EvaluationReport,
  EvaluationSummaryPayload as EvaluationSummary,
  MlflowExportReceipt,
  ModelFileExportReceipt,
  TrainExportReceipts,
  TrainShapBeeswarmFeature,
  TrainShapCurveFeature,
  TrainShapCurvePoint,
  TuningReportPayload as TuningReport,
  TuningTrialPayload as TuningTrial,
} from "../generated/api-contracts.generated"

/**
 * Fields the server model leaves as open objects (diagnostic rows, loss history)
 * or that the UI reshapes (feature selection); `parseTrainResponse` gives them
 * these shapes on top of the generated structure.
 */
type TrainResponseUiFields = {
  feature_importance: TrainFeatureImportanceRow[]
  loss_history: Array<{ iteration: number; [key: string]: number }>
  validation_loss_history: Array<{ iteration: number; [key: string]: number }>
  double_lift: TrainDoubleLiftRow[]
  shap_summary: TrainShapSummaryRow[]
  feature_importance_loss: TrainFeatureImportanceRow[]
  ave_per_feature: TrainAvePerFeatureRow[]
  residuals_histogram: TrainResidualHistogramRow[]
  actual_vs_predicted: ActualVsPredictedRow[]
  lorenz_curve: LorenzCurvePoint[]
  lorenz_curve_perfect: LorenzCurvePoint[]
  pdp_data: PdpFeatureRow[]
  glm_coefficients: GlmCoefficientRow[]
  glm_relativities: GlmRelativityRow[]
  glm_inference: GlmInference | null
  glm_smooth_terms: GlmSmoothTerm[]
  glm_regularization: GlmRegularization | null
  ebm_terms: EbmTerm[]
  tboost_tables: TBoostTables | null
  diagnostics_errors: TrainDiagnosticsError[]
  feature_selection: TrainFeatureSelection | null
}

export type TrainResponse = Omit<GeneratedTrainResponse, keyof TrainResponseUiFields> & TrainResponseUiFields

type TrainStatusUiFields = {
  train_loss_history: Array<{ iteration: number; [key: string]: number }>
  result: TrainResponse | null
  execution_metrics: ExecutionMetrics | null
  feature_selection: TrainFeatureSelection | null
}

export type TrainStatusResponse = Omit<GeneratedTrainStatusResponse, keyof TrainStatusUiFields> & TrainStatusUiFields

// ---------------------------------------------------------------------------
// Explore types
// ---------------------------------------------------------------------------

/** Per-column statistics surfaced in the Schema overview card. */
export type ExploreColumnKind = "Numeric" | "Text" | "Temporal" | "Boolean" | "Nested" | "Other"

// The profile's column and overview statistics are generated with the node-data
// responses that carry them.
export type {
  ExploreCategoricalColumnProfile,
  ExploreColumnStat,
  ExploreDataQualityIssue,
  ExploreDataQualitySummary,
  ExploreDistinctValueCount,
  ExploreHistogram,
  ExploreHistogramBin,
  ExploreOverviewSummary,
} from "../generated/api-contracts.generated"

/** Lightweight descriptor of a materialised Explore cache entry. */
export const NODE_DATA_POINT_KINDS = ["data_input", "api_input_table", "node_output"] as const

export type NodeDataPointKind = (typeof NODE_DATA_POINT_KINDS)[number]

export const NODE_DATA_POINT_STATES = [
  "current",
  "stale",
  "partial",
  "missing",
  "building",
  "corrupt",
] as const

export type NodeDataPointState = (typeof NODE_DATA_POINT_STATES)[number]

/** A generation's column set: every column, or exactly the named ones. */
export type NodeDataColumns = "all" | string[]

export type NodeDataRetention = "pinned" | "automatic"

// The node-data responses are generated, and with them the point the banding,
// rating-level and data-profile responses carry.
export type {
  BandingHistogramBin,
  BandingStatsResponse,
  BandingValueCount,
  NodeDataClearResponse,
  NodeDataGeneration,
  NodeDataJob,
  NodeDataPointRef,
  NodeDataPointResponse,
  NodeDataProfile,
  NodeDataProfileResponse,
  NodeDataRunResponse,
  RatingLevelColumn,
  RatingLevelValue,
  RatingLevelsResponse,
} from "../generated/api-contracts.generated"

export type NodeDataStatusResponse = Omit<GeneratedNodeDataStatusResponse, "execution_metrics"> & {
  execution_metrics: ExecutionMetrics | null
}

export type ExplorePivotMemberKind =
  | "null"
  | "string"
  | "boolean"
  | "integer"
  | "float"
  | "nan"
  | "date"
  | "datetime"
  | "time"
  | "decimal"

export type ExplorePivotMemberKey =
  | { kind: "null" | "nan"; value: null }
  | { kind: "string" | "integer" | "date" | "datetime" | "time" | "decimal"; value: string }
  | { kind: "boolean"; value: boolean }
  | { kind: "float"; value: number }

export type {
  ExplorePivotCell,
  ExplorePivotFailure,
  ExplorePivotValueIdentity,
} from "../generated/api-contracts.generated"

// The pivot responses are generated; the UI narrows each member key to the
// value its kind carries and parses execution metrics with the shared parser.
export type ExplorePivotPath = Omit<GeneratedExplorePivotPath, "members"> & {
  members: ExplorePivotMemberKey[]
}

export type ExplorePivotMemberOption = Omit<GeneratedExplorePivotMemberOption, "key"> & {
  key: ExplorePivotMemberKey
}

export type ExplorePivotResult = Omit<
  GeneratedExplorePivotResult,
  "row_paths" | "column_paths" | "execution_metrics"
> & {
  row_paths: ExplorePivotPath[]
  column_paths: ExplorePivotPath[]
  execution_metrics: ExecutionMetrics | null
}

export type ExplorePivotRunResponse = Omit<GeneratedExplorePivotRunResponse, "result"> & {
  result: ExplorePivotResult | null
}

export type ExplorePivotStatusResponse = Omit<
  GeneratedExplorePivotStatusResponse,
  "result" | "execution_metrics"
> & {
  result: ExplorePivotResult | null
  execution_metrics: ExecutionMetrics | null
}

export type ExplorePivotMembersResponse = Omit<GeneratedExplorePivotMembersResponse, "members"> & {
  members: ExplorePivotMemberOption[]
}

export interface ModelSaveDestinationRequest {
  /** A bare filename saves under models/; paths are project-root-relative. */
  output_path: string
  algorithm: "catboost" | "glm" | "xgboost" | "lightgbm" | "ebm" | "tboost"
}

export interface SaveModelRequest {
  job_id: string
  /** A bare filename saves under models/; paths are project-root-relative. */
  output_path: string
  /** Replace an existing destination; without it the server answers 409. */
  overwrite: boolean
}

// ---------------------------------------------------------------------------
// Optimiser types
// ---------------------------------------------------------------------------

// The optimiser responses are generated, frontier points and factor-table rows
// included; the status responses parse execution metrics with the shared parser.
export type {
  OptimiserApplyResponse as ApplyOptimiserResponse,
  OptimiserAdjustmentBar,
  OptimiserAdjustmentReport,
  OptimiserAdjustmentWeighting,
  OptimiserDiagnosticError,
  OptimiserEffectiveBound,
  OptimiserFactorTableRow,
  OptimiserInputSummary,
  OptimiserOnlineFrontierPoint,
  OptimiserQuoteColumn,
  OptimiserRatebookCdTrace,
  OptimiserRatebookCdTraceRecord,
  OptimiserRatebookFrontierPoint,
  OptimiserEstimateResponse as OptimiserEstimate,
  OptimiserFrontierAutoRangeResponse as FrontierAutoRangeResponse,
  OptimiserFrontierAutoRangeStartResponse as FrontierAutoRangeStartResponse,
  OptimiserFrontierPointSummary as FrontierPointSummary,
  OptimiserFrontierRange as FrontierRange,
  OptimiserFrontierSelectResponse as FrontierSelectResponse,
  OptimiserHistoryEntry,
  OptimiserMlflowLogResponse as MlflowLogResponse,
  OptimiserSaveResponse as SaveOptimiserResponse,
  OptimiserSegmentFigures,
  OptimiserSegmentIndexKey,
  OptimiserSegmentIndexResponse,
  OptimiserSegmentKey,
  OptimiserSegmentRow,
  OptimiserSegmentsResponse,
  OptimiserSolveResponse,
} from "../generated/api-contracts.generated"

export type SolveOptimiserResponse = GeneratedOptimiserSolveResponse

/** The Quotes explorer's filters (OPT-V12), combined with AND; unset ones are null, false or `{}`. */
export interface OptimiserQuoteFilters {
  scenario_value_min: number | null
  scenario_value_max: number | null
  at_range_edge: boolean
  /** `{analysis column: value}`; `null` matches a missing value. */
  analysis_equals: Record<string, string | number | boolean | null>
  deployed_factor_differs: boolean
}

/** A Quotes page's query beyond its target, always fully specified so one query has one cache key. */
export interface OptimiserApplyQuery {
  /** `null` keeps the apply frame's quote order; ties always break by quote id. */
  sort_by: string | null
  descending: boolean
  quote_id_prefix: string | null
  filters: OptimiserQuoteFilters
  offset: number
  limit: number
}

/** Omitting `point_index` pages the job's own solve; a number, that frontier point. */
export interface ApplyOptimiserRequest extends OptimiserApplyQuery {
  job_id: string
  point_index?: number
}

/** Omitting `point_index` publishes the job's own solve; a number, that frontier point. */
export interface SaveOptimiserRequest {
  job_id: string
  output_path: string
  point_index?: number
  /** Artifact version label; blank lets the server derive one. */
  version?: string
  /** Replace an existing file instead of refusing with `optimiser_result_exists`. */
  overwrite?: boolean
  /** The node config changed since the solve; recorded as `stale_at_publish`. */
  stale?: boolean
}

export interface LogOptimiserToMlflowRequest {
  job_id: string
  experiment_name?: string | null
  point_index?: number
  /** `""` logs to the local folder. */
  destination: "" | MlflowDestinationKey
  /** The node config changed since the solve; recorded as `stale_at_publish`. */
  stale?: boolean
}

/** A typed frontier row: price-contour's point, per-constraint columns as maps. */
export type FrontierPoint = GeneratedOptimiserFrontierResponse["points"][number]

export type FrontierResponse = GeneratedOptimiserFrontierResponse

/** A solve's frontier as the results store keeps it. */
export type FrontierData = Omit<FrontierResponse, "status">

export const JOB_STATUS_VALUES = [
  "running",
  "completed",
  "error",
  "cancelled",
  "superseded",
  "timed_out",
  "memory_limited",
  "contract_error",
] as const

export type JobStatus = typeof JOB_STATUS_VALUES[number]

export const FAILED_JOB_STATUSES: ReadonlySet<JobStatus> = new Set([
  "error",
  "cancelled",
  "superseded",
  "timed_out",
  "memory_limited",
  "contract_error",
])

export const TERMINAL_JOB_STATUSES: ReadonlySet<JobStatus> = new Set([
  "completed",
  ...FAILED_JOB_STATUSES,
])

export type FrontierAutoRangeStatusResponse = Omit<
  GeneratedOptimiserFrontierAutoRangeStatusResponse,
  "execution_metrics"
> & {
  execution_metrics: ExecutionMetrics | null
}

export type OptimiserSolveResult = Omit<GeneratedOptimiserSolveResult, "frontier"> & {
  frontier: FrontierResponse | null
}

export type OptimiserStatusResponse = Omit<
  GeneratedOptimiserStatusResponse,
  "result" | "frontier" | "execution_metrics"
> & {
  result: OptimiserSolveResult | null
  frontier: FrontierResponse | null
  execution_metrics: ExecutionMetrics | null
}

// ---------------------------------------------------------------------------
// Databricks types
// ---------------------------------------------------------------------------

// Generated from the backend response models (scripts/generate_api_contracts.py).
export type {
  CatalogItem as DatabricksCatalog,
  CatalogListResponse as DatabricksCatalogsResponse,
  SchemaItem as DatabricksSchema,
  SchemaListResponse as DatabricksSchemasResponse,
  TableItem as DatabricksTable,
  TableListResponse as DatabricksTablesResponse,
  WarehouseItem as DatabricksWarehouse,
  WarehouseListResponse as DatabricksWarehousesResponse,
} from "../generated/api-contracts.generated"

// ---------------------------------------------------------------------------
// MLflow browser types
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// File browsing types
// ---------------------------------------------------------------------------

export type {
  BrowseFilesResponse,
  FileItem as FileListItem,
  ModelFileInspectionResponse,
  ProjectFileDeleteResponse,
  ProjectFileEntry,
  ProjectFileWriteResponse,
  ProjectFolderResponse,
  SessionStatusResponse,
} from "../generated/api-contracts.generated"

/** The Files panel's two places: the pipeline folder's versioned files/, and local data/ and outputs/. */
export type ProjectFilePlace = import("../generated/api-contracts.generated").ProjectFolderResponse["place"]

// ---------------------------------------------------------------------------
// Utility types
// ---------------------------------------------------------------------------

// Generated from the backend response models (scripts/generate_api_contracts.py).
export type {
  UtilityDeleteResponse,
  UtilityFileItem as UtilityFile,
  UtilityListResponse,
  UtilityReadResponse,
  UtilityWriteResponse as UtilityWriteResult,
} from "../generated/api-contracts.generated"

// ---------------------------------------------------------------------------
// Git types
// ---------------------------------------------------------------------------

// Generated from the backend response models (scripts/generate_api_contracts.py).
export type {
  GitArchiveResponse,
  GitBindStorageResponse,
  GitBranchAwayResponse,
  GitBrokenPair,
  GitCiSecrets,
  GitCommitContext,
  GitCommitRef,
  GitCommitResponse,
  GitCreateWorkingBranchResponse,
  GitDeleteBranchResponse,
  GitDeletedBranch,
  GitFastForwardResponse,
  GitFileChange,
  GitForkStorageResponse,
  GitGraphBranch,
  GitGraphEntry,
  GitGraphResponse,
  GitLedgerSave,
  GitLedgerSavesResponse,
  GitManagedBranch,
  GitMilestoneEntry,
  GitMilestonesResponse,
  GitPrefs,
  GitPushResponse,
  GitRemote,
  GitRemoteLeg,
  GitRemotesResponse,
  GitRepairPairResponse,
  GitRestoreResponse,
  GitRestoreVersionResponse,
  GitSetIdentityResponse,
  GitSetWorkingBranchResponse,
  GitStorageBind,
  GitStorageClaim,
  GitStorageSync,
  GitUndeleteResponse,
  GitWorkingBranchesResponse,
  GitWorkingBranchResponse,
  GitUpstreamStatusResponse as GitUpstreamStatus,
  SaveCapture,
} from "../generated/api-contracts.generated"

export type WorkingBranchState = GeneratedGitWorkingBranchResponse["state"]

/** Whether this deployment can durably remember a bound remote at all (§ hosted storage). */
export type StorageState = GeneratedGitWorkingBranchResponse["storage"]

export type SyncState = GeneratedGitStorageSync["state"]

export type SyncFailure = NonNullable<GeneratedGitStorageSync["failure"]>

export type BindState = GeneratedGitStorageBind["state"]

// The 409 advisory bodies of a diverged push and a forking milestone.
export type { GitMilestoneFork, GitPushRejection } from "../generated/api-contracts.generated"
