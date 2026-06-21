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
  ApplyOptimiserRequest,
  ApplyOptimiserResponse,
  CacheStatusResponse,
  DatabricksCatalogsResponse,
  DatabricksSchemasResponse,
  DatabricksTablesResponse,
  DatabricksWarehousesResponse,
  DissolveSubmodelResponse,
  FetchProgressResponse,
  FetchTableResponse,
  FileListItem,
  FrontierAutoRangeResponse,
  FrontierResponse,
  FrontierSelectResponse,
  GitArchiveResponse,
  GitDeleteBranchResponse,
  GitCommitResponse,
  GitMilestonesResponse,
  GitLedgerSavesResponse,
  GitWorkingBranchesResponse,
  GitRestoreResponse,
  GitCreateWorkingBranchResponse,
  GitPrefs,
  GitRemotesResponse,
  GitPushResponse,
  GitCommitContext,
  GitMoveResponse,
  GitSetIdentityResponse,
  GitSetWorkingBranchResponse,
  GitStatus,
  GitWorkingBranchResponse,
  GraphPayload,
  JsonCacheBuildResponse,
  JsonCacheProgressResponse,
  JsonCacheStatusResponse,
  MlflowCheckResponse,
  MlflowExperiment,
  MlflowLogResponse,
  MlflowModel,
  MlflowModelVersion,
  MlflowRun,
  LogOptimiserToMlflowRequest,
  OptimiserEstimate,
  OptimiserSolveResponse,
  OptimiserStatusResponse,
  PipelineGraph,
  PreviewNodeResponse,
  SaveOptimiserRequest,
  SaveOptimiserResponse,
  SavePipelineResponse,
  SchemaResult,
  SinkResponse,
  SubmodelCreateResponse,
  SubmodelGraphResponse,
  TraceResponse,
  TrainEstimate,
  TrainResponse,
  TrainStatusResponse,
  UtilityDeleteResponse,
  UtilityListResponse,
  UtilityReadResponse,
  UtilityWriteResult,
} from "./types"
import {
  parseApplyOptimiserResponse,
  parseCacheStatusResponse,
  parseDatabricksCatalogsResponse,
  parseDatabricksSchemasResponse,
  parseDatabricksTablesResponse,
  parseDatabricksWarehousesResponse,
  parseDissolveSubmodelResponse,
  parseFetchProgressResponse,
  parseFetchTableResponse,
  parseFrontierAutoRangeResponse,
  parseFrontierResponse,
  parseFrontierSelectResponse,
  parseGitArchiveResponse,
  parseGitDeleteBranchResponse,
  parseGitCommitResponse,
  parseGitMilestonesResponse,
  parseGitLedgerSavesResponse,
  parseGitWorkingBranchesResponse,
  parseGitRestoreResponse,
  parseGitCreateWorkingBranchResponse,
  parseGitPrefs,
  parseGitRemotesResponse,
  parseGitPushResponse,
  parseGitCommitContext,
  parseGitMoveResponse,
  parseGitSetIdentityResponse,
  parseGitSetWorkingBranchResponse,
  parseGitStatusResponse,
  parseGitWorkingBranchResponse,
  parseJsonCacheBuildResponse,
  parseJsonCacheProgressResponse,
  parseJsonCacheStatusResponse,
  parseMlflowCheckResponse,
  parseMlflowLogResponse,
  parseOptimiserEstimateResponse,
  parseSaveOptimiserResponse,
  parseSolveOptimiserResponse,
  parseOptimiserStatusResponse,
  parsePipelineResponse,
  parsePreviewNodeResponse,
  parseSavePipelineResponse,
  parseSchemaResponse,
  parseSubmodelCreateResponse,
  parseSubmodelGraphResponse,
  parseTraceResponse,
  parseTrainEstimateResponse,
  parseTrainResponse,
  parseTrainStatusResponse,
  parseUtilityDeleteResponse,
  parseUtilityListResponse,
  parseUtilityReadResponse,
  parseUtilityWriteResponse,
} from "../types/guards"

export class ApiError extends Error {
  status: number
  detail?: string
  /** The parsed JSON error body when available, so callers can read a structured
   *  error payload (e.g. the push-rejection divergence data on a 409) instead of
   *  only the stringified `detail`. */
  body?: unknown

  constructor(message: string, status: number, detail?: string, body?: unknown) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.detail = detail
    this.body = body
  }
}

