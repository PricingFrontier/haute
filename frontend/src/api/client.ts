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
  CacheClearResponse,
  CacheNodesResponse,
  CacheUsageResponse,
  DatabricksCatalogsResponse,
  DatabricksSchemasResponse,
  DatabricksTablesResponse,
  DatabricksWarehousesResponse,
  DissolveSubmodelResponse,
  EditorIdentityBatchRequest,
  EditorIdentityBatchResponse,
  ExplorePivotMembersResponse,
  ExplorePivotRunResponse,
  ExplorePivotStatusResponse,
  BrowseFilesResponse,
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
  GitBindStorageResponse,
  GitForkStorageResponse,
  GitUpstreamStatus,
  GitCommitContext,
  GitGraphResponse,
  GitMoveResponse,
  GitSetIdentityResponse,
  GitSetWorkingBranchResponse,
  GitWorkingBranchResponse,
  GraphPayload,
  IoCapabilitiesResponse,
  ModellingGpuStatusResponse,
  InputCacheBuildRequest,
  InputCacheBuildResponse,
  InputCacheCancelResponse,
  InputCacheJobStatusResponse,
  ExecutionSettings,
  InputCacheSnapshotResponse,
  InputCacheSourceRequest,
  MlflowDestinationKey,
  MlflowDestinationsResponse,
  MlflowSettingsResponse,
  MlflowSettingsUpdateRequest,
  MlflowTestConnectionRequest,
  MlflowTestConnectionResponse,
  MlflowExperiment,
  LogExperimentResponse,
  MlflowLogResponse,
  ModelSaveDestinationRequest,
  ModelSaveDestinationResponse,
  SaveModelRequest,
  SaveModelResponse,
  MlflowModel,
  MlflowModelVersion,
  MlflowRun,
  NodeDataClearResponse,
  NodeDataPointResponse,
  BandingStatsResponse,
  RatingLevelsResponse,
  NodeDataProfileResponse,
  NodeDataRunResponse,
  NodeDataStatusResponse,
  LogOptimiserToMlflowRequest,
  OptimiserEstimate,
  OptimiserSolveResponse,
  OptimiserStatusResponse,
  OutputAssembleDryRunResponse,
  OutputDestinationResponse,
  PipelineGraph,
  PolarsStepsRenderResponse,
  PreviewInputsResponse,
  PreviewNodeResponse,
  PreviewProgressResponse,
  SaveOptimiserRequest,
  TraceSeedPlanEntry,
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
  GitMilestoneFork,
  GitPushRejection,
  SessionStatusResponse,
} from "./types"
import {
  parseCacheClearResponse,
  parseCacheNodesResponse,
  parseCacheUsageResponse,
  parseDissolveSubmodelResponse,
  explorePivotMembersFromContract,
  frontierAutoRangeStatusFromContract,
  frontierStatusFromContract,
  optimiserStatusFromContract,
  explorePivotRunFromContract,
  explorePivotStatusFromContract,
  parseNodeDataClearResponse,
  parseNodeDataPointResponse,
  parseNodeDataRunResponse,
  parseNodeDataStatusResponse,
  parseInputCacheBuildResponse,
  parseInputCacheCancelResponse,
  parseInputCacheJobStatusResponse,
  parseInputCacheSnapshotResponse,
  parseJsonCacheSchemaInferenceResponse,
  parseOutputDestinationResponse,
  parseOutputAssembleDryRunResponse,
  parsePipelineResponse,
  parsePreviewInputsResponse,
  parsePreviewNodeResponse,
  parsePreviewProgressResponse,
  parseSavePipelineResponse,
  parseSchemaResponse,
  parseWriteOutputResponse,
  parseSubmodelCreateResponse,
  parseSubmodelGraphResponse,
  parseTraceResponse,
  isPlainObject,
} from "../types/guards"
import { expectGeneratedContract } from "../types/generatedContractValidation"

// Generated response validators load with their first response, so none of
// them reaches the initial bundle.
const databricksValidators = () => import("../generated/api-contracts.databricks.validators.mjs")
const editorValidators = () => import("../generated/api-contracts.editor.validators.mjs")
const exploreValidators = () => import("../generated/api-contracts.explore.validators.mjs")
const factorsValidators = () => import("../generated/api-contracts.factors.validators.mjs")
const gitValidators = () => import("../generated/api-contracts.git.validators.mjs")
const ioValidators = () => import("../generated/api-contracts.io.validators.mjs")
const mlflowValidators = () => import("../generated/api-contracts.mlflow.validators.mjs")
const modellingValidators = () => import("../generated/api-contracts.modelling.validators.mjs")
const optimiserValidators = () => import("../generated/api-contracts.optimiser.validators.mjs")
const sessionValidators = () => import("../generated/api-contracts.session.validators.mjs")
const utilityValidators = () => import("../generated/api-contracts.utility.validators.mjs")
import {
  parseRemoveUnavailableNodeApplyResponse,
  parseRecoverUnavailableNodeApplyResponse,
  type RecoverUnavailableNodeApplyResponse,
  type RecoverUnavailableNodeRequest,
  type RemoveUnavailableNodeApplyResponse,
  type RemoveUnavailableNodeRequest,
} from "../types/pipelineRepair"
import {
  parsePipelineEditorDocument,
  type PipelineEditorDocument,
} from "../types/pipelineDocument"

import { validateApiResponse } from "./responseValidation"

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

// PAIRED INVARIANT with stores/singleFlight.ts: the retry loop's worst case
// (default 30s timeout × (maxRetries + 1) attempts + backoff ≈ 121s) must stay
// BELOW DEFAULT_STALE_PENDING_AFTER_MS (150s), so a slow network always
// surfaces the transport's specific ApiTimeoutError before the store-level
// stalled-request backstop fires. Raise that constant in step with any change
// that lengthens this budget.
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
  read: (response: Response) => Promise<T>,
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
    return await read(res)
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
export function request<T>(url: string, options: RequestOptions = {}): Promise<T> {
  return requestWith(url, options, (response) => response.json() as Promise<T>)
}

