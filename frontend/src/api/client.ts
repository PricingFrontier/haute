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
  DatabricksCatalogsResponse,
  DatabricksSchemasResponse,
  DatabricksTablesResponse,
  DatabricksWarehousesResponse,
  DissolveSubmodelResponse,
  ExploreRunResponse,
  ExploreStatusResponse,
  FileListItem,
  FrontierAutoRangeStartResponse,
  FrontierAutoRangeStatusResponse,
  FrontierSelectResponse,
  FrontierStatusResponse,
  GitArchiveResponse,
  GitDeleteBranchResponse,
  GitCommitResponse,
  GitMilestonesResponse,
  GitLedgerSavesResponse,
  GitWorkingBranchesResponse,
  GitRestoreResponse,
  GitUndeleteResponse,
  GitCreateWorkingBranchResponse,
  GitPrefs,
  GitRemotesResponse,
  GitPushResponse,
  GitFastForwardResponse,
  GitBranchAwayResponse,
  GitCommitContext,
  GitGraphResponse,
  GitMoveResponse,
  GitSetIdentityResponse,
  GitSetWorkingBranchResponse,
  GitWorkingBranchResponse,
  GraphPayload,
  IoCapabilitiesResponse,
  InputCacheBuildRequest,
  InputCacheBuildResponse,
  InputCacheCancelResponse,
  InputCacheJobStatusResponse,
  InputCacheSnapshotResponse,
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
  OutputAssembleDryRunResponse,
  OutputDestinationResponse,
  PipelineGraph,
  PreviewNodeResponse,
  SaveOptimiserRequest,
  SaveOptimiserResponse,
  SavePipelineResponse,
  SchemaResult,
  WriteOutputResponse,
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
  parseDatabricksCatalogsResponse,
  parseDatabricksSchemasResponse,
  parseDatabricksTablesResponse,
  parseDatabricksWarehousesResponse,
  parseDissolveSubmodelResponse,
  parseExploreRunResponse,
  parseExploreStatusResponse,
  parseFrontierAutoRangeStartResponse,
  parseFrontierAutoRangeStatusResponse,
  parseFrontierStatusResponse,
  parseFrontierSelectResponse,
  parseGitArchiveResponse,
  parseGitDeleteBranchResponse,
  parseGitCommitResponse,
  parseGitMilestonesResponse,
  parseGitLedgerSavesResponse,
  parseGitWorkingBranchesResponse,
  parseGitRestoreResponse,
  parseGitUndeleteResponse,
  parseGitCreateWorkingBranchResponse,
  parseGitPrefs,
  parseGitRemotesResponse,
  parseGitPushResponse,
  parseGitFastForwardResponse,
  parseGitGraphResponse,
  parseGitBranchAwayResponse,
  parseGitCommitContext,
  parseGitMoveResponse,
  parseGitSetIdentityResponse,
  parseGitSetWorkingBranchResponse,
  parseGitWorkingBranchResponse,
  parseIoCapabilitiesResponse,
  parseInputCacheBuildResponse,
  parseInputCacheCancelResponse,
  parseInputCacheJobStatusResponse,
  parseInputCacheSnapshotResponse,
  parseJsonCacheBuildResponse,
  parseJsonCacheDeleteResponse,
  parseJsonCacheProgressResponse,
  parseJsonCacheSchemaInferenceResponse,
  parseJsonCacheStatusResponse,
  parseMlflowCheckResponse,
  parseMlflowExperiments,
  parseMlflowLogResponse,
  parseMlflowModels,
  parseMlflowModelVersions,
  parseMlflowRuns,
  parseFileListResponse,
  parseHauteSessionResponse,
  parseOptimiserEstimateResponse,
  parseSaveOptimiserResponse,
  parseSolveOptimiserResponse,
  parseOptimiserStatusResponse,
  parseOutputDestinationResponse,
  parseOutputAssembleDryRunResponse,
  parsePipelineResponse,
  parsePreviewNodeResponse,
  parseSavePipelineResponse,
  parseSchemaResponse,
  parseWriteOutputResponse,
  parseSubmodelCreateResponse,
  parseSubmodelGraphResponse,
  parseTraceResponse,
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
  /** The raw (pre-stringify) value extracted from the error body — either
   *  `body.detail` or the whole body. Consumed by execution diagnostics to read
   *  structured failure fields without re-parsing `detail`. */
  rawDetail?: unknown

  constructor(
    message: string,
    status: number,
    detail?: string,
    body?: unknown,
    rawDetail?: unknown,
  ) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.detail = detail
    this.body = body
    this.rawDetail = rawDetail
  }
}

