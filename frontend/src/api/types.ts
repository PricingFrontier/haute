/** Shared API response/request types for the Haute backend. */

// Re-export canonical types from their source locations
import type { ColumnInfo } from "../types/node"
export type { ColumnInfo } from "../types/node"
export type { TraceResult, TraceStep, TraceSchemaDiff } from "../types/trace"

export interface PipelineGraph {
  nodes: import("@xyflow/react").Node[]
  edges: import("@xyflow/react").Edge[]
  pipeline_name?: string
  pipeline_description?: string
  preamble?: string
  source_file?: string
  submodels?: Record<string, unknown>
  warning?: string
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
  file: string
  pipeline_name: string
}

export interface SubmodelCreateResponse {
  graph?: PipelineGraph
}

export interface SubmodelGraphResponse {
  graph?: {
    nodes: import("@xyflow/react").Node[]
    edges: import("@xyflow/react").Edge[]
  }
}

export interface DissolveSubmodelResponse {
  graph?: PipelineGraph
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
  mlflow_installed?: boolean
  backend?: string
  databricks_host?: string
}

export interface TrainEstimate {
  total_rows?: number | null
  safe_row_limit?: number | null
  estimated_mb: number
  training_mb: number
  available_mb: number
  bytes_per_row: number
  was_downsampled: boolean
  warning?: string | null
  // GPU VRAM estimation (only populated when task_type is GPU)
  gpu_vram_estimated_mb?: number | null
  gpu_vram_available_mb?: number | null
  gpu_warning?: string | null
}

export interface MlflowLogResponse {
  status: string
  backend?: string
  experiment_name?: string
  run_id?: string
  run_url?: string | null
  tracking_uri?: string
  error?: string
}

// ---------------------------------------------------------------------------
// Optimiser types
// ---------------------------------------------------------------------------

export interface SolveOptimiserResponse {
  status: string
  job_id?: string
  error?: string
}

export interface OptimiserEstimate {
  /** Source row count from parquet metadata, or null when unreadable. */
  total_rows?: number | null
  /** Approximate scored-frame size, in megabytes. */
  estimated_mb: number
  /** Available system RAM, in megabytes. */
  available_mb: number
}

export interface ApplyOptimiserResponse {
  status: string
  total_objective?: number
  constraints?: Record<string, number>
  preview?: Record<string, unknown>[]
  row_count?: number
  error?: string
}

export interface SaveOptimiserResponse {
  status: string
  path?: string
  message?: string
}

export interface FrontierResponse {
  status: string
  points: Record<string, unknown>[]
  n_points: number
  constraint_names: string[]
}

export type FrontierData = Omit<FrontierResponse, 'status'>

export interface FrontierSelectResponse {
  status: string
  total_objective: number
  constraints: Record<string, number>
  baseline_objective: number
  baseline_constraints: Record<string, number>
  lambdas: Record<string, number>
  converged: boolean
  error?: string
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

export interface UtilityWriteResult {
  status: string
  name: string
  module: string
  import_line: string
  error?: string | null
  error_line?: number | null
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
  main_last_updated: string | null
}

export interface GitBranchInfo {
  name: string
  is_yours: boolean
  is_current: boolean
  is_archived: boolean
  last_commit_time: string
  commit_count: number
}

export interface GitHistoryEntry {
  sha: string
  short_sha: string
  message: string
  timestamp: string
  files_changed: string[]
}
