/**
 * Zustand store for node computation results — previews, optimiser solves, training runs.
 *
 * Results are keyed by node ID and survive panel unmount/remount. This is the
 * core persistence layer that prevents losing expensive computation results
 * when clicking away from a node and back.
 * Occurrence outputs use individual cache keys; their data retains the visible node ID.
 *
 * Cache invalidation:
 *   - Previews/columns: keyed on (nodeId, source, rowLimit, structuralVersion);
 *     a preview also on the node-data epoch its request was sent under.
 *   - Solve/train results: keyed on (nodeId, configHash). A config change
 *     doesn't delete the old result — it's kept with a staleness flag so
 *     the panel can show "config changed since last run".
 */
import { create } from "zustand"
import useGraphStore from "./useGraphStore"
import {
  captureDocumentExecutionFence,
  isDocumentExecutionFenceCurrent,
  type DocumentExecutionFence,
} from "./useDocumentStatusStore"
import type { PreviewData } from "../panels/DataPreview"
import type { OptimiserPreviewData } from "../panels/OptimiserPreview"
import type { ModellingPreviewData } from "../panels/ModellingPreview"
import type {
  ApplyOptimiserResponse,
  ExecutionMetrics,
  ExplorePivotResult,
  ExplorePivotStatusResponse,
  FrontierData,
  FrontierSelectResponse,
  JobStatus,
  OptimiserApplyQuery,
  OptimiserSolveResult,
  TrainResponse,
} from "../api/types"
import type { ColumnInfo } from "../types/node"
import { clearTrainedJobHandle, writeTrainedJobHandle } from "../utils/trainedJobHandles"
import {
  applyFrontierPointSummary,
  assertSelectReplyForPoint,
  frontierFromSolveResult,
  frontierPointSummaryFromSelect,
  optimiserApplyIdentityFor,
  optimiserApplyKey,
  resultForFrontierPoint,
  sameApplyTarget,
  selectReplyIsCurrent,
  withoutApplyEntriesForNodes,
  withoutSupersededApplyEntries,
} from "../utils/optimiserSolveResults"
import { nextTrainEstimate, type TrainEstimateSample } from "../utils/trainEstimate"
import { hasTrainResult, modellingPreviewFor, optimiserPreviewFor } from "./nodeResultsSelectors"

export const MAX_CACHED_PREVIEWS = 24
export const MAX_CACHED_SOLVE_RESULTS = 8
export const MAX_CACHED_TRAIN_RESULTS = 8
export const MAX_CACHED_EXPLORE_PIVOT_RESULTS = 32
export const MAX_CACHED_OPTIMISER_APPLY = 16

/** The `/apply` request's query beyond its target (OPT-V12): sort, search, filters and page. */
export type { OptimiserApplyQuery }

/** Everything an `/apply` response depends on. */
export interface OptimiserApplyIdentity {
  jobId: string
  target: "solved" | number
  query: OptimiserApplyQuery
}

export interface OptimiserApplyCacheEntry {
  nodeId: string
  key: string
  identity: OptimiserApplyIdentity
  response: ApplyOptimiserResponse
}

// Result caches use entry-count LRU deliberately: preview payloads are already
// bounded by backend row/column limits, and byte-accurate browser-side accounting
// would be expensive/noisy. Revisit byte caps if heap evidence shows store pressure.

// ─── Types ───────────────────────────────────────────────────────

export type SolveProgress = {
  status: JobStatus
  progress: number
  message: string
  elapsed_seconds: number
  result?: OptimiserSolveResult | null
  terminal_reason?: string | null
  execution_metrics?: ExecutionMetrics | null
}

export type TrainResult = TrainResponse

export type TrainProgress = {
  status: JobStatus
  progress: number
  message: string
  iteration: number
  total_iterations: number
  train_loss: Record<string, number>
  train_loss_history?: Array<{ iteration: number; [key: string]: number }>
  train_loss_history_truncated?: boolean
  elapsed_seconds: number
  result?: TrainResult | null
  warning?: string | null
  terminal_reason?: string | null
  error_code?: string | null
  http_status_code?: number | null
  error_detail?: unknown
  worker_remote_traceback?: string | null
  execution_metrics?: ExecutionMetrics | null
  phase?: "planning" | "trial_fit" | "trial_complete" | "final_fit" | "publication" | "completed" | null
  trial_index?: number | null
  trial_count?: number | null
  fold_index?: number | null
  fold_count?: number | null
  completed_fits?: number | null
  total_fits?: number | null
  best_objective?: number | null
}

export type ExplorePivotProgress = ExplorePivotStatusResponse

/** A pivot ID is only unique within its owning Explore node. */
export function explorePivotResultKey(nodeId: string, pivotId: string): string {
  return `${nodeId}:${pivotId}`
}

interface CachedPreview {
  data: PreviewData
  structuralVersion: number
  source?: string
  rowLimit?: number
  /**
   * The node-data epoch the request was sent under. A snapshot published,
   * refreshed, or cleared since may change the rows, so the entry matches a
   * request only at this epoch.
   */
  nodeDataEpoch?: number
}

interface CachedSolveFields {
  terminalStatus?: SolveProgress | null
  jobId: string
  configHash: string
  /** Staleness key contract: configHash + source + structuralVersion (see useStaleConfigEstimate) */
  source: string
  structuralVersion: number
  /** Constraint config snapshot for OptimiserPreview */
  constraints: Record<string, Record<string, number>>
  nodeLabel: string
}

/** The node's latest solve attempt, when it failed: what it was asked to solve and why it failed. */
export interface FailedSolveAttempt {
  jobId: string
  configHash: string
  source: string
  structuralVersion: number
  error: string
  terminalStatus: SolveProgress | null
}

/** A node's solve result: the displayed result (a selected frontier point's
 *  summary applied, or the solve's) and the as-solved one, under the identity of
 *  the job that solved it. A later failed attempt is kept beside it in
 *  `failedAttempt` and never relabels it. */