/** `request` with a caller-supplied reader for a successful response (e.g. to read its headers). */
async function requestWith<T>(
  url: string,
  options: RequestOptions,
  read: (response: Response) => Promise<T>,
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
      return await attemptFetch<T>(url, fetchOptions, timeout, externalSignal, read)
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

export function checkHauteSession(options: ApiClientOptions = {}): Promise<SessionStatusResponse> {
  return request<unknown>("/api/session", {
    ...options,
    timeout: options.timeout ?? 5_000,
    retry: options.retry ?? { maxRetries: 0, baseDelayMs: 100 },
  }).then(async (data) => expectGeneratedContract("SessionStatusResponse", (await sessionValidators()).validateSessionStatusResponse, data))
}

// ---------------------------------------------------------------------------
// Pipeline endpoints
// ---------------------------------------------------------------------------

/** Response header naming the fingerprint of the editor document a load route returns. */
export const DOCUMENT_FINGERPRINT_HEADER = "x-haute-document-fingerprint"

export interface LoadedPipeline {
  /** The unvalidated editor document; narrow it with `parsePipelineEditorDocument`. */
  document: unknown
  /** The server's fingerprint of exactly this document, sent back on the first live-sync resync. */
  documentFingerprint: string
}

export async function loadPipeline(options: ApiClientOptions = {}): Promise<LoadedPipeline> {
  const { document, documentFingerprint } = await requestWith("/api/pipeline", options, async (response) => ({
    document: await response.json() as unknown,
    documentFingerprint: response.headers.get(DOCUMENT_FINGERPRINT_HEADER)?.trim() ?? "",
  }))
  if (!documentFingerprint) {
    throw new Error(`loadPipeline: response has no ${DOCUMENT_FINGERPRINT_HEADER} header`)
  }
  return { document, documentFingerprint }
}

const EDITOR_NODE_TYPES_WITHOUT_DEFAULT_INPUT = new Set([
  "apiInput",
  "submodel",
  "submodelPort",
])

export async function resolveEditorNodeIdentities(
  payload: EditorIdentityBatchRequest,
  options?: MutationOptions,
): Promise<EditorIdentityBatchResponse> {
  const data = await post<unknown>("/api/pipeline/editor-identities", payload, options)
  const response = expectGeneratedContract(
    "EditorIdentitiesResponse",
    (await editorValidators()).validateEditorIdentitiesResponse,
    data,
  )
  if (
    response.identities.length !== payload.nodes.length
    || response.identities.some(
      (identity, index) => identity.node_id !== payload.nodes[index]?.node_id,
    )
  ) {
    throw new Error(
      "resolveEditorNodeIdentities: response identities must exactly match request node order",
    )
  }
  response.identities.forEach((identity, index) => {
    const requestNode = payload.nodes[index]
    if (!requestNode) {
      throw new Error("resolveEditorNodeIdentities: response identities must exactly match request node order")
    }
    const actualHandles = Object.keys(identity.source_handle_input_names)
    if (
      actualHandles.length !== requestNode.source_handles.length
      || requestNode.source_handles.some(
        (handle) => !Object.prototype.hasOwnProperty.call(
          identity.source_handle_input_names,
          handle,
        ),
      )
    ) {
      throw new Error(
        `resolveEditorNodeIdentities: source handles must exactly match the request for node ${requestNode.node_id}`,
      )
    }
    if (
      requestNode.node_type === "apiInput"
      && requestNode.source_handles.some(
        (handle) => identity.source_handle_input_names[handle] !== handle,
      )
    ) {
      throw new Error(
        `resolveEditorNodeIdentities: API frame identities must preserve raw source handles for node ${requestNode.node_id}`,
      )
    }
    const expectsNullDefault = EDITOR_NODE_TYPES_WITHOUT_DEFAULT_INPUT.has(
      requestNode.node_type,
    )
    if (
      (expectsNullDefault && identity.default_input_name !== null)
      || (!expectsNullDefault && identity.default_input_name === null)
    ) {
      throw new Error(
        `resolveEditorNodeIdentities: invalid default input identity for node ${requestNode.node_id}`,
      )
    }
  })
  return response
}

export function applyRemoveUnavailableNode(
  args: RemoveUnavailableNodeRequest,
  options?: MutationOptions,
): Promise<RemoveUnavailableNodeApplyResponse> {
  return post<unknown>("/api/pipeline/repair/remove/apply", {
    source_file: args.sourceFile,
    source_revision: args.sourceRevision,
    target_source_file: args.targetSourceFile,
    target_recovery_id: args.targetRecoveryId,
    delete_config: args.deleteConfig,
  }, options).then(parseRemoveUnavailableNodeApplyResponse)
}

export function applyRecoverUnavailableNode(
  args: RecoverUnavailableNodeRequest,
  options?: MutationOptions,
): Promise<RecoverUnavailableNodeApplyResponse> {
  return post<unknown>("/api/pipeline/repair/recover/apply", {
    source_file: args.sourceFile,
    source_revision: args.sourceRevision,
    target_source_file: args.targetSourceFile,
    target_recovery_id: args.targetRecoveryId,
    action: args.action,
  }, options).then(parseRecoverUnavailableNodeApplyResponse)
}

export interface ScopedNodeSaveRequest {
  sourceFile: string
  sourceRevision: string
  targetSourceFile: string
  targetRecoveryId: string
  config: Record<string, unknown>
}

/** Save one `scoped_editable` node in isolation while the document stays fenced. */
export function saveNodeScoped(
  args: ScopedNodeSaveRequest,
  options?: MutationOptions,
): Promise<PipelineEditorDocument> {
  return post<unknown>("/api/pipeline/node/save", {
    source_file: args.sourceFile,
    source_revision: args.sourceRevision,
    target_source_file: args.targetSourceFile,
    target_recovery_id: args.targetRecoveryId,
    config: args.config,
  }, options).then(parsePipelineEditorDocument)
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
  /** Chosen by the caller so it can poll this request's step progress. */
  requestId?: string
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
    requestId,
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
      ...(requestId !== undefined ? { request_id: requestId } : {}),
    },
    { signal, timeout },
  ).then((data) => parsePreviewNodeResponse(data) as PreviewNodeResponse)
}

/**
 * The step progress of this client's own preview *requestId*, or null when
 * there is none to show: not registered yet, answered from a cache, or settled.
 * One quick attempt, no retries: the next poll asks again.
 */
export async function getPreviewProgress(
  requestId: string,
  options: { signal?: AbortSignal } = {},
): Promise<PreviewProgressResponse | null> {
  try {
    return parsePreviewProgressResponse(
      await request<unknown>(`/api/pipeline/preview/progress/${encodeURIComponent(requestId)}`, {
        ...options,
        timeout: 2_000,
        retry: { maxRetries: 0 },
      }),
    )
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null
    throw err
  }
}

export interface PreviewInputsArgs {
  graph: GraphPayload
  nodeId: string
  source?: string
  requestedPreviewColumns?: string[]
  portLabel?: string
  signal?: AbortSignal
}

/**
 * The inputs a preview of `nodeId` would read — snapshot-backed Data Inputs
 * and structured API Inputs — so only those are prepared before it. A preview
 * seeded from shared snapshots reads nothing above its seeds.
 */
