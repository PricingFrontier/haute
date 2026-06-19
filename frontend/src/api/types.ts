/** Shared API response/request types for the Haute backend. */

// Re-export canonical types from their source locations
import type { ColumnInfo } from "../types/node"
export type { ColumnInfo } from "../types/node"
export type { TraceResult, TraceStep, TraceSchemaDiff } from "../types/trace"

export interface PipelineGraph {
  nodes: import("@xyflow/react").Node[]
  edges: import("@xyflow/react").Edge[]
  pipeline_name?: string | null
  pipeline_description?: string | null
  preamble?: string | null
  source_file?: string | null
  submodels?: Record<string, unknown> | null
  warning?: string | null
  sources?: string[]
  active_source?: string
}

export interface SchemaWarning {
  column: string
  status: string
}

export interface NodeResult {
  status: string
  row_count?: number
  column_count?: number
  columns?: ColumnInfo[]
  available_columns?: ColumnInfo[]
  preview?: Record<string, unknown>[]
  preview_columns?: string[]
  preview_row_count?: number
  preview_row_limit?: number | null
  preview_truncated?: boolean
  error?: string | null
  error_line?: number | null
  timing_ms?: number
  memory_bytes?: number
  timings?: NodeTiming[]
  memory?: NodeMemory[]
  schema_warnings?: SchemaWarning[]
  node_statuses?: Record<string, string>
}

export interface NodeTiming {
  node_id: string
  label: string
  timing_ms: number
}

export interface NodeMemory {
  node_id: string
  label: string
  memory_bytes: number
}

export interface SavePipelineResponse {
  status?: string
  file: string
  pipeline_name: string
  warnings?: string[]
  /** SHA of the ledger commit this save produced, or null when no working
   *  branch is configured. Feeds the toolbar branch/SHA indicator. */
  git_sha?: string | null
}

export interface PreviewNodeResponse extends NodeResult {
  node_id: string
  timings?: NodeTiming[]
  memory?: NodeMemory[]
  node_statuses?: Record<string, string>
}

export interface SubmodelCreateResponse {
  status: string
  submodel_file: string
  parent_file: string
  graph: PipelineGraph
}

export interface SubmodelGraphResponse {
  status: string
  submodel_name: string
  graph: PipelineGraph
}

export interface DissolveSubmodelResponse {
  status: string
  graph: PipelineGraph
}

/** HTTP response envelope for /api/pipeline/trace (wraps TraceResult). */
export interface TraceResponse {
  status: string
  trace?: import("../types/trace").TraceResult
  error?: string
}

export interface SinkResponse {
  status: string
  message?: string
  row_count?: number
  path?: string
  format?: string
}

/** Schema info returned by /api/schema and /api/schema/databricks. */
export interface SchemaResult {
  path: string
  columns: ColumnInfo[]
  row_count: number | null
  row_count_estimated?: boolean
  column_count: number
  preview: Record<string, unknown>[]
}

// ---------------------------------------------------------------------------
// Graph payload — internal to the API client layer
// ---------------------------------------------------------------------------

import type { Node, Edge } from "@xyflow/react"

/** Graph payload accepted by most pipeline endpoints. */
export type GraphPayload = { nodes: Node[]; edges: Edge[]; submodels?: Record<string, unknown>; preamble?: string }

// ---------------------------------------------------------------------------
// Modelling types
// ---------------------------------------------------------------------------

export interface MlflowCheckResponse {
  mlflow_installed: boolean
  mlflow_importable?: boolean
  tracking_configured?: boolean
  backend: string
  databricks_host: string
  detail?: string
}

export interface TrainEstimate {
  total_rows: number | null
  safe_row_limit: number | null
  estimated_mb: number
  training_mb: number
  available_mb: number
  bytes_per_row: number
  was_downsampled: boolean
  warning: string | null
  // GPU VRAM estimation (only populated when task_type is GPU)
  gpu_vram_estimated_mb: number | null
  gpu_vram_available_mb: number | null
  gpu_warning: string | null
}

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
  value: number | string
  avg_prediction: number
}

export interface PdpFeatureRow {
  feature: string
  type: string
  grid: PdpGridPoint[]
}

export interface GlmCoefficientRow {
  feature: string
  coefficient: number
  std_error: number
  z_value: number
  p_value: number
  significance: string
}

export interface GlmRelativityRow {
  feature: string
  relativity: number
  ci_lower?: number
  ci_upper?: number
}

