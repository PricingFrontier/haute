/**
 * Typed API client for the Haute backend.
 *
 * Centralizes all fetch() calls with:
 * - Typed request/response interfaces
 * - AbortController support for request cancellation
 * - Configurable timeouts
 * - Consistent error handling via ApiError
 */

import type {
  PipelineGraph,
  SavePipelineResponse,
  TraceResponse,
  NodeResult,
  SinkResponse,
  SubmodelCreateResponse,
  SubmodelGraphResponse,
  DissolveSubmodelResponse,
  SchemaResult,
  GraphPayload,
  MlflowCheckResponse,
  TrainEstimate,
  MlflowLogResponse,
  SolveOptimiserResponse,
  ApplyOptimiserResponse,
  SaveOptimiserResponse,
  FrontierResponse,
  FrontierSelectResponse,
  DatabricksWarehouse,
  DatabricksCatalog,
  DatabricksSchema,
  DatabricksTable,
  CacheStatusResponse,
  FetchProgressResponse,
  JsonCacheProgressResponse,
  JsonCacheStatusResponse,
  MlflowExperiment,
  MlflowRun,
  MlflowModel,
  MlflowModelVersion,
  FileListItem,
  UtilityFile,
  UtilityWriteResult,
  GitStatus,
  GitBranchInfo,
  GitHistoryEntry,
} from "./types"

// Re-export types so existing consumers importing from client.ts still work
export type { TrainEstimate, UtilityFile, UtilityWriteResult, GitStatus, GitHistoryEntry } from "./types"
/** @deprecated Import GitBranchInfo from api/types instead. */
export type GitBranch = GitBranchInfo

export class ApiError extends Error {
  status: number
  detail?: string

  constructor(message: string, status: number, detail?: string) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.detail = detail
  }
}

async function request<T>(
  url: string,
  options: RequestInit & { timeout?: number } = {},
): Promise<T> {
  const { timeout = 30_000, signal: externalSignal, ...fetchOptions } = options

  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeout)

  // If an external signal is provided, abort our controller when it fires
  if (externalSignal) {
    if (externalSignal.aborted) {
      controller.abort()
    } else {
      externalSignal.addEventListener("abort", () => controller.abort(), { once: true })
    }
  }

  try {
    const res = await fetch(url, { ...fetchOptions, signal: controller.signal })
    if (!res.ok) {
      let detail: string | undefined
      try {
        const body = await res.json()
        const raw = body.detail ?? body
        detail = typeof raw === "string" ? raw : JSON.stringify(raw)
      } catch {
        detail = res.statusText
      }
      throw new ApiError(`HTTP ${res.status}`, res.status, detail)
    }
    return await res.json() as T
  } finally {
    clearTimeout(timeoutId)
  }
}

function post<T>(url: string, body: unknown, options: { signal?: AbortSignal; timeout?: number } = {}): Promise<T> {
  return request<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...options,
  })
}

function del<T>(url: string, options: { signal?: AbortSignal; timeout?: number } = {}): Promise<T> {
  return request<T>(url, { method: "DELETE", ...options })
}

// ---------------------------------------------------------------------------
// Pipeline endpoints
// ---------------------------------------------------------------------------

export function loadPipeline(options?: { signal?: AbortSignal }): Promise<PipelineGraph> {
  return request<PipelineGraph>("/api/pipeline", options).catch((err) => {
    if (err instanceof ApiError && err.status === 404) {
      return { nodes: [], edges: [] } as PipelineGraph
    }
    throw err
  })
}

export function previewNode(
  graph: GraphPayload,
  nodeId: string,
  rowLimit: number,
  source?: string,
  options?: { signal?: AbortSignal; timeout?: number },
): Promise<NodeResult & { node_id: string }> {
  return post("/api/pipeline/preview", {
    graph, node_id: nodeId, row_limit: rowLimit, source: source ?? "live",
  }, {
    timeout: 120_000,
    ...options,
  })
}

export function savePipeline(
  payload: {
    name: string
    description: string
    graph: GraphPayload
    preamble: string
    source_file: string
    sources?: string[]
    active_source?: string
  },
  options?: { signal?: AbortSignal },
): Promise<SavePipelineResponse> {
  return post("/api/pipeline/save", payload, options)
}

export function traceCell(
  payload: {
    graph: GraphPayload
    row_index: number
    target_node_id: string
    column?: string | null
    row_limit?: number
    source?: string
    row_values?: Record<string, unknown>
  },
  options?: { signal?: AbortSignal; timeout?: number },
): Promise<TraceResponse> {
  return post("/api/pipeline/trace", payload, { timeout: 120_000, ...options })
}