export interface SolvedCachedSolveResult extends CachedSolveFields {
  result: OptimiserSolveResult
  originalResult: OptimiserSolveResult
  frontier: FrontierData | null
  selectedPointIndex: number | null
  /** Absent until an attempt after this result fails; a completed solve has none. */
  failedAttempt?: FailedSolveAttempt
}

/** A failed solve with no earlier result: nothing was solved, so no result is
 *  fabricated for it (no zero objective or baseline) and there is nothing to preview. */
interface FailedCachedSolveResult extends CachedSolveFields {
  result: null
  originalResult: null
  frontier: null
  selectedPointIndex: null
  failedAttempt: FailedSolveAttempt
}

export type CachedSolveResult = SolvedCachedSolveResult | FailedCachedSolveResult

interface ActiveSolveJob {
  jobId: string
  nodeId: string
  nodeLabel: string
  progress: SolveProgress | null
  error: string | null
  /** Constraint config snapshot for OptimiserPreview */
  constraints: Record<string, Record<string, number>>
  configHash: string
  source: string
  structuralVersion: number
  documentFence?: DocumentExecutionFence
}

/** A node's training outcome: the server's result, or, for a job that failed,
 *  no result and the failure, as the solve cache models it. */
export interface CachedTrainResult {
  result: TrainResult | null
  /** Why the job failed; set exactly when `result` is null. */
  error?: string
  terminalStatus?: TrainProgress | null
  jobId: string
  configHash: string
  /** Staleness key contract: configHash + source + structuralVersion (see useStaleConfigEstimate) */
  source: string
  structuralVersion: number
}

interface ActiveTrainJob {
  jobId: string
  nodeId: string
  nodeLabel: string
  progress: TrainProgress | null
  error: string | null
  configHash: string
  source: string
  structuralVersion: number
  documentFence?: DocumentExecutionFence
  /**
   * `trainingLineage` of the graph payload the training request submitted,
   * remembered with a completed result so a reload can tell whether the graph
   * changed since. A job started without one is never remembered.
   */
  lineage?: string
  /** The only two samples retained for the browser-derived ETA. */
  estimateSamples?: TrainEstimateSample[]
  estimatedRemainingSeconds?: number | null
}

interface CachedExplorePivotResult {
  result: ExplorePivotResult | null
  error?: string
  terminalStatus?: ExplorePivotProgress | null
  jobId: string
  nodeId: string
  pivotId: string
  nodeLabel: string
  pivotName: string
  calculationIdentity: string
  lastAttemptedCalculationIdentity: string
  lastAttemptedDataframeCacheKey: string | null
  source: string
  structuralVersion: number
}

export interface ExplorePivotStartClaim {
  nodeId: string
  dataVersion: string | null
  calculationIdentity: string
  token: number
  documentFence?: DocumentExecutionFence
}

// Monotonic token source for pivot auto-update claims; uniqueness is all that
// matters, so it never resets.
let nextExplorePivotClaimToken = 1

interface ActiveExplorePivotJob {
  jobId: string
  key: string
  nodeId: string
  pivotId: string
  nodeLabel: string
  pivotName: string
  calculationIdentity: string
  requestedDataVersion: string | null
  source: string
  structuralVersion: number
  documentFence?: DocumentExecutionFence
  progress: ExplorePivotProgress | null
  error: string | null
}

function omitRecordKey<T>(record: Record<string, T>, key: string): Record<string, T> {
  const next = { ...record }
  delete next[key]
  return next
}

/** The expired-training record without *nodeId*, the same object when it has no entry. */
function forgetExpiredTrainJob(expired: Record<string, string>, nodeId: string): Record<string, string> {
  return Object.hasOwn(expired, nodeId) ? omitRecordKey(expired, nodeId) : expired
}

function jobFenceIsCurrent(job: { documentFence?: DocumentExecutionFence }): boolean {
  return job.documentFence === undefined ||
    isDocumentExecutionFenceCurrent(job.documentFence)
}

/**
 * Remember or forget a node's completed training job in browser storage.
 *
 * Runs at the store's completion boundary, so a job that finishes while the
 * node's editor is closed is still restorable after a reload. A completed job
 * started through `startTrainJob` is remembered under its document; an error,
 * or a job with no lineage (a refused start), forgets the node's handle.
 */
function rememberTrainOutcome(
  nodeId: string,
  job: ActiveTrainJob,
  result: TrainResult | null,
): void {
  const sourceFile = job.documentFence?.sourceFile
  if (!sourceFile) return
  if (!job.lineage || result === null || result.status === "error") {
    clearTrainedJobHandle(sourceFile, nodeId)
    return
  }
  writeTrainedJobHandle(sourceFile, nodeId, {
    jobId: job.jobId,
    configHash: job.configHash,
    source: job.source,
    lineage: job.lineage,
  })
}


// ─── Recency of the bounded result caches ───────────────────────
// The previews derived from a record are memoised on it in
// `nodeResultsSelectors.ts`; only the LRU recency lives here.

let resultCacheClock = 0
const previewRecency = new Map<string, number>()
const solveResultRecency = new Map<string, number>()
const trainResultRecency = new Map<string, number>()
const explorePivotResultRecency = new Map<string, number>()

/** Forget every cache's recency (tests, and a hot module reload). */
export function resetNodeResultsDerivedCaches(): void {
  previewRecency.clear()
  solveResultRecency.clear()
  trainResultRecency.clear()
  explorePivotResultRecency.clear()
  resultCacheClock = 0
}

if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    resetNodeResultsDerivedCaches()
  })
}

function assertValidCacheLimit(maxEntries: number): void {
  if (!Number.isInteger(maxEntries) || maxEntries < 1) {
    throw new Error(`Invalid node result cache limit: ${maxEntries}`)
  }
}

function touchCachedResult(recency: Map<string, number>, key: string): void {
  resultCacheClock += 1
  recency.set(key, resultCacheClock)
}

function dropCachedResult(recency: Map<string, number>, key: string): void {
  recency.delete(key)
}












/**
 * Whether a frontier select reply answers the node's installed solve: the same
 * job and the same frontier generation. A recompute keeps the job and reuses
 * point indices for different points, so a reply from another generation (a
 * late one, or one the node has not installed yet) describes another point.
 */






