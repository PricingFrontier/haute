/**
 * Zustand store for node computation results — previews, optimiser solves, training runs.
 *
 * Results are keyed by node ID and survive panel unmount/remount. This is the
 * core persistence layer that prevents losing expensive computation results
 * when clicking away from a node and back.
 *
 * Cache invalidation:
 *   - Previews/columns: keyed on (nodeId, source, rowLimit, structuralVersion).
 *   - Solve/train results: keyed on (nodeId, configHash). A config change
 *     doesn't delete the old result — it's kept with a staleness flag so
 *     the panel can show "config changed since last run".
 */
import { create } from "zustand"
import useGraphStore from "./useGraphStore"
import type { PreviewData } from "../panels/DataPreview"
import type { SolveResult, OptimiserPreviewData } from "../panels/OptimiserPreview"
import type { FrontierSelectResponse, FrontierData } from "../api/types"
import type { ColumnInfo } from "../types/node"

export const MAX_CACHED_PREVIEWS = 24
export const MAX_CACHED_SOLVE_RESULTS = 8
export const MAX_CACHED_TRAIN_RESULTS = 8

// Result caches use entry-count LRU deliberately: preview payloads are already
// bounded by backend row/column limits, and byte-accurate browser-side accounting
// would be expensive/noisy. Revisit byte caps if heap evidence shows store pressure.

// ─── Types ───────────────────────────────────────────────────────

export type SolveProgress = {
  status: string
  progress: number
  message: string
  elapsed_seconds: number
  result?: SolveResult
}

export type TrainResult = {
  status: string
  metrics: Record<string, number>
  feature_importance: { feature: string; importance: number }[]
  model_path: string
  train_rows: number
  test_rows: number  // validation rows
  holdout_rows?: number
  holdout_metrics?: Record<string, number>
  diagnostics_set?: string  // "train" | "validation" | "holdout"
  features?: string[]
  cat_features?: string[]
  error?: string
  best_iteration?: number | null
  loss_history?: { iteration: number; [key: string]: number }[]
  double_lift?: { decile: number; actual: number; predicted: number; count: number }[]
  shap_summary?: { feature: string; mean_abs_shap: number }[]
  feature_importance_loss?: { feature: string; importance: number }[]
  ave_per_feature?: { feature: string; type: string; bins: { label: string; exposure: number; avg_actual: number; avg_predicted: number }[] }[]
  residuals_histogram?: { bin_center: number; count: number; weighted_count: number }[]
  residuals_stats?: { mean: number; std: number; skew: number; min: number; max: number }
  actual_vs_predicted?: { actual: number; predicted: number; weight: number }[]
  lorenz_curve?: { cum_weight_frac: number; cum_actual_frac: number }[]
  lorenz_curve_perfect?: { cum_weight_frac: number; cum_actual_frac: number }[]
  pdp_data?: { feature: string; type: string; grid: { value: number | string; avg_prediction: number }[] }[]
  warning?: string | null
  total_source_rows?: number | null
  // GLM-specific
  glm_coefficients?: { feature: string; coefficient: number; std_error: number; z_value: number; p_value: number; significance: string }[]
  glm_relativities?: { feature: string; relativity: number; ci_lower?: number; ci_upper?: number }[]
  glm_fit_statistics?: Record<string, number>
  glm_regularization_path?: { selected_alpha?: number; n_nonzero?: number }
}

export type TrainProgress = {
  status: string
  progress: number
  message: string
  iteration: number
  total_iterations: number
  train_loss: Record<string, number>
  elapsed_seconds: number
  result?: TrainResult
  warning?: string | null
}

interface CachedPreview {
  data: PreviewData
  structuralVersion: number
  source?: string
  rowLimit?: number
}

interface CachedSolveResult {
  result: SolveResult
  originalResult: SolveResult
  error?: string
  jobId: string
  configHash: string
  /** Constraint config snapshot for OptimiserPreview */
  constraints: Record<string, Record<string, number>>
  nodeLabel: string
  frontier: FrontierData | null
  selectedPointIndex: number | null
}