export function executeSink(
  graph: GraphPayload,
  nodeId: string,
  source?: string,
  options?: { signal?: AbortSignal; timeout?: number },
): Promise<SinkResponse> {
  return post("/api/pipeline/sink", { graph, node_id: nodeId, source: source ?? "live" }, { timeout: 300_000, ...options })
}

// ---------------------------------------------------------------------------
// Submodel endpoints
// ---------------------------------------------------------------------------

export function createSubmodel(
  payload: {
    name: string
    node_ids: string[]
    graph: GraphPayload
    preamble: string
    source_file: string
    pipeline_name: string
    pipeline_description?: string
  },
  options?: { signal?: AbortSignal },
): Promise<SubmodelCreateResponse> {
  return post("/api/submodel/create", payload, options)
}

export function loadSubmodel(
  name: string,
  options?: { signal?: AbortSignal },
): Promise<SubmodelGraphResponse> {
  return request<SubmodelGraphResponse>(
    `/api/submodel/${encodeURIComponent(name)}`,
    options,
  )
}

export function dissolveSubmodel(
  payload: {
    submodel_name: string
    graph: GraphPayload
    preamble: string
    source_file: string
    pipeline_name: string
    pipeline_description?: string
  },
  options?: { signal?: AbortSignal },
): Promise<DissolveSubmodelResponse> {
  return post("/api/submodel/dissolve", payload, options)
}

// ---------------------------------------------------------------------------
// Schema endpoints
// ---------------------------------------------------------------------------

export function fetchSchema(
  path: string,
  options?: { signal?: AbortSignal },
): Promise<SchemaResult> {
  return request<SchemaResult>(`/api/schema?path=${encodeURIComponent(path)}`, options)
}

export function fetchDatabricksSchema(
  table: string,
  options?: { signal?: AbortSignal },
): Promise<SchemaResult> {
  return request<SchemaResult>(`/api/schema/databricks?table=${encodeURIComponent(table)}`, options)
}

// ---------------------------------------------------------------------------
// Modelling endpoints
// ---------------------------------------------------------------------------

export function checkMlflow(
  options?: { signal?: AbortSignal },
): Promise<MlflowCheckResponse> {
  return request("/api/modelling/mlflow/check", options)
}

export function getTrainStatus<T = unknown>(
  jobId: string,
  options?: { signal?: AbortSignal },
): Promise<T> {
  return request<T>(`/api/modelling/train/status/${encodeURIComponent(jobId)}`, options)
}

export function trainModel(
  payload: { graph: GraphPayload; node_id: string; source?: string },
  options?: { signal?: AbortSignal },
): Promise<Record<string, unknown>> {
  // Pipeline execution can take minutes for large datasets — use a 10-minute timeout
  return post("/api/modelling/train", { ...payload, source: payload.source ?? "live" }, { ...options, timeout: 600_000 })
}

export function estimateTrainingRam(
  payload: { graph: GraphPayload; node_id: string; source?: string },
  options?: { signal?: AbortSignal },
): Promise<TrainEstimate> {
  return post("/api/modelling/estimate", { ...payload, source: payload.source ?? "live" }, { timeout: 30_000, ...options })
}

export function logToMlflow(
  payload: { job_id: string; experiment_name?: string | null; model_name?: string | null },
  options?: { signal?: AbortSignal },
): Promise<MlflowLogResponse> {
  return post("/api/modelling/mlflow/log", payload, { timeout: 600_000, ...options })
}

// ---------------------------------------------------------------------------
// Optimiser endpoints
// ---------------------------------------------------------------------------

export function solveOptimiser(
  payload: { graph: GraphPayload; node_id: string },
  options?: { signal?: AbortSignal },
): Promise<SolveOptimiserResponse> {
  return post("/api/optimiser/solve", payload, { timeout: 300_000, ...options })
}

export function getOptimiserStatus<T = unknown>(
  jobId: string,
  options?: { signal?: AbortSignal },
): Promise<T> {
  return request<T>(`/api/optimiser/solve/status/${encodeURIComponent(jobId)}`, options)
}

export function applyOptimiser(
  payload: { job_id: string },
  options?: { signal?: AbortSignal },
): Promise<ApplyOptimiserResponse> {
  return post("/api/optimiser/apply", payload, { timeout: 120_000, ...options })
}

export function saveOptimiser(
  payload: { job_id: string; output_path: string },
  options?: { signal?: AbortSignal },
): Promise<SaveOptimiserResponse> {
  return post("/api/optimiser/save", payload, options)
}