function formatTimeoutDuration(timeoutMs: number): string {
  if (timeoutMs >= 1000 && timeoutMs % 1000 === 0) {
    const seconds = timeoutMs / 1000
    return `${seconds} second${seconds === 1 ? "" : "s"}`
  }
  return `${timeoutMs} ms`
}

export class ApiTimeoutError extends Error {
  timeoutMs: number
  url: string

  constructor(url: string, timeoutMs: number) {
    super(`Request timed out after ${formatTimeoutDuration(timeoutMs)}.`)
    this.name = "ApiTimeoutError"
    this.timeoutMs = timeoutMs
    this.url = url
  }
}

export const HAUTE_SESSION_EXPIRED_EVENT = "haute:session-expired"
export const HAUTE_SESSION_EXPIRED_REASON = "Missing or invalid Haute session token"

export interface HauteSessionExpiredEventDetail {
  reason: string
}

export function isHauteSessionExpiredReason(reason: unknown): boolean {
  return typeof reason === "string" && reason.includes(HAUTE_SESSION_EXPIRED_REASON)
}

export function isHauteSessionExpiredError(err: unknown): boolean {
  return err instanceof ApiError &&
    err.status === 403 &&
    isHauteSessionExpiredReason(err.detail)
}

export function notifyHauteSessionExpired(reason = HAUTE_SESSION_EXPIRED_REASON): void {
  if (typeof window === "undefined") return
  window.dispatchEvent(
    new CustomEvent<HauteSessionExpiredEventDetail>(HAUTE_SESSION_EXPIRED_EVENT, {
      detail: { reason },
    }),
  )
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

function requestHeaders(headers: HeadersInit | undefined): Record<string, string> {
  const resolved: Record<string, string> = {}
  if (headers instanceof Headers) {
    headers.forEach((value, key) => {
      resolved[key] = value
    })
  } else if (Array.isArray(headers)) {
    for (const [key, value] of headers) {
      resolved[key] = value
    }
  } else if (headers) {
    Object.assign(resolved, headers)
  }
  return resolved
}

async function throwApiError(response: Response): Promise<never> {
  let detail: string | undefined
  let body: unknown
  let rawDetail: unknown
  try {
    body = await response.json()
    const raw = (body as { detail?: unknown }).detail ?? body
    rawDetail = raw
    detail = typeof raw === "string" ? raw : JSON.stringify(raw)
  } catch {
    detail = response.statusText
    rawDetail = detail
  }
  if (response.status === 403 && isHauteSessionExpiredReason(detail)) {
    notifyHauteSessionExpired(detail)
  }
  throw new ApiError(`HTTP ${response.status}`, response.status, detail, body, rawDetail)
}

let sessionBootstrap: Promise<void> | null = null
let activeBootstrapIsForced = false
let sessionBootstrapped = false

function performSessionBootstrap(): Promise<void> {
  return fetch("/api/session/bootstrap", {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
  }).then(async (response) => {
    if (!response.ok) await throwApiError(response)
    sessionBootstrapped = true
  })
}

function trackSessionBootstrap(promise: Promise<void>, forced: boolean): Promise<void> {
  sessionBootstrap = promise
  activeBootstrapIsForced = forced
  promise.then(
    () => {
      if (sessionBootstrap === promise) {
        sessionBootstrap = null
        activeBootstrapIsForced = false
      }
    },
    () => {
      if (sessionBootstrap === promise) {
        sessionBootstrap = null
        activeBootstrapIsForced = false
      }
    },
  )
  return promise
}

/**
 * Establish the local UI's HttpOnly session cookie.
 *
 * The credential never enters JavaScript: the browser accepts it from the
 * same-origin response and sends it automatically on API and WebSocket
 * requests. Concurrent callers share one request.
 */
export function bootstrapHauteSession(force = false): Promise<void> {
  if (force) sessionBootstrapped = false
  if (sessionBootstrap) {
    if (!force || activeBootstrapIsForced) return sessionBootstrap
    const currentBootstrap = sessionBootstrap
    const forcedBootstrap = currentBootstrap.then(
      performSessionBootstrap,
      performSessionBootstrap,
    )
    return trackSessionBootstrap(forcedBootstrap, true)
  }
  if (sessionBootstrapped && !force) return Promise.resolve()
  return trackSessionBootstrap(performSessionBootstrap(), force)
}

const IDEMPOTENT_METHODS = new Set(["GET", "HEAD", "PUT", "DELETE", "OPTIONS"])

function isIdempotent(method: string | undefined): boolean {
  return IDEMPOTENT_METHODS.has((method ?? "GET").toUpperCase())
}

function isAbortError(err: unknown): boolean {
  return typeof err === "object" &&
    err !== null &&
    (err as { name?: unknown }).name === "AbortError"
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
  let abortSource: "timeout" | "external" | undefined
  const abortAttempt = (source: "timeout" | "external") => {
    abortSource ??= source
    controller.abort()
  }
  const timeoutId = setTimeout(() => {
    abortAttempt("timeout")
  }, timeout)

  // If an external signal is provided, abort our controller when it fires.
  // We track the listener so we can remove it in the finally block below —
  // otherwise retry-loop one-shot listeners accumulate on the
  // caller's signal across retries before any of them fire.  The signal
  // itself typically lives at least as long as a user interaction, so
  // ambient listener pressure during a retry burst is worth avoiding.
  let externalAbortHandler: (() => void) | undefined
  if (externalSignal) {
    if (externalSignal.aborted) {
      abortAttempt("external")
    } else {
      externalAbortHandler = () => abortAttempt("external")
      externalSignal.addEventListener("abort", externalAbortHandler, { once: true })
    }
  }

  try {
    const res = await fetch(url, {
      ...fetchOptions,
      credentials: fetchOptions.credentials ?? "same-origin",
      headers: requestHeaders(fetchOptions.headers),
      signal: controller.signal,
    })
    if (!res.ok) {
      await throwApiError(res)
    }
    return await res.json() as T
  } catch (err) {
    if (abortSource === "timeout" && isAbortError(err)) {
      throw new ApiTimeoutError(url, timeout)
    }
    throw err
  } finally {
    clearTimeout(timeoutId)
    if (externalAbortHandler) {
      externalSignal?.removeEventListener("abort", externalAbortHandler)
    }
  }
}

// Exported for split-chunk API modules (e.g. api/dispersion.ts): endpoints
// consumed only by lazy-loaded panels live outside this module so their code
// stays out of the initial bundle, but they share the same fetch machinery.
export async function request<T>(
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

export function post<T>(url: string, body: unknown, options: MutationOptions = {}): Promise<T> {
  return request<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...options,
  })
}