interface ActiveSolveJob {
  jobId: string
  nodeId: string
  nodeLabel: string
  progress: SolveProgress | null
  error: string | null
  /** Constraint config snapshot for OptimiserPreview */
  constraints: Record<string, Record<string, number>>
  configHash: string
}

interface CachedTrainResult {
  result: TrainResult
  jobId: string
  configHash: string
}

interface ActiveTrainJob {
  jobId: string
  nodeId: string
  nodeLabel: string
  progress: TrainProgress | null
  error: string | null
  configHash: string
}

// ─── Config hashing ──────────────────────────────────────────────

/** Fast djb2 string hash — good enough for staleness detection. */
function djb2(s: string): string {
  let hash = 5381
  for (let i = 0; i < s.length; i++) {
    hash = ((hash << 5) + hash + s.charCodeAt(i)) | 0
  }
  return (hash >>> 0).toString(36)
}

export function hashConfig(config: Record<string, unknown>): string {
  // Strip internal keys that don't affect computation
  const { _nodeId, _columns, _schemaWarnings, _availableColumns, ...rest } = config
  void _nodeId; void _columns; void _schemaWarnings; void _availableColumns
  const sortKeys = (o: unknown): unknown => {
    if (o === null || typeof o !== "object") return o
    if (Array.isArray(o)) return o.map(sortKeys)
    const sorted = Object.keys(o as Record<string, unknown>).sort()
    return Object.fromEntries(sorted.map(k => [k, sortKeys((o as Record<string, unknown>)[k])]))
  }
  return djb2(JSON.stringify(sortKeys(rest)))
}

// ─── Derived-getter caches (Issue #13) ──────────────────────────
// Writes keep these memo entries up to date so getOptimiserPreview /
// getModellingPreview can be read during render without mutating module state.

type ModellingPreviewData = { result: TrainResult; jobId: string; nodeLabel: string; configHash: string }

const _optimiserPreviewCache: Record<string, { source: CachedSolveResult; result: OptimiserPreviewData }> = {}
const _modellingPreviewCache: Record<string, { source: CachedTrainResult; nodeLabel: string; result: ModellingPreviewData }> = {}

let resultCacheClock = 0
const previewRecency = new Map<string, number>()
const solveResultRecency = new Map<string, number>()
const trainResultRecency = new Map<string, number>()

export function resetNodeResultsDerivedCaches(): void {
  for (const key of Object.keys(_optimiserPreviewCache)) delete _optimiserPreviewCache[key]
  for (const key of Object.keys(_modellingPreviewCache)) delete _modellingPreviewCache[key]
  previewRecency.clear()
  solveResultRecency.clear()
  trainResultRecency.clear()
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

function buildOptimiserPreview(cached: CachedSolveResult): OptimiserPreviewData {
  return {
    result: cached.result,
    jobId: cached.jobId,
    constraints: cached.constraints,
    nodeLabel: cached.nodeLabel,
    frontier: cached.frontier,
    selectedPointIndex: cached.selectedPointIndex,
  }
}

function cacheOptimiserPreview(nodeId: string, cached: CachedSolveResult): void {
  _optimiserPreviewCache[nodeId] = { source: cached, result: buildOptimiserPreview(cached) }
}

function readOptimiserPreview(nodeId: string, cached: CachedSolveResult): OptimiserPreviewData {
  const prev = _optimiserPreviewCache[nodeId]
  return prev && prev.source === cached ? prev.result : buildOptimiserPreview(cached)
}

function numericFrontierValue(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`Frontier point field '${field}' must be a finite number`)
  }
  return value
}

function recordValue(value: unknown, field: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`Frontier point field '${field}' must be an object`)
  }
  return value as Record<string, unknown>
}

function frontierConstraintValue(row: Record<string, unknown>, name: string): [unknown, string] {
  const totalKey = `total_${name}`
  if (row[totalKey] !== undefined) return [row[totalKey], totalKey]
  if (row.constraints !== undefined && row.constraints !== null) {
    const nestedConstraints = recordValue(row.constraints, "constraints")
    if (nestedConstraints[name] !== undefined) return [nestedConstraints[name], `constraints.${name}`]
  }
  if (row[name] !== undefined) return [row[name], name]
  return [undefined, totalKey]
}

