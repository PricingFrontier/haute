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
import type { OptimiserPreviewData } from "../panels/OptimiserPreview"
import type {
  CrossValidationReport,
  ExecutionMetrics,
  ExploreCacheReport,
  ExploreStatusResponse,
  FrontierData,
  FrontierSelectResponse,
  JobStatus,
  OptimiserSolveResult,
} from "../api/types"
import type { ColumnInfo } from "../types/node"
import { TERMINAL_JOB_STATUSES } from "../api/types"

export const MAX_CACHED_PREVIEWS = 24
export const MAX_CACHED_SOLVE_RESULTS = 8
export const MAX_CACHED_TRAIN_RESULTS = 8
export const MAX_CACHED_EXPLORE_RESULTS = 8
const NON_CONVERGED_WARNING = "Solver did not converge. Consider increasing max_iter or relaxing tolerance."

type TrainEstimateSample = {
  iteration: number
  elapsedSeconds: number
  totalIterations: number
}

export function nextTrainEstimate(
  previous: TrainEstimateSample[],
  progress: TrainProgress,
): {
  samples: TrainEstimateSample[]
  estimatedRemainingSeconds: number | null
} {
  if (TERMINAL_JOB_STATUSES.has(progress.status)) {
    return { samples: [], estimatedRemainingSeconds: null }
  }

  const sample = {
    iteration: progress.iteration,
    elapsedSeconds: progress.elapsed_seconds,
    totalIterations: progress.total_iterations,
  }
  const validSample =
    Number.isFinite(sample.iteration)
    && Number.isFinite(sample.elapsedSeconds)
    && Number.isFinite(sample.totalIterations)
    && sample.iteration > 0
    && sample.elapsedSeconds > 0
    && sample.totalIterations > sample.iteration
  if (!validSample) {
    return { samples: previous, estimatedRemainingSeconds: null }
  }

  const prior = previous.at(-1)
  if (!prior) return { samples: [sample], estimatedRemainingSeconds: null }
  if (
    sample.iteration <= prior.iteration
    || sample.elapsedSeconds <= prior.elapsedSeconds
  ) {
    return { samples: previous, estimatedRemainingSeconds: null }
  }

  const rate =
    (sample.iteration - prior.iteration)
    / (sample.elapsedSeconds - prior.elapsedSeconds)
  const remaining = (sample.totalIterations - sample.iteration) / rate
  const estimatedRemainingSeconds =
    Number.isFinite(rate)
    && rate > 0
    && Number.isFinite(remaining)
    && remaining > 0
      ? remaining
      : null

  return {
    samples: [prior, sample],
    estimatedRemainingSeconds,
  }
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
  result?: OptimiserSolveResult
  terminal_reason?: string | null
  execution_metrics?: ExecutionMetrics | null
}

export type TrainResult = {
  status: string
  metrics: Record<string, number>
  feature_importance: { feature: string; importance: number }[]
  model_path: string
  train_rows: number
  validation_rows: number  // validation rows
  holdout_rows?: number
  holdout_metrics?: Record<string, number>
  diagnostics_set?: string  // "train" | "validation" | "holdout"
  features?: string[]
  cat_features?: string[]
  error?: string
  best_iteration?: number | null
  loss_history?: { iteration: number; [key: string]: number }[]
  loss_history_truncated?: boolean
  double_lift?: { decile: number; actual: number; predicted: number; count: number }[]
  shap_summary?: { feature: string; mean_abs_shap: number }[]
  feature_importance_loss?: { feature: string; importance: number }[]
  ave_per_feature?: { feature: string; type: string; bins: { label: string; exposure: number; avg_actual: number; avg_predicted: number }[] }[]
  residuals_histogram?: { bin_center: number; count: number; weighted_count: number }[]
  residuals_stats?: { mean: number; std: number; skew: number; min: number; max: number }
  actual_vs_predicted?: { actual: number; predicted: number; weight: number }[]
  lorenz_curve?: { cum_weight_frac: number; cum_actual_frac: number }[]
  lorenz_curve_perfect?: { cum_weight_frac: number; cum_actual_frac: number }[]
  pdp_data?: { feature: string; type: string; grid: { value: number | string; avg_prediction: number }[]; error?: string; error_type?: string }[]
  warning?: string | null
  total_source_rows?: number | null
  // GLM-specific
  glm_coefficients?: { feature: string; coefficient: number; std_error: number; z_value: number; p_value: number; significance: string }[]
  glm_relativities?: { feature: string; relativity: number; ci_lower?: number; ci_upper?: number }[]
  glm_fit_statistics?: Record<string, number>
  glm_regularization_path?: { selected_alpha?: number; n_nonzero?: number }
  diagnostics_errors?: { diagnostic: string; error: string; error_type: string }[]
  cross_validation?: CrossValidationReport | null
}

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
  result?: TrainResult
  warning?: string | null
  terminal_reason?: string | null
  error_code?: string | null
  http_status_code?: number | null
  error_detail?: unknown
  execution_metrics?: ExecutionMetrics | null
}