/**
 * POST JSON and return the untouched response for non-JSON transports.
 * Split endpoint modules use this for authenticated SSE streams.
 */
export async function postRawStream(
  url: string,
  body: unknown,
  options: Pick<ApiClientOptions, "signal"> = {},
): Promise<Response> {
  const response = await fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: requestHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
    signal: options.signal,
  })
  if (!response.ok) {
    await throwApiError(response)
  }
  return response
}

function del<T>(url: string, options: ApiClientOptions = {}): Promise<T> {
  return request<T>(url, { method: "DELETE", ...options })
}

export function checkHauteSession(options: ApiClientOptions = {}): Promise<{ ok: boolean }> {
  return request<unknown>("/api/session", {
    ...options,
    timeout: options.timeout ?? 5_000,
    retry: options.retry ?? { maxRetries: 0, baseDelayMs: 100 },
  }).then(parseHauteSessionResponse)
}

// ---------------------------------------------------------------------------
// Pipeline endpoints
// ---------------------------------------------------------------------------

export function loadPipeline(options?: ApiClientOptions): Promise<PipelineGraph> {
  return request<unknown>("/api/pipeline", options)
    .then(parsePipelineResponse)
}

export interface PreviewNodeArgs {
  graph: GraphPayload
  nodeId: string
  rowLimit: number
  source?: string
  requestedPreviewColumns?: string[]
  /** Frame/emit-table label to preview for a multi-frame producer (a
   * multi-table apiInput). Sent as `port_label`; part of the backend preview
   * cache key, so each frame is a distinct cache entry. */
  portLabel?: string
  streamingChunkSize?: number
  signal?: AbortSignal
  timeout?: number
}

