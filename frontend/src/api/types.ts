/** Shared API response/request types for the Haute backend. */

// Re-export canonical types from their source locations
import type {
  BackendNodeStatus,
  ColumnInfo,
  NodeTypeValue,
  PipelineEdge,
  PipelineGraph,
} from "../types/node"
import type {
  EvaluationPreviewPayload as EvaluationPreview,
  GitRemoteLeg as GeneratedGitRemoteLeg,
  GitStorageBind as GeneratedGitStorageBind,
  GitStorageSync as GeneratedGitStorageSync,
  GitWorkingBranchResponse as GeneratedGitWorkingBranchResponse,
  MlflowDestinationEntry as GeneratedMlflowDestinationEntry,
  MlflowTestConnectionResponse as GeneratedMlflowTestConnectionResponse,
  TrainEstimateResponse as GeneratedTrainEstimateResponse,
} from "../generated/api-contracts.generated"
import type {
  ExecutionStrategyBoundaryCollectionPayload as GeneratedExecutionStrategyCollection,
  ExecutionStrategyBoundaryPayload as GeneratedExecutionStrategyBoundary,
  ExecutionStrategyDiagnosticPayload as GeneratedExecutionStrategyDiagnostic,
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
}

export interface EditorIdentityBatchRequest {
  nodes: EditorIdentityRequestNode[]
}

export interface EditorNodeIdentity {
  node_id: string
  function_name: string
  config_reference: string | null
  default_input_name: string | null
  source_handle_input_names: Record<string, string>
}