function deriveSolveResultForFrontierPoint(cached: CachedSolveResult, pointIndex: number): SolveResult {
  const frontier = cached.frontier
  if (!frontier) return cached.result
  const point = frontier.points[pointIndex]
  if (!point) throw new Error(`Frontier point index ${pointIndex} is out of range`)

  const row = recordValue(point, "point")
  const constraints = Object.fromEntries(
    frontier.constraint_names.map((name) => {
      const [raw, field] = frontierConstraintValue(row, name)
      return [name, numericFrontierValue(raw, field)]
    }),
  )

  const lambdas: Record<string, number> = {}
  if (row.lambdas != null) {
    const nestedLambdas = recordValue(row.lambdas, "lambdas")
    for (const [name, value] of Object.entries(nestedLambdas)) {
      lambdas[name] = numericFrontierValue(value, `lambdas.${name}`)
    }
  } else {
    for (const [key, value] of Object.entries(row)) {
      if (key.startsWith("lambda_")) {
        lambdas[key.replace(/^lambda_/, "")] = numericFrontierValue(value, key)
      }
    }
  }

  let converged = cached.originalResult.converged
  if (row.converged !== undefined) {
    if (typeof row.converged !== "boolean") {
      throw new Error("Frontier point field 'converged' must be a boolean")
    }
    converged = row.converged
  }
  const baselineConstraints = row.baseline_constraints == null
    ? cached.originalResult.baseline_constraints
    : Object.fromEntries(
        Object.entries(recordValue(row.baseline_constraints, "baseline_constraints")).map(([name, value]) => [
          name,
          numericFrontierValue(value, `baseline_constraints.${name}`),
        ]),
      )

  return {
    ...cached.originalResult,
    total_objective: numericFrontierValue(row.total_objective, "total_objective"),
    constraints,
    lambdas,
    baseline_objective: row.baseline_objective === undefined
      ? cached.originalResult.baseline_objective
      : numericFrontierValue(row.baseline_objective, "baseline_objective"),
    baseline_constraints: baselineConstraints,
    converged,
    iterations: typeof row.iterations === "number" ? row.iterations : cached.originalResult.iterations,
    cd_iterations: typeof row.cd_iterations === "number" ? row.cd_iterations : cached.originalResult.cd_iterations,
    clamp_rate: typeof row.clamp_rate === "number" ? row.clamp_rate : cached.originalResult.clamp_rate,
  }
}

function modellingPreviewNodeLabel(job: ActiveTrainJob | undefined): string {
  return job?.nodeLabel ?? "Model"
}

function buildModellingPreview(cached: CachedTrainResult, nodeLabel: string): ModellingPreviewData {
  return {
    result: cached.result,
    jobId: cached.jobId,
    nodeLabel,
    configHash: cached.configHash,
  }
}

function cacheModellingPreview(nodeId: string, cached: CachedTrainResult, job: ActiveTrainJob | undefined): void {
  const nodeLabel = modellingPreviewNodeLabel(job)
  _modellingPreviewCache[nodeId] = { source: cached, nodeLabel, result: buildModellingPreview(cached, nodeLabel) }
}

function readModellingPreview(
  nodeId: string,
  cached: CachedTrainResult,
  job: ActiveTrainJob | undefined,
): ModellingPreviewData {
  const nodeLabel = modellingPreviewNodeLabel(job)
  const prev = _modellingPreviewCache[nodeId]
  return prev && prev.source === cached && prev.nodeLabel === nodeLabel
    ? prev.result
    : buildModellingPreview(cached, nodeLabel)
}

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

// ─── Store ───────────────────────────────────────────────────────

interface NodeResultsState {
  // Preview cache
  previews: Record<string, CachedPreview>
  pinnedPreviewNodeId: string | null