export function previewNode(args: PreviewNodeArgs): Promise<PreviewNodeResponse> {
  const {
    graph,
    nodeId,
    rowLimit,
    source,
    requestedPreviewColumns,
    portLabel,
    streamingChunkSize,
    signal,
    timeout = 120_000,
  } = args
  return post<unknown>(
    "/api/pipeline/preview",
    {
      graph,
      node_id: nodeId,
      row_limit: rowLimit,
      source: source ?? "live",
      ...(requestedPreviewColumns ? { requested_preview_columns: requestedPreviewColumns } : {}),
      ...(portLabel !== undefined ? { port_label: portLabel } : {}),
      ...(streamingChunkSize !== undefined ? { streaming_chunk_size: streamingChunkSize } : {}),
    },
    { signal, timeout },
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
    preserved_blocks: string[]
  },
  options?: MutationOptions,
): Promise<SavePipelineResponse> {
  return post<unknown>("/api/pipeline/save", payload, options).then(parseSavePipelineResponse)
}

export interface OutputAssembleDryRunArgs {
  graph: GraphPayload
  nodeId: string
  /** The in-progress (volatile, unsaved) outputMapping to preview. The route
   * swaps this into the OUTPUT node's config, overriding whatever is on disk —
   * so the preview reflects the editor's CURRENT mapping, not the saved file. */
  outputMapping: Array<Record<string, unknown>>
  outputFormat?: string
  rowLimit?: number
  source?: string
  signal?: AbortSignal
  timeout?: number
}

/**
 * Assemble an OUTPUT node's response document from an UNSAVED outputMapping.
 *
 * Mirrors `POST /api/output-assemble/dry-run` (see
 * `src/haute/routes/output_assemble.py`): the route validates the volatile
 * mapping, swaps it into the target node's config, runs the graph up to that
 * node, and returns the rendered/pruned document. Structured failures arrive
 * as ApiError (422 mapping-invalid, 400 bad graph/node, 404 node-not-found,
 * 503 admission, 504 timeout, 500 internal); a run that completes but the node
 * itself errored returns 200 with `status: "error"` + `error`.
 */
export function outputAssembleDryRun(
  args: OutputAssembleDryRunArgs,
): Promise<OutputAssembleDryRunResponse> {
  const {
    graph,
    nodeId,
    outputMapping,
    outputFormat,
    rowLimit,
    source,
    signal,
    timeout = 120_000,
  } = args
  return post<unknown>(
    "/api/output-assemble/dry-run",
    {
      graph,
      node_id: nodeId,
      output_mapping: outputMapping,
      output_format: outputFormat ?? "json",
      ...(rowLimit !== undefined ? { row_limit: rowLimit } : {}),
      source: source ?? "live",
    },
    { signal, timeout },
  ).then(parseOutputAssembleDryRunResponse)
}

export interface TraceCellArgs {
  graph: GraphPayload
  row_index: number
  target_node_id: string
  column?: string | null
  row_limit?: number
  source?: string
  row_values?: Record<string, unknown>
  streamingChunkSize?: number
  signal?: AbortSignal
  timeout?: number
}

export function traceCell(args: TraceCellArgs): Promise<TraceResponse> {
  const { streamingChunkSize, signal, timeout = 120_000, ...payload } = args
  const body = streamingChunkSize !== undefined
    ? { ...payload, streaming_chunk_size: streamingChunkSize }
    : payload
  return post<unknown>("/api/pipeline/trace", body, { signal, timeout }).then(parseTraceResponse)
}