export function previewInputs(args: PreviewInputsArgs): Promise<PreviewInputsResponse> {
  return post<unknown>(
    "/api/pipeline/preview/inputs",
    {
      graph: args.graph,
      node_id: args.nodeId,
      source: args.source ?? "live",
      ...(args.requestedPreviewColumns
        ? { requested_preview_columns: args.requestedPreviewColumns }
        : {}),
      ...(args.portLabel !== undefined ? { port_label: args.portLabel } : {}),
    },
    { signal: args.signal },
  ).then(parsePreviewInputsResponse)
}

export interface RenderPolarsStepsArgs {
  steps: unknown[]
  inputNames: string[]
  /** `input` for a Transform (the first step chooses an input), `frame` when `df` is already bound. */
  start: "input" | "frame"
  signal?: AbortSignal
}

/** Render a low-code step list to the Polars code it stands for. */
export function renderPolarsSteps(args: RenderPolarsStepsArgs): Promise<PolarsStepsRenderResponse> {
  return post<unknown>(
    "/api/pipeline/polars-steps/render",
    { steps: args.steps, input_names: args.inputNames, start: args.start },
    { signal: args.signal },
  ).then(async (data) => expectGeneratedContract("PolarsStepsRenderResponse", (await editorValidators()).validatePolarsStepsRenderResponse, data))
}

export interface RecoveryPreviewNodeArgs {
  sourceFile: string
  sourceRevision: string
  targetRecoveryId: string
  rowLimit: number
  source?: string
  requestedPreviewColumns?: string[]
  portLabel?: string
  requestId?: string
  signal?: AbortSignal
  timeout?: number
}