  // Optimiser
  solveResults: Record<string, CachedSolveResult>
  solveJobs: Record<string, ActiveSolveJob>

  // Training
  trainResults: Record<string, CachedTrainResult>
  trainJobs: Record<string, ActiveTrainJob>

  // Column cache — keyed by "nodeId:source", cached across panel mounts.
  // structuralVersion stores the graph version captured at fetch time.
  columnCache: Record<string, { columns: ColumnInfo[]; structuralVersion: number }>

  // ── Column cache actions ──
  setColumns: (sourceNodeId: string, columns: ColumnInfo[], structuralVersion: number, source?: string) => void
  getColumns: (sourceNodeId: string, source?: string) => { columns: ColumnInfo[]; fresh: boolean } | null

  // ── Preview actions ──
  setPreview: (nodeId: string, data: PreviewData, structuralVersion: number, source?: string, rowLimit?: number) => void
  /** Returns cached preview, or null if no entry exists. */
  getPreview: (nodeId: string) => CachedPreview | null
  /** Protect the open preview node from entry-count LRU eviction. */
  setPinnedPreviewNodeId: (nodeId: string | null) => void

  // ── Optimiser actions ──
  startSolveJob: (nodeId: string, jobId: string, nodeLabel: string, constraints: Record<string, Record<string, number>>, configHash: string) => void
  updateSolveProgress: (nodeId: string, progress: SolveProgress) => void
  completeSolveJob: (nodeId: string, result: SolveResult) => void
  failSolveJob: (nodeId: string, error: string) => void
  selectFrontierPoint: (nodeId: string, pointIndex: number | null) => void
  updateFrontierAfterSelect: (nodeId: string, pointIndex: number, selectResult: FrontierSelectResponse) => void

  // ── Training actions ──
  startTrainJob: (nodeId: string, jobId: string, nodeLabel: string, configHash: string) => void
  updateTrainProgress: (nodeId: string, progress: TrainProgress) => void
  completeTrainJob: (nodeId: string, result: TrainResult) => void
  failTrainJob: (nodeId: string, error: string) => void

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
  clearNode: (nodeId: string) => void
}