export interface WriteOutputArgs {
  graph: GraphPayload
  nodeId: string
  source?: string
  streamingChunkSize?: number
  overwrite?: boolean
  signal?: AbortSignal
  timeout?: number
}

export interface ResolveOutputDestinationArgs {
  graph: GraphPayload
  nodeId: string
  signal?: AbortSignal
  timeout?: number
}

export function resolveOutputDestination(
  args: ResolveOutputDestinationArgs,
): Promise<OutputDestinationResponse> {
  const { graph, nodeId, signal, timeout = 30_000 } = args
  return post<unknown>(
    "/api/pipeline/output-destination",
    {
      graph,
      node_id: nodeId,
    },
    { signal, timeout },
  ).then(parseOutputDestinationResponse)
}

export function writeOutput(args: WriteOutputArgs): Promise<WriteOutputResponse> {
  const {
    graph,
    nodeId,
    source,
    streamingChunkSize,
    overwrite,
    signal,
    timeout = 300_000,
  } = args
  return post<unknown>(
    "/api/pipeline/write-output",
    {
      graph,
      node_id: nodeId,
      source: source ?? "live",
      overwrite: overwrite ?? false,
      ...(streamingChunkSize !== undefined ? { streaming_chunk_size: streamingChunkSize } : {}),
    },
    { signal, timeout },
  ).then(parseWriteOutputResponse)
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
    base_revision: string
    preserved_blocks: string[]
  },
  options?: { signal?: AbortSignal },
): Promise<SubmodelCreateResponse> {
  return post<unknown>("/api/submodel/create", payload, options).then(parseSubmodelCreateResponse)
}

export function loadSubmodel(
  definitionId: string,
  parentSourceFile: string,
  options?: { signal?: AbortSignal },
): Promise<SubmodelGraphResponse> {
  return request<unknown>(
    `/api/submodel/${encodeURIComponent(definitionId)}?source_file=${encodeURIComponent(parentSourceFile)}`,
    options,
  ).then(parseSubmodelGraphResponse)
}