export function previewRecoveryNode(
  args: RecoveryPreviewNodeArgs,
): Promise<PreviewNodeResponse> {
  const {
    sourceFile,
    sourceRevision,
    targetRecoveryId,
    rowLimit,
    source,
    requestedPreviewColumns,
    portLabel,
    requestId,
    signal,
    timeout = 120_000,
  } = args
  return post<unknown>(
    "/api/pipeline/recovery-preview",
    {
      source_file: sourceFile,
      source_revision: sourceRevision,
      target_recovery_id: targetRecoveryId,
      row_limit: rowLimit,
      source: source ?? "live",
      ...(requestedPreviewColumns ? { requested_preview_columns: requestedPreviewColumns } : {}),
      ...(portLabel !== undefined ? { port_label: portLabel } : {}),
      ...(requestId !== undefined ? { request_id: requestId } : {}),
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
    base_revision: string | null
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
  /** The `seed_plan` of the preview being traced: the trace reads exactly
   * those generations, and none when it is empty. */
  seed_plan: TraceSeedPlanEntry[]
  signal?: AbortSignal
  timeout?: number
}

export function traceCell(args: TraceCellArgs): Promise<TraceResponse> {
  const { signal, timeout = 120_000, ...payload } = args
  return post<unknown>("/api/pipeline/trace", payload, { signal, timeout }).then(parseTraceResponse)
}

export interface WriteOutputArgs {
  graph: GraphPayload
  nodeId: string
  source?: string
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

/**
 * Loads the backend's canonical transport graph. The returned graph is not
 * editor-ready: callers must resolve its root and nested definition identities
 * before publishing it into live editor state.
 */
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
  return request<unknown>("/api/io-capabilities", options).then(async (data) => expectGeneratedContract("IoCapabilitiesResponse", (await ioValidators()).validateIoCapabilitiesResponse, data))
}

// ---------------------------------------------------------------------------
// Cache inventory (the cache settings pane)
// ---------------------------------------------------------------------------

/**
 * Report every node of `graph` and everything else the store holds.
 *
 * Costs one point resolution per node on top of the store walk, so it is read
 * with the usage on open and on Refresh, never on a timer.
 */
/**
 * Clear the identities a report's row named — exactly what that row reported,
 * and nothing else. A digest the store no longer holds is reported as not
 * cleared rather than failing.
 */
export function clearCacheIdentities(
  digests: string[],
  options?: { signal?: AbortSignal },
): Promise<CacheClearResponse> {
  return post<unknown>("/api/cache/clear", { digests }, options).then(parseCacheClearResponse)
}

/**
 * The snapshot store's size and the automatic captures' share of its budget.
 *
 * One pass over the store and no point resolution, so the preview status bar
 * reads it each time a preview settles.
 */
export function fetchCacheUsage(
  options?: { signal?: AbortSignal },
): Promise<CacheUsageResponse> {
  return request<unknown>("/api/cache/usage", options).then(parseCacheUsageResponse)
}

export function fetchCacheNodes(
  payload: { graph: unknown; source: string },
  options?: { signal?: AbortSignal },
): Promise<CacheNodesResponse> {
  return post<unknown>("/api/cache/nodes", payload, options).then(parseCacheNodesResponse)
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
  payload: InputCacheSourceRequest,
  options?: { signal?: AbortSignal },
): Promise<InputCacheSnapshotResponse> {
  return post<unknown>("/api/input-cache/status", payload, options).then(parseInputCacheSnapshotResponse)
}

export function clearInputCache(
  payload: InputCacheSourceRequest,
  options?: { signal?: AbortSignal },
): Promise<InputCacheSnapshotResponse> {
  return post<unknown>("/api/input-cache/clear", payload, options).then(parseInputCacheSnapshotResponse)
}

// ---------------------------------------------------------------------------
// Node data endpoints
// ---------------------------------------------------------------------------

export interface NodeDataArgs {
  graph: GraphPayload
  node_id: string
  source?: string
  signal?: AbortSignal
}

export function getNodeDataPoint(args: NodeDataArgs): Promise<NodeDataPointResponse> {
  const { signal, ...payload } = args
  return post<unknown>(
    "/api/node-data/point",
    {
      ...payload,
      source: payload.source ?? "live",
    },
    { signal },
  ).then(parseNodeDataPointResponse)
}

export function runNodeData(
  args: NodeDataArgs & { refresh?: boolean; timeout?: number },
): Promise<NodeDataRunResponse> {
  const { signal, timeout = 300_000, ...payload } = args
  return post<unknown>(
    "/api/node-data/run",
    {
      ...payload,
      source: payload.source ?? "live",
    },
    { signal, timeout },
  ).then(parseNodeDataRunResponse)
}

export function getNodeDataStatus(
  jobId: string,
  options?: { signal?: AbortSignal },
): Promise<NodeDataStatusResponse> {
  return request<unknown>(`/api/node-data/status/${encodeURIComponent(jobId)}`, options).then(
    (data) =>
      validateApiResponse("Could not read node data status", () =>
        parseNodeDataStatusResponse(data),
      ),
  )
}

export function cancelNodeData(
  jobId: string,
  options?: { signal?: AbortSignal },
): Promise<NodeDataStatusResponse> {
  return post<unknown>(
    `/api/node-data/cancel/${encodeURIComponent(jobId)}`,
    {},
    options,
  ).then(parseNodeDataStatusResponse)
}

export function getNodeDataProfile(args: NodeDataArgs): Promise<NodeDataProfileResponse> {
  const { signal, ...payload } = args
  return post<unknown>(
    "/api/node-data/profile",
    {
      ...payload,
      source: payload.source ?? "live",
    },
    { signal },
  ).then(async (data) => expectGeneratedContract("NodeDataProfileResponse", (await exploreValidators()).validateNodeDataProfileResponse, data))
}

export interface BandingStatsArgs extends NodeDataArgs {
  /** The factor in the editor, so counts follow what is being edited. */
  factor: Record<string, unknown>
  histogramBins?: number
  valueLimit?: number
}

export function getBandingStats(args: BandingStatsArgs): Promise<BandingStatsResponse> {
  const { signal, factor, histogramBins, valueLimit, ...payload } = args
  return post<unknown>(
    "/api/banding/stats",
    {
      ...payload,
      source: payload.source ?? "live",
      factor,
      ...(histogramBins === undefined ? {} : { histogram_bins: histogramBins }),
      ...(valueLimit === undefined ? {} : { value_limit: valueLimit }),
    },
    { signal },
  ).then(async (data) => expectGeneratedContract("BandingStatsResponse", (await factorsValidators()).validateBandingStatsResponse, data))
}

export interface RatingLevelsArgs {
  graph: GraphPayload
  node_id: string
  source?: string
  columns: string[]
  valueLimit?: number
  signal?: AbortSignal
}

export function getRatingLevels(args: RatingLevelsArgs): Promise<RatingLevelsResponse> {
  const { signal, columns, valueLimit, ...payload } = args
  return post<unknown>(
    "/api/rating/levels",
    {
      ...payload,
      source: payload.source ?? "live",
      columns,
      ...(valueLimit === undefined ? {} : { value_limit: valueLimit }),
    },
    { signal },
  ).then(async (data) => expectGeneratedContract("RatingLevelsResponse", (await factorsValidators()).validateRatingLevelsResponse, data))
}

export function clearNodeData(args: NodeDataArgs): Promise<NodeDataClearResponse> {
  const { signal, ...payload } = args
  return post<unknown>(
    "/api/node-data/clear",
    {
      ...payload,
      source: payload.source ?? "live",
    },
    { signal },
  ).then(parseNodeDataClearResponse)
}

// ---------------------------------------------------------------------------
// Explore endpoints
// ---------------------------------------------------------------------------

export interface RunExploreArgs {
  graph: GraphPayload
  node_id: string
  source?: string
  refresh?: boolean
  signal?: AbortSignal
  timeout?: number
}

export interface RunExplorePivotArgs {
  graph: GraphPayload
  node_id: string
  pivot: Record<string, unknown>
  source?: string
  signal?: AbortSignal
  timeout?: number
}

export function runExplorePivot(args: RunExplorePivotArgs): Promise<ExplorePivotRunResponse> {
  const { signal, timeout = 300_000, ...payload } = args
  return post<unknown>("/api/explore/pivots/run", {
    ...payload,
    source: payload.source ?? "live",
  }, { signal, timeout }).then(async (data) => explorePivotRunFromContract(
    expectGeneratedContract("ExplorePivotRunResponse", (await exploreValidators()).validateExplorePivotRunResponse, data),
  ))
}

export function getExplorePivotStatus(
  jobId: string,
  options?: { signal?: AbortSignal },
): Promise<ExplorePivotStatusResponse> {
  return request<unknown>(`/api/explore/pivots/status/${encodeURIComponent(jobId)}`, options)
    .then(async (data) => {
      const { validateExplorePivotStatusResponse } = await exploreValidators()
      return validateApiResponse("Could not read pivot status", () => explorePivotStatusFromContract(
        expectGeneratedContract("ExplorePivotStatusResponse", validateExplorePivotStatusResponse, data),
      ))
    })
}

export function cancelExplorePivot(
  jobId: string,
  options?: { signal?: AbortSignal },
): Promise<ExplorePivotStatusResponse> {
  return post<unknown>(`/api/explore/pivots/cancel/${encodeURIComponent(jobId)}`, {}, options)
    .then(async (data) => explorePivotStatusFromContract(
      expectGeneratedContract("ExplorePivotStatusResponse", (await exploreValidators()).validateExplorePivotStatusResponse, data),
    ))
}

export interface FetchExplorePivotMembersArgs {
  graph: GraphPayload
  node_id: string
  field: string
  source?: string
  search?: string | null
  signal?: AbortSignal
  timeout?: number
}

export function fetchExplorePivotMembers(
  args: FetchExplorePivotMembersArgs,
): Promise<ExplorePivotMembersResponse> {
  const { signal, timeout, ...payload } = args
  return post<unknown>("/api/explore/pivots/members", {
    ...payload,
    source: payload.source ?? "live",
  }, { signal, timeout }).then(async (data) => explorePivotMembersFromContract(
    expectGeneratedContract("ExplorePivotMembersResponse", (await exploreValidators()).validateExplorePivotMembersResponse, data),
  ))
}

// ---------------------------------------------------------------------------
// Modelling endpoints
// ---------------------------------------------------------------------------

/** Whether XGBoost can train on a GPU in the running server. */
export function fetchModellingGpuStatus(
  options?: { signal?: AbortSignal },
): Promise<ModellingGpuStatusResponse> {
  return request<unknown>("/api/modelling/gpu", options).then(async (data) => expectGeneratedContract("ModellingGpuStatusResponse", (await modellingValidators()).validateModellingGpuStatusResponse, data))
}


export function getTrainStatus(
  jobId: string,
  options?: { signal?: AbortSignal },
): Promise<TrainStatusResponse> {
  return request<unknown>(`/api/modelling/train/status/${encodeURIComponent(jobId)}`, options)
    .then(async (data) => {
      const { parseTrainStatusResponse } = await import("../types/trainGuards")
      return validateApiResponse("Could not read training status", () => parseTrainStatusResponse(data))
    })
}

export function cancelTrain(
  jobId: string,
  options?: { signal?: AbortSignal },
): Promise<TrainStatusResponse> {
  return post<unknown>(
    `/api/modelling/train/cancel/${encodeURIComponent(jobId)}`,
    undefined,
    options,
  ).then(async (data) => (await import("../types/trainGuards")).parseTrainStatusResponse(data))
}

export interface TrainModelArgs {
  graph: GraphPayload
  node_id: string
  source?: string
  signal?: AbortSignal
  timeout?: number
}

export function trainModel(args: TrainModelArgs): Promise<TrainResponse> {
  // Pipeline execution can take minutes for large datasets - use a 10-minute timeout
  const { signal, timeout = 600_000, ...payload } = args
  return post<unknown>(
    "/api/modelling/train",
    {
      ...payload,
      source: payload.source ?? "live",
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
  payload: {
    job_id: string
    experiment_name?: string | null
    /** `""` logs to the local folder. */
    destination: "" | MlflowDestinationKey
    /** One user action; a retry with the same ID returns the recorded run. */
    operation_id?: string
  },
  options?: { signal?: AbortSignal },
): Promise<LogExperimentResponse> {
  return post<unknown>("/api/modelling/mlflow/log", payload, { timeout: 600_000, ...options })
    .then(async (data) => expectGeneratedContract("LogExperimentResponse", (await modellingValidators()).validateLogExperimentResponse, data))
}

/** Resolves where "Save model to file" would write, without writing. */
export function resolveModelSaveDestination(
  payload: ModelSaveDestinationRequest,
  options?: { signal?: AbortSignal },
): Promise<ModelSaveDestinationResponse> {
  return post<unknown>("/api/modelling/save/destination", payload, { timeout: 30_000, ...options })
    .then(async (data) => expectGeneratedContract("ModelSaveDestinationResponse", (await modellingValidators()).validateModelSaveDestinationResponse, data))
}

/** Copies a completed training job's model and feature contract to a project file. */
export function saveTrainedModel(
  payload: SaveModelRequest,
  options?: { signal?: AbortSignal },
): Promise<SaveModelResponse> {
  return post<unknown>("/api/modelling/save", payload, { timeout: 600_000, ...options })
    .then(async (data) => expectGeneratedContract("SaveModelResponse", (await modellingValidators()).validateSaveModelResponse, data))
}

// ---------------------------------------------------------------------------
// Optimiser endpoints
// ---------------------------------------------------------------------------

export interface SolveOptimiserArgs {
  graph: GraphPayload
  node_id: string
  signal?: AbortSignal
  timeout?: number
}

export function solveOptimiser(args: SolveOptimiserArgs): Promise<OptimiserSolveResponse> {
  const { signal, timeout = 300_000, ...payload } = args
  return post<unknown>("/api/optimiser/solve", payload, { signal, timeout })
    .then(async (data) => expectGeneratedContract("OptimiserSolveResponse", (await optimiserValidators()).validateOptimiserSolveResponse, data))
}

export interface EstimateOptimiserSolveArgs {
  graph: GraphPayload
  node_id: string
  source?: string
  signal?: AbortSignal
  timeout?: number
}

export function estimateOptimiserSolve(
  args: EstimateOptimiserSolveArgs,
): Promise<OptimiserEstimate> {
  const { signal, timeout = 30_000, ...payload } = args
  return post<unknown>(
    "/api/optimiser/estimate",
    {
      ...payload,
      source: payload.source ?? "live",
    },
    { signal, timeout },
  ).then(async (data) => expectGeneratedContract("OptimiserEstimateResponse", (await optimiserValidators()).validateOptimiserEstimateResponse, data))
}

export function getOptimiserStatus(
  jobId: string,
  options?: { signal?: AbortSignal },
): Promise<OptimiserStatusResponse> {
  return request<unknown>(`/api/optimiser/solve/status/${encodeURIComponent(jobId)}`, options)
    .then(async (data) => {
      const { validateOptimiserStatusResponse } = await optimiserValidators()
      return validateApiResponse("Could not read optimiser status", () => optimiserStatusFromContract(
        expectGeneratedContract("OptimiserStatusResponse", validateOptimiserStatusResponse, data),
      ))
    })
}

export function applyOptimiser(
  payload: ApplyOptimiserRequest,
  options?: { signal?: AbortSignal },
): Promise<ApplyOptimiserResponse> {
  return post<unknown>("/api/optimiser/apply", payload, { timeout: 120_000, ...options })
    .then(async (data) => expectGeneratedContract("OptimiserApplyResponse", (await optimiserValidators()).validateOptimiserApplyResponse, data))
}

export function saveOptimiser(
  payload: SaveOptimiserRequest,
  options?: { signal?: AbortSignal },
): Promise<SaveOptimiserResponse> {
  return post<unknown>("/api/optimiser/save", payload, options).then(async (data) => expectGeneratedContract("OptimiserSaveResponse", (await optimiserValidators()).validateOptimiserSaveResponse, data))
}

export function logOptimiserToMlflow(
  payload: LogOptimiserToMlflowRequest,
  options?: { signal?: AbortSignal },
): Promise<MlflowLogResponse> {
  return post<unknown>("/api/optimiser/mlflow/log", payload, options).then(async (data) => expectGeneratedContract("OptimiserMlflowLogResponse", (await optimiserValidators()).validateOptimiserMlflowLogResponse, data))
}

export function getFrontierStatus(
  jobId: string,
  options?: { signal?: AbortSignal },
): Promise<FrontierStatusResponse> {
  return request<unknown>(
    `/api/optimiser/frontier/status/${encodeURIComponent(jobId)}`,
    options,
  ).then(async (data) => frontierStatusFromContract(
    expectGeneratedContract("OptimiserFrontierStatusResponse", (await optimiserValidators()).validateOptimiserFrontierStatusResponse, data),
  ))
}

export interface StartOptimiserFrontierAutoRangeArgs {
  graph: GraphPayload
  node_id: string
  signal?: AbortSignal
  timeout?: number
}

export function startOptimiserFrontierAutoRange(
  args: StartOptimiserFrontierAutoRangeArgs,
): Promise<FrontierAutoRangeStartResponse> {
  const { signal, timeout, ...payload } = args
  return post<unknown>("/api/optimiser/frontier/auto-range/start", payload, { signal, timeout })
    .then(async (data) => expectGeneratedContract("OptimiserFrontierAutoRangeStartResponse", (await optimiserValidators()).validateOptimiserFrontierAutoRangeStartResponse, data))
}

export function getOptimiserFrontierAutoRangeStatus(
  jobId: string,
  options?: { signal?: AbortSignal },
): Promise<FrontierAutoRangeStatusResponse> {
  return request<unknown>(
    `/api/optimiser/frontier/auto-range/status/${encodeURIComponent(jobId)}`,
    options,
  ).then(async (data) => frontierAutoRangeStatusFromContract(
    expectGeneratedContract("OptimiserFrontierAutoRangeStatusResponse", (await optimiserValidators()).validateOptimiserFrontierAutoRangeStatusResponse, data),
  ))
}

export function cancelOptimiserFrontierAutoRange(
  jobId: string,
  options?: { signal?: AbortSignal },
): Promise<FrontierAutoRangeStatusResponse> {
  return post<unknown>(
    `/api/optimiser/frontier/auto-range/cancel/${encodeURIComponent(jobId)}`,
    {},
    options,
  ).then(async (data) => frontierAutoRangeStatusFromContract(
    expectGeneratedContract("OptimiserFrontierAutoRangeStatusResponse", (await optimiserValidators()).validateOptimiserFrontierAutoRangeStatusResponse, data),
  ))
}

export function selectFrontierPoint(
  payload: { job_id: string; point_index: number; include_ratebook_tables?: boolean },
  options?: { signal?: AbortSignal },
): Promise<FrontierSelectResponse> {
  return post<unknown>("/api/optimiser/frontier/select", payload, options).then(async (data) => expectGeneratedContract("OptimiserFrontierSelectResponse", (await optimiserValidators()).validateOptimiserFrontierSelectResponse, data))
}

// ---------------------------------------------------------------------------
// Databricks endpoints
// ---------------------------------------------------------------------------

export function getWarehouses(
  options?: { signal?: AbortSignal },
): Promise<DatabricksWarehousesResponse> {
  return request<unknown>("/api/databricks/warehouses", options)
    .then(async (data) => expectGeneratedContract("WarehouseListResponse", (await databricksValidators()).validateWarehouseListResponse, data))
}

export function getCatalogs(
  options?: { signal?: AbortSignal },
): Promise<DatabricksCatalogsResponse> {
  return request<unknown>("/api/databricks/catalogs", options)
    .then(async (data) => expectGeneratedContract("CatalogListResponse", (await databricksValidators()).validateCatalogListResponse, data))
}

export function getSchemas(
  catalog: string,
  options?: { signal?: AbortSignal },
): Promise<DatabricksSchemasResponse> {
  return request<unknown>(`/api/databricks/schemas?catalog=${encodeURIComponent(catalog)}`, options)
    .then(async (data) => expectGeneratedContract("SchemaListResponse", (await databricksValidators()).validateSchemaListResponse, data))
}

export function getTables(
  catalog: string,
  schema: string,
  options?: { signal?: AbortSignal },
): Promise<DatabricksTablesResponse> {
  return request<unknown>(`/api/databricks/tables?catalog=${encodeURIComponent(catalog)}&schema=${encodeURIComponent(schema)}`, options)
    .then(async (data) => expectGeneratedContract("TableListResponse", (await databricksValidators()).validateTableListResponse, data))
}

// ---------------------------------------------------------------------------
// JSON schema inference
// ---------------------------------------------------------------------------

/**
 * Request budget for complete *Infer Tables* schema discovery.
 *
 * The shared client timeout is 30 seconds, but a multi-GB structured input can
 * legitimately take minutes to scan. We keep inference complete by default
 * and give it the same budget as a table snapshot build. A hidden head sample is
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
// Execution settings (server-owned, editor-wide: the streaming chunk size)
// ---------------------------------------------------------------------------

export function getExecutionSettings(
  options?: { signal?: AbortSignal },
): Promise<ExecutionSettings> {
  return request<unknown>("/api/execution-settings", options).then(async (data) => expectGeneratedContract("ExecutionSettings", (await editorValidators()).validateExecutionSettings, data))
}

export function putExecutionSettings(
  streamingChunkSize: number,
  options?: { signal?: AbortSignal },
): Promise<ExecutionSettings> {
  return request<unknown>("/api/execution-settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ streaming_chunk_size: streamingChunkSize }),
    ...options,
  }).then(async (data) => expectGeneratedContract("ExecutionSettings", (await editorValidators()).validateExecutionSettings, data))
}

// ---------------------------------------------------------------------------
// MLflow endpoints (connection surface + discovery for the model editors)
// ---------------------------------------------------------------------------

export function getMlflowDestinations(
  probe: boolean,
  options?: { signal?: AbortSignal },
): Promise<MlflowDestinationsResponse> {
  return request<unknown>(`/api/mlflow/destinations?probe=${probe ? "true" : "false"}`, options)
    .then(async (data) => expectGeneratedContract("MlflowDestinationsResponse", (await mlflowValidators()).validateMlflowDestinationsResponse, data))
}

export function getMlflowSettings(
  options?: { signal?: AbortSignal },
): Promise<MlflowSettingsResponse> {
  return request<unknown>("/api/mlflow/settings", options).then(async (data) => expectGeneratedContract("MlflowSettingsResponse", (await mlflowValidators()).validateMlflowSettingsResponse, data))
}

export function putMlflowSettings(
  payload: MlflowSettingsUpdateRequest,
  options?: { signal?: AbortSignal },
): Promise<MlflowSettingsResponse> {
  return request<unknown>("/api/mlflow/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    ...options,
  }).then(async (data) => expectGeneratedContract("MlflowSettingsResponse", (await mlflowValidators()).validateMlflowSettingsResponse, data))
}

export function testMlflowConnection(
  payload: MlflowTestConnectionRequest = { destination: "" },
  options?: { signal?: AbortSignal },
): Promise<MlflowTestConnectionResponse> {
  return post<unknown>("/api/mlflow/test-connection", payload, options ?? {})
    .then(async (data) => expectGeneratedContract("MlflowTestConnectionResponse", (await mlflowValidators()).validateMlflowTestConnectionResponse, data))
}

// Discovery is destination-scoped: `""` means the local folder and is sent as
// an absent query param, which the backend also reads as the local folder.
function destinationQuery(destination: string): string {
  return destination === "" ? "" : `destination=${encodeURIComponent(destination)}`
}

export function getExperiments(
  destination: string,
  options?: { signal?: AbortSignal },
): Promise<MlflowExperiment[]> {
  const query = destinationQuery(destination)
  return request<unknown>(`/api/mlflow/experiments${query === "" ? "" : `?${query}`}`, options)
    .then(async (data) => expectGeneratedContract("MlflowExperimentList", (await mlflowValidators()).validateMlflowExperimentList, data))
}

export function getRuns(
  experimentId: string,
  artifactFilter: string | undefined,
  destination: string,
  options?: { signal?: AbortSignal },
): Promise<MlflowRun[]> {
  const params = new URLSearchParams({ experiment_id: experimentId })
  if (artifactFilter) params.set("artifact_filter", artifactFilter)
  if (destination !== "") params.set("destination", destination)
  return request<unknown>(`/api/mlflow/runs?${params.toString()}`, options).then(async (data) => expectGeneratedContract("MlflowRunList", (await mlflowValidators()).validateMlflowRunList, data))
}

export function getModels(
  destination: string,
  options?: { signal?: AbortSignal },
): Promise<MlflowModel[]> {
  const query = destinationQuery(destination)
  return request<unknown>(`/api/mlflow/models${query === "" ? "" : `?${query}`}`, options)
    .then(async (data) => expectGeneratedContract("MlflowModelList", (await mlflowValidators()).validateMlflowModelList, data))
}

export function getModelVersions(
  modelName: string,
  destination: string,
  options?: { signal?: AbortSignal },
): Promise<MlflowModelVersion[]> {
  const query = destinationQuery(destination)
  return request<unknown>(
    `/api/mlflow/model-versions?model_name=${encodeURIComponent(modelName)}${query === "" ? "" : `&${query}`}`,
    options,
  ).then(async (data) => expectGeneratedContract("MlflowModelVersionList", (await mlflowValidators()).validateMlflowModelVersionList, data))
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
  return request<unknown>("/api/utility", options).then(async (data) => expectGeneratedContract("UtilityListResponse", (await utilityValidators()).validateUtilityListResponse, data))
}

export function readUtilityFile(
  module: string,
  options?: { signal?: AbortSignal },
): Promise<UtilityReadResponse> {
  return request<unknown>(`/api/utility/${encodeURIComponent(module)}`, options).then(async (data) => expectGeneratedContract("UtilityReadResponse", (await utilityValidators()).validateUtilityReadResponse, data))
}

export function createUtilityFile(
  payload: { name: string; content?: string },
  options?: { signal?: AbortSignal },
): Promise<UtilityWriteResult> {
  return post<unknown>("/api/utility", payload, options).then(async (data) => expectGeneratedContract("UtilityWriteResponse", (await utilityValidators()).validateUtilityWriteResponse, data))
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
  }).then(async (data) => expectGeneratedContract("UtilityWriteResponse", (await utilityValidators()).validateUtilityWriteResponse, data))
}

export function deleteUtilityFile(
  module: string,
  options?: { signal?: AbortSignal },
): Promise<UtilityDeleteResponse> {
  return del<unknown>(`/api/utility/${encodeURIComponent(module)}`, options).then(async (data) => expectGeneratedContract("UtilityDeleteResponse", (await utilityValidators()).validateUtilityDeleteResponse, data))
}

// ---------------------------------------------------------------------------
// File browsing
// ---------------------------------------------------------------------------

export function listFiles(
  dir: string,
  extensions?: string,
  options?: { signal?: AbortSignal },
): Promise<BrowseFilesResponse> {
  const params = new URLSearchParams({ dir })
  if (extensions) params.set("extensions", extensions)
  return request<unknown>(`/api/files?${params.toString()}`, options).then(async (data) => expectGeneratedContract("BrowseFilesResponse", (await sessionValidators()).validateBrowseFilesResponse, data))
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
  return request<unknown>("/api/git/working-branch", options).then(async (data) => expectGeneratedContract("GitWorkingBranchResponse", (await gitValidators()).validateGitWorkingBranchResponse, data))
}

export function setWorkingBranch(
  branch: string,
  create: boolean,
  options?: { signal?: AbortSignal },
): Promise<GitSetWorkingBranchResponse> {
  return post<unknown>("/api/git/working-branch", { branch, create }, options).then(async (data) => expectGeneratedContract("GitSetWorkingBranchResponse", (await gitValidators()).validateGitSetWorkingBranchResponse, data))
}

/** Bind this clone's state volume to a remote for durable storage (hosted mode only). */
export function bindGitStorage(
  remoteUrl: string,
  options?: { signal?: AbortSignal },
): Promise<GitBindStorageResponse> {
  return post<unknown>("/api/git/storage/bind", { remote_url: remoteUrl }, options).then(async (data) => expectGeneratedContract("GitBindStorageResponse", (await gitValidators()).validateGitBindStorageResponse, data))
}

/** Fork a held uc:// location's published state into an empty one. */
export function forkGitStorage(
  sourceUrl: string,
  targetUrl: string,
  options?: { signal?: AbortSignal },
): Promise<GitForkStorageResponse> {
  return post<unknown>(
    "/api/git/storage/fork",
    { source_url: sourceUrl, target_url: targetUrl },
    options,
  ).then(async (data) => expectGeneratedContract("GitForkStorageResponse", (await gitValidators()).validateGitForkStorageResponse, data))
}

/** Measure this fork against the parent it was forked from. On demand only:
 *  the server downloads the parent's whole stored bundle to answer. */
export function checkGitUpstream(
  options?: { signal?: AbortSignal },
): Promise<GitUpstreamStatus> {
  return post<unknown>("/api/git/storage/upstream/check", {}, options).then(async (data) => expectGeneratedContract("GitUpstreamStatusResponse", (await gitValidators()).validateGitUpstreamStatusResponse, data))
}

/** Catch this fork up to its parent's tips, fast-forward only. */
export function pullGitUpstream(
  options?: { signal?: AbortSignal },
): Promise<GitFastForwardResponse> {
  return post<unknown>("/api/git/storage/upstream/pull", {}, options).then(async (data) => expectGeneratedContract("GitFastForwardResponse", (await gitValidators()).validateGitFastForwardResponse, data))
}

/** Clear a finished bind result once the dialog has shown it. */
export function acknowledgeGitBind(
  options?: { signal?: AbortSignal },
): Promise<GitWorkingBranchResponse> {
  return post<unknown>("/api/git/storage/bind/ack", {}, options).then(async (data) => expectGeneratedContract("GitWorkingBranchResponse", (await gitValidators()).validateGitWorkingBranchResponse, data))
}

/** Retry a failed sync to the bound remote and return refreshed readiness. */
export function retryGitStorageSync(
  options?: { signal?: AbortSignal },
): Promise<GitWorkingBranchResponse> {
  return post<unknown>("/api/git/storage/retry", {}, options).then(async (data) => expectGeneratedContract("GitWorkingBranchResponse", (await gitValidators()).validateGitWorkingBranchResponse, data))
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
  ).then(async (data) => expectGeneratedContract("GitSetIdentityResponse", (await gitValidators()).validateGitSetIdentityResponse, data))
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
  ).then(async (data) => expectGeneratedContract("GitCommitResponse", (await gitValidators()).validateGitCommitResponse, data))
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
  return request<unknown>(`/api/git/milestones${qs ? `?${qs}` : ""}`, options).then(async (data) => expectGeneratedContract("GitMilestonesResponse", (await gitValidators()).validateGitMilestonesResponse, data))
}

export function getMilestoneSaves(
  sha: string,
  options?: { signal?: AbortSignal },
): Promise<GitLedgerSavesResponse> {
  return request<unknown>(
    `/api/git/milestones/${encodeURIComponent(sha)}/saves`,
    options,
  ).then(async (data) => expectGeneratedContract("GitLedgerSavesResponse", (await gitValidators()).validateGitLedgerSavesResponse, data))
}

export function getPendingSaves(
  branch?: string | null,
  options?: { signal?: AbortSignal },
): Promise<GitLedgerSavesResponse> {
  const qs = branch ? `?branch=${encodeURIComponent(branch)}` : ""
  return request<unknown>(`/api/git/pending-saves${qs}`, options).then(async (data) => expectGeneratedContract("GitLedgerSavesResponse", (await gitValidators()).validateGitLedgerSavesResponse, data))
}

/** Whole-forest topology for the graph rail: every working pair's spine plus
 *  ancestry-derived fork attachments. Chrome, not history — callers fetch it
 *  best-effort and degrade to no rail on transport or parser failure. */
export function getGitGraph(
  limit?: number,
  options?: { signal?: AbortSignal },
): Promise<GitGraphResponse> {
  const qs = limit !== undefined ? `?limit=${limit}` : ""
  return request<unknown>(`/api/git/graph${qs}`, options).then(async (data) => expectGeneratedContract("GitGraphResponse", (await gitValidators()).validateGitGraphResponse, data))
}

export function gitArchiveBranch(
  branch: string,
  options?: { signal?: AbortSignal },
): Promise<GitArchiveResponse> {
  return post<unknown>("/api/git/archive", { branch }, options).then(async (data) => expectGeneratedContract("GitArchiveResponse", (await gitValidators()).validateGitArchiveResponse, data))
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
  }).then(async (data) => expectGeneratedContract("GitDeleteBranchResponse", (await gitValidators()).validateGitDeleteBranchResponse, data))
}