export interface GlmRegularizationPath {
  selected_alpha?: number
  n_nonzero?: number
}

export interface TrainDiagnosticsError {
  diagnostic: string
  error: string
  error_type: string
}

export interface TrainResponse {
  status: string
  job_id?: string | null
  metrics?: Record<string, number>
  feature_importance?: TrainFeatureImportanceRow[]
  model_path?: string
  train_rows?: number
  test_rows?: number
  holdout_rows?: number
  holdout_metrics?: Record<string, number>
  diagnostics_set?: string
  features?: string[]
  cat_features?: string[]
  error?: string | null
  best_iteration?: number | null
  loss_history?: Array<{ iteration: number; [key: string]: number }>
  double_lift?: TrainDoubleLiftRow[]
  shap_summary?: TrainShapSummaryRow[]
  feature_importance_loss?: TrainFeatureImportanceRow[]
  ave_per_feature?: TrainAvePerFeatureRow[]
  residuals_histogram?: TrainResidualHistogramRow[]
  residuals_stats?: Record<string, number>
  actual_vs_predicted?: ActualVsPredictedRow[]
  lorenz_curve?: LorenzCurvePoint[]
  lorenz_curve_perfect?: LorenzCurvePoint[]
  pdp_data?: PdpFeatureRow[]
  warning?: string | null
  total_source_rows?: number | null
  glm_coefficients?: GlmCoefficientRow[]
  glm_relativities?: GlmRelativityRow[]
  glm_fit_statistics?: Record<string, number>
  glm_regularization_path?: GlmRegularizationPath | null
  diagnostics_errors?: TrainDiagnosticsError[]
}

export interface TrainStatusResponse {
  status: string
  progress: number
  message: string
  iteration: number
  total_iterations: number
  train_loss: Record<string, number>
  elapsed_seconds: number
  result?: TrainResponse | null
  warning?: string | null
}

export interface MlflowLogResponse {
  status: string
  backend: string
  experiment_name: string
  run_id: string | null
  run_url: string | null
  tracking_uri: string
  error: string | null
}

// ---------------------------------------------------------------------------
// Optimiser types
// ---------------------------------------------------------------------------

export interface OptimiserSolveResponse {
  status: string
  job_id: string | null
  error: string | null
}

export type SolveOptimiserResponse = OptimiserSolveResponse

export interface OptimiserEstimate {
  /** Raw ancestor source row count from parquet metadata, or null when unreadable. */
  total_rows: number | null
  /** Distinct quotes in the optimiser input after scenario expansion. */
  quote_count?: number | null
  /** Minimum scenario rows per quote in the optimiser input. */
  scenarios_per_quote_min?: number | null
  /** Maximum scenario rows per quote in the optimiser input. */
  scenarios_per_quote_max?: number | null
  /** Mean scenario rows per quote in the optimiser input. */
  scenarios_per_quote_mean?: number | null
  /** Total rows in the optimiser input after scenario expansion. */
  expanded_row_count?: number | null
}

export interface ApplyOptimiserRequest {
  job_id: string
  point_index?: number
}

export interface ApplyOptimiserResponse {
  status: string
  total_objective: number
  constraints: Record<string, number>
  from_artifact: boolean
  preview: Record<string, unknown>[]
  row_count: number
  preview_row_count: number
  preview_row_limit: number | null
  preview_truncated: boolean
  error: string | null
}

export interface SaveOptimiserRequest {
  job_id: string
  output_path: string
  point_index?: number
}

export interface SaveOptimiserResponse {
  status: string
  path: string | null
  message: string
}

export interface LogOptimiserToMlflowRequest {
  job_id: string
  experiment_name?: string | null
  model_name?: string | null
  point_index?: number
}

export type FrontierPoint = Record<string, unknown> & {
  index?: number
  total_objective?: number
  constraints?: Record<string, number>
  lambdas?: Record<string, number>
}

export interface FrontierResponse {
  status: string
  points: FrontierPoint[]
  n_points: number
  points_returned: number
  constraint_names: string[]
  points_limit: number | null
  points_truncated: boolean
}

export type FrontierData = Omit<FrontierResponse, 'status'>

export interface FrontierRange {
  min: number
  max: number
}

export interface FrontierAutoRangeResponse {
  status: string
  ranges: Record<string, FrontierRange>
  method: string
  warning: string | null
}