// ---------------------------------------------------------------------------
// Retry policy
// ---------------------------------------------------------------------------
//
// Idempotent verbs (GET, HEAD, PUT, DELETE, OPTIONS) retry transient failures
// - network errors (TypeError from fetch) and 5xx responses - with exponential
// backoff + equal jitter, capped at the default retry policy (so up to
// maxRetries + 1 attempts total).
//
// POST is NOT retried by default: retrying a non-idempotent request without
// server-side deduplication risks duplicate side-effects. 4xx responses are
// NOT retried because they indicate client bugs, not transient server issues.
//
// Backoff uses equal jitter: delay in [base*2^n / 2, base*2^n], giving growth
// without the pathological case of every client retrying in lockstep. With
// baseDelayMs=100 and maxRetries=3, the worst-case total backoff budget is
// 100 + 200 + 400 = 700ms - well under a 1s user-perceived latency ceiling.
//
// A caller-supplied AbortSignal cancels the retry loop immediately, including
// while sleeping between attempts. AbortError from fetch (user intent) is
// surfaced as-is and never retried.

export interface RetryPolicy {
  maxRetries?: number
  baseDelayMs?: number
}

export interface ApiClientOptions {
  signal?: AbortSignal
  timeout?: number
  retry?: RetryPolicy
}

type ResolvedRetryPolicy = Required<RetryPolicy>
type RequestOptions = RequestInit & ApiClientOptions
type MutationOptions = Pick<ApiClientOptions, "signal" | "timeout">

const DEFAULT_RETRY_POLICY: ResolvedRetryPolicy = {
  maxRetries: 3,
  baseDelayMs: 100,
}

const IDEMPOTENT_METHODS = new Set(["GET", "HEAD", "PUT", "DELETE", "OPTIONS"])

function isIdempotent(method: string | undefined): boolean {
  return IDEMPOTENT_METHODS.has((method ?? "GET").toUpperCase())
}

function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError"
}

function shouldRetry(method: string | undefined, err: unknown): boolean {
  if (!isIdempotent(method)) return false
  // User-initiated cancel: propagate immediately.
  if (isAbortError(err)) return false
  // Network-layer failure (fetch throws TypeError on connection issues).
  if (err instanceof TypeError) return true
  // Server-side transient failure (5xx).
  if (err instanceof ApiError && err.status >= 500 && err.status < 600) return true
  return false
}

/**
 * Equal-jitter exponential backoff.
 *
 *   attempt 0 -> [BASE/2, BASE]            ~ [50,  100] ms
 *   attempt 1 -> [BASE,   BASE*2]          ~ [100, 200] ms
 *   attempt 2 -> [BASE*2, BASE*4]          ~ [200, 400] ms
 *
 * Worst-case sum for maxRetries=3, baseDelayMs=100 is 700ms.
 */
function resolveRetryPolicy(policy?: RetryPolicy): ResolvedRetryPolicy {
  const resolved = {
    ...DEFAULT_RETRY_POLICY,
    ...policy,
  }
  if (!Number.isInteger(resolved.maxRetries) || resolved.maxRetries < 0) {
    throw new Error("retry.maxRetries must be a non-negative integer")
  }
  if (!Number.isFinite(resolved.baseDelayMs) || resolved.baseDelayMs <= 0) {
    throw new Error("retry.baseDelayMs must be a positive finite number")
  }
  return resolved
}

function backoffDelayMs(attempt: number, policy: ResolvedRetryPolicy): number {
  const exp = policy.baseDelayMs * Math.pow(2, attempt)
  return exp / 2 + Math.random() * (exp / 2)
}

/**
 * Sleep for `ms` milliseconds, rejecting early with AbortError if `signal`
 * fires. Used between retry attempts so a caller's abort cancels the retry
 * loop without waiting out the backoff.
 */
function backoffSleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Aborted", "AbortError"))
      return
    }
    const onAbort = () => {
      clearTimeout(timer)
      reject(new DOMException("Aborted", "AbortError"))
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort)
      resolve()
    }, ms)
    signal?.addEventListener("abort", onAbort, { once: true })
  })
}

/**
 * Issue a single HTTP attempt. Owns its own AbortController so the timeout
 * guard and external-signal bridge remain scoped to one fetch; each retry gets
 * a fresh controller.
 */