const useNodeResultsStore = create<NodeResultsState>()((set, get) => ({
  previews: {},
  pinnedPreviewNodeId: null,
  columnCache: {},
  solveResults: {},
  solveJobs: {},
  trainResults: {},
  trainJobs: {},

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

  setPreview: (nodeId, data, structuralVersion, source, rowLimit) =>
    set((s) => {
      touchCachedResult(previewRecency, nodeId)
      const bounded = trimCacheByRecency(
        {
          ...s.previews,
          [nodeId]: { data, structuralVersion, source, rowLimit },
        },
        previewRecency,
        MAX_CACHED_PREVIEWS,
        s.pinnedPreviewNodeId,
      )
      return {
        previews: bounded.records,
      }
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

  startSolveJob: (nodeId, jobId, nodeLabel, constraints, configHash) =>
    set((s) => ({
      solveJobs: {
        ...s.solveJobs,
        [nodeId]: { jobId, nodeId, nodeLabel, progress: null, error: null, constraints, configHash },
      },
    })),

  updateSolveProgress: (nodeId, progress) =>
    set((s) => {
      const job = s.solveJobs[nodeId]
      if (!job) return s
      return {
        solveJobs: { ...s.solveJobs, [nodeId]: { ...job, progress } },
      }
    }),

  completeSolveJob: (nodeId, result) =>
    set((s) => {
      const job = s.solveJobs[nodeId]
      if (!job) return s
      const { [nodeId]: _removedJob, ...remainingJobs } = s.solveJobs; void _removedJob
      // Extract frontier data from the result if present
      const rawFrontier = result.frontier
      const frontier: FrontierData | null = rawFrontier && rawFrontier.points?.length
        ? {
            points: rawFrontier.points,
            n_points: rawFrontier.n_points,
            points_returned: rawFrontier.points_returned,
            constraint_names: rawFrontier.constraint_names,
            points_limit: rawFrontier.points_limit,
            points_truncated: rawFrontier.points_truncated,
          }
        : null
      touchCachedResult(solveResultRecency, nodeId)
      const nextCached: CachedSolveResult = {
        result,
        originalResult: result,
        jobId: job.jobId,
        configHash: job.configHash,
        constraints: job.constraints,
        nodeLabel: job.nodeLabel,
        frontier,
        selectedPointIndex: null,
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
      for (const evictedNodeId of bounded.evicted) {
        delete _optimiserPreviewCache[evictedNodeId]
      }
      if (bounded.records[nodeId]) cacheOptimiserPreview(nodeId, bounded.records[nodeId])
      return {
        solveJobs: remainingJobs,
        solveResults: bounded.records,
      }
    }),

  failSolveJob: (nodeId, error) =>
    set((s) => {
      const job = s.solveJobs[nodeId]
      if (!job) return s
      const { [nodeId]: _removedJob, ...remainingJobs } = s.solveJobs; void _removedJob
      touchCachedResult(solveResultRecency, nodeId)
      const nextCached: CachedSolveResult = {
        ...(s.solveResults[nodeId] ?? {
          result: { status: "error", total_objective: 0, baseline_objective: 0, constraints: {}, baseline_constraints: {}, lambdas: {}, converged: false } as SolveResult,
          originalResult: { status: "error", total_objective: 0, baseline_objective: 0, constraints: {}, baseline_constraints: {}, lambdas: {}, converged: false } as SolveResult,
        }),
        jobId: job.jobId,
        configHash: job.configHash,
        constraints: job.constraints,
        nodeLabel: job.nodeLabel,
        frontier: null,
        selectedPointIndex: null,
        error,
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
      for (const evictedNodeId of bounded.evicted) {
        delete _optimiserPreviewCache[evictedNodeId]
      }
      if (bounded.records[nodeId]) cacheOptimiserPreview(nodeId, bounded.records[nodeId])
      return {
        solveJobs: remainingJobs,
        solveResults: bounded.records,
      }
    }),

  selectFrontierPoint: (nodeId, pointIndex) =>
    set((s) => {
      const cached = s.solveResults[nodeId]
      if (!cached) return s
      touchCachedResult(solveResultRecency, nodeId)
      const result = pointIndex === null
        ? cached.originalResult
        : deriveSolveResultForFrontierPoint(cached, pointIndex)
      const nextCached = {
        ...cached,
        selectedPointIndex: pointIndex,
        result,
      }
      cacheOptimiserPreview(nodeId, nextCached)
      return {
        solveResults: {
          ...s.solveResults,
          [nodeId]: nextCached,
        },
      }
    }),

  updateFrontierAfterSelect: (nodeId, pointIndex, selectResult) =>
    set((s) => {
      const cached = s.solveResults[nodeId]
      if (!cached) return s
      touchCachedResult(solveResultRecency, nodeId)
      const nextCached = {
        ...cached,
        selectedPointIndex: pointIndex,
        result: {
          ...cached.result,
          total_objective: selectResult.total_objective,
          constraints: selectResult.constraints,
          baseline_objective: selectResult.baseline_objective,
          baseline_constraints: selectResult.baseline_constraints,
          lambdas: selectResult.lambdas,
          converged: selectResult.converged,
        },
      }
      cacheOptimiserPreview(nodeId, nextCached)
      return {
        solveResults: {
          ...s.solveResults,
          [nodeId]: nextCached,
        },
      }
    }),

  // ── Training ──

  startTrainJob: (nodeId, jobId, nodeLabel, configHash) =>
    set((s) => {
      const nextJob = { jobId, nodeId, nodeLabel, progress: null, error: null, configHash }
      const cached = s.trainResults[nodeId]
      if (cached && cached.result.status !== "error") {
        cacheModellingPreview(nodeId, cached, nextJob)
      }
      return {
        trainJobs: {
          ...s.trainJobs,
          [nodeId]: nextJob,
        },
      }
    }),

  updateTrainProgress: (nodeId, progress) =>
    set((s) => {
      const job = s.trainJobs[nodeId]
      if (!job) return s
      return {
        trainJobs: { ...s.trainJobs, [nodeId]: { ...job, progress } },
      }
    }),

  completeTrainJob: (nodeId, result) =>
    set((s) => {
      const job = s.trainJobs[nodeId]
      // Remove the active job if present; also works for direct completion
      // (no active job) used by ModellingConfig for sync/error results.
      const { [nodeId]: _removedJob, ...remainingJobs } = s.trainJobs; void _removedJob
      touchCachedResult(trainResultRecency, nodeId)
      const nextCached: CachedTrainResult = {
        result,
        jobId: job?.jobId ?? "",
        configHash: job?.configHash ?? "",
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
      for (const evictedNodeId of bounded.evicted) {
        delete _modellingPreviewCache[evictedNodeId]
      }
      if (bounded.records[nodeId]?.result.status === "error") {
        delete _modellingPreviewCache[nodeId]
      } else if (bounded.records[nodeId]) {
        cacheModellingPreview(nodeId, bounded.records[nodeId], undefined)
      }
      return {
        trainJobs: job ? remainingJobs : s.trainJobs,
        trainResults: bounded.records,
      }
    }),

  failTrainJob: (nodeId, error) =>
    set((s) => {
      const job = s.trainJobs[nodeId]
      if (!job) return s
      const { [nodeId]: _removedJob, ...remainingJobs } = s.trainJobs; void _removedJob
      touchCachedResult(trainResultRecency, nodeId)
      const nextCached: CachedTrainResult = {
        result: { status: "error", error, metrics: {}, feature_importance: [], model_path: "", train_rows: 0, test_rows: 0 } as TrainResult,
        jobId: job.jobId,
        configHash: job.configHash,
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
      for (const evictedNodeId of bounded.evicted) {
        delete _modellingPreviewCache[evictedNodeId]
      }
      delete _modellingPreviewCache[nodeId]
      return {
        trainJobs: remainingJobs,
        trainResults: bounded.records,
      }
    }),

  // ── Derived ──

  getOptimiserPreview: (nodeId) => {
    const cached = get().solveResults[nodeId]
    if (!cached) {
      return null
    }
    return readOptimiserPreview(nodeId, cached)
  },

  getModellingPreview: (nodeId) => {
    const cached = get().trainResults[nodeId]
    if (!cached || cached.result.status === "error") {
      return null
    }
    const job = get().trainJobs[nodeId]
    return readModellingPreview(nodeId, cached, job)
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
    if (cached && cached.result.status !== "error") {
      touchCachedResult(trainResultRecency, nodeId)
    } else {
      dropCachedResult(trainResultRecency, nodeId)
    }
  },

  clearNode: (nodeId) => {
    // Clear derived-getter caches for this node
    delete _optimiserPreviewCache[nodeId]
    delete _modellingPreviewCache[nodeId]
    dropCachedResult(previewRecency, nodeId)
    dropCachedResult(solveResultRecency, nodeId)
    dropCachedResult(trainResultRecency, nodeId)
    set((s) => {
      const { [nodeId]: _rp, ...previews } = s.previews; void _rp
      const columnCache = Object.fromEntries(
        Object.entries(s.columnCache).filter(([k]) => k !== nodeId && !k.startsWith(`${nodeId}:`))
      )
      const { [nodeId]: _rsr, ...solveResults } = s.solveResults; void _rsr
      const { [nodeId]: _rsj, ...solveJobs } = s.solveJobs; void _rsj
      const { [nodeId]: _rtr, ...trainResults } = s.trainResults; void _rtr
      const { [nodeId]: _rtj, ...trainJobs } = s.trainJobs; void _rtj
      return {
        previews,
        pinnedPreviewNodeId: s.pinnedPreviewNodeId === nodeId ? null : s.pinnedPreviewNodeId,
        columnCache,
        solveResults,
        solveJobs,
        trainResults,
        trainJobs,
      }
    })
  },
}))

export default useNodeResultsStore