function trimCacheByRecency<T>(
  records: Record<string, T>,
  recency: Map<string, number>,
  maxEntries: number,
  pinnedKey?: string | null,
): { records: Record<string, T>; evicted: string[] } {
  assertValidCacheLimit(maxEntries)

  for (const key of Array.from(recency.keys())) {
    if (!Object.prototype.hasOwnProperty.call(records, key)) {
      recency.delete(key)
    }
  }

  const keys = Object.keys(records)
  const evictCount = keys.length - maxEntries
  if (evictCount <= 0) {
    return { records, evicted: [] }
  }

  const evicted = keys
    .filter((key) => key !== pinnedKey)
    .sort((a, b) => (recency.get(a) ?? 0) - (recency.get(b) ?? 0) || a.localeCompare(b))
    .slice(0, evictCount)
  const nextRecords = { ...records }
  for (const key of evicted) {
    delete nextRecords[key]
    recency.delete(key)
  }
  return { records: nextRecords, evicted }
}

function trimExplorePivotCache(
  records: Record<string, CachedExplorePivotResult>,
  pinnedNodeId: string | null,
): { records: Record<string, CachedExplorePivotResult>; evicted: string[] } {
  assertValidCacheLimit(MAX_CACHED_EXPLORE_PIVOT_RESULTS)
  for (const key of Array.from(explorePivotResultRecency.keys())) {
    if (!Object.prototype.hasOwnProperty.call(records, key)) explorePivotResultRecency.delete(key)
  }
  const protectedKeys = new Set(
    Object.entries(records)
      .filter(([, entry]) => entry.nodeId === pinnedNodeId)
      .map(([key]) => key),
  )
  const keys = Object.keys(records)
  const evictCount = keys.length - MAX_CACHED_EXPLORE_PIVOT_RESULTS
  if (evictCount <= 0) return { records, evicted: [] }
  const byRecency = (a: string, b: string) => (explorePivotResultRecency.get(a) ?? 0) - (explorePivotResultRecency.get(b) ?? 0) || a.localeCompare(b)
  const evicted = keys
    .filter((key) => !protectedKeys.has(key))
    .sort(byRecency)
    .slice(0, evictCount)
  const nextRecords = { ...records }
  for (const key of evicted) {
    delete nextRecords[key]
    explorePivotResultRecency.delete(key)
  }
  return { records: nextRecords, evicted }
}

// ─── Store ───────────────────────────────────────────────────────

interface NodeResultsState {
  // Preview cache
  previews: Record<string, CachedPreview>
  pinnedPreviewNodeId: string | null

  // Optimiser
  solveResults: Record<string, CachedSolveResult>
  solveJobs: Record<string, ActiveSolveJob>
  /** `/apply` responses, least recently used first; see `recordOptimiserApply`. */
  optimiserApplyCache: OptimiserApplyCacheEntry[]

  // Training
  trainResults: Record<string, CachedTrainResult>
  trainJobs: Record<string, ActiveTrainJob>
  /**
   * Nodes whose remembered completed training result the server no longer
   * holds (it restarted, or the job expired), mapped to that job's id. The
   * Export pane and the results panel both read it; it lasts until the node
   * starts training or gets a result.
   */
  expiredTrainJobs: Record<string, string>

  // Explore pivots, keyed by explorePivotResultKey(nodeId, pivotId).
  pivotResults: Record<string, CachedExplorePivotResult>
  pivotJobs: Record<string, ActiveExplorePivotJob>
  // Atomic start claims, keyed like pivotResults. One current claim per pivot
  // serialises concurrent consumers: automatic matching targets no-op, while
  // newer automatic targets and every manual start replace the generation.
  pivotStartClaims: Record<string, ExplorePivotStartClaim>

  // Column cache — keyed by "nodeId:source", cached across panel mounts.
  // structuralVersion stores the graph version captured at fetch time.
  columnCache: Record<string, { columns: ColumnInfo[]; structuralVersion: number }>

  // ── Column cache actions ──
  setColumns: (sourceNodeId: string, columns: ColumnInfo[], structuralVersion: number, source?: string) => void
  getColumns: (sourceNodeId: string, source?: string) => { columns: ColumnInfo[]; fresh: boolean } | null

  // ── Preview actions ──
  setPreview: (nodeId: string, data: PreviewData, structuralVersion: number, source?: string, rowLimit?: number, nodeDataEpoch?: number) => void
  /**
   * Mark the stored preview holding *data* current at *nodeDataEpoch*: the
   * epoch its own captures raised the node-data epoch to. An entry holding
   * other data is left alone.
   */
  advancePreviewEpoch: (nodeId: string, data: PreviewData, nodeDataEpoch: number) => void
  /** Returns cached preview, or null if no entry exists. */
  getPreview: (nodeId: string) => CachedPreview | null
  /** Protect the open preview node from entry-count LRU eviction. */
  setPinnedPreviewNodeId: (nodeId: string | null) => void

  // ── Optimiser actions ──
  startSolveJob: (nodeId: string, jobId: string, nodeLabel: string, constraints: Record<string, Record<string, number>>, configHash: string, source: string, structuralVersion: number) => void
  updateSolveProgress: (nodeId: string, progress: SolveProgress) => void
  completeSolveJob: (nodeId: string, result: OptimiserSolveResult, terminalStatus?: SolveProgress) => void
  failSolveJob: (nodeId: string, error: string, terminalStatus?: SolveProgress) => void
  selectFrontierPoint: (nodeId: string, pointIndex: number | null) => void
  /**
   * Store the reply for a point the user selected, and display it unless the
   * selection has moved on. A reply for a job other than the node's installed
   * one is dropped.
   */
  updateFrontierAfterSelect: (nodeId: string, jobId: string, pointIndex: number, selectResult: FrontierSelectResponse) => void
  /**
   * Store a materialised point summary for one job without changing the
   * selection: a reply for a job the node does not hold is dropped, and the
   * displayed result changes only if that point is (still) selected.
   */
  recordFrontierPointSummary: (nodeId: string, jobId: string, pointIndex: number, selectResult: FrontierSelectResponse) => void
  /**
   * Cache an `/apply` response under its request identity. It is stored only
   * while that identity's job and target are still the node's; otherwise it
   * is a late response and dropped. Returns whether it was stored.
   */
  recordOptimiserApply: (nodeId: string, identity: OptimiserApplyIdentity, response: ApplyOptimiserResponse) => boolean
  /** Mark a cached `/apply` response most recently used. */
  touchOptimiserApply: (key: string) => void