export interface OptimiserHistoryEntry {
  iteration: number
  total_objective: number
  max_lambda_change: number
  all_constraints_satisfied?: boolean
  lambdas?: Record<string, number>
  total_constraints?: Record<string, number>
}

export interface OptimiserScenarioValueStats {
  mean: number
  std: number
  min: number
  max: number
  p5: number
  p25: number
  p50: number
  p75: number
  p95: number
  pct_increase: number
  pct_decrease: number
}

export interface OptimiserScenarioValueHistogram {
  counts: number[]
  edges: number[]
}

export interface OptimiserSolveResult {
  mode?: string | null
  total_objective: number
  baseline_objective: number
  constraints: Record<string, number>
  baseline_constraints: Record<string, number>
  lambdas: Record<string, number>
  converged: boolean
  iterations?: number | null
  n_quotes?: number | null
  n_steps?: number | null
  cd_iterations?: number | null
  factor_tables?: Record<string, Record<string, unknown>[]>
  history?: OptimiserHistoryEntry[] | null
  warning?: string | null
  frontier_error?: string | null
  scenario_value_stats?: OptimiserScenarioValueStats
  scenario_value_histogram?: OptimiserScenarioValueHistogram
  clamp_rate?: number | null
  frontier?: FrontierResponse | null
}

export interface OptimiserStatusResponse {
  status: string
  progress: number
  message?: string
  elapsed_seconds: number
  result?: OptimiserSolveResult | null
  frontier?: FrontierResponse | null
}

export interface FrontierSelectResponse {
  status: string
  point_index?: number | null
  total_objective: number
  constraints: Record<string, number>
  baseline_objective: number
  baseline_constraints: Record<string, number>
  lambdas: Record<string, number>
  converged: boolean
  iterations?: number | null
  cd_iterations?: number | null
  factor_tables?: Record<string, Record<string, unknown>[]>
  history?: OptimiserHistoryEntry[] | null
  warning?: string | null
  scenario_value_stats?: OptimiserScenarioValueStats
  scenario_value_histogram?: OptimiserScenarioValueHistogram
  clamp_rate?: number | null
  error: string | null
}

// ---------------------------------------------------------------------------
// Databricks types
// ---------------------------------------------------------------------------

export interface DatabricksWarehouse {
  id: string
  name: string
  http_path: string
  state: string
  size: string
}

export interface DatabricksCatalog {
  name: string
  comment: string
}

export interface DatabricksSchema {
  name: string
  comment: string
}

export interface DatabricksTable {
  name: string
  full_name: string
  table_type: string
  comment: string
}

export interface DatabricksWarehousesResponse {
  warehouses: DatabricksWarehouse[]
}

export interface DatabricksCatalogsResponse {
  catalogs: DatabricksCatalog[]
}

export interface DatabricksSchemasResponse {
  schemas: DatabricksSchema[]
}

export interface DatabricksTablesResponse {
  tables: DatabricksTable[]
}

export interface FetchTableResponse {
  path: string
  table: string
  row_count: number
  column_count: number
  columns: Record<string, string>
  size_bytes: number
  fetched_at: number
  fetch_seconds: number
}

export interface CacheStatusResponse {
  cached: boolean
  path?: string
  table: string
  row_count: number
  column_count: number
  size_bytes: number
  fetched_at: number
  columns?: Record<string, string>
}

export interface FetchProgressResponse {
  active: boolean
  rows?: number
  elapsed?: number
  batches?: number
}

// ---------------------------------------------------------------------------
// JSON cache types
// ---------------------------------------------------------------------------

export interface JsonCacheProgressResponse {
  active: boolean
  rows?: number
  elapsed?: number
  phase?: string
}

export interface JsonCacheBuildResponse {
  path: string
  data_path: string
  row_count: number
  column_count: number
  columns: Record<string, string>
  size_bytes: number
  cached_at: number
  cache_seconds: number
}

export interface JsonCacheStatusResponse {
  cached: boolean
  path?: string
  data_path: string
  row_count: number
  column_count: number
  size_bytes: number
  cached_at: number
  columns?: Record<string, string>
}

// ---------------------------------------------------------------------------
// MLflow browser types
// ---------------------------------------------------------------------------

export interface MlflowExperiment {
  experiment_id: string
  name: string
}

export interface MlflowRun {
  run_id: string
  run_name: string
  metrics: Record<string, number>
  artifacts: string[]
  status?: string
  start_time?: number | null
  params?: Record<string, string>
}