async function attemptFetch<T>(
  url: string,
  fetchOptions: RequestInit,
  timeout: number,
  externalSignal: AbortSignal | undefined,
): Promise<T> {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeout)

  // If an external signal is provided, abort our controller when it fires.
  // We track the listener so we can remove it in the finally block below —
  // otherwise retry-loop one-shot listeners accumulate on the
  // caller's signal across retries before any of them fire.  The signal
  // itself typically lives at least as long as a user interaction, so
  // ambient listener pressure during a retry burst is worth avoiding.
  let externalAbortHandler: (() => void) | undefined
  if (externalSignal) {
    if (externalSignal.aborted) {
      controller.abort()
    } else {
      externalAbortHandler = () => controller.abort()
      externalSignal.addEventListener("abort", externalAbortHandler, { once: true })
    }
  }

  try {
    const res = await fetch(url, { ...fetchOptions, signal: controller.signal })
    if (!res.ok) {
      let detail: string | undefined
      let body: unknown
      try {
        body = await res.json()
        const raw = (body as { detail?: unknown }).detail ?? body
        detail = typeof raw === "string" ? raw : JSON.stringify(raw)
      } catch {
        detail = res.statusText
      }
      throw new ApiError(`HTTP ${res.status}`, res.status, detail, body)
    }
    return await res.json() as T
  } finally {
    clearTimeout(timeoutId)
    if (externalAbortHandler) {
      externalSignal?.removeEventListener("abort", externalAbortHandler)
    }
  }
}

async function request<T>(
  url: string,
  options: RequestOptions = {},
): Promise<T> {
  const { timeout = 30_000, signal: rawSignal, retry, ...fetchOptions } = options
  // Normalise RequestInit's `AbortSignal | null` to `AbortSignal | undefined`
  // so internal helpers can use a single optional shape.
  const externalSignal: AbortSignal | undefined = rawSignal ?? undefined
  const method = fetchOptions.method
  const retryPolicy = resolveRetryPolicy(retry)

  let lastError: unknown
  for (let attempt = 0; attempt <= retryPolicy.maxRetries; attempt++) {
    // Honor external abort before issuing the next attempt.
    if (externalSignal?.aborted) {
      throw new DOMException("Aborted", "AbortError")
    }

    try {
      return await attemptFetch<T>(url, fetchOptions, timeout, externalSignal)
    } catch (err) {
      lastError = err
      // Non-retryable errors (AbortError, 4xx, non-idempotent method) short-circuit.
      if (!shouldRetry(method, err)) throw err
      // Out of budget - surface the last failure.
      if (attempt >= retryPolicy.maxRetries) throw err
      // Sleep before retrying; a caller-supplied signal cancels the sleep.
      await backoffSleep(backoffDelayMs(attempt, retryPolicy), externalSignal)
    }
  }
  // Unreachable: the loop either returns, throws inside the catch, or completes
  // the final iteration and throws via the `attempt >= maxRetries` guard.
  throw lastError
}

function post<T>(url: string, body: unknown, options: MutationOptions = {}): Promise<T> {
  return request<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...options,
  })
}

function del<T>(url: string, options: ApiClientOptions = {}): Promise<T> {
  return request<T>(url, { method: "DELETE", ...options })
}

// ---------------------------------------------------------------------------
// Pipeline endpoints
// ---------------------------------------------------------------------------