  // ── Training actions ──
  startTrainJob: (nodeId: string, jobId: string, nodeLabel: string, configHash: string, source: string, structuralVersion: number, lineage?: string) => void
  // The training and pivot job actions name the job a reply is for and change
  // nothing unless the key's active job has that id.
  updateTrainProgress: (nodeId: string, jobId: string, progress: TrainProgress) => void
  completeTrainJob: (nodeId: string, jobId: string, result: TrainResult, terminalStatus?: TrainProgress) => void
  /**
   * Put back a completed result the server still holds (after a browser reload).
   * Never replaces a result or a running job the node already has.
   */
  restoreTrainResult: (nodeId: string, cached: CachedTrainResult) => void
  /**
   * Record that the server no longer holds the node's remembered completed
   * job. Ignored when the node has meanwhile got a result or a running job.
   */
  markTrainResultExpired: (nodeId: string, jobId: string) => void
  failTrainJob: (nodeId: string, jobId: string, error: string, terminalStatus?: TrainProgress) => void
  /**
   * Record a training request refused before any job existed, under the identity
   * it was asked for, keeping a running job's tracking and the remembered handle.
   */
  failTrainStart: (
    nodeId: string,
    attempt: { configHash: string; source: string; structuralVersion: number },
    error: string,
    terminalStatus?: TrainProgress,
  ) => void

  startExplorePivotJob: (key: string, jobId: string, nodeId: string, pivotId: string, nodeLabel: string, pivotName: string, calculationIdentity: string, source: string, structuralVersion: number, requestedDataVersion?: string | null) => void
  updateExplorePivotProgress: (key: string, jobId: string, progress: ExplorePivotProgress) => void
  completeExplorePivotJob: (key: string, jobId: string, result: ExplorePivotResult, terminalStatus?: ExplorePivotProgress) => void
  failExplorePivotJob: (key: string, jobId: string, error: string, terminalStatus?: ExplorePivotProgress) => void
  /**
   * Takes the auto-update claim for a pivot before submitting. Returns the
   * claim token, or null when an identical target is already claimed (the
   * caller must not submit). A different target atomically replaces the
   * current claim, invalidating its token.
   */
  claimExplorePivotAuto: (
    key: string,
    nodeId: string,
    dataVersion: string,
    calculationIdentity: string,
  ) => number | null
  /** A manual start always becomes the current generation for this pivot. */
  claimExplorePivotManual: (
    key: string,
    nodeId: string,
    dataVersion: string | null,
    calculationIdentity: string,
  ) => number
  /** Releases the claim only when `token` is still current; stale tokens no-op. */
  releaseExplorePivotStart: (key: string, token: number) => void

  // ── Derived helpers ──
  /** Build OptimiserPreviewData for a node (from completed result or null). */
  getOptimiserPreview: (nodeId: string) => OptimiserPreviewData | null
  /** Return completed training result for a node, or null. */
  getModellingPreview: (nodeId: string) => ModellingPreviewData | null
  /** Mark a solved optimiser preview as recently displayed outside render. */
  touchOptimiserPreview: (nodeId: string) => void
  /** Mark a completed modelling preview as recently displayed outside render. */
  touchModellingPreview: (nodeId: string) => void

  // ── Cleanup ──
  /** Drop active work captured under an obsolete live-document fence. */
  discardActiveJobs: () => void
  clearNode: (nodeId: string) => void
}