export interface EditorIdentityBatchResponse {
  identities: EditorNodeIdentity[]
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

export interface InputPreparationRecord {
  node_id: string
  identity_digest: string
  action: "reused" | "built" | "refreshed"
  build_class: string
  execution: "in_process" | "worker"
  memory_limit_bytes: number | null
  elapsed_seconds: number
  row_count: number | null
  size_bytes: number | null
  generation_id: string | null
  warning_code: string | null
}

/** A node output an execution read from a shared snapshot generation. */
export interface SharedSnapshotSeed {
  node_id: string
  identity_digest: string
  generation_id: string
  columns: "all" | string[]
}

/**
 * A full-data materialisation an execution wrote to shared snapshots. Only a
 * `published` capture names the generation it continued from; otherwise the
 * execution continued from its own staged data.
 */
export interface SharedSnapshotCapture {
  node_id: string
  identity_digest: string
  kind: "structural" | "materialising" | "model_score" | "consumed"
  outcome: "published" | "superseded"
  generation_id: string | null
  columns: "all" | string[]
  write_strategy?: "chunked_join" | "sliced" | "input_sliced" | "native" | "prewritten" | null
  write_parts?: number | null
  write_chunk_rows?: number | null
  write_staged_inputs?: number | null
  write_input_slices?: number | null
  write_native_reason?: string | null
  write_blocking_operator?: string | null
}

/** A candidate capture point skipped under cost gating. */
export interface SharedSnapshotCaptureSkip {
  node_id: string
  reason: "cheap_segment" | "slice_transparent_feeder"
}

/** A non-fatal condition an execution continued past. */
export interface ExecutionWarning {
  code: string
  node_id: string | null
  reason: string | null
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
  input_preparation: InputPreparationRecord[]
  shared_snapshot_seeds: SharedSnapshotSeed[]
  shared_snapshot_captures: SharedSnapshotCapture[]
  shared_snapshot_capture_skips: SharedSnapshotCaptureSkip[]
  warnings: ExecutionWarning[]
  training_write_strategy?: string | null
  training_write_input_slices?: number | null
  training_write_native_reason?: string | null
  training_write_blocking_operator?: string | null
  data_output_write_strategy?: string | null
  data_output_write_input_slices?: number | null
  data_output_write_native_reason?: string | null
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
  /** Per-output-handle columns of every multi-frame producer the preview ran. */
  node_frame_columns?: Record<string, Record<string, ColumnInfo[]>>
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

/** One shared-snapshot generation a preview's collected rows were computed from. */
export interface PreviewSeedPlanEntry {
  node_id: string
  /** Always null: only node outputs are seeded or captured. */
  port_label: null
  node_label: string
  identity_digest: string
  generation_id: string
  /** The generation's column set; null means all columns. */
  columns: string[] | null
  /** ISO-8601 UTC. */
  created_at: string
  /** `seeded`: read instead of computing the node. `captured`: computed by
   * this preview, written, and read by everything below it. */
  kind: "seeded" | "captured"
}

export interface PreviewNodeResponse extends NodeResult {
  node_id: string
  /** Per-frame column schemas for multi-frame producers, keyed
   * node_id → frame label → columns. Only nodes that emit 2+ frames appear;
   * single-frame nodes are absent. Additive to `node_columns`. */
  node_frame_columns?: Record<string, Record<string, ColumnInfo[]>>
  /** Every snapshot generation the rows were computed from, in topological
   * order; empty when the preview read no snapshot. */
  seed_plan?: PreviewSeedPlanEntry[]
}

/** One generation a trace reads: an entry of the preview's `seed_plan`. */
export interface TraceSeedPlanEntry {
  node_id: string
  port_label: null
  identity_digest: string
  generation_id: string
}

/** The inputs a preview would read, so only those are prepared before it. */
export interface PreviewInputsResponse {
  input_node_ids: string[]
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
// Cache-inventory contracts (/api/cache)
// ---------------------------------------------------------------------------

/**
 * One node of the graph. `state`/`row_count` describe the generation this node
 * would read for its own columns; `generations`/`size_bytes` are every
 * signature the store still holds for it, including data retained for active readers.
 */
export interface CacheNodeEntry {
  node_id: string
  kind: "data_input" | "api_input_table" | "node_output" | null
  state: "current" | "stale" | "partial" | "missing" | "building" | "corrupt" | null
  reads_directly: boolean
  /** The node this one reads from, when that is another node: its cache is
   *  reported on that node's row, so this row carries no size. */
  reads_from: string | null
  /**
   * Other nodes resolving to the same input snapshot. Every sharer names the
   * others, and exactly one of them carries the bytes — the one with a size.
   */
  shares_snapshot_with: string[]
  row_count: number | null
  generations: number
  size_bytes: number
  newest_created_at: number | null
  /** How long the newest generation took to cache; null when unrecorded. */
  build_seconds: number | null
  /** The store identities this row is responsible for; what clearing it clears. */
  identity_digests: string[]
  retention: "pinned" | "automatic" | null
  /** Why this node has no data point at all — an unwired Banding, say. */
  unavailable_reason: string | null
}

/** Cached data not attributable to any node of the graph as it stands. */
export interface CacheOwnerEntry {
  bucket: "node_output" | "input"
  label: string
  node_id: string | null
  source: string | null
  generations: number
  row_count: number | null
  size_bytes: number
  newest_created_at: number | null
  build_seconds: number | null
  identity_digests: string[]
}

export interface CacheClearResponse {
  schema_version: 1
  cleared: string[]
  freed_bytes: number
}

export interface CacheNodesResponse {
  schema_version: 1
  source: string
  nodes: CacheNodeEntry[]
  other: CacheOwnerEntry[]
  unattributed_generations: number
  unattributed_bytes: number
  /** Identities whose provider marker is missing or unrecognised. */
  unmarked_identities: number
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

export interface ExecutionSettings {
  streaming_chunk_size: number
}

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
  refit_on_development?: boolean
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
  /** Absent for a fixed-budget family (EBM), whose refit reuses the winning budget. */
  final_tree_count?: number
  trial_count: number
  trial_fit_count: number
  total_fit_count: number
  trials: TuningTrial[]
  plan_path: string
  trials_path: string
  report_path: string
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

export interface TrainResponse {
  status: "started" | "completed" | "error"
  job_id: string | null
  diagnostic_metrics: Record<string, number>
  final_test_metrics: Record<string, number>
  feature_importance: TrainFeatureImportanceRow[]
  model_path: string
  development_rows: number
  final_test_rows: number
  diagnostics_set: "development" | "validation" | "final_test"
  features: string[]
  cat_features: string[]
  error: string | null
  best_iteration: number | null
  final_tree_count?: number | null
  fit_evidence?: {
    threads: number
    rounds_configured: number | null
    rounds_fitted: number | null
    /** EBM's native best_iteration_: term updates per stage, never rounds. */
    term_update_steps: number[] | null
    stopping_reason: "none" | "validation" | "native_exhaustion" | null
    /** The device XGBoost actually trained on (``cuda:0``) for a GPU fit. */
    device?: string | null
  } | null
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
  glm_inference: GlmInference | null
  glm_smooth_terms: GlmSmoothTerm[]
  glm_regularization: GlmRegularization | null
  ebm_terms: EbmTerm[]
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
  /** Where the completed result has been exported, oldest first. */
  export_receipts?: TrainExportReceipts
}

export interface MlflowExportReceipt {
  operation_id: string
  /** The request's destination: `""` is the local folder. */
  destination: "" | MlflowDestinationKey
  backend: string
  experiment_name: string
  run_id: string
  run_url: string | null
  tracking_uri: string
  logged_at: string
}

export interface ModelFileExportReceipt {
  path: string
  feature_contract_path: string
  saved_at: string
}

export interface TrainExportReceipts {
  mlflow: MlflowExportReceipt[]
  model_files: ModelFileExportReceipt[]
}

// ---------------------------------------------------------------------------
// Explore types
// ---------------------------------------------------------------------------

/** Per-column statistics surfaced in the Schema overview card. */
export type ExploreColumnKind = "Numeric" | "Text" | "Temporal" | "Boolean" | "Nested" | "Other"

export interface ExploreHistogramBin {
  start: number
  end: number
  count: number
}

/**
 * Equal-width bins over a numeric column's finite values: `ok` bins span the
 * finite minimum to maximum, `constant` has one bin, `empty` has no finite
 * values, and `skipped` is past the profile's histogram column limit or an
 * integer column too large for the browser to hold its boundaries exactly.
 */
export interface ExploreHistogram {
  status: "ok" | "constant" | "empty" | "skipped"
  bins: ExploreHistogramBin[]
  finite_count: number | null
  non_finite_count: number | null
  skipped_reason: "column_limit" | "integer_precision" | null
}

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
  /** Server-binned distribution; null for non-numeric columns. */
  histogram?: ExploreHistogram | null
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

export interface NodeDataPointRef {
  producer_node_id: string
  port_label?: string | null
}

export interface NodeDataGeneration {
  generation_id: string
  columns: NodeDataColumns
  row_count: number
  column_count: number
  size_bytes: number
  retention: NodeDataRetention
  fresh: boolean
  created_at: number
}

export interface NodeDataJob {
  job_id: string
  progress: number
  message: string
}

export interface NodeDataPointResponse {
  consumer_node_id: string
  point: NodeDataPointRef
  slot_key: string
  kind: NodeDataPointKind
  state: NodeDataPointState
  demand: NodeDataColumns
  data_version?: string | null
  row_count?: number | null
  size_bytes?: number | null
  retention?: NodeDataRetention | null
  generation?: NodeDataGeneration | null
  job?: NodeDataJob | null
  reads_directly: boolean
  build_endpoint?: string | null
  clear_endpoint?: string | null
}

export interface NodeDataRunResponse {
  status: "started" | "joined" | "completed" | "delegated"
  job_id?: string | null
  cached: boolean
  message: string
  point: NodeDataPointResponse
}

export interface NodeDataProfile {
  row_count: number
  column_count: number
  columns: ExploreColumnStat[]
  overview_summary: ExploreOverviewSummary
  data_version: string
  generated_at: number
}

export interface BandingHistogramBin {
  lower: number
  upper: number
  count: number
}

export interface BandingValueCount {
  value: string
  count: number
}

/** Whole-dataset statistics for one banding factor, or why there are none. */
export interface BandingStatsResponse {
  status: "ok" | "cache_required"
  point: NodeDataPointResponse
  data_version?: string | null
  total_rows: number
  null_count: number
  /** Numeric modes: values no bin can hold, and the extent of those it can. */
  non_finite_count?: number | null
  minimum?: number | null
  maximum?: number | null
  bins: BandingHistogramBin[]
  /** Categorical mode. */
  values: BandingValueCount[]
  distinct_count?: number | null
  other_count?: number | null
  /** Counts aligned to the user's rules, and the rows no rule claimed. */
  rule_counts: number[]
  unmatched_count?: number | null
}

export interface RatingLevelValue {
  value: string
  count: number
}

/** What one column offers as rating levels, neither missing nor blank. */
export interface RatingLevelColumn {
  column: string
  values: RatingLevelValue[]
  distinct_count: number
  null_count: number
}

/** Whole-dataset levels for raw rating factor columns, or why there are none. */
export interface RatingLevelsResponse {
  status: "ok" | "cache_required"
  point: NodeDataPointResponse
  data_version?: string | null
  total_rows: number
  columns: RatingLevelColumn[]
}

export interface NodeDataProfileResponse {
  status: "completed" | "started" | "joined" | "cache_required"
  job_id?: string | null
  message: string
  result?: NodeDataProfile | null
  point: NodeDataPointResponse
}

export interface NodeDataStatusResponse {
  status: JobStatus
  progress: number
  message: string
  terminal_reason?: string | null
  error?: string | null
  error_code?: string | null
  execution_metrics?: ExecutionMetrics | null
  generation_id?: string | null
  outcome?: "published" | "superseded" | null
  profile?: NodeDataProfile | null
}

export interface NodeDataClearResponse {
  status: "cleared" | "delegated"
  point: NodeDataPointResponse
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
  data_version: string
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
  /** The logged operation (training logs only); a retry with it returns this run. */
  operation_id?: string | null
  logged_at?: string | null
}

export interface ModelSaveDestinationRequest {
  /** A bare filename saves under models/; paths are project-root-relative. */
  output_path: string
  algorithm: "catboost" | "glm" | "xgboost" | "lightgbm" | "ebm"
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
  point_index?: number
  /** `""` logs to the local folder. */
  destination: "" | MlflowDestinationKey
}

export type FrontierPoint = Record<string, unknown> & {
  index?: number
  total_objective?: number
  constraints?: Record<string, number>
  lambdas?: Record<string, number>
}

/** The server's summary of one frontier point: every result field that
 *  differs from its solve, `null` where the point has none (applying it
 *  clears that field). Mirrors `OptimiserFrontierPointSummary` in
 *  `src/haute/schemas.py`. */
export interface FrontierPointSummary {
  total_objective: number
  constraints: Record<string, number>
  lambdas: Record<string, number>
  converged: boolean
  iterations: number | null
  cd_iterations: number | null
  clamp_rate: number | null
  history: OptimiserHistoryEntry[] | null
  scenario_value_stats: OptimiserScenarioValueStats | null
  scenario_value_histogram: OptimiserScenarioValueHistogram | null
  factor_tables: Record<string, Record<string, unknown>[]> | null
  warning: string | null
  frontier_error: string | null
}

export interface FrontierResponse {
  status: string
  points: FrontierPoint[]
  /** One server summary per point, in point order. */
  point_summaries: FrontierPointSummary[]
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
  GitCommitContext,
  GitCommitRef,
  GitCommitResponse,
  GitCreateWorkingBranchResponse,
  GitDeleteBranchResponse,
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
  GitMoveResponse,
  GitPrefs,
  GitPushResponse,
  GitRemote,
  GitRemoteLeg,
  GitRemotesResponse,
  GitRestoreResponse,
  GitSetIdentityResponse,
  GitSetWorkingBranchResponse,
  GitStorageBind,
  GitStorageClaim,
  GitStorageSync,
  GitUndeleteResponse,
  GitWorkingBranchesResponse,
  GitWorkingBranchResponse,
  GitUpstreamStatusResponse as GitUpstreamStatus,
} from "../generated/api-contracts.generated"

export type WorkingBranchState = GeneratedGitWorkingBranchResponse["state"]

/** Whether this deployment can durably remember a bound remote at all (§ hosted storage). */
export type StorageState = GeneratedGitWorkingBranchResponse["storage"]

export type SyncState = GeneratedGitStorageSync["state"]

export type SyncFailure = NonNullable<GeneratedGitStorageSync["failure"]>

export type BindState = GeneratedGitStorageBind["state"]

/** A non-fast-forward push rejection (P7 M7): the body of a 409 from
 *  POST /api/git/push, carrying the per-leg divergence so the UI shows the honest
 *  fork rather than a dead-end string. `ledger` is null when it isn't spawned. */
export interface GitPushRejection {
  status: "rejected_diverged"
  remote: string
  working: GeneratedGitRemoteLeg
  ledger: GeneratedGitRemoteLeg | null
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
  working: GeneratedGitRemoteLeg
  message: string
}

/** Result of rendering a low-code Transform step list to Polars code. A step
 *  validation failure is data (`ok: false` with the failing step index and
 *  message), never a transport error. */
export interface PolarsStepsRenderResponse {
  ok: boolean
  code: string
  /** 1-based inclusive `[start, end]` line range per step. */
  step_lines: number[][]
  /** Zero-based index of the failing step; null for a list-level problem. */
  step_index: number | null
  message: string
}