export function getWorkingBranches(
  options?: { signal?: AbortSignal },
): Promise<GitWorkingBranchesResponse> {
  return request<unknown>("/api/git/working-branches", options).then(async (data) => expectGeneratedContract("GitWorkingBranchesResponse", (await gitValidators()).validateGitWorkingBranchesResponse, data))
}

export function restoreBranch(
  branch: string,
  options?: { signal?: AbortSignal },
): Promise<GitRestoreResponse> {
  return post<unknown>("/api/git/restore", { branch }, options).then(async (data) => expectGeneratedContract("GitRestoreResponse", (await gitValidators()).validateGitRestoreResponse, data))
}

/** Restore a deleted pair from the trash tombstone (the inverse of delete —
 *  pure ref/state ops, so it's instant and safe to drive from Undo). */
export function undeleteBranch(
  branch: string,
  options?: { signal?: AbortSignal },
): Promise<GitUndeleteResponse> {
  return post<unknown>("/api/git/undelete", { branch }, options).then(async (data) => expectGeneratedContract("GitUndeleteResponse", (await gitValidators()).validateGitUndeleteResponse, data))
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
  ).then(async (data) => expectGeneratedContract("GitCreateWorkingBranchResponse", (await gitValidators()).validateGitCreateWorkingBranchResponse, data))
}