export function logOptimiserToMlflow(
  payload: { job_id: string; experiment_name?: string | null; model_name?: string | null },
  options?: { signal?: AbortSignal },
): Promise<MlflowLogResponse> {
  return post("/api/optimiser/mlflow/log", payload, options)
}

export function runFrontier(
  payload: { job_id: string; threshold_ranges: Record<string, [number, number]>; n_points_per_dim?: number },
  options?: { signal?: AbortSignal },
): Promise<FrontierResponse> {
  return post("/api/optimiser/frontier", payload, { timeout: 120_000, ...options })
}

export function selectFrontierPoint(
  payload: { job_id: string; point_index: number },
  options?: { signal?: AbortSignal },
): Promise<FrontierSelectResponse> {
  return post("/api/optimiser/frontier/select", payload, options)
}

// ---------------------------------------------------------------------------
// Databricks endpoints
// ---------------------------------------------------------------------------

export function getWarehouses(
  options?: { signal?: AbortSignal },
): Promise<{ warehouses?: DatabricksWarehouse[] }> {
  return request("/api/databricks/warehouses", options)
}

export function getCatalogs(
  options?: { signal?: AbortSignal },
): Promise<{ catalogs?: DatabricksCatalog[] }> {
  return request("/api/databricks/catalogs", options)
}

export function getSchemas(
  catalog: string,
  options?: { signal?: AbortSignal },
): Promise<{ schemas?: DatabricksSchema[] }> {
  return request(`/api/databricks/schemas?catalog=${encodeURIComponent(catalog)}`, options)
}

export function getTables(
  catalog: string,
  schema: string,
  options?: { signal?: AbortSignal },
): Promise<{ tables?: DatabricksTable[] }> {
  return request(`/api/databricks/tables?catalog=${encodeURIComponent(catalog)}&schema=${encodeURIComponent(schema)}`, options)
}

export function getCacheStatus(
  table: string,
  options?: { signal?: AbortSignal },
): Promise<CacheStatusResponse> {
  return request(`/api/databricks/cache?table=${encodeURIComponent(table)}`, options)
}

export function getFetchProgress(
  table: string,
  options?: { signal?: AbortSignal },
): Promise<FetchProgressResponse> {
  return request(`/api/databricks/fetch/progress?table=${encodeURIComponent(table)}`, options)
}

export function fetchDatabricksData(
  payload: { table: string; http_path?: string; query?: string },
  options?: { signal?: AbortSignal; timeout?: number },
): Promise<Record<string, unknown>> {
  return post("/api/databricks/fetch", payload, { timeout: 300_000, ...options })
}

export function deleteCache(
  table: string,
  options?: { signal?: AbortSignal },
): Promise<CacheStatusResponse> {
  return del(`/api/databricks/cache?table=${encodeURIComponent(table)}`, options)
}

// ---------------------------------------------------------------------------
// JSON cache endpoints
// ---------------------------------------------------------------------------

export function buildJsonCache(
  payload: { path: string; config_path?: string },
  options?: { signal?: AbortSignal; timeout?: number },
): Promise<Record<string, unknown>> {
  return post("/api/json-cache/build", payload, { timeout: 1_800_000, ...options })
}

export function cancelJsonCache(
  path: string,
  options?: { signal?: AbortSignal },
): Promise<{ cancelled: boolean; data_path: string }> {
  return post("/api/json-cache/cancel", { path }, options)
}

export function getJsonCacheProgress(
  path: string,
  options?: { signal?: AbortSignal },
): Promise<JsonCacheProgressResponse> {
  return request(`/api/json-cache/progress?path=${encodeURIComponent(path)}`, options)
}

export function getJsonCacheStatus(
  path: string,
  options?: { signal?: AbortSignal },
): Promise<JsonCacheStatusResponse> {
  return request(`/api/json-cache/status?path=${encodeURIComponent(path)}`, options)
}

export function deleteJsonCache(
  path: string,
  options?: { signal?: AbortSignal },
): Promise<{ cached: boolean; data_path: string }> {
  return del(`/api/json-cache?path=${encodeURIComponent(path)}`, options)
}

// ---------------------------------------------------------------------------
// MLflow endpoints (used by ModelScoreEditor + OptimiserApplyEditor)
// ---------------------------------------------------------------------------

export function getExperiments(
  options?: { signal?: AbortSignal },
): Promise<MlflowExperiment[]> {
  return request("/api/mlflow/experiments", options)
}

