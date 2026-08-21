/** Shared API response/request types for the Haute backend. */

// Re-export canonical types from their source locations
import type { BackendNodeStatus, ColumnInfo } from "../types/node"
import type { PipelineEdge } from "../types/node"
export type { BackendNodeStatus, ColumnInfo, NodeStatus } from "../types/node"
export type { TraceResult, TraceStep, TraceSchemaDiff } from "../types/trace"

export interface PipelineGraph {
  nodes: import("@xyflow/react").Node[]
  edges: PipelineEdge[]
  pipeline_name?: string | null
  pipeline_description?: string | null
  preamble?: string | null
  source_file?: string | null
  submodels?: Record<string, unknown> | null
  warning?: string | null
  sources?: string[]
  active_source?: string
  preserved_blocks?: string[]
  source_revision?: string | null
}
export interface SchemaWarning {
  column: string
  status: string
}

export interface ExecutionStageMetrics {
  schema_version: number
  name: string
  operation: string
  profile: string
  elapsed_ms: number
  node_id: string | null
  job_id: string | null
  rss_start_bytes: number | null
  rss_end_bytes: number | null
  rss_delta_bytes: number | null
  rss_peak_bytes: number | null
  rows_in: number | null
  rows_out: number | null
  bytes_read: number | null
  bytes_written: number | null
  columns_scanned: number | null
  n_collects: number
  n_checkpoints: number
}

export interface ExecutionAdmission {
  admitted: boolean
  operation: string
  profile: string
  memory_limit_bytes: number
  rss_at_admission_bytes: number | null
  rss_limit_bytes: number | null
  process_rss_limit_bytes: number | null
  headroom_bytes: number | null
  config_key: string
  budget_policy: string
  available_ram_bytes: number | null
  os_reserve_bytes: number | null
  reason: string
}

export interface ExecutionMemoryPressureEvent {
  schema_version: number
  event: "memory_pressure"
  operation: string
  profile: string
  job_id: string | null
  node_id: string | null
  stage: string | null
  label: string | null
  threshold_ratio: number
  threshold_percent: number
  rss_bytes: number
  rss_limit_bytes: number
  headroom_bytes: number
  headroom_used_bytes: number
  rss_peak_bytes: number
  memory_limit_bytes: number | null
  memory_baseline_bytes: number | null
  baseline_rss_bytes: number | null
  budget_policy: string | null
  config_key: string | null
  available_ram_bytes: number | null
  os_reserve_bytes: number | null
  pressure_ratio: number
}

export type ExecutionStrategyStatus = "projected" | "admitted_eager" | "boundary" | "rejected" | "not_planned"
export type ExecutionStrategy = "projected" | "schema-all-except" | "full-width-admitted-eager" | "unprojected-streaming-boundary" | "materialisation-boundary" | "unsupported" | "not-planned"
export type ExecutionStrategyProfile = "preview_eager" | "lazy_sink" | "training_prep" | "optimiser_setup" | "explore_analysis" | "auto_range" | "deploy_live" | "deploy_batch" | "chunked_map_reduce"
export type ExecutionStrategyBoundedness = "bounded" | "unbounded" | "unknown"
export type ExecutionStrategyDetailState = "available" | "unavailable" | "truncated"

export interface ExecutionStrategyBoundary {
  topological_rank: number
  node_id: string
  operator: string
  boundary_kind: "unprojected-streaming-boundary" | "materialisation-boundary"
}

export interface ExecutionStrategyReason {
  reason_code: string
  topological_rank: number | null
  node_id: string | null
  operator: string | null
  message?: string | null
  parent_node_id?: string | null
}

export interface ExecutionStrategyProvenance {
  column: string
  origin_kind: "seed" | "contract" | "expression" | "join_key" | "conservative_boundary"
  source_node_id?: string | null
  source_column?: string | null
}

export interface ExecutionStrategyBoundedCollection<T> {
  state: ExecutionStrategyDetailState
  total_count: number | null
  items: T[]
}