export interface MlflowModel {
  name: string
  latest_versions: { version: string; status: string; run_id: string }[]
}

export interface MlflowModelVersion {
  version: string
  run_id: string
  status: string
  description: string
  params?: Record<string, string>
  creation_timestamp?: number | null
}

// ---------------------------------------------------------------------------
// File browsing types
// ---------------------------------------------------------------------------

export interface FileListItem {
  name: string
  path: string
  type: "file" | "directory"
  size?: number
}

// ---------------------------------------------------------------------------
// Utility types
// ---------------------------------------------------------------------------

export interface UtilityFile {
  name: string
  module: string
}

export interface UtilityListResponse {
  files: UtilityFile[]
}

export interface UtilityReadResponse {
  name: string
  module: string
  content: string
}

export interface UtilityWriteResult {
  status: string
  name: string
  module: string
  import_line: string
  error: string | null
  error_line: number | null
}

export interface UtilityDeleteResponse {
  status: string
  module: string
}

// ---------------------------------------------------------------------------
// Git types
// ---------------------------------------------------------------------------

export interface GitStatus {
  branch: string
  is_main: boolean
  is_read_only: boolean
  changed_files: string[]
  main_ahead: boolean
  main_ahead_by: number
  main_last_updated?: string | null
}

export type WorkingBranchState = "ready" | "unset" | "invalid" | "divergent"

export interface GitWorkingBranchResponse {
  working_branch: string | null
  state: WorkingBranchState
  errors: string[]
  current_branch: string
  last_save_sha: string | null
  eligible_branches: string[]
  identity_set: boolean
  user_name: string | null
  user_email: string | null
}

export interface GitSetWorkingBranchResponse {
  working_branch: string
  state: WorkingBranchState
  last_save_sha: string | null
}

export interface GitSetIdentityResponse {
  user_name: string
  user_email: string
  scope: "local" | "global"
}

export interface GitCommitResponse {
  sha: string
  short_sha: string
  working_branch: string
  version_label: string | null
}

export interface GitMilestoneEntry {
  sha: string
  short_sha: string
  message: string
  timestamp: string
  version_label: string | null
}

export interface GitMilestonesResponse {
  working_branch: string | null
  entries: GitMilestoneEntry[]
}

/** A commit referenced in a breadcrumb (the nearest milestone, or a commit). */
export interface GitCommitRef {
  sha: string
  short_sha: string
  message: string
  version_label: string | null
  is_root: boolean
}

/** A commit's breadcrumb context: its nearest ancestor milestone + distance (S11). */
export interface GitCommitContext {
  sha: string
  short_sha: string
  message: string
  timestamp: string
  is_root: boolean
  is_milestone: boolean
  version_label: string | null
  nearest_milestone: GitCommitRef
  distance: number
}

export interface GitFileChange {
  status: string // M | A | D | R | C | T
  path: string
  old_path: string | null
}

export interface GitLedgerSave {
  sha: string
  short_sha: string
  message: string
  timestamp: string
  files: GitFileChange[]
}

export interface GitLedgerSavesResponse {
  saves: GitLedgerSave[]
}

export interface GitManagedBranch {
  name: string
  is_current: boolean
  is_archived: boolean
  has_unmerged_saves: boolean
  has_uncommitted_changes: boolean
  forked_from: string | null
}

export interface GitWorkingBranchesResponse {
  current: string | null
  branches: GitManagedBranch[]
}

export interface GitRestoreResponse {
  restored_as: string
}

export interface GitCreateWorkingBranchResponse {
  working_branch: string
  moved: boolean
  switched: boolean
  last_save_sha: string | null
}

export interface GitPrefs {
  skip_switch_confirm: boolean
}

export interface GitArchiveResponse {
  archived_as: string
}

export interface GitDeleteBranchResponse {
  status: string
  branch: string
}

export interface GitRemote {
  name: string
  url: string | null
  /** Working-branch commits not on this remote (null = no local tracking ref yet). */
  ahead: number | null
  /** Remote commits not in the local working branch (null = no local tracking ref yet). */
  behind: number | null
}

export interface GitRemotesResponse {
  remotes: GitRemote[]
  working_branch: string | null
}

export interface GitPushResponse {
  remote: string
  working_branch: string
  ledger_branch: string
  pushed_refs: string[]
}