export function getRuns(
  experimentId: string,
  artifactFilter?: string,
  options?: { signal?: AbortSignal },
): Promise<MlflowRun[]> {
  const params = new URLSearchParams({ experiment_id: experimentId })
  if (artifactFilter) params.set("artifact_filter", artifactFilter)
  return request(`/api/mlflow/runs?${params.toString()}`, options)
}

export function getModels(
  options?: { signal?: AbortSignal },
): Promise<MlflowModel[]> {
  return request("/api/mlflow/models", options)
}

export function getModelVersions(
  modelName: string,
  options?: { signal?: AbortSignal },
): Promise<MlflowModelVersion[]> {
  return request(`/api/mlflow/model-versions?model_name=${encodeURIComponent(modelName)}`, options)
}

// ---------------------------------------------------------------------------
// File browsing
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Utility endpoints
// ---------------------------------------------------------------------------

export function listUtilityFiles(
  options?: { signal?: AbortSignal },
): Promise<{ files: UtilityFile[] }> {
  return request("/api/utility", options)
}

export function readUtilityFile(
  module: string,
  options?: { signal?: AbortSignal },
): Promise<{ name: string; module: string; content: string }> {
  return request(`/api/utility/${encodeURIComponent(module)}`, options)
}

export function createUtilityFile(
  payload: { name: string; content?: string },
  options?: { signal?: AbortSignal },
): Promise<UtilityWriteResult> {
  return post("/api/utility", payload, options)
}

export function updateUtilityFile(
  module: string,
  content: string,
  options?: { signal?: AbortSignal },
): Promise<UtilityWriteResult> {
  return request(`/api/utility/${encodeURIComponent(module)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
    ...options,
  })
}

export function deleteUtilityFile(
  module: string,
  options?: { signal?: AbortSignal },
): Promise<{ status: string; module: string }> {
  return del(`/api/utility/${encodeURIComponent(module)}`, options)
}

// ---------------------------------------------------------------------------
// File browsing
// ---------------------------------------------------------------------------

export function listFiles(
  dir: string,
  extensions?: string,
  options?: { signal?: AbortSignal },
): Promise<{ items?: FileListItem[] }> {
  const params = new URLSearchParams({ dir })
  if (extensions) params.set("extensions", extensions)
  return request(`/api/files?${params.toString()}`, options)
}

export function readJson<T = unknown>(
  path: string,
  options?: { signal?: AbortSignal; timeout?: number },
): Promise<T> {
  return post<T>("/api/pipeline/read-json", { path }, options)
}

// ---------------------------------------------------------------------------
// Git endpoints
// ---------------------------------------------------------------------------

export function getGitStatus(
  options?: { signal?: AbortSignal },
): Promise<GitStatus> {
  return request("/api/git/status", options)
}

export function listGitBranches(
  options?: { signal?: AbortSignal },
): Promise<{ current: string; branches: GitBranchInfo[] }> {
  return request("/api/git/branches", options)
}

export function createGitBranch(
  description: string,
  options?: { signal?: AbortSignal },
): Promise<{ branch: string }> {
  return post("/api/git/branches", { description }, options)
}

export function switchGitBranch(
  branch: string,
  options?: { signal?: AbortSignal },
): Promise<{ status: string; branch: string }> {
  return post("/api/git/switch", { branch }, options)
}

export function gitSave(
  options?: { signal?: AbortSignal },
): Promise<{ commit_sha: string; message: string; timestamp: string }> {
  return post("/api/git/save", {}, options)
}

export function gitSubmit(
  options?: { signal?: AbortSignal },
): Promise<{ compare_url: string | null; branch: string }> {
  return post("/api/git/submit", {}, options)
}

export function getGitHistory(
  limit?: number,
  options?: { signal?: AbortSignal },
): Promise<{ entries: GitHistoryEntry[] }> {
  const params = limit ? `?limit=${limit}` : ""
  return request(`/api/git/history${params}`, options)
}

export function gitRevert(
  sha: string,
  options?: { signal?: AbortSignal },
): Promise<{ backup_tag: string; reverted_to: string }> {
  return post("/api/git/revert", { sha }, options)
}

export function gitPull(
  options?: { signal?: AbortSignal },
): Promise<{ success: boolean; conflict: boolean; conflict_message: string | null; commits_pulled: number }> {
  return post("/api/git/pull", {}, options)
}

export function gitArchiveBranch(
  branch: string,
  options?: { signal?: AbortSignal },
): Promise<{ archived_as: string }> {
  return post("/api/git/archive", { branch }, options)
}

export function gitDeleteBranch(
  branch: string,
  options?: { signal?: AbortSignal },
): Promise<{ status: string; branch: string }> {
  return request("/api/git/branches", {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ branch }),
    ...options,
  })
}