export interface ExecutionStrategyDiagnostic {
  schema_version: 1
  status: ExecutionStrategyStatus
  strategy: ExecutionStrategy
  profile: ExecutionStrategyProfile
  boundedness: ExecutionStrategyBoundedness
  reason_code: string
  detail_state: ExecutionStrategyDetailState
  boundaries: ExecutionStrategyBoundedCollection<ExecutionStrategyBoundary>
  reasons: ExecutionStrategyBoundedCollection<ExecutionStrategyReason>
  provenance: ExecutionStrategyBoundedCollection<ExecutionStrategyProvenance>
  blocking_node_id?: string | null
  blocking_operator?: string | null
  remediation?: string | null
  estimated_peak_bytes?: number | null
  raw_estimated_peak_bytes?: number | null
  estimate_calibration_factor_basis_points?: number | null
  estimate_admission_basis?: "provided" | "projected_columns" | "complete_width_fallback" | null
  headroom_bytes?: number | null
  assumptions?: string[]
}

export interface ExecutionStreamabilityEvidence {
  state: "available" | "unavailable" | "truncated"
  total_count: number | null
  items: string[]
}

export interface ExecutionColumnWidth {
  node_id: string
  input_width: number | null
  output_width: number | null
  requested_width: number | null
  physically_scanned_width: number | null
}

export interface ExecutionColumnWidths {
  state: "available" | "truncated"
  total_count: number
  items: ExecutionColumnWidth[]
}

export interface ExecutionCacheProof {
  hits: number
  misses: number
  direct_fallbacks: number
  miss_reason_counts: {
    metadata_source_mismatch: number
    artifact_integrity_schema_failure: number
    unreadable_artifact: number
    proof_unavailable: number
  }
}

export interface ExecutionMetrics {
  schema_version: number
  operation: string
  profile: string
  job_id: string | null
  status: string | null
  terminal_reason: string | null
  stage_count: number
  retained_stage_count: number
  truncated_stage_count: number
  stages_truncated: boolean
  total_elapsed_ms: number
  node_elapsed_ms: Record<string, number>
  stage_elapsed_ms: Record<string, number>
  rss_start_bytes: number | null
  rss_end_bytes: number | null
  rss_delta_bytes: number | null
  rss_peak_bytes: number | null
  max_rss_bytes: number | null
  n_collects: number
  n_checkpoints: number
  memory_pressure_event_count: number
  retained_memory_pressure_event_count: number
  truncated_memory_pressure_event_count: number
  memory_pressure_events_truncated: boolean
  memory_limit_bytes: number | null
  memory_baseline_bytes: number | null
  rss_limit_bytes: number | null
  streamability: "streaming" | "materialising" | null
  streamability_evidence: ExecutionStreamabilityEvidence
  column_widths: ExecutionColumnWidths
  requested_column_width_total: number | null
  physically_scanned_column_width_total: number | null
  cache_proof: ExecutionCacheProof
  bytes_read: number | null
  bytes_written: number | null
  estimated_bytes: number | null
  raw_estimated_bytes: number | null
  estimate_calibration_factor_basis_points: number | null
  estimate_admission_basis: "provided" | "projected_columns" | "complete_width_fallback" | null
  observed_peak_rss_bytes: number | null
  observed_peak_rss_growth_bytes: number | null
  checkpoint_count: number
  chunk_count: number
  admission: ExecutionAdmission | null
  execution_strategy: ExecutionStrategyDiagnostic | null
  stages: ExecutionStageMetrics[]
  memory_pressure_events: ExecutionMemoryPressureEvent[]
}

export interface NodeResult {
  status: BackendNodeStatus
  row_count?: number
  column_count?: number
  columns?: ColumnInfo[]
  available_columns?: ColumnInfo[]
  /** Per-frame column schema for a multi-frame producer (a multi-table
   * apiInput today), keyed by emit-table label. Empty for single-frame
   * nodes; additive to `columns`, never replaces it. */
  frame_columns?: Record<string, ColumnInfo[]>
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
  node_statuses?: Record<string, BackendNodeStatus>
  node_columns?: Record<string, ColumnInfo[]>
  node_available_columns?: Record<string, ColumnInfo[]>
  node_schema_warnings?: Record<string, SchemaWarning[]>
  execution_metrics?: ExecutionMetrics | null
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
   *  branch is configured. Updates the saved comparison anchor. */
  git_sha?: string | null
  source_revision: string
  /** True when version capture was skipped only because git has no commit
   *  identity. The app prompts for a name/email and retries the save. */
  identity_required?: boolean
}

export interface PreviewNodeResponse extends NodeResult {
  node_id: string
  /** Per-frame column schemas for multi-frame producers, keyed
   * node_id → frame label → columns. Only nodes that emit 2+ frames appear;
   * single-frame nodes are absent. Additive to `node_columns`. */
  node_frame_columns?: Record<string, Record<string, ColumnInfo[]>>
}