const useNodeResultsStore = create<NodeResultsState>()((set, get) => ({
  previews: {},
  pinnedPreviewNodeId: null,
  columnCache: {},
  solveResults: {},
  solveJobs: {},
  optimiserApplyCache: [],
  trainResults: {},
  trainJobs: {},
  expiredTrainJobs: {},
  pivotResults: {},
  pivotJobs: {},
  pivotStartClaims: {},

  // ── Column cache ──

  setColumns: (sourceNodeId, columns, structuralVersion, source) => {
    const key = source ? `${sourceNodeId}:${source}` : sourceNodeId
    set((s) => ({
      columnCache: { ...s.columnCache, [key]: { columns, structuralVersion } },
    }))
  },

  getColumns: (sourceNodeId, source) => {
    const key = source ? `${sourceNodeId}:${source}` : sourceNodeId
    const entry = get().columnCache[key]
    if (!entry) return null
    return { columns: entry.columns, fresh: entry.structuralVersion === useGraphStore.getState().structuralVersion }
  },

  // ── Preview ──

  setPreview: (nodeId, data, structuralVersion, source, rowLimit, nodeDataEpoch) =>
    set((s) => {
      touchCachedResult(previewRecency, nodeId)
      const pinnedKey = s.pinnedPreviewNodeId === data.nodeId ? nodeId
        : Object.keys(s.previews)
          .filter((key) => key === s.pinnedPreviewNodeId || s.previews[key].data.nodeId === s.pinnedPreviewNodeId)
          .sort((a, b) => (previewRecency.get(b) ?? 0) - (previewRecency.get(a) ?? 0))[0]
      const bounded = trimCacheByRecency(
        {
          ...s.previews,
          [nodeId]: { data, structuralVersion, source, rowLimit, nodeDataEpoch },
        },
        previewRecency,
        MAX_CACHED_PREVIEWS,
        pinnedKey,
      )
      return {
        previews: bounded.records,
      }
    }),

  advancePreviewEpoch: (nodeId, data, nodeDataEpoch) =>
    set((s) => {
      const entry = s.previews[nodeId]
      if (!entry || entry.data !== data) return {}
      return { previews: { ...s.previews, [nodeId]: { ...entry, nodeDataEpoch } } }
    }),

  getPreview: (nodeId) => {
    const cached = get().previews[nodeId]
    if (!cached) {
      dropCachedResult(previewRecency, nodeId)
      return null
    }
    touchCachedResult(previewRecency, nodeId)
    return cached
  },

  setPinnedPreviewNodeId: (nodeId) => {
    if (get().pinnedPreviewNodeId === nodeId) return
    set({ pinnedPreviewNodeId: nodeId })
  },

  // ── Optimiser ──

  startSolveJob: (nodeId, jobId, nodeLabel, constraints, configHash, source, structuralVersion) =>
    set((s) => ({
      solveJobs: {
        ...s.solveJobs,
        [nodeId]: {
          jobId,
          nodeId,
          nodeLabel,
          progress: null,
          error: null,
          constraints,
          configHash,
          source,
          structuralVersion,
          documentFence: captureDocumentExecutionFence(),
        },
      },
    })),

  updateSolveProgress: (nodeId, progress) =>
    set((s) => {
      const job = s.solveJobs[nodeId]
      if (!job) return s
      if (!jobFenceIsCurrent(job)) {
        return { solveJobs: omitRecordKey(s.solveJobs, nodeId) }
      }
      return {
        solveJobs: { ...s.solveJobs, [nodeId]: { ...job, progress } },
      }
    }),

  completeSolveJob: (nodeId, result, terminalStatus) =>
    set((s) => {
      const job = s.solveJobs[nodeId]
      if (!job) return s
      if (!jobFenceIsCurrent(job)) {
        return { solveJobs: omitRecordKey(s.solveJobs, nodeId) }
      }
      const { [nodeId]: _removedJob, ...remainingJobs } = s.solveJobs; void _removedJob
      const frontier = frontierFromSolveResult(result)
      const initialPointIndex = frontier ? 0 : null
      touchCachedResult(solveResultRecency, nodeId)
      let nextCached: CachedSolveResult = {
        result,
        originalResult: result,
        terminalStatus: terminalStatus ?? null,
        jobId: job.jobId,
        configHash: job.configHash,
        source: job.source,
        structuralVersion: job.structuralVersion,
        constraints: job.constraints,
        nodeLabel: job.nodeLabel,
        frontier,
        selectedPointIndex: initialPointIndex,
      }
      if (initialPointIndex !== null) {
        nextCached = {
          ...nextCached,
          result: resultForFrontierPoint(nextCached, initialPointIndex),
        }
      }
      const bounded = trimCacheByRecency(
        {
          ...s.solveResults,
          [nodeId]: nextCached,
        },
        solveResultRecency,
        MAX_CACHED_SOLVE_RESULTS,
        s.pinnedPreviewNodeId,
      )
      // A new job makes the node's /apply responses answers to other requests.
      const optimiserApplyCache = withoutApplyEntriesForNodes(
        withoutSupersededApplyEntries(s.optimiserApplyCache, nodeId, job.jobId),
        bounded.evicted,
      )
      return {
        solveJobs: remainingJobs,
        solveResults: bounded.records,
        optimiserApplyCache,
      }
    }),

  failSolveJob: (nodeId, error, terminalStatus) =>
    set((s) => {
      const job = s.solveJobs[nodeId]
      if (!job) return s
      if (!jobFenceIsCurrent(job)) {
        return { solveJobs: omitRecordKey(s.solveJobs, nodeId) }
      }
      const { [nodeId]: _removedJob, ...remainingJobs } = s.solveJobs; void _removedJob
      touchCachedResult(solveResultRecency, nodeId)
      const failedAttempt: FailedSolveAttempt = {
        jobId: job.jobId,
        configHash: job.configHash,
        source: job.source,
        structuralVersion: job.structuralVersion,
        error,
        terminalStatus: terminalStatus ?? null,
      }
      // An earlier result keeps its own identity, with the failed attempt beside
      // it; with none, nothing was solved and no result stands in for one.
      const previous = s.solveResults[nodeId]
      const nextCached: CachedSolveResult = previous !== undefined && previous.result !== null
        ? { ...previous, failedAttempt }
        : {
            terminalStatus: failedAttempt.terminalStatus,
            jobId: job.jobId,
            configHash: job.configHash,
            source: job.source,
            structuralVersion: job.structuralVersion,
            constraints: job.constraints,
            nodeLabel: job.nodeLabel,
            result: null,
            originalResult: null,
            frontier: null,
            selectedPointIndex: null,
            failedAttempt,
          }
      const bounded = trimCacheByRecency(
        {
          ...s.solveResults,
          [nodeId]: nextCached,
        },
        solveResultRecency,
        MAX_CACHED_SOLVE_RESULTS,
        s.pinnedPreviewNodeId,
      )
      return {
        solveJobs: remainingJobs,
        solveResults: bounded.records,
        optimiserApplyCache: withoutApplyEntriesForNodes(
          s.optimiserApplyCache,
          [nodeId, ...bounded.evicted],
        ),
      }
    }),

  selectFrontierPoint: (nodeId, pointIndex) =>
    set((s) => {
      const cached = s.solveResults[nodeId]
      // A failed solve with no result has no points to select.
      if (!cached || cached.result === null) return s
      touchCachedResult(solveResultRecency, nodeId)
      const result = pointIndex === null
        ? cached.originalResult
        : resultForFrontierPoint(cached, pointIndex)
      const nextCached: SolvedCachedSolveResult = {
        ...cached,
        selectedPointIndex: pointIndex,
        result,
      }
      return {
        solveResults: {
          ...s.solveResults,
          [nodeId]: nextCached,
        },
      }
    }),

  updateFrontierAfterSelect: (nodeId, jobId, pointIndex, selectResult) => {
    assertSelectReplyForPoint(pointIndex, selectResult)
    set((s) => {
      const cached = s.solveResults[nodeId]
      if (!selectReplyIsCurrent(cached, jobId)) return s
      touchCachedResult(solveResultRecency, nodeId)
      const summary = frontierPointSummaryFromSelect(selectResult)
      // Keep the richer summary so re-selecting this point needs no round trip.
      const frontier = cached.frontier && cached.frontier.point_summaries[pointIndex]
        ? {
            ...cached.frontier,
            point_summaries: cached.frontier.point_summaries.map((stored, index) => (index === pointIndex ? summary : stored)),
          }
        : cached.frontier
      // Stale-response guard: if the user has already moved on to a different
      // point, keep the stored summary (per-point data never goes stale) but do
      // not regress the displayed result/selectedPointIndex back to the older
      // request.  ``null`` means "no selection in flight" (e.g. the very first
      // response after a fresh solve), which is not a stale case.
      if (cached.selectedPointIndex !== null && cached.selectedPointIndex !== pointIndex) {
        if (frontier === cached.frontier) return s
        const nextCached: SolvedCachedSolveResult = { ...cached, frontier }
        return {
          solveResults: {
            ...s.solveResults,
            [nodeId]: nextCached,
          },
        }
      }
      const nextCached: SolvedCachedSolveResult = {
        ...cached,
        frontier,
        selectedPointIndex: pointIndex,
        result: applyFrontierPointSummary(cached.originalResult, summary),
      }
      return {
        solveResults: {
          ...s.solveResults,
          [nodeId]: nextCached,
        },
      }
    })
  },

  recordFrontierPointSummary: (nodeId, jobId, pointIndex, selectResult) => {
    assertSelectReplyForPoint(pointIndex, selectResult)
    set((s) => {
      const cached = s.solveResults[nodeId]
      if (
        !selectReplyIsCurrent(cached, jobId)
        || !cached.frontier?.point_summaries[pointIndex]
      ) return s
      touchCachedResult(solveResultRecency, nodeId)
      const summary = frontierPointSummaryFromSelect(selectResult)
      const nextCached: SolvedCachedSolveResult = {
        ...cached,
        frontier: {
          ...cached.frontier,
          point_summaries: cached.frontier.point_summaries.map((stored, index) => (index === pointIndex ? summary : stored)),
        },
        result: cached.selectedPointIndex === pointIndex
          ? applyFrontierPointSummary(cached.originalResult, summary)
          : cached.result,
      }
      return { solveResults: { ...s.solveResults, [nodeId]: nextCached } }
    })
  },

  recordOptimiserApply: (nodeId, identity, response) => {
    const cached = get().solveResults[nodeId]
    if (
      !cached
      || cached.result === null
      || !sameApplyTarget(identity, optimiserApplyIdentityFor(cached, identity.query))
    ) {
      return false
    }
    const key = optimiserApplyKey(identity)
    set((s) => ({
      optimiserApplyCache: [
        ...s.optimiserApplyCache.filter((entry) => entry.key !== key),
        { nodeId, key, identity, response },
      ].slice(-MAX_CACHED_OPTIMISER_APPLY),
    }))
    return true
  },

  touchOptimiserApply: (key) =>
    set((s) => {
      const index = s.optimiserApplyCache.findIndex((entry) => entry.key === key)
      if (index === -1) throw new Error(`No cached optimiser apply response for ${key}`)
      if (index === s.optimiserApplyCache.length - 1) return s
      const entry = s.optimiserApplyCache[index]
      return {
        optimiserApplyCache: [
          ...s.optimiserApplyCache.slice(0, index),
          ...s.optimiserApplyCache.slice(index + 1),
          entry,
        ],
      }
    }),

  // ── Training ──

  startTrainJob: (nodeId, jobId, nodeLabel, configHash, source, structuralVersion, lineage) =>
    set((s) => {
      const nextJob = {
        jobId,
        nodeId,
        nodeLabel,
        progress: null,
        error: null,
        configHash,
        source,
        structuralVersion,
        documentFence: captureDocumentExecutionFence(),
        lineage,
        estimateSamples: [],
        estimatedRemainingSeconds: null,
      }
      return {
        trainJobs: {
          ...s.trainJobs,
          [nodeId]: nextJob,
        },
        expiredTrainJobs: forgetExpiredTrainJob(s.expiredTrainJobs, nodeId),
      }
    }),

  updateTrainProgress: (nodeId, jobId, progress) =>
    set((s) => {
      const job = s.trainJobs[nodeId]
      if (job?.jobId !== jobId) return s
      if (!jobFenceIsCurrent(job)) {
        return { trainJobs: omitRecordKey(s.trainJobs, nodeId) }
      }
      const estimate = nextTrainEstimate(job.estimateSamples ?? [], progress)
      return {
        trainJobs: { ...s.trainJobs, [nodeId]: { ...job, progress, estimateSamples: estimate.samples, estimatedRemainingSeconds: estimate.estimatedRemainingSeconds } },
      }
    }),

  completeTrainJob: (nodeId, jobId, result, terminalStatus) => {
    const completingJob = get().trainJobs[nodeId]
    if (completingJob?.jobId !== jobId) return
    const fenceCurrent = jobFenceIsCurrent(completingJob)
    set((s) => {
      const job = s.trainJobs[nodeId]
      if (job?.jobId !== jobId) return s
      if (!jobFenceIsCurrent(job)) {
        return { trainJobs: omitRecordKey(s.trainJobs, nodeId) }
      }
      const { [nodeId]: _removedJob, ...remainingJobs } = s.trainJobs; void _removedJob
      touchCachedResult(trainResultRecency, nodeId)
      const nextCached: CachedTrainResult = {
        result,
        terminalStatus: terminalStatus ?? null,
        jobId: job.jobId,
        configHash: job.configHash,
        source: job.source,
        structuralVersion: job.structuralVersion,
      }
      const bounded = trimCacheByRecency(
        {
          ...s.trainResults,
          [nodeId]: nextCached,
        },
        trainResultRecency,
        MAX_CACHED_TRAIN_RESULTS,
        s.pinnedPreviewNodeId,
      )
      return {
        trainJobs: remainingJobs,
        trainResults: bounded.records,
        expiredTrainJobs: forgetExpiredTrainJob(s.expiredTrainJobs, nodeId),
      }
    })
    if (fenceCurrent) rememberTrainOutcome(nodeId, completingJob, result)
  },

  failTrainStart: (nodeId, attempt, error, terminalStatus) =>
    // The request was refused before any job existed: the node shows the
    // failure, while a running job's tracking and the browser's handle to the
    // last completed job, which this attempt neither replaced nor invalidated
    // on the server, stay as they are.
    set((s) => {
      touchCachedResult(trainResultRecency, nodeId)
      const bounded = trimCacheByRecency(
        {
          ...s.trainResults,
          [nodeId]: { result: null, error, terminalStatus: terminalStatus ?? null, jobId: "", ...attempt },
        },
        trainResultRecency,
        MAX_CACHED_TRAIN_RESULTS,
        s.pinnedPreviewNodeId,
      )
      return {
        trainResults: bounded.records,
        expiredTrainJobs: forgetExpiredTrainJob(s.expiredTrainJobs, nodeId),
      }
    }),

  markTrainResultExpired: (nodeId, jobId) =>
    set((s) => {
      if (s.trainResults[nodeId] || s.trainJobs[nodeId]) return s
      return { expiredTrainJobs: { ...s.expiredTrainJobs, [nodeId]: jobId } }
    }),

  restoreTrainResult: (nodeId, cached) =>
    set((s) => {
      if (s.trainResults[nodeId] || s.trainJobs[nodeId] || !hasTrainResult(cached)) return s
      touchCachedResult(trainResultRecency, nodeId)
      const bounded = trimCacheByRecency(
        { ...s.trainResults, [nodeId]: cached },
        trainResultRecency,
        MAX_CACHED_TRAIN_RESULTS,
        s.pinnedPreviewNodeId,
      )
      return {
        trainResults: bounded.records,
        expiredTrainJobs: forgetExpiredTrainJob(s.expiredTrainJobs, nodeId),
      }
    }),

  failTrainJob: (nodeId, jobId, error, terminalStatus) => {
    const failingJob = get().trainJobs[nodeId]
    if (failingJob?.jobId !== jobId) return
    const fenceCurrent = jobFenceIsCurrent(failingJob)
    set((s) => {
      const job = s.trainJobs[nodeId]
      if (job?.jobId !== jobId) return s
      if (!jobFenceIsCurrent(job)) {
        return { trainJobs: omitRecordKey(s.trainJobs, nodeId) }
      }
      const { [nodeId]: _removedJob, ...remainingJobs } = s.trainJobs; void _removedJob
      touchCachedResult(trainResultRecency, nodeId)
      const nextCached: CachedTrainResult = {
        result: null,
        error,
        terminalStatus: terminalStatus ?? null,
        jobId: job.jobId,
        configHash: job.configHash,
        source: job.source,
        structuralVersion: job.structuralVersion,
      }
      const bounded = trimCacheByRecency(
        {
          ...s.trainResults,
          [nodeId]: nextCached,
        },
        trainResultRecency,
        MAX_CACHED_TRAIN_RESULTS,
        s.pinnedPreviewNodeId,
      )
      return {
        trainJobs: remainingJobs,
        trainResults: bounded.records,
      }
    })
    if (fenceCurrent) rememberTrainOutcome(nodeId, failingJob, null)
  },

  startExplorePivotJob: (key, jobId, nodeId, pivotId, nodeLabel, pivotName, calculationIdentity, source, structuralVersion, requestedDataVersion = null) =>
    set((s) => ({
      pivotJobs: {
        ...s.pivotJobs,
        [key]: {
          jobId,
          key,
          nodeId,
          pivotId,
          nodeLabel,
          pivotName,
          calculationIdentity,
          requestedDataVersion,
          source,
          structuralVersion,
          documentFence: captureDocumentExecutionFence(),
          progress: null,
          error: null,
        },
      },
    })),

  updateExplorePivotProgress: (key, jobId, progress) =>
    set((s) => {
      const job = s.pivotJobs[key]
      if (job?.jobId !== jobId) return s
      if (!jobFenceIsCurrent(job)) {
        return { pivotJobs: omitRecordKey(s.pivotJobs, key) }
      }
      return { pivotJobs: { ...s.pivotJobs, [key]: { ...job, progress } } }
    }),

  completeExplorePivotJob: (key, jobId, result, terminalStatus) =>
    set((s) => {
      const job = s.pivotJobs[key]
      if (job?.jobId !== jobId) return s
      if (!jobFenceIsCurrent(job)) {
        return { pivotJobs: omitRecordKey(s.pivotJobs, key) }
      }
      const { [key]: _removedJob, ...pivotJobs } = s.pivotJobs; void _removedJob
      touchCachedResult(explorePivotResultRecency, key)
      const pivotResults = trimExplorePivotCache({
        ...s.pivotResults,
        [key]: {
          result,
          terminalStatus: terminalStatus ?? null,
          jobId: job.jobId,
          nodeId: job.nodeId,
          pivotId: job.pivotId,
          nodeLabel: job.nodeLabel,
          pivotName: job.pivotName,
          calculationIdentity: job.calculationIdentity,
          lastAttemptedCalculationIdentity: job.calculationIdentity,
          lastAttemptedDataframeCacheKey:
            job.requestedDataVersion ?? result.data_version,
          source: job.source,
          structuralVersion: job.structuralVersion,
        },
      }, s.pinnedPreviewNodeId).records
      return { pivotJobs, pivotResults }
    }),

  failExplorePivotJob: (key, jobId, error, terminalStatus) =>
    set((s) => {
      const job = s.pivotJobs[key]
      if (job?.jobId !== jobId) return s
      if (!jobFenceIsCurrent(job)) {
        return { pivotJobs: omitRecordKey(s.pivotJobs, key) }
      }
      const { [key]: _removedJob, ...pivotJobs } = s.pivotJobs; void _removedJob
      touchCachedResult(explorePivotResultRecency, key)
      const previous = s.pivotResults[key]
      const retainedResult = previous?.result ?? null
      const pivotResults = trimExplorePivotCache({
        ...s.pivotResults,
        [key]: {
          result: retainedResult,
          error,
          terminalStatus: terminalStatus ?? null,
          jobId: job.jobId,
          nodeId: job.nodeId,
          pivotId: job.pivotId,
          nodeLabel: job.nodeLabel,
          pivotName: job.pivotName,
          // A failed refresh may retain the last usable table, but it must
          // retain that table's identity too. Otherwise an old result could
          // be presented as current for the newly failed configuration.
          calculationIdentity: retainedResult
            ? previous.calculationIdentity
            : job.calculationIdentity,
          lastAttemptedCalculationIdentity: job.calculationIdentity,
          lastAttemptedDataframeCacheKey: job.requestedDataVersion,
          source: retainedResult ? previous.source : job.source,
          structuralVersion: retainedResult
            ? previous.structuralVersion
            : job.structuralVersion,
        },
      }, s.pinnedPreviewNodeId).records
      return { pivotJobs, pivotResults }
    }),

  claimExplorePivotAuto: (key, nodeId, dataVersion, calculationIdentity) => {
    const current = get().pivotStartClaims[key]
    if (
      current
      && current.dataVersion === dataVersion
      && current.calculationIdentity === calculationIdentity
    ) {
      return null
    }
    const token = nextExplorePivotClaimToken
    nextExplorePivotClaimToken += 1
    set((s) => ({
      pivotStartClaims: {
        ...s.pivotStartClaims,
        [key]: {
          nodeId,
          dataVersion,
          calculationIdentity,
          token,
          documentFence: captureDocumentExecutionFence(),
        },
      },
    }))
    return token
  },

  claimExplorePivotManual: (
    key,
    nodeId,
    dataVersion,
    calculationIdentity,
  ) => {
    const token = nextExplorePivotClaimToken
    nextExplorePivotClaimToken += 1
    set((s) => ({
      pivotStartClaims: {
        ...s.pivotStartClaims,
        [key]: {
          nodeId,
          dataVersion,
          calculationIdentity,
          token,
          documentFence: captureDocumentExecutionFence(),
        },
      },
    }))
    return token
  },

  releaseExplorePivotStart: (key, token) =>
    set((s) => {
      const current = s.pivotStartClaims[key]
      if (!current || current.token !== token) return s
      const { [key]: _removed, ...pivotStartClaims } = s.pivotStartClaims
      void _removed
      return { pivotStartClaims }
    }),

  // ── Derived ──

  getOptimiserPreview: (nodeId) => {
    const cached = get().solveResults[nodeId]
    // A failed solve with no result has nothing to preview.
    if (!cached || cached.result === null) {
      return null
    }
    return optimiserPreviewFor(cached)
  },

  getModellingPreview: (nodeId) => {
    const cached = get().trainResults[nodeId]
    if (!cached || !hasTrainResult(cached)) {
      return null
    }
    return modellingPreviewFor(cached, get().trainJobs[nodeId]?.nodeLabel ?? "Model")
  },

  // ── Cleanup ──

  touchOptimiserPreview: (nodeId) => {
    if (get().solveResults[nodeId]) {
      touchCachedResult(solveResultRecency, nodeId)
    } else {
      dropCachedResult(solveResultRecency, nodeId)
    }
  },

  touchModellingPreview: (nodeId) => {
    const cached = get().trainResults[nodeId]
    if (cached && hasTrainResult(cached)) {
      touchCachedResult(trainResultRecency, nodeId)
    } else {
      dropCachedResult(trainResultRecency, nodeId)
    }
  },

  discardActiveJobs: () => set({
    solveJobs: {},
    trainJobs: {},
    pivotJobs: {},
    pivotStartClaims: {},
  }),

  clearNode: (nodeId) => {
    dropCachedResult(previewRecency, nodeId)
    dropCachedResult(solveResultRecency, nodeId)
    dropCachedResult(trainResultRecency, nodeId)
    set((s) => {
      const previews = Object.fromEntries(Object.entries(s.previews).filter(([key, entry]) => {
        if (key !== nodeId && entry.data.nodeId !== nodeId) return true
        dropCachedResult(previewRecency, key)
        return false
      }))
      const columnCache = Object.fromEntries(
        Object.entries(s.columnCache).filter(([k]) => k !== nodeId && !k.startsWith(`${nodeId}:`))
      )
      const { [nodeId]: _rsr, ...solveResults } = s.solveResults; void _rsr
      const { [nodeId]: _rsj, ...solveJobs } = s.solveJobs; void _rsj
      const { [nodeId]: _rtr, ...trainResults } = s.trainResults; void _rtr
      const { [nodeId]: _rtj, ...trainJobs } = s.trainJobs; void _rtj
      const expiredTrainJobs = forgetExpiredTrainJob(s.expiredTrainJobs, nodeId)
      const pivotResults = Object.fromEntries(
        Object.entries(s.pivotResults).filter(([, entry]) => entry.nodeId !== nodeId),
      )
      const pivotJobs = Object.fromEntries(
        Object.entries(s.pivotJobs).filter(([, job]) => job.nodeId !== nodeId),
      )
      // Invalidating the node's claims makes any in-flight submission's
      // claimCurrent() gate fail, so a late response cannot repopulate the
      // cleared state or leak into a later node reusing the id.
      const pivotStartClaims = Object.fromEntries(
        Object.entries(s.pivotStartClaims).filter(
          ([, claim]) => claim.nodeId !== nodeId,
        ),
      )
      for (const key of Object.keys(s.pivotResults)) {
        if (s.pivotResults[key]?.nodeId === nodeId) dropCachedResult(explorePivotResultRecency, key)
      }
      return {
        previews,
        pinnedPreviewNodeId: s.pinnedPreviewNodeId === nodeId ? null : s.pinnedPreviewNodeId,
        columnCache,
        solveResults,
        solveJobs,
        optimiserApplyCache: withoutApplyEntriesForNodes(s.optimiserApplyCache, [nodeId]),
        trainResults,
        trainJobs,
        expiredTrainJobs,
        pivotResults,
        pivotJobs,
        pivotStartClaims,
      }
    })
  },
}))

export default useNodeResultsStore