export function dissolveSubmodel(
  payload: {
    instance_id: string
    graph: GraphPayload
    preamble: string
    source_file: string
    pipeline_name: string
    pipeline_description?: string
    base_revision: string
    preserved_blocks: string[]
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

// ---------------------------------------------------------------------------
// Data In/Out format capabilities (dataInput / dataOutput node editors)
// ---------------------------------------------------------------------------

export function fetchIoCapabilities(
  options?: { signal?: AbortSignal },
): Promise<IoCapabilitiesResponse> {
  return request<unknown>("/api/io-capabilities", options).then(parseIoCapabilitiesResponse)
}

// ---------------------------------------------------------------------------
// Input-cache endpoints
// ---------------------------------------------------------------------------

export function buildInputCache(
  payload: InputCacheBuildRequest,
  options?: { signal?: AbortSignal; timeout?: number },
): Promise<InputCacheBuildResponse> {
  return post<unknown>("/api/input-cache/build", payload, options).then(parseInputCacheBuildResponse)
}

export function getInputCacheJob(
  jobId: string,
  options?: { signal?: AbortSignal },
): Promise<InputCacheJobStatusResponse> {
  return request<unknown>(`/api/input-cache/jobs/${encodeURIComponent(jobId)}`, options).then(parseInputCacheJobStatusResponse)
}

export function cancelInputCacheJob(
  jobId: string,
  options?: { signal?: AbortSignal },
): Promise<InputCacheCancelResponse> {
  return del<unknown>(`/api/input-cache/jobs/${encodeURIComponent(jobId)}`, options).then(parseInputCacheCancelResponse)
}

export function getInputCacheStatus(
  payload: { schema_version: 1; config: Record<string, unknown> },
  options?: { signal?: AbortSignal },
): Promise<InputCacheSnapshotResponse> {
  return post<unknown>("/api/input-cache/status", payload, options).then(parseInputCacheSnapshotResponse)
}

export function clearInputCache(
  payload: { schema_version: 1; config: Record<string, unknown> },
  options?: { signal?: AbortSignal },
): Promise<InputCacheSnapshotResponse> {
  return post<unknown>("/api/input-cache/clear", payload, options).then(parseInputCacheSnapshotResponse)
}

// ---------------------------------------------------------------------------
// Explore endpoints
// ---------------------------------------------------------------------------

export interface RunExploreArgs {
  graph: GraphPayload
  node_id: string
  source?: string
  streamingChunkSize?: number
  signal?: AbortSignal
  timeout?: number
}

export function runExplore(args: RunExploreArgs): Promise<ExploreRunResponse> {
  const { streamingChunkSize, signal, timeout = 300_000, ...payload } = args
  return post<unknown>(
    "/api/explore/run",
    {
      ...payload,
      source: payload.source ?? "live",
      ...(streamingChunkSize !== undefined ? { streaming_chunk_size: streamingChunkSize } : {}),
    },
    { signal, timeout },
  ).then(parseExploreRunResponse)
}

export function getExploreStatus<T extends ExploreStatusResponse = ExploreStatusResponse>(
  jobId: string,
  options?: { signal?: AbortSignal },
): Promise<T> {
  return request<unknown>(`/api/explore/status/${encodeURIComponent(jobId)}`, options)
    .then((data) => parseExploreStatusResponse(data) as T)
}

export function cancelExplore<T extends ExploreStatusResponse = ExploreStatusResponse>(
  jobId: string,
  options?: { signal?: AbortSignal },
): Promise<T> {
  return post<unknown>(`/api/explore/cancel/${encodeURIComponent(jobId)}`, {}, options)
    .then((data) => parseExploreStatusResponse(data) as T)
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
    .then(async (data) => (await import("../types/trainGuards")).parseTrainStatusResponse(data) as T)
}

export function cancelTrain<T extends TrainStatusResponse = TrainStatusResponse>(
  jobId: string,
  options?: { signal?: AbortSignal },
): Promise<T> {
  return post<unknown>(
    `/api/modelling/train/cancel/${encodeURIComponent(jobId)}`,
    undefined,
    options,
  ).then(async (data) => (await import("../types/trainGuards")).parseTrainStatusResponse(data) as T)
}

export interface TrainModelArgs {
  graph: GraphPayload
  node_id: string
  source?: string
  streamingChunkSize?: number
  signal?: AbortSignal
  timeout?: number
}

export function trainModel(args: TrainModelArgs): Promise<TrainResponse> {
  // Pipeline execution can take minutes for large datasets - use a 10-minute timeout
  const { streamingChunkSize, signal, timeout = 600_000, ...payload } = args
  return post<unknown>(
    "/api/modelling/train",
    {
      ...payload,
      source: payload.source ?? "live",
      ...(streamingChunkSize !== undefined ? { streaming_chunk_size: streamingChunkSize } : {}),
    },
    { signal, timeout },
  ).then(async (data) => (await import("../types/trainGuards")).parseTrainResponse(data))
}

export function estimateTrainingRam(
  payload: { graph: GraphPayload; node_id: string; source?: string },
  options?: { signal?: AbortSignal },
): Promise<TrainEstimate> {
  return post<unknown>("/api/modelling/estimate", { ...payload, source: payload.source ?? "live" }, { timeout: 30_000, ...options })
    .then(async (data) => (await import("../types/trainGuards")).parseTrainEstimateResponse(data))
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

export interface SolveOptimiserArgs {
  graph: GraphPayload
  node_id: string
  streamingChunkSize?: number
  signal?: AbortSignal
  timeout?: number
}

export function solveOptimiser(args: SolveOptimiserArgs): Promise<OptimiserSolveResponse> {
  const { streamingChunkSize, signal, timeout = 300_000, ...payload } = args
  const body = streamingChunkSize !== undefined
    ? { ...payload, streaming_chunk_size: streamingChunkSize }
    : payload
  return post<unknown>("/api/optimiser/solve", body, { signal, timeout })
    .then(parseSolveOptimiserResponse)
}

export interface EstimateOptimiserSolveArgs {
  graph: GraphPayload
  node_id: string
  source?: string
  streamingChunkSize?: number
  signal?: AbortSignal
  timeout?: number
}

export function estimateOptimiserSolve(
  args: EstimateOptimiserSolveArgs,
): Promise<OptimiserEstimate> {
  const { streamingChunkSize, signal, timeout = 30_000, ...payload } = args
  return post<unknown>(
    "/api/optimiser/estimate",
    {
      ...payload,
      source: payload.source ?? "live",
      ...(streamingChunkSize !== undefined ? { streaming_chunk_size: streamingChunkSize } : {}),
    },
    { signal, timeout },
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

export function getFrontierStatus(
  jobId: string,
  options?: { signal?: AbortSignal },
): Promise<FrontierStatusResponse> {
  return request<unknown>(
    `/api/optimiser/frontier/status/${encodeURIComponent(jobId)}`,
    options,
  ).then(parseFrontierStatusResponse)
}

export interface StartOptimiserFrontierAutoRangeArgs {
  graph: GraphPayload
  node_id: string
  streamingChunkSize?: number
  signal?: AbortSignal
  timeout?: number
}

export function startOptimiserFrontierAutoRange(
  args: StartOptimiserFrontierAutoRangeArgs,
): Promise<FrontierAutoRangeStartResponse> {
  const { streamingChunkSize, signal, timeout, ...payload } = args
  const body = streamingChunkSize !== undefined
    ? { ...payload, streaming_chunk_size: streamingChunkSize }
    : payload
  return post<unknown>("/api/optimiser/frontier/auto-range/start", body, { signal, timeout })
    .then(parseFrontierAutoRangeStartResponse)
}

export function getOptimiserFrontierAutoRangeStatus(
  jobId: string,
  options?: { signal?: AbortSignal },
): Promise<FrontierAutoRangeStatusResponse> {
  return request<unknown>(
    `/api/optimiser/frontier/auto-range/status/${encodeURIComponent(jobId)}`,
    options,
  ).then(parseFrontierAutoRangeStatusResponse)
}

export function cancelOptimiserFrontierAutoRange(
  jobId: string,
  options?: { signal?: AbortSignal },
): Promise<FrontierAutoRangeStatusResponse> {
  return post<unknown>(
    `/api/optimiser/frontier/auto-range/cancel/${encodeURIComponent(jobId)}`,
    {},
    options,
  ).then(parseFrontierAutoRangeStatusResponse)
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

// ---------------------------------------------------------------------------
// JSON cache endpoints
// ---------------------------------------------------------------------------

export function buildJsonCache(
  payload: { path: string; config_path?: string; volatile_schema?: Record<string, unknown> },
  options?: { signal?: AbortSignal; timeout?: number },
): Promise<JsonCacheBuildResponse> {
  return post<unknown>("/api/json-cache/build", payload, { timeout: 1_800_000, ...options }).then(parseJsonCacheBuildResponse)
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
  payload: { path: string; config_path?: string; volatile_schema?: Record<string, unknown> },
  options?: { signal?: AbortSignal },
): Promise<JsonCacheStatusResponse> {
  return post<unknown>("/api/json-cache/status", payload, options).then(parseJsonCacheStatusResponse)
}

export function deleteJsonCache(
  path: string,
  options?: { signal?: AbortSignal },
): Promise<{ cached: boolean; data_path: string }> {
  return del<unknown>(`/api/json-cache?path=${encodeURIComponent(path)}`, options).then(parseJsonCacheDeleteResponse)
}

/**
 * Request budget for complete *Infer Tables* schema discovery.
 *
 * The shared client timeout is 30 seconds, but a multi-GB structured input can
 * legitimately take minutes to scan. We keep inference complete by default
 * and give it the same budget as cache construction. A hidden head sample is
 * unsafe: a wholly new field appearing after the sample is ignored by build,
 * not rejected as a type widening, so it would disappear without warning.
 */
export const JSON_CACHE_INFER_TIMEOUT_MS = 1_800_000

/**
 * Sniff a v2 schema mapping from a structured input file.
 * Drives the ApiInputEditor's *Infer Tables* button.
 *
 * Returns a v2-shaped ``tables`` array; the caller stitches it into the
 * apiInput's existing ``path`` + ``contract``.
 */
export function inferJsonCacheSchema(
  payload: { path: string; sample_size?: number },
  options?: { signal?: AbortSignal; timeout?: number },
): Promise<{ tables: Array<Record<string, unknown>> }> {
  return post<unknown>(
    "/api/json-cache/infer",
    payload,
    { timeout: JSON_CACHE_INFER_TIMEOUT_MS, ...options },
  ).then(parseJsonCacheSchemaInferenceResponse)
}

// ---------------------------------------------------------------------------
// MLflow endpoints (used by ModelScoreEditor + OptimiserApplyEditor)
// ---------------------------------------------------------------------------

export function getExperiments(
  options?: { signal?: AbortSignal },
): Promise<MlflowExperiment[]> {
  return request<unknown>("/api/mlflow/experiments", options).then(parseMlflowExperiments)
}

export function getRuns(
  experimentId: string,
  artifactFilter?: string,
  options?: { signal?: AbortSignal },
): Promise<MlflowRun[]> {
  const params = new URLSearchParams({ experiment_id: experimentId })
  if (artifactFilter) params.set("artifact_filter", artifactFilter)
  return request<unknown>(`/api/mlflow/runs?${params.toString()}`, options).then(parseMlflowRuns)
}

export function getModels(
  options?: { signal?: AbortSignal },
): Promise<MlflowModel[]> {
  return request<unknown>("/api/mlflow/models", options).then(parseMlflowModels)
}

export function getModelVersions(
  modelName: string,
  options?: { signal?: AbortSignal },
): Promise<MlflowModelVersion[]> {
  return request<unknown>(`/api/mlflow/model-versions?model_name=${encodeURIComponent(modelName)}`, options).then(parseMlflowModelVersions)
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
  return request<unknown>(`/api/files?${params.toString()}`, options).then(parseFileListResponse)
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
  // Explicit `=== undefined` (not truthiness) so a caller-supplied `limit` of 0
  // is forwarded and rejected loudly by the backend (Query(ge=1)) rather than
  // silently swapped for the default.
  if (limit !== undefined) p.set("limit", String(limit))
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

/** Whole-forest topology for the graph rail: every working pair's spine plus
 *  ancestry-derived fork attachments. Chrome, not history — callers fetch it
 *  best-effort and degrade to no rail on transport or parser failure. */
export function getGitGraph(
  limit?: number,
  options?: { signal?: AbortSignal },
): Promise<GitGraphResponse> {
  const qs = limit !== undefined ? `?limit=${limit}` : ""
  return request<unknown>(`/api/git/graph${qs}`, options).then(parseGitGraphResponse)
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

/** Restore a deleted pair from the trash tombstone (the inverse of delete —
 *  pure ref/state ops, so it's instant and safe to drive from Undo). */
export function undeleteBranch(
  branch: string,
  options?: { signal?: AbortSignal },
): Promise<GitUndeleteResponse> {
  return post<unknown>("/api/git/undelete", { branch }, options).then(parseGitUndeleteResponse)
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

/** Deliberately publish branch history to a remote, bootstrapping its default branch when needed (S16/S33). */
export function gitPush(
  remote: string,
  options?: { signal?: AbortSignal },
): Promise<GitPushResponse> {
  return post<unknown>("/api/git/push", { remote }, options).then(parseGitPushResponse)
}

/** Catch the working pair up to a remote by fast-forward only (P7 D1/D2) — a
 *  conflict-free ref advance, never a merge. */
export function gitFastForward(
  remote: string,
  options?: { signal?: AbortSignal },
): Promise<GitFastForwardResponse> {
  return post<unknown>("/api/git/fast-forward", { remote }, options).then(
    parseGitFastForwardResponse,
  )
}

/** Set the local fork aside under a dated name and adopt the remote (P7 M3) —
 *  both lineages kept, never a merge. */
export function gitBranchAway(
  remote: string,
  options?: { signal?: AbortSignal },
): Promise<GitBranchAwayResponse> {
  return post<unknown>("/api/git/branch-away", { remote }, options).then(
    parseGitBranchAwayResponse,
  )
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