export interface SubmodelCreateResponse {
  status: string
  submodel_file: string
  parent_file: string
  graph: PipelineGraph
  source_revision: string
}

export interface SubmodelGraphResponse {
  status: string
  submodel_name: string
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

/** HTTP response envelope for /api/pipeline/trace (wraps TraceResult).
 *  The backend (`TraceResponse` in `src/haute/schemas.py`) always returns a
 *  `trace`; failures raise an HTTP error rather than a 200 body — so `trace`
 *  is required and there is no `error` field on the envelope. */
export interface TraceResponse {
  status: string
  trace: import("../types/trace").TraceResult
}

export interface WriteOutputResponse {
  status: string
  message?: string
  row_count?: number
  path?: string
  format?: string
  execution_metrics?: ExecutionMetrics | null
}

export interface OutputDestinationResponse {
  path: string
  format: string
  suffix_mismatch: boolean
}

/** Schema info returned by the schema endpoint. */
export interface SchemaResult {
  path: string
  columns: ColumnInfo[]
  row_count: number | null
  row_count_estimated?: boolean
  column_count: number
  preview: Record<string, unknown>[]
}

// ---------------------------------------------------------------------------
// Data In/Out capabilities (GET /api/io-capabilities)
// ---------------------------------------------------------------------------

/**
 * One format's capabilities from the dataInput/dataOutput registry.
 * Mirrors `IoFormatCapability` in `src/haute/schemas.py` so the frontend
 * never hard-codes registry knowledge: its grouping, input/output modes,
 * accepted arguments, execution guarantees, and unavailable engines.
 */
export interface IoInputCapability {
  modes: ("scan" | "read")[]
  arguments: Record<string, string[]>
  engines_missing: string[]
  cache_mode: "direct" | "snapshot"
  direct_bounded: boolean
  needs_schema_when_bounded: boolean
  snapshot_build: "bounded" | "admitted_eager" | "unsupported"
  cached_read: boolean
}

export interface IoOutputCapability {
  modes: ("sink" | "write")[]
  arguments: Record<string, string[]>
  engines_missing: string[]
  native_sink: boolean
  eager_writer: boolean
  publication: "atomic_file" | "transactional"
}

export interface IoFormatCapability {
  name: string
  label: string
  group: "file" | "database" | "lakehouse" | "inline"
  extensions: string[]
  unstable: boolean
  input: IoInputCapability | null
  output: IoOutputCapability | null
}

export interface IoFieldCapability {
  name: string
  label: string
  kind: "path" | "connection" | "text" | "query" | "table" | "records"
  required: boolean
}

export interface IoCapabilityGroup {
  name: "file" | "database" | "lakehouse" | "databricks" | "inline"
  label: string
  input_available: boolean
  output_available: boolean
  cache_modes: ("direct" | "snapshot")[]
  input_fields: IoFieldCapability[]
  output_fields: IoFieldCapability[]
  formats: IoFormatCapability[]
}

export interface IoCapabilitiesResponse {
  schema_version: 1
  groups: IoCapabilityGroup[]
}

// ---------------------------------------------------------------------------
// Input-cache contracts (/api/input-cache)
// ---------------------------------------------------------------------------

export interface InputCacheSourceRequest {
  schema_version: 1
  config: Record<string, unknown>
}

export interface InputCacheBuildRequest extends InputCacheSourceRequest {
  refresh: boolean
  profile: "preview_eager" | "lazy_sink"
}

export interface InputCacheBuildResponse {
  schema_version: 1
  job_id: string
  identity_digest: string
  status: "running"
  joined: boolean
}

export interface InputCacheProgress {
  phase: "queued" | "building" | "publishing" | "completed" | "failed" | "cancelled"
  rows: number
  batches: number
  bytes: number
  elapsed_seconds: number
}

export interface InputCacheGeneration {
  generation_id: string
  row_count: number
  column_count: number
  columns: Record<string, string>
  size_bytes: number
  created_at: number
  build_class: "bounded" | "admitted_eager" | "unsupported"
}

export interface InputCacheSnapshotResponse {
  schema_version: 1
  identity_digest: string
  state: "missing" | "building" | "ready" | "corrupt" | "failed"
  freshness: "fresh" | "stale" | "unknown"
  generation: InputCacheGeneration | null
}

export interface InputCacheJobStatusResponse {
  schema_version: 1
  job_id: string
  identity_digest: string
  status: JobStatus
  terminal_reason: string | null
  message: string
  refresh: boolean
  build_class: "bounded" | "admitted_eager" | "unsupported"
  progress: InputCacheProgress
  snapshot: InputCacheSnapshotResponse | null
  error_code: string | null
}

export interface InputCacheCancelResponse {
  schema_version: 1
  job_id: string
  cancellation_requested: boolean
  status: JobStatus
}

// ---------------------------------------------------------------------------
// Graph payload — internal to the API client layer
// ---------------------------------------------------------------------------

import type { Node } from "@xyflow/react"

/** Graph payload accepted by most pipeline endpoints. */
export type GraphPayload = { nodes: Node[]; edges: PipelineEdge[]; submodels?: Record<string, unknown>; preamble?: string }

// ---------------------------------------------------------------------------
// OUTPUT assemble dry-run (/api/output-assemble/dry-run)
// ---------------------------------------------------------------------------

/**
 * Response from the OUTPUT assembler dry-run. `document` is the assembled
 * response document (already pruned by the render path); `status` is "ok" or
 * "error" (an assembly that ran but failed surfaces `error` with `status:
 * "error"` and a 200 — transport/validation failures arrive as ApiError).
 */
export interface OutputAssembleDryRunResponse {
  status: string
  document: unknown[]
  row_count: number
  error?: string | null
}

// ---------------------------------------------------------------------------
// Modelling types
// ---------------------------------------------------------------------------

export interface MlflowCheckResponse {
  mlflow_installed: boolean
  mlflow_importable: boolean
  tracking_configured: boolean
  backend: string
  databricks_host: string
  detail?: string
}

export interface EvaluationDateRange {
  start: string
  end: string
}

export interface EvaluationPreview {
  schema_version: 1
  strategy: "random" | "group" | "temporal"
  validation_method: "none" | "single" | "cross_validation"
  development_rows: number
  final_test_rows: number
  validation_fit_count: number
  min_selection_train_rows?: number
  max_selection_train_rows?: number
  min_selection_validation_rows?: number
  max_selection_validation_rows?: number
  development_group_count?: number
  final_test_group_count?: number
  development_date_range?: EvaluationDateRange
  final_test_date_range?: EvaluationDateRange
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
  evaluation_preview: EvaluationPreview | null
}

export type DispersionParam = "theta" | "var_power"

export interface DispersionEstimateStart {
  status: "started"
  job_id: string
}

export interface DispersionEstimateStatus {
  status: JobStatus
  progress: number
  message: string
  elapsed_seconds: number
  param: string | null
  value: number | null
  llf: number | null
  n_fits: number | null
  error: string | null
  terminal_reason: string | null
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
  error?: string
  error_type?: string
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

export interface TrainFeatureSelectionExcludedColumn {
  column: string
  reason: "target" | "weight" | "offset" | "fold" | "identifier" | "evaluation" | "configured_exclusion" | "not_selected" | "not_in_formula"
}

export interface TrainFeatureSelectionCollection<T> {
  state: "available" | "truncated"
  total_count: number
  items: T[]
}

export interface TrainFeatureSelection {
  schema_version: 1
  mode: "explicit" | "all_except" | "glm_terms"
  feature_count: number
  detail_state: "available" | "truncated"
  features: TrainFeatureSelectionCollection<string>
  retained_metadata: TrainFeatureSelectionCollection<TrainFeatureSelectionExcludedColumn>
  excluded_columns: TrainFeatureSelectionCollection<TrainFeatureSelectionExcludedColumn>
}

export interface EvaluationFit {
  schema_version: 1
  fit_index: number
  train_rows: number
  validation_rows: number
  metrics: Record<string, number>
  best_iteration: number | null
}

export interface EvaluationMetricSummary {
  mean: number
  stddev: number
  min: number
  max: number
  fit_count: number
  validation_rows: number
}

export interface EvaluationSummary {
  development_rows: number
  test_rows: number
  validation_fit_count: number
  development_group_count: number | null
  test_group_count: number | null
  development_date_count: number | null
  test_date_count: number | null
}

export interface EvaluationReport {
  schema_version: 1
  strategy: "random" | "group" | "temporal"
  validation_method: "none" | "single" | "cross_validation"
  validation_fit_count: number
  fit_count: number
  development_rows: number
  final_test_rows: number
  selection_fits: EvaluationFit[]
  selection_metrics: Record<string, EvaluationMetricSummary>
  plan_sha256: string
  results_sha256: string
  plan_path: string
  results_path: string
  report_path: string
  summary: EvaluationSummary
}

export interface TuningTrial {
  schema_version: 1
  trial_index: number
  label: "baseline" | "sampled"
  sampled_params: Record<string, unknown>
  resolved_params: Record<string, unknown>
  fits: EvaluationFit[]
  aggregate_metrics: Record<string, number>
  objective: number
  elapsed_seconds: number
}

export interface TuningReport {
  schema_version: 1
  plan_sha256: string
  trials_sha256: string
  evaluation_plan_sha256: string
  metric: string
  direction: "maximize" | "minimize"
  baseline_objective: number
  winner_trial_index: number
  winner_objective: number
  improvement: number
  best_sampled_params: Record<string, unknown>
  final_params: Record<string, unknown>
  final_tree_count: number
  trial_count: number
  trial_fit_count: number
  total_fit_count: number
  trials: TuningTrial[]
  plan_path: string
  trials_path: string
  report_path: string
}

export interface TrainResponse {
  status: "started" | "completed" | "error"
  job_id: string | null
  diagnostic_metrics: Record<string, number>
  final_test_metrics: Record<string, number>
  feature_importance: TrainFeatureImportanceRow[]
  model_path: string
  development_rows: number
  final_test_rows: number
  diagnostics_set: "development" | "final_test"
  features: string[]
  cat_features: string[]
  error: string | null
  best_iteration: number | null
  loss_history: Array<{ iteration: number; [key: string]: number }>
  loss_history_truncated: boolean
  double_lift: TrainDoubleLiftRow[]
  shap_summary: TrainShapSummaryRow[]
  feature_importance_loss: TrainFeatureImportanceRow[]
  ave_per_feature: TrainAvePerFeatureRow[]
  residuals_histogram: TrainResidualHistogramRow[]
  residuals_stats: Record<string, number>
  actual_vs_predicted: ActualVsPredictedRow[]
  lorenz_curve: LorenzCurvePoint[]
  lorenz_curve_perfect: LorenzCurvePoint[]
  pdp_data: PdpFeatureRow[]
  warning: string | null
  total_source_rows: number | null
  glm_coefficients: GlmCoefficientRow[]
  glm_relativities: GlmRelativityRow[]
  glm_fit_statistics: Record<string, number>
  glm_regularization_path: GlmRegularizationPath | null
  diagnostics_errors: TrainDiagnosticsError[]
  feature_selection: TrainFeatureSelection | null
  evaluation?: EvaluationReport
  tuning?: TuningReport
}

export interface TrainStatusResponse {
  status: JobStatus
  progress: number
  message: string
  iteration: number
  total_iterations: number
  train_loss: Record<string, number>
  train_loss_history?: Array<{ iteration: number; [key: string]: number }>
  train_loss_history_truncated?: boolean
  elapsed_seconds: number
  result?: TrainResponse | null
  warning?: string | null
  terminal_reason?: string | null
  error_code?: string | null
  http_status_code?: number | null
  error_detail?: unknown
  execution_metrics?: ExecutionMetrics | null
  feature_selection?: TrainFeatureSelection | null
  phase?: "planning" | "trial_fit" | "trial_complete" | "final_fit" | "publication" | "completed" | null
  trial_index?: number | null
  trial_count?: number | null
  fold_index?: number | null
  fold_count?: number | null
  completed_fits?: number | null
  total_fits?: number | null
  best_objective?: number | null
}

// ---------------------------------------------------------------------------
// Explore types
// ---------------------------------------------------------------------------

/** Per-column statistics surfaced in the Schema overview card. */
export type ExploreColumnKind = "Numeric" | "Text" | "Temporal" | "Boolean" | "Nested" | "Other"

export interface ExploreColumnStat {
  name: string
  dtype: string
  kind: ExploreColumnKind
  null_count: number
  /** Float NaN count — the invalid-numeric bucket, distinct from null. Null for non-float dtypes. */
  nan_count?: number | null
  distinct_count: number | null
  min_value?: string | null
  p25_value?: string | null
  median_value?: string | null
  mean_value?: string | null
  p75_value?: string | null
  max_value?: string | null
  std_value?: string | null
  zero_count?: number | null
  negative_count?: number | null
  unique_ratio: number | null
  is_high_cardinality: boolean
  is_identifier_candidate: boolean
  text_min_length: number | null
  text_mean_length: number | null
  text_max_length: number | null
  temporal_span: string | null
}

export interface ExploreDataQualityIssue {
  severity: "warning" | "danger"
  label: string
  detail: string
}

export interface ExploreDataQualitySummary {
  issue_count: number
  issues: ExploreDataQualityIssue[]
  duplicate_row_count: number | null
  duplicate_ratio: number | null
}

export interface ExploreDistinctValueCount {
  value: string | null
  count: number
}

export interface ExploreCategoricalColumnProfile {
  field: string
  distinct_count: number | null
  expandable: boolean
  values_truncated: boolean
  values: ExploreDistinctValueCount[]
}

export interface ExploreOverviewSummary {
  data_quality: ExploreDataQualitySummary
  categorical_summary: ExploreCategoricalColumnProfile[]
}

/** Lightweight descriptor of a materialised Explore cache entry. */
export interface ExploreCacheReport {
  status: "ok"
  node_id: string
  upstream_node_id: string
  source: string
  dataframe_cache_key: string
  row_count: number
  column_count: number
  generated_at: number
  columns: ExploreColumnStat[]
  overview_summary: ExploreOverviewSummary
  execution_metrics?: ExecutionMetrics | null
}

export interface ExploreRunResponse {
  status: "started" | "running" | "completed"
  job_id?: string | null
  cached: boolean
  message: string
  result?: ExploreCacheReport | null
}

export interface ExploreCacheSnapshotResponse {
  state: "missing" | "current" | "stale"
  message: string
  result?: ExploreCacheReport | null
}

export interface ExploreStatusResponse {
  status: JobStatus
  progress: number
  message: string
  result?: ExploreCacheReport | null
  terminal_reason?: string | null
  execution_metrics?: ExecutionMetrics | null
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

export interface ExplorePivotFailure {
  reason_code: string
  message: string
  remediation: string
  dimensions: Record<string, string | number>
}

export interface ExplorePivotMemberOption {
  key: ExplorePivotMemberKey
  label: string
  count: number
}

export interface ExplorePivotValueIdentity {
  id: string
  field: string
  aggregation: "sum" | "count" | "average" | "min" | "max" | "median" | "distinct_count" | "formula"
}

export interface ExplorePivotPath {
  members: ExplorePivotMemberKey[]
  is_grand_total: boolean
}

export interface ExplorePivotCell {
  row_index: number
  column_index: number
  value_id: string
  value: string | number | boolean | null
}

export interface ExplorePivotResult {
  version: 1
  node_id: string
  pivot_id: string
  source: string
  dataframe_cache_key: string
  calculation_key: string
  row_fields: string[]
  column_fields: string[]
  values: ExplorePivotValueIdentity[]
  row_paths: ExplorePivotPath[]
  column_paths: ExplorePivotPath[]
  cells: ExplorePivotCell[]
  warnings: string[]
  generated_at: number
  execution_metrics: ExecutionMetrics | null
}

export interface ExplorePivotRunResponse {
  status: "started" | "completed" | "cache_required"
  job_id: string | null
  cached: boolean
  message: string
  result: ExplorePivotResult | null
  failure: ExplorePivotFailure | null
}

export interface ExplorePivotStatusResponse {
  status: JobStatus
  progress: number
  message: string
  result: ExplorePivotResult | null
  failure: ExplorePivotFailure | null
  terminal_reason: string | null
  execution_metrics: ExecutionMetrics | null
}

export interface ExplorePivotMembersResponse {
  status: "ok" | "cache_required" | "error"
  field: string | null
  members: ExplorePivotMemberOption[]
  failure: ExplorePivotFailure | null
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
  /** Pollable frontier job handle when `status === "started"`. */
  job_id?: string | null
}

export type FrontierData = Omit<FrontierResponse, 'status'>

export interface FrontierRange {
  min: number
  max: number
}

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

export interface FrontierAutoRangeResponse {
  status: string
  ranges: Record<string, FrontierRange>
  method: string
  warning: string | null
}

export interface FrontierAutoRangeStartResponse {
  status: "started" | "error"
  job_id: string | null
  error: string | null
}

export interface FrontierStatusResponse {
  status: JobStatus
  progress: number
  message: string
  elapsed_seconds: number
  result: FrontierResponse | null
  terminal_reason?: string | null
  error_code?: string | null
  http_status_code?: number | null
  error_detail?: unknown
  execution_metrics?: ExecutionMetrics | null
}

export interface FrontierAutoRangeStatusResponse {
  status: JobStatus
  progress: number
  message: string
  elapsed_seconds: number
  result: FrontierAutoRangeResponse | null
  terminal_reason?: string | null
  error_code?: string | null
  http_status_code?: number | null
  error_detail?: unknown
  execution_metrics?: ExecutionMetrics | null
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
  /** Index of the frontier point the backend auto-selected for this solve,
   *  or null when none. Mirrors `OptimiserSolveResult.selected_frontier_point`
   *  in `src/haute/schemas.py`. */
  selected_frontier_point?: number | null
}

export interface OptimiserStatusResponse {
  status: JobStatus
  progress: number
  message?: string
  elapsed_seconds: number
  result?: OptimiserSolveResult | null
  frontier?: FrontierResponse | null
  terminal_reason?: string | null
  execution_metrics?: ExecutionMetrics | null
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
  skipped_records: number
  skipped_rows: Record<string, number>
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
  skipped_records: number
  skipped_rows: Record<string, number>
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
  size?: number | null
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

export type WorkingBranchState = "git-unavailable" | "no-repository" | "unset" | "detached" | "invalid" | "divergent" | "ready"

/** Whether this deployment can durably remember a bound remote at all (§ hosted storage). */
export type StorageState = "unsupported" | "unbound" | "bound"

export type SyncState = "synced" | "pending" | "failed"

export type SyncFailure = "transport" | "rejected" | "config"

export interface GitStorageSync {
  state: SyncState
  pending: number
  failure: SyncFailure | null
  message: string | null
}

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
  head_sha?: string | null
  /** Whether this deployment can durably remember a bound remote. Optional so
   *  older backends (and existing fixtures) that omit it still type-check; the
   *  parser defaults it to "unsupported" (hide the storage surface). */
  storage?: StorageState
  storage_remote?: string | null
  /** Parent uc:// URL when the bound location is a fork (provenance). */
  storage_forked_from?: string | null
  sync?: GitStorageSync | null
  /** Progress of a bind running in the background. */
  storage_bind?: GitStorageBind | null
}

/** A bind is accepted immediately; the outcome arrives on `storage_bind`. */
export interface GitBindStorageResponse {
  outcome: "pending"
  remote_url: string
  message: string
}

export type BindState = "idle" | "running" | "succeeded" | "failed"

export interface GitStorageBind {
  state: BindState
  outcome: "adopted" | "restart-required" | null
  message: string | null
  /** Set when the bind failed because another app holds the location. */
  claim: GitStorageClaim | null
  remote_url: string | null
}

/** Who holds a uc:// location's lease. */
export interface GitStorageClaim {
  app_name: string
  user: string | null
  refreshed_at: string | null
  message: string
}

export interface GitForkStorageResponse {
  outcome: "forked"
  target_url: string
  parent_url: string
  parent_generation: number
  message: string
}

/** A fork's measured relationship to the parent it was forked from.
 *  `can_fast_forward` is the single predicate the catch-up affordance keys on;
 *  `message` is hand-authored prose safe to render verbatim. */
export interface GitUpstreamStatus {
  parent_url: string
  parent_generation: number
  working: GitRemoteLeg
  ledger: GitRemoteLeg
  can_fast_forward: boolean
  checked_at: string
  message: string
}

export interface GitSetWorkingBranchResponse {
  working_branch: string
  state: WorkingBranchState
  last_save_sha: string | null
}

/** Result of moving to a historical commit (detached checkout, §3.4). */
export interface GitMoveResponse {
  /** The commit now checked out (detached HEAD). */
  sha: string
  short_sha: string
  /** The branch HEAD was on before the move — still reachable (no ref moved). */
  prior_branch: string
  /** Always true: a move leaves HEAD detached with no working branch recorded. */
  is_detached: boolean
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
  /** The repo's initial commit (no parents) — shown with an "init" tag. */
  is_root?: boolean
}

export interface GitMilestonesResponse {
  working_branch: string | null
  entries: GitMilestoneEntry[]
}

/** One commit on a branch's first-parent spine, in the graph payload. */
export interface GitGraphEntry extends GitMilestoneEntry {
  /** Full parent shas; >= 2 means the milestone folds ledger saves (a merge). */
  parents: string[]
}

export interface GitGraphBranch {
  name: string
  is_archived: boolean
  is_current: boolean
  tip_sha: string
  /** Newest spine commit already owned by an earlier-processed branch;
   *  null for the root of its tree. */
  fork_point_sha: string | null
  /** Name of the branch owning that commit; null for the root of its tree. */
  fork_of: string | null
  /** The ledger SAVE this branch was actually spawned from, when that differs
   *  from the fork-point milestone (crystallized / pending-save forks);
   *  ancestry-derived. Anchors the spawn chip on the save row when visible. */
  fork_source_sha: string | null
  /** The parent-spine milestone whose fold contains fork_source_sha — the
   *  milestone that takes credit for the spawn while collapsed. Null when the
   *  source save is still pending (or there is no source). */
  fork_credit_sha: string | null
  /** Spine longer than the requested limit (entries windowed). */
  truncated: boolean
  /** Newest-first first-parent spine, windowed to the limit. */
  entries: GitGraphEntry[]
}

export interface GitGraphResponse {
  working_branch: string | null
  /** Server-computed lane/colour ordering (processing order of the fork forest). */
  order: string[]
  branches: GitGraphBranch[]
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
  /** The latest milestone at this commit (its working-chain anchor). */
  nearest_milestone: GitCommitRef
  /** Commits between that milestone's fold-point and this commit. */
  distance: number
  /** Commits between a caller-supplied base and this commit (the historic↔current
   *  span); null unless commit-context was queried with `?base=`. */
  delta_from_base: number | null
  /** Per-commit push status (nick-dev multi-frame addition). Optional: VC's
   *  commit-context parser/model doesn't populate it, so consumers must treat
   *  it as possibly-absent. */
  pushed?: boolean
  push_error?: string | null
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
}

export interface GitWorkingBranchesResponse {
  current: string | null
  branches: GitManagedBranch[]
}

/** POST /api/git/undelete — restore a trash-preserved deleted pair. */
export interface GitUndeleteResponse {
  status: string
  branch: string
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

/** Divergence of one local branch (working or its ledger) vs its remote-tracking
 *  ref. `status` is the honest tri-state (P7 F2): "untracked" = never pushed here
 *  (NOT in-sync), "unknown" = couldn't read, otherwise the measured state. */
export interface GitRemoteLeg {
  status: "untracked" | "unknown" | "synced" | "ahead" | "behind" | "diverged"
  ahead: number | null
  behind: number | null
}

export interface GitRemote {
  name: string
  url: string | null
  /** Per-leg structured divergence (P7 F6). `working` is the working branch;
   *  `ledger` surfaces the save-history leg — the two-machine accident is here. */
  working: GitRemoteLeg | null
  ledger: GitRemoteLeg | null
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
  default_branch: string
  bootstrapped_default: boolean
}

/** A conflict-free catch-up result (P7 D1/D2): the working pair advanced to the
 *  remote's tips by fast-forward only. `fast_forwarded` lists the refs moved. */
export interface GitFastForwardResponse {
  remote: string
  working_branch: string
  fast_forwarded: string[]
}

/** A branch-away result (P7 M3): the local fork was set aside under a dated name
 *  and the canonical name now tracks the remote. `set_aside_as` is that dated
 *  name (surfaced to the user — S35). */
export interface GitBranchAwayResponse {
  working_branch: string
  set_aside_as: string
}

/** A non-fast-forward push rejection (P7 M7): the body of a 409 from
 *  POST /api/git/push, carrying the per-leg divergence so the UI shows the honest
 *  fork rather than a dead-end string. `ledger` is null when it isn't spawned. */
export interface GitPushRejection {
  status: "rejected_diverged"
  remote: string
  working: GitRemoteLeg
  ledger: GitRemoteLeg | null
  message: string
  /** X3: the remote dropped a published commit (a rebase/force-push upstream),
   *  not an ordinary divergence — the modal says so distinctly. */
  is_rewrite: boolean
}

/** The pre-milestone fork warning (P7 U4/D4): the body of a 409 from
 *  POST /api/git/commit when the working branch is behind its remote, so a
 *  milestone now would branch off the shared copy. Drives the warn + "commit
 *  anyway (creates a fork)" confirm. */
export interface GitMilestoneFork {
  status: "would_fork"
  remote: string
  working: GitRemoteLeg
  message: string
}