export function getGitPrefs(
  options?: { signal?: AbortSignal },
): Promise<GitPrefs> {
  return request<unknown>("/api/git/prefs", options).then(async (data) => expectGeneratedContract("GitPrefs", (await gitValidators()).validateGitPrefs, data))
}

export function setGitPrefs(
  prefs: GitPrefs,
  options?: { signal?: AbortSignal },
): Promise<GitPrefs> {
  return post<unknown>("/api/git/prefs", prefs, options).then(async (data) => expectGeneratedContract("GitPrefs", (await gitValidators()).validateGitPrefs, data))
}

/** Configured remotes + the working branch's ahead/behind vs each (S16). */
export function getGitRemotes(
  options?: { signal?: AbortSignal },
): Promise<GitRemotesResponse> {
  return request<unknown>("/api/git/remotes", options).then(async (data) => expectGeneratedContract("GitRemotesResponse", (await gitValidators()).validateGitRemotesResponse, data))
}

/** Read a 409 push-rejection body; a body with another discriminator reads as null. */
export async function parseGitPushRejection(detail: unknown): Promise<GitPushRejection | null> {
  if (!isPlainObject(detail) || detail.status !== "rejected_diverged") return null
  return expectGeneratedContract("GitPushRejection", (await gitValidators()).validateGitPushRejection, detail)
}