export function loadPipeline(options?: ApiClientOptions): Promise<PipelineGraph> {
  return request<unknown>("/api/pipeline", options)
    .then(parsePipelineResponse)
    .catch((err) => {
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
  requestedPreviewColumns?: string[],
): Promise<PreviewNodeResponse> {
  return post<unknown>(
    "/api/pipeline/preview",
    {
      graph,
      node_id: nodeId,
      row_limit: rowLimit,
      source: source ?? "live",
      ...(requestedPreviewColumns ? { requested_preview_columns: requestedPreviewColumns } : {}),
    },
    {
      timeout: 120_000,
      ...options,
    },
  ).then((data) => parsePreviewNodeResponse(data) as PreviewNodeResponse)
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
  options?: MutationOptions,
): Promise<SavePipelineResponse> {
  return post<unknown>("/api/pipeline/save", payload, options).then(parseSavePipelineResponse)
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
  return post<unknown>("/api/pipeline/trace", payload, { timeout: 120_000, ...options }).then(parseTraceResponse)
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
  return post<unknown>("/api/submodel/create", payload, options).then(parseSubmodelCreateResponse)
}

export function loadSubmodel(
  name: string,
  options?: { signal?: AbortSignal },
): Promise<SubmodelGraphResponse> {
  return request<unknown>(
    `/api/submodel/${encodeURIComponent(name)}`,
    options,
  ).then(parseSubmodelGraphResponse)
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
  return post<unknown>("/api/submodel/dissolve", payload, options).then(parseDissolveSubmodelResponse)
}

// ---------------------------------------------------------------------------
// Schema endpoints
// ---------------------------------------------------------------------------

export function fetchSchema(
  path: string,
  options?: { signal?: AbortSignal },
): Promise<SchemaResult> {
  return request<unknown>(`/api/schema?path=${encodeURIComponent(path)}`, options).then(parseSchemaResponse)
}

export function fetchDatabricksSchema(
  table: string,
  options?: { signal?: AbortSignal },
): Promise<SchemaResult> {
  return request<unknown>(`/api/schema/databricks?table=${encodeURIComponent(table)}`, options).then(parseSchemaResponse)
}

// ---------------------------------------------------------------------------
// Modelling endpoints
// ---------------------------------------------------------------------------

export function checkMlflow(
  options?: { signal?: AbortSignal },
): Promise<MlflowCheckResponse> {
  return request<unknown>("/api/modelling/mlflow/check", options).then(parseMlflowCheckResponse)
}

export function getTrainStatus<T extends TrainStatusResponse = TrainStatusResponse>(
  jobId: string,
  options?: { signal?: AbortSignal },
): Promise<T> {
  return request<unknown>(`/api/modelling/train/status/${encodeURIComponent(jobId)}`, options)
    .then((data) => parseTrainStatusResponse(data) as T)
}

export function trainModel(
  payload: { graph: GraphPayload; node_id: string; source?: string },
  options?: { signal?: AbortSignal },
): Promise<TrainResponse> {
  // Pipeline execution can take minutes for large datasets - use a 10-minute timeout
  return post<unknown>("/api/modelling/train", { ...payload, source: payload.source ?? "live" }, { ...options, timeout: 600_000 })
    .then(parseTrainResponse)
}

export function estimateTrainingRam(
  payload: { graph: GraphPayload; node_id: string; source?: string },
  options?: { signal?: AbortSignal },
): Promise<TrainEstimate> {
  return post<unknown>("/api/modelling/estimate", { ...payload, source: payload.source ?? "live" }, { timeout: 30_000, ...options })
    .then(parseTrainEstimateResponse)
}

export function logToMlflow(
  payload: { job_id: string; experiment_name?: string | null; model_name?: string | null },
  options?: { signal?: AbortSignal },
): Promise<MlflowLogResponse> {
  return post<unknown>("/api/modelling/mlflow/log", payload, { timeout: 600_000, ...options })
    .then(parseMlflowLogResponse)
}

// ---------------------------------------------------------------------------
// Optimiser endpoints
// ---------------------------------------------------------------------------

export function solveOptimiser(
  payload: { graph: GraphPayload; node_id: string },
  options?: { signal?: AbortSignal },
): Promise<OptimiserSolveResponse> {
  return post<unknown>("/api/optimiser/solve", payload, { timeout: 300_000, ...options })
    .then(parseSolveOptimiserResponse)
}

export function estimateOptimiserSolve(
  payload: { graph: GraphPayload; node_id: string; source?: string },
  options?: { signal?: AbortSignal },
): Promise<OptimiserEstimate> {
  return post<unknown>(
    "/api/optimiser/estimate",
    { ...payload, source: payload.source ?? "live" },
    { timeout: 30_000, ...options },
  ).then(parseOptimiserEstimateResponse)
}

export function getOptimiserStatus<T extends OptimiserStatusResponse = OptimiserStatusResponse>(
  jobId: string,
  options?: { signal?: AbortSignal },
): Promise<T> {
  return request<unknown>(`/api/optimiser/solve/status/${encodeURIComponent(jobId)}`, options)
    .then((data) => parseOptimiserStatusResponse(data) as T)
}

export function applyOptimiser(
  payload: ApplyOptimiserRequest,
  options?: { signal?: AbortSignal },
): Promise<ApplyOptimiserResponse> {
  return post<unknown>("/api/optimiser/apply", payload, { timeout: 120_000, ...options })
    .then(parseApplyOptimiserResponse)
}

export function saveOptimiser(
  payload: SaveOptimiserRequest,
  options?: { signal?: AbortSignal },
): Promise<SaveOptimiserResponse> {
  return post<unknown>("/api/optimiser/save", payload, options).then(parseSaveOptimiserResponse)
}

export function logOptimiserToMlflow(
  payload: LogOptimiserToMlflowRequest,
  options?: { signal?: AbortSignal },
): Promise<MlflowLogResponse> {
  return post<unknown>("/api/optimiser/mlflow/log", payload, options).then(parseMlflowLogResponse)
}

export function runFrontier(
  payload: { job_id: string; threshold_ranges: Record<string, [number, number]>; n_points_per_dim?: number },
  options?: { signal?: AbortSignal },
): Promise<FrontierResponse> {
  return post<unknown>("/api/optimiser/frontier", payload, { timeout: 120_000, ...options })
    .then((data) => parseFrontierResponse(data))
}

export function estimateOptimiserFrontierAutoRange(
  payload: { graph: GraphPayload; node_id: string },
  options?: { signal?: AbortSignal },
): Promise<FrontierAutoRangeResponse> {
  return post<unknown>("/api/optimiser/frontier/auto-range", payload, { timeout: 300_000, ...options })
    .then(parseFrontierAutoRangeResponse)
}

export function selectFrontierPoint(
  payload: { job_id: string; point_index: number; include_ratebook_tables?: boolean },
  options?: { signal?: AbortSignal },
): Promise<FrontierSelectResponse> {
  return post<unknown>("/api/optimiser/frontier/select", payload, options).then(parseFrontierSelectResponse)
}

// ---------------------------------------------------------------------------
// Databricks endpoints
// ---------------------------------------------------------------------------

export function getWarehouses(
  options?: { signal?: AbortSignal },
): Promise<DatabricksWarehousesResponse> {
  return request<unknown>("/api/databricks/warehouses", options)
    .then((data) => parseDatabricksWarehousesResponse(data) as DatabricksWarehousesResponse)
}

export function getCatalogs(
  options?: { signal?: AbortSignal },
): Promise<DatabricksCatalogsResponse> {
  return request<unknown>("/api/databricks/catalogs", options)
    .then((data) => parseDatabricksCatalogsResponse(data) as DatabricksCatalogsResponse)
}

export function getSchemas(
  catalog: string,
  options?: { signal?: AbortSignal },
): Promise<DatabricksSchemasResponse> {
  return request<unknown>(`/api/databricks/schemas?catalog=${encodeURIComponent(catalog)}`, options)
    .then((data) => parseDatabricksSchemasResponse(data) as DatabricksSchemasResponse)
}

export function getTables(
  catalog: string,
  schema: string,
  options?: { signal?: AbortSignal },
): Promise<DatabricksTablesResponse> {
  return request<unknown>(`/api/databricks/tables?catalog=${encodeURIComponent(catalog)}&schema=${encodeURIComponent(schema)}`, options)
    .then((data) => parseDatabricksTablesResponse(data) as DatabricksTablesResponse)
}

export function getCacheStatus(
  table: string,
  options?: { signal?: AbortSignal },
): Promise<CacheStatusResponse> {
  return request<unknown>(`/api/databricks/cache?table=${encodeURIComponent(table)}`, options).then(parseCacheStatusResponse)
}

export function getFetchProgress(
  table: string,
  options?: { signal?: AbortSignal },
): Promise<FetchProgressResponse> {
  return request<unknown>(`/api/databricks/fetch/progress?table=${encodeURIComponent(table)}`, options).then(parseFetchProgressResponse)
}

export function fetchDatabricksData(
  payload: { table: string; http_path?: string; query?: string },
  options?: { signal?: AbortSignal; timeout?: number },
): Promise<FetchTableResponse> {
  return post<unknown>("/api/databricks/fetch", payload, { timeout: 300_000, ...options }).then(parseFetchTableResponse)
}

export function deleteCache(
  table: string,
  options?: { signal?: AbortSignal },
): Promise<CacheStatusResponse> {
  return del<unknown>(`/api/databricks/cache?table=${encodeURIComponent(table)}`, options).then(parseCacheStatusResponse)
}

// ---------------------------------------------------------------------------
// JSON cache endpoints
// ---------------------------------------------------------------------------

export function buildJsonCache(
  payload: { path: string; config_path?: string; flatten_schema?: Record<string, unknown> },
  options?: { signal?: AbortSignal; timeout?: number },
): Promise<JsonCacheBuildResponse> {
  return post<unknown>("/api/json-cache/build", payload, { timeout: 1_800_000, ...options }).then(parseJsonCacheBuildResponse)
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
  return request<unknown>(`/api/json-cache/progress?path=${encodeURIComponent(path)}`, options).then(parseJsonCacheProgressResponse)
}

export function getJsonCacheStatus(
  path: string,
  options?: { signal?: AbortSignal },
): Promise<JsonCacheStatusResponse> {
  return request<unknown>(`/api/json-cache/status?path=${encodeURIComponent(path)}`, options).then(parseJsonCacheStatusResponse)
}

export function getJsonCacheStatusForSchema(
  payload: { path: string; config_path?: string; flatten_schema?: Record<string, unknown> },
  options?: { signal?: AbortSignal },
): Promise<JsonCacheStatusResponse> {
  return post<unknown>("/api/json-cache/status", payload, options).then(parseJsonCacheStatusResponse)
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
): Promise<UtilityListResponse> {
  return request<unknown>("/api/utility", options).then(parseUtilityListResponse)
}

export function readUtilityFile(
  module: string,
  options?: { signal?: AbortSignal },
): Promise<UtilityReadResponse> {
  return request<unknown>(`/api/utility/${encodeURIComponent(module)}`, options).then(parseUtilityReadResponse)
}

export function createUtilityFile(
  payload: { name: string; content?: string },
  options?: { signal?: AbortSignal },
): Promise<UtilityWriteResult> {
  return post<unknown>("/api/utility", payload, options).then(parseUtilityWriteResponse)
}

export function updateUtilityFile(
  module: string,
  content: string,
  options?: { signal?: AbortSignal },
): Promise<UtilityWriteResult> {
  return request<unknown>(`/api/utility/${encodeURIComponent(module)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
    ...options,
  }).then(parseUtilityWriteResponse)
}

export function deleteUtilityFile(
  module: string,
  options?: { signal?: AbortSignal },
): Promise<UtilityDeleteResponse> {
  return del<unknown>(`/api/utility/${encodeURIComponent(module)}`, options).then(parseUtilityDeleteResponse)
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
  return request<unknown>("/api/git/status", options).then(parseGitStatusResponse)
}

export function getWorkingBranch(
  options?: { signal?: AbortSignal },
): Promise<GitWorkingBranchResponse> {
  return request<unknown>("/api/git/working-branch", options).then(parseGitWorkingBranchResponse)
}

export function setWorkingBranch(
  branch: string,
  create: boolean,
  options?: { signal?: AbortSignal },
): Promise<GitSetWorkingBranchResponse> {
  return post<unknown>("/api/git/working-branch", { branch, create }, options).then(
    parseGitSetWorkingBranchResponse,
  )
}

export function setGitIdentity(
  userName: string,
  userEmail: string,
  setGlobal: boolean,
  options?: { signal?: AbortSignal },
): Promise<GitSetIdentityResponse> {
  return post<unknown>(
    "/api/git/identity",
    { user_name: userName, user_email: userEmail, set_global: setGlobal },
    options,
  ).then(parseGitSetIdentityResponse)
}

export function commitMilestone(
  message: string,
  versionLabel: string | null,
  options?: { signal?: AbortSignal; allowFork?: boolean },
): Promise<GitCommitResponse> {
  return post<unknown>(
    "/api/git/commit",
    { message, version_label: versionLabel, allow_fork: options?.allowFork ?? false },
    { signal: options?.signal },
  ).then(parseGitCommitResponse)
}

export function getMilestones(
  limit?: number,
  branch?: string | null,
  options?: { signal?: AbortSignal },
): Promise<GitMilestonesResponse> {
  const p = new URLSearchParams()
  if (limit) p.set("limit", String(limit))
  if (branch) p.set("branch", branch)
  const qs = p.toString()
  return request<unknown>(`/api/git/milestones${qs ? `?${qs}` : ""}`, options).then(
    parseGitMilestonesResponse,
  )
}

export function getMilestoneSaves(
  sha: string,
  options?: { signal?: AbortSignal },
): Promise<GitLedgerSavesResponse> {
  return request<unknown>(
    `/api/git/milestones/${encodeURIComponent(sha)}/saves`,
    options,
  ).then(parseGitLedgerSavesResponse)
}

export function getPendingSaves(
  branch?: string | null,
  options?: { signal?: AbortSignal },
): Promise<GitLedgerSavesResponse> {
  const qs = branch ? `?branch=${encodeURIComponent(branch)}` : ""
  return request<unknown>(`/api/git/pending-saves${qs}`, options).then(
    parseGitLedgerSavesResponse,
  )
}

export function gitArchiveBranch(
  branch: string,
  options?: { signal?: AbortSignal },
): Promise<GitArchiveResponse> {
  return post<unknown>("/api/git/archive", { branch }, options).then(parseGitArchiveResponse)
}

export function gitDeleteBranch(
  branch: string,
  confirm = false,
  options?: { signal?: AbortSignal },
): Promise<GitDeleteBranchResponse> {
  return request<unknown>("/api/git/branches", {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ branch, confirm }),
    ...options,
  }).then(parseGitDeleteBranchResponse)
}

export function getWorkingBranches(
  options?: { signal?: AbortSignal },
): Promise<GitWorkingBranchesResponse> {
  return request<unknown>("/api/git/working-branches", options).then(
    parseGitWorkingBranchesResponse,
  )
}

export function restoreBranch(
  branch: string,
  options?: { signal?: AbortSignal },
): Promise<GitRestoreResponse> {
  return post<unknown>("/api/git/restore", { branch }, options).then(parseGitRestoreResponse)
}

export function createWorkingBranch(
  name: string,
  opts: { at?: string | null; move?: boolean } = {},
  options?: { signal?: AbortSignal },
): Promise<GitCreateWorkingBranchResponse> {
  return post<unknown>(
    "/api/git/working-branches",
    { name, at: opts.at ?? null, move: opts.move ?? false },
    options,
  ).then(parseGitCreateWorkingBranchResponse)
}

export function getGitPrefs(
  options?: { signal?: AbortSignal },
): Promise<GitPrefs> {
  return request<unknown>("/api/git/prefs", options).then(parseGitPrefs)
}

export function setGitPrefs(
  prefs: GitPrefs,
  options?: { signal?: AbortSignal },
): Promise<GitPrefs> {
  return post<unknown>("/api/git/prefs", prefs, options).then(parseGitPrefs)
}

/** Configured remotes + the working branch's ahead/behind vs each (S16). */
export function getGitRemotes(
  options?: { signal?: AbortSignal },
): Promise<GitRemotesResponse> {
  return request<unknown>("/api/git/remotes", options).then(parseGitRemotesResponse)
}

/** Deliberately push the working branch + its ledger to a remote (S16/S33). */
export function gitPush(
  remote: string,
  options?: { signal?: AbortSignal },
): Promise<GitPushResponse> {
  return post<unknown>("/api/git/push", { remote }, options).then(parseGitPushResponse)
}

/**
 * Read-only view of a commit's pipeline (S11): materialise the pipeline as it
 * stood at `sha` and parse it to the same graph shape the editor loads. Backs
 * the side-by-side comparison view. No checkout — the working tree is untouched.
 */
export function getCommitPipeline(
  sha: string,
  options?: { signal?: AbortSignal },
): Promise<PipelineGraph> {
  return request<unknown>(`/api/git/show/${encodeURIComponent(sha)}`, options).then(
    parsePipelineResponse,
  )
}

/** A commit's breadcrumb context — nearest ancestor milestone + distance (S11).
 *  `base` adds the commit delta `base..sha` (the historic↔current span). */
export function getCommitContext(
  sha: string,
  options?: { signal?: AbortSignal; base?: string },
): Promise<GitCommitContext> {
  const query = options?.base ? `?base=${encodeURIComponent(options.base)}` : ""
  return request<unknown>(
    `/api/git/commit-context/${encodeURIComponent(sha)}${query}`,
    options,
  ).then(parseGitCommitContext)
}

/**
 * Move the working directory to a historical commit (S11/S13 — §3.4): a real
 * detached checkout that materialises `sha`'s tree as the repo state. Unlike
 * the read-only `getCommitPipeline`, this changes HEAD and the working tree.
 * Creates nothing — the next save spawns a fresh working branch here (S13).
 */
export function moveToVersion(
  sha: string,
  options?: { signal?: AbortSignal },
): Promise<GitMoveResponse> {
  return post<unknown>("/api/git/move", { sha }, options).then(parseGitMoveResponse)
}