export type ExploreProgress = ExploreStatusResponse

interface CachedPreview {
  data: PreviewData
  structuralVersion: number
  source?: string
  rowLimit?: number
}

interface CachedSolveResult {
  result: OptimiserSolveResult
  originalResult: OptimiserSolveResult
  error?: string
  terminalStatus?: SolveProgress | null
  jobId: string
  configHash: string
  /** Staleness key contract: configHash + source + structuralVersion (see useStaleConfigEstimate) */
  source: string
  structuralVersion: number
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
  source: string
  structuralVersion: number
}

interface CachedTrainResult {
  result: TrainResult
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
  /** The only two samples retained for the browser-derived ETA. */
  estimateSamples?: TrainEstimateSample[]
  estimatedRemainingSeconds?: number | null
}

interface CachedExploreResult {
  result: ExploreCacheReport | null
  error?: string
  terminalStatus?: ExploreProgress | null
  jobId: string
  configHash: string
  source: string
  structuralVersion: number
  nodeLabel: string
}

interface ActiveExploreJob {
  jobId: string
  nodeId: string
  nodeLabel: string
  progress: ExploreProgress | null
  error: string | null
  configHash: string
  source: string
  structuralVersion: number
}

// ─── Config hashing ──────────────────────────────────────────────

export function hashConfig(config: Record<string, unknown>): string {
  const {
    _nodeId,
    _columns,
    _schemaWarnings,
    _availableColumns,
    ...semanticConfig
  } = config
  void _nodeId
  void _columns
  void _schemaWarnings
  void _availableColumns

  // Normalise through JSON first so identity follows the same value semantics
  // as persisted config: undefined object fields are omitted, non-finite
  // numbers become null, and serialisable values may use toJSON.
  const json = JSON.stringify(semanticConfig)
  const jsonValue: unknown = JSON.parse(json)

  const sortObjectKeys = (value: unknown): unknown => {
    if (Array.isArray(value)) {
      return value.map(sortObjectKeys)
    }
    if (value !== null && typeof value === "object") {
      const object = value as Record<string, unknown>
      return Object.fromEntries(
        Object.keys(object)
          .sort()
          .map(key => [key, sortObjectKeys(object[key])]),
      )
    }
    return value
  }

  return JSON.stringify(sortObjectKeys(jsonValue))
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
const exploreResultRecency = new Map<string, number>()

export function resetNodeResultsDerivedCaches(): void {
  for (const key of Object.keys(_optimiserPreviewCache)) delete _optimiserPreviewCache[key]
  for (const key of Object.keys(_modellingPreviewCache)) delete _modellingPreviewCache[key]
  previewRecency.clear()
  solveResultRecency.clear()
  trainResultRecency.clear()
  exploreResultRecency.clear()
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

function optionalFrontierNumber(row: Record<string, unknown>, field: string): number | undefined {
  return row[field] === undefined ? undefined : numericFrontierValue(row[field], field)
}

function optionalFrontierInteger(row: Record<string, unknown>, field: string): number | undefined {
  const value = optionalFrontierNumber(row, field)
  if (value === undefined) return undefined
  if (!Number.isInteger(value)) {
    throw new Error(`Frontier point field '${field}' must be an integer`)
  }
  return value
}

function optionalScenarioValueStats(row: Record<string, unknown>): OptimiserSolveResult["scenario_value_stats"] | undefined {
  if (row.scenario_value_stats != null) {
    const stats = recordValue(row.scenario_value_stats, "scenario_value_stats")
    return {
      mean: numericFrontierValue(stats.mean, "scenario_value_stats.mean"),
      std: numericFrontierValue(stats.std, "scenario_value_stats.std"),
      min: numericFrontierValue(stats.min, "scenario_value_stats.min"),
      max: numericFrontierValue(stats.max, "scenario_value_stats.max"),
      p5: numericFrontierValue(stats.p5, "scenario_value_stats.p5"),
      p25: numericFrontierValue(stats.p25, "scenario_value_stats.p25"),
      p50: numericFrontierValue(stats.p50, "scenario_value_stats.p50"),
      p75: numericFrontierValue(stats.p75, "scenario_value_stats.p75"),
      p95: numericFrontierValue(stats.p95, "scenario_value_stats.p95"),
      pct_increase: numericFrontierValue(stats.pct_increase, "scenario_value_stats.pct_increase"),
      pct_decrease: numericFrontierValue(stats.pct_decrease, "scenario_value_stats.pct_decrease"),
    }
  }

  const flatStats = {
    mean: "sv_mean",
    std: "sv_std",
    min: "sv_min",
    max: "sv_max",
    p5: "sv_p5",
    p25: "sv_p25",
    p50: "sv_median",
    p75: "sv_p75",
    p95: "sv_p95",
    pct_increase: "sv_pct_increase",
    pct_decrease: "sv_pct_decrease",
  } as const
  if (!Object.values(flatStats).some((field) => row[field] !== undefined)) return undefined

  return {
    mean: numericFrontierValue(row[flatStats.mean], flatStats.mean),
    std: numericFrontierValue(row[flatStats.std], flatStats.std),
    min: numericFrontierValue(row[flatStats.min], flatStats.min),
    max: numericFrontierValue(row[flatStats.max], flatStats.max),
    p5: numericFrontierValue(row[flatStats.p5], flatStats.p5),
    p25: numericFrontierValue(row[flatStats.p25], flatStats.p25),
    p50: numericFrontierValue(row[flatStats.p50], flatStats.p50),
    p75: numericFrontierValue(row[flatStats.p75], flatStats.p75),
    p95: numericFrontierValue(row[flatStats.p95], flatStats.p95),
    pct_increase: numericFrontierValue(row[flatStats.pct_increase], flatStats.pct_increase),
    pct_decrease: numericFrontierValue(row[flatStats.pct_decrease], flatStats.pct_decrease),
  }
}

function numericArrayValue(value: unknown, field: string): number[] {
  if (!Array.isArray(value)) {
    throw new Error(`Frontier point field '${field}' must be an array`)
  }
  return value.map((entry, index) => numericFrontierValue(entry, `${field}[${index}]`))
}

function optionalScenarioValueHistogram(row: Record<string, unknown>): OptimiserSolveResult["scenario_value_histogram"] | undefined {
  if (row.scenario_value_histogram == null) return undefined
  const histogram = recordValue(row.scenario_value_histogram, "scenario_value_histogram")
  return {
    counts: numericArrayValue(histogram.counts, "scenario_value_histogram.counts"),
    edges: numericArrayValue(histogram.edges, "scenario_value_histogram.edges"),
  }
}

function optionalFactorTables(row: Record<string, unknown>): OptimiserSolveResult["factor_tables"] | undefined {
  if (row.factor_tables == null) return undefined
  const tables = recordValue(row.factor_tables, "factor_tables")
  return Object.fromEntries(
    Object.entries(tables).map(([name, rows]) => {
      if (!Array.isArray(rows) || rows.some(row => !row || typeof row !== "object" || Array.isArray(row))) {
        throw new Error(`Frontier point field 'factor_tables.${name}' must be an array of objects`)
      }
      return [name, rows as Record<string, unknown>[]]
    }),
  )
}

function optionalHistory(row: Record<string, unknown>): OptimiserSolveResult["history"] | null {
  if (row.history === undefined || row.history === null) return null
  if (!Array.isArray(row.history)) {
    throw new Error("Frontier point field 'history' must be an array")
  }
  return row.history.map((entry, index) => {
    const historyEntry = recordValue(entry, `history[${index}]`)
    const parsed = {
      iteration: numericFrontierValue(historyEntry.iteration, `history[${index}].iteration`),
      total_objective: numericFrontierValue(historyEntry.total_objective, `history[${index}].total_objective`),
      max_lambda_change: numericFrontierValue(historyEntry.max_lambda_change, `history[${index}].max_lambda_change`),
    } as NonNullable<OptimiserSolveResult["history"]>[number]
    if (historyEntry.all_constraints_satisfied !== undefined) {
      if (typeof historyEntry.all_constraints_satisfied !== "boolean") {
        throw new Error(`Frontier point field 'history[${index}].all_constraints_satisfied' must be a boolean`)
      }
      parsed.all_constraints_satisfied = historyEntry.all_constraints_satisfied
    }
    if (historyEntry.lambdas !== undefined) {
      const lambdas = recordValue(historyEntry.lambdas, `history[${index}].lambdas`)
      parsed.lambdas = Object.fromEntries(
        Object.entries(lambdas).map(([name, value]) => [name, numericFrontierValue(value, `history[${index}].lambdas.${name}`)]),
      )
    }
    if (historyEntry.total_constraints !== undefined) {
      const constraints = recordValue(historyEntry.total_constraints, `history[${index}].total_constraints`)
      parsed.total_constraints = Object.fromEntries(
        Object.entries(constraints).map(([name, value]) => [name, numericFrontierValue(value, `history[${index}].total_constraints.${name}`)]),
      )
    }
    return parsed
  })
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

function frontierPointHasSelectableSummary(frontier: FrontierData, point: unknown): boolean {
  const row = recordValue(point, "point")
  if (row.total_objective === undefined) return false
  return frontier.constraint_names.every((name) => {
    const [raw] = frontierConstraintValue(row, name)
    return raw !== undefined
  })
}

function deriveSolveResultForFrontierPoint(cached: CachedSolveResult, pointIndex: number): OptimiserSolveResult {
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
    iterations: optionalFrontierInteger(row, "iterations"),
    cd_iterations: optionalFrontierInteger(row, "cd_iterations"),
    clamp_rate: row.clamp_rate === null ? null : optionalFrontierNumber(row, "clamp_rate"),
    history: optionalHistory(row),
    scenario_value_stats: optionalScenarioValueStats(row),
    scenario_value_histogram: optionalScenarioValueHistogram(row),
    factor_tables: optionalFactorTables(row),
    warning: converged ? undefined : NON_CONVERGED_WARNING,
    frontier_error: undefined,
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

  // Explore
  exploreResults: Record<string, CachedExploreResult>
  exploreJobs: Record<string, ActiveExploreJob>

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
  startSolveJob: (nodeId: string, jobId: string, nodeLabel: string, constraints: Record<string, Record<string, number>>, configHash: string, source: string, structuralVersion: number) => void
  updateSolveProgress: (nodeId: string, progress: SolveProgress) => void
  completeSolveJob: (nodeId: string, result: OptimiserSolveResult, terminalStatus?: SolveProgress) => void
  failSolveJob: (nodeId: string, error: string, terminalStatus?: SolveProgress) => void
  selectFrontierPoint: (nodeId: string, pointIndex: number | null) => void
  updateFrontierAfterSelect: (nodeId: string, pointIndex: number, selectResult: FrontierSelectResponse) => void

  // ── Training actions ──
  startTrainJob: (nodeId: string, jobId: string, nodeLabel: string, configHash: string, source: string, structuralVersion: number) => void
  updateTrainProgress: (nodeId: string, progress: TrainProgress) => void
  completeTrainJob: (nodeId: string, result: TrainResult, terminalStatus?: TrainProgress) => void
  failTrainJob: (nodeId: string, error: string, terminalStatus?: TrainProgress) => void

  // ── Explore actions ──
  startExploreJob: (
    nodeId: string,
    jobId: string,
    nodeLabel: string,
    configHash: string,
    source: string,
    structuralVersion: number,
  ) => void
  updateExploreProgress: (nodeId: string, progress: ExploreProgress) => void
  completeExploreJob: (nodeId: string, result: ExploreCacheReport, terminalStatus?: ExploreProgress) => void
  failExploreJob: (nodeId: string, error: string, terminalStatus?: ExploreProgress) => void

  // ── Derived helpers ──
  /** Build OptimiserPreviewData for a node (from completed result or null). */
  getOptimiserPreview: (nodeId: string) => OptimiserPreviewData | null
  /** Return completed training result for a node, or null. */
  getModellingPreview: (nodeId: string) => ModellingPreviewData | null
  /** Mark a solved optimiser preview as recently displayed outside render. */
  touchOptimiserPreview: (nodeId: string) => void
  /** Mark a completed modelling preview as recently displayed outside render. */
  touchModellingPreview: (nodeId: string) => void
  /** Mark a completed Explore result as recently displayed outside render. */
  touchExplorePreview: (nodeId: string) => void

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
  exploreResults: {},
  exploreJobs: {},

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

  startSolveJob: (nodeId, jobId, nodeLabel, constraints, configHash, source, structuralVersion) =>
    set((s) => ({
      solveJobs: {
        ...s.solveJobs,
        [nodeId]: { jobId, nodeId, nodeLabel, progress: null, error: null, constraints, configHash, source, structuralVersion },
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

  completeSolveJob: (nodeId, result, terminalStatus) =>
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
      const initialPointIndex = frontier && frontierPointHasSelectableSummary(frontier, frontier.points[0]) ? 0 : null
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
          result: deriveSolveResultForFrontierPoint(nextCached, initialPointIndex),
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
      for (const evictedNodeId of bounded.evicted) {
        delete _optimiserPreviewCache[evictedNodeId]
      }
      if (bounded.records[nodeId]) cacheOptimiserPreview(nodeId, bounded.records[nodeId])
      return {
        solveJobs: remainingJobs,
        solveResults: bounded.records,
      }
    }),

  failSolveJob: (nodeId, error, terminalStatus) =>
    set((s) => {
      const job = s.solveJobs[nodeId]
      if (!job) return s
      const { [nodeId]: _removedJob, ...remainingJobs } = s.solveJobs; void _removedJob
      touchCachedResult(solveResultRecency, nodeId)
      const nextCached: CachedSolveResult = {
        ...(s.solveResults[nodeId] ?? {
          result: { status: "error", total_objective: 0, baseline_objective: 0, constraints: {}, baseline_constraints: {}, lambdas: {}, converged: false } as OptimiserSolveResult,
          originalResult: { status: "error", total_objective: 0, baseline_objective: 0, constraints: {}, baseline_constraints: {}, lambdas: {}, converged: false } as OptimiserSolveResult,
        }),
        terminalStatus: terminalStatus ?? null,
        jobId: job.jobId,
        configHash: job.configHash,
        source: job.source,
        structuralVersion: job.structuralVersion,
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

  updateFrontierAfterSelect: (nodeId, pointIndex, selectResult) => {
    // Backend echoes ``point_index`` in every select response.  A mismatch is
    // never a race — it is a contract violation, so fail loudly per CLAUDE.md.
    if (selectResult.point_index != null && selectResult.point_index !== pointIndex) {
      throw new Error(
        `Frontier select response point_index (${selectResult.point_index}) does not match requested index (${pointIndex})`,
      )
    }
    set((s) => {
      const cached = s.solveResults[nodeId]
      if (!cached) return s
      touchCachedResult(solveResultRecency, nodeId)
      const enrichedFrontier = cached.frontier && cached.frontier.points[pointIndex]
        ? {
            ...cached.frontier,
            points: cached.frontier.points.map((point, index) => {
              if (index !== pointIndex) return point
              return {
                ...point,
                ...(selectResult.iterations !== undefined ? { iterations: selectResult.iterations } : {}),
                ...(selectResult.cd_iterations !== undefined ? { cd_iterations: selectResult.cd_iterations } : {}),
                ...(selectResult.clamp_rate !== undefined ? { clamp_rate: selectResult.clamp_rate } : {}),
                ...(selectResult.history !== undefined ? { history: selectResult.history } : {}),
                ...(selectResult.scenario_value_stats !== undefined ? { scenario_value_stats: selectResult.scenario_value_stats } : {}),
                ...(selectResult.scenario_value_histogram !== undefined ? { scenario_value_histogram: selectResult.scenario_value_histogram } : {}),
                ...(selectResult.factor_tables !== undefined ? { factor_tables: selectResult.factor_tables } : {}),
              }
            }),
          }
        : cached.frontier
      // Stale-response guard: if the user has already moved on to a different
      // point, keep the enriched frontier (per-point data never goes stale)
      // but do not regress the displayed result/selectedPointIndex back to the
      // older request.  ``null`` means "no selection in flight" (e.g. the very
      // first response after a fresh solve), which is not a stale case.
      if (cached.selectedPointIndex !== null && cached.selectedPointIndex !== pointIndex) {
        if (enrichedFrontier === cached.frontier) return s
        const nextCached = { ...cached, frontier: enrichedFrontier }
        cacheOptimiserPreview(nodeId, nextCached)
        return {
          solveResults: {
            ...s.solveResults,
            [nodeId]: nextCached,
          },
        }
      }
      const pointResult = cached.frontier && cached.frontier.points[pointIndex]
        ? deriveSolveResultForFrontierPoint(cached, pointIndex)
        : {
            ...cached.originalResult,
            iterations: undefined,
            cd_iterations: undefined,
            clamp_rate: undefined,
            history: null,
            scenario_value_stats: undefined,
            scenario_value_histogram: undefined,
            factor_tables: undefined,
            frontier_error: undefined,
          }
      const nextCached = {
        ...cached,
        frontier: enrichedFrontier,
        selectedPointIndex: pointIndex,
        result: {
          ...pointResult,
          total_objective: selectResult.total_objective,
          constraints: selectResult.constraints,
          baseline_objective: selectResult.baseline_objective,
          baseline_constraints: selectResult.baseline_constraints,
          lambdas: selectResult.lambdas,
          converged: selectResult.converged,
          iterations: selectResult.iterations ?? pointResult.iterations,
          cd_iterations: selectResult.cd_iterations ?? pointResult.cd_iterations,
          clamp_rate: selectResult.clamp_rate === undefined ? pointResult.clamp_rate : selectResult.clamp_rate,
          history: selectResult.history === undefined ? pointResult.history : selectResult.history,
          scenario_value_stats: selectResult.scenario_value_stats ?? pointResult.scenario_value_stats,
          scenario_value_histogram: selectResult.scenario_value_histogram ?? pointResult.scenario_value_histogram,
          factor_tables: selectResult.factor_tables ?? pointResult.factor_tables,
          warning: selectResult.warning ?? (selectResult.converged ? undefined : NON_CONVERGED_WARNING),
        },
      }
      cacheOptimiserPreview(nodeId, nextCached)
      return {
        solveResults: {
          ...s.solveResults,
          [nodeId]: nextCached,
        },
      }
    })
  },

  // ── Training ──

  startTrainJob: (nodeId, jobId, nodeLabel, configHash, source, structuralVersion) =>
    set((s) => {
      const nextJob = { jobId, nodeId, nodeLabel, progress: null, error: null, configHash, source, structuralVersion, estimateSamples: [], estimatedRemainingSeconds: null }
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
      const estimate = nextTrainEstimate(job.estimateSamples ?? [], progress)
      return {
        trainJobs: { ...s.trainJobs, [nodeId]: { ...job, progress, estimateSamples: estimate.samples, estimatedRemainingSeconds: estimate.estimatedRemainingSeconds } },
      }
    }),

  completeTrainJob: (nodeId, result, terminalStatus) =>
    set((s) => {
      const job = s.trainJobs[nodeId]
      // Remove the active job if present; also works for direct completion
      // (no active job) used by ModellingConfig for sync/error results.
      const { [nodeId]: _removedJob, ...remainingJobs } = s.trainJobs; void _removedJob
      touchCachedResult(trainResultRecency, nodeId)
      const nextCached: CachedTrainResult = {
        result,
        terminalStatus: terminalStatus ?? null,
        jobId: job?.jobId ?? "",
        configHash: job?.configHash ?? "",
        // Direct completion with no active job has no recorded source; the
        // empty sentinel never matches a real source, so it reads as stale.
        source: job?.source ?? "",
        structuralVersion: job?.structuralVersion ?? -1,
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

  failTrainJob: (nodeId, error, terminalStatus) =>
    set((s) => {
      const job = s.trainJobs[nodeId]
      if (!job) return s
      const { [nodeId]: _removedJob, ...remainingJobs } = s.trainJobs; void _removedJob
      touchCachedResult(trainResultRecency, nodeId)
      const nextCached: CachedTrainResult = {
        result: { status: "error", error, metrics: {}, feature_importance: [], model_path: "", train_rows: 0, validation_rows: 0 } as TrainResult,
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
      for (const evictedNodeId of bounded.evicted) {
        delete _modellingPreviewCache[evictedNodeId]
      }
      delete _modellingPreviewCache[nodeId]
      return {
        trainJobs: remainingJobs,
        trainResults: bounded.records,
      }
    }),

  // ── Explore ──

  startExploreJob: (nodeId, jobId, nodeLabel, configHash, source, structuralVersion) =>
    set((s) => ({
      exploreJobs: {
        ...s.exploreJobs,
        [nodeId]: {
          jobId,
          nodeId,
          nodeLabel,
          progress: null,
          error: null,
          configHash,
          source,
          structuralVersion,
        },
      },
    })),

  updateExploreProgress: (nodeId, progress) =>
    set((s) => {
      const job = s.exploreJobs[nodeId]
      if (!job) return s
      return {
        exploreJobs: { ...s.exploreJobs, [nodeId]: { ...job, progress } },
      }
    }),

  completeExploreJob: (nodeId, result, terminalStatus) =>
    set((s) => {
      const job = s.exploreJobs[nodeId]
      if (!job) return s
      const { [nodeId]: _removedJob, ...remainingJobs } = s.exploreJobs; void _removedJob
      touchCachedResult(exploreResultRecency, nodeId)
      const nextCached: CachedExploreResult = {
        result,
        terminalStatus: terminalStatus ?? null,
        jobId: job.jobId,
        configHash: job.configHash,
        source: job.source,
        structuralVersion: job.structuralVersion,
        nodeLabel: job.nodeLabel,
      }
      const bounded = trimCacheByRecency(
        {
          ...s.exploreResults,
          [nodeId]: nextCached,
        },
        exploreResultRecency,
        MAX_CACHED_EXPLORE_RESULTS,
        s.pinnedPreviewNodeId,
      )
      return {
        exploreJobs: remainingJobs,
        exploreResults: bounded.records,
      }
    }),

  failExploreJob: (nodeId, error, terminalStatus) =>
    set((s) => {
      const job = s.exploreJobs[nodeId]
      if (!job) return s
      const { [nodeId]: _removedJob, ...remainingJobs } = s.exploreJobs; void _removedJob
      touchCachedResult(exploreResultRecency, nodeId)
      const previous = s.exploreResults[nodeId]
      const nextCached: CachedExploreResult = {
        result: previous?.result ?? null,
        terminalStatus: terminalStatus ?? null,
        jobId: job.jobId,
        configHash: job.configHash,
        source: job.source,
        structuralVersion: job.structuralVersion,
        nodeLabel: job.nodeLabel,
        error,
      }
      const bounded = trimCacheByRecency(
        {
          ...s.exploreResults,
          [nodeId]: nextCached,
        },
        exploreResultRecency,
        MAX_CACHED_EXPLORE_RESULTS,
        s.pinnedPreviewNodeId,
      )
      return {
        exploreJobs: remainingJobs,
        exploreResults: bounded.records,
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

  touchExplorePreview: (nodeId) => {
    const cached = get().exploreResults[nodeId]
    if (cached?.result) {
      touchCachedResult(exploreResultRecency, nodeId)
    } else {
      dropCachedResult(exploreResultRecency, nodeId)
    }
  },

  clearNode: (nodeId) => {
    // Clear derived-getter caches for this node
    delete _optimiserPreviewCache[nodeId]
    delete _modellingPreviewCache[nodeId]
    dropCachedResult(previewRecency, nodeId)
    dropCachedResult(solveResultRecency, nodeId)
    dropCachedResult(trainResultRecency, nodeId)
    dropCachedResult(exploreResultRecency, nodeId)
    set((s) => {
      const { [nodeId]: _rp, ...previews } = s.previews; void _rp
      const columnCache = Object.fromEntries(
        Object.entries(s.columnCache).filter(([k]) => k !== nodeId && !k.startsWith(`${nodeId}:`))
      )
      const { [nodeId]: _rsr, ...solveResults } = s.solveResults; void _rsr
      const { [nodeId]: _rsj, ...solveJobs } = s.solveJobs; void _rsj
      const { [nodeId]: _rtr, ...trainResults } = s.trainResults; void _rtr
      const { [nodeId]: _rtj, ...trainJobs } = s.trainJobs; void _rtj
      const { [nodeId]: _rer, ...exploreResults } = s.exploreResults; void _rer
      const { [nodeId]: _rej, ...exploreJobs } = s.exploreJobs; void _rej
      return {
        previews,
        pinnedPreviewNodeId: s.pinnedPreviewNodeId === nodeId ? null : s.pinnedPreviewNodeId,
        columnCache,
        solveResults,
        solveJobs,
        trainResults,
        trainJobs,
        exploreResults,
        exploreJobs,
      }
    })
  },
}))

export default useNodeResultsStore