/** Read a 409 milestone-fork body; a body with another discriminator reads as null. */
export async function parseGitMilestoneFork(detail: unknown): Promise<GitMilestoneFork | null> {
  if (!isPlainObject(detail) || detail.status !== "would_fork") return null
  return expectGeneratedContract("GitMilestoneFork", (await gitValidators()).validateGitMilestoneFork, detail)
}

/** Deliberately publish branch history to a remote, bootstrapping its default branch when needed (S16/S33). */
export function gitPush(
  remote: string,
  options?: { signal?: AbortSignal },
): Promise<GitPushResponse> {
  return post<unknown>("/api/git/push", { remote }, options).then(async (data) => expectGeneratedContract("GitPushResponse", (await gitValidators()).validateGitPushResponse, data))
}

/** Catch the working pair up to a remote by fast-forward only (P7 D1/D2) — a
 *  conflict-free ref advance, never a merge. */
export function gitFastForward(
  remote: string,
  options?: { signal?: AbortSignal },
): Promise<GitFastForwardResponse> {
  return post<unknown>("/api/git/fast-forward", { remote }, options).then(async (data) => expectGeneratedContract("GitFastForwardResponse", (await gitValidators()).validateGitFastForwardResponse, data))
}

/** Set the local fork aside under a dated name and adopt the remote (P7 M3) —
 *  both lineages kept, never a merge. */
export function gitBranchAway(
  remote: string,
  options?: { signal?: AbortSignal },
): Promise<GitBranchAwayResponse> {
  return post<unknown>("/api/git/branch-away", { remote }, options).then(async (data) => expectGeneratedContract("GitBranchAwayResponse", (await gitValidators()).validateGitBranchAwayResponse, data))
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
  ).then(async (data) => expectGeneratedContract("GitCommitContext", (await gitValidators()).validateGitCommitContext, data))
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
  return post<unknown>("/api/git/move", { sha }, options).then(async (data) => expectGeneratedContract("GitMoveResponse", (await gitValidators()).validateGitMoveResponse, data))
}
