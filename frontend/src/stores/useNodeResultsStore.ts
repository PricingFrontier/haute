/**
 * Zustand store for node computation results — previews, optimiser solves, training runs.
 *
 * Results are keyed by node ID and survive panel unmount/remount. This is the
 * core persistence layer that prevents losing expensive computation results
 * when clicking away from a node and back.
 *
 * Cache invalidation:
 *   - Previews: keyed on (nodeId, graphVersion). Stale entries are still
 *     returned for instant display but flagged via getPreview().
 *   - Solve/train results: keyed on (nodeId, configHash). A config change
 *     doesn't delete the old result — it's kept with a staleness flag so
 *     the panel can show "config changed since last run".
 */
import { create } from "zustand"
import type { PreviewData } from "../panels/DataPreview"
import type { SolveResult, OptimiserPreviewData } from "../panels/OptimiserPreview"
import type { FrontierSelectResponse, FrontierData } from "../api/types"
import type { ColumnInfo } from "../types/node"

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
  test_rows: number  // validation rows (kept as test_rows for backward compat)
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
  cv_results?: { mean_metrics: Record<string, number>; std_metrics: Record<string, number>; n_folds: number } | null
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
  graphVersion: number
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

// ─── Store ───────────────────────────────────────────────────────

interface NodeResultsState {
  // Preview cache
  previews: Record<string, CachedPreview>

  // Optimiser
  solveResults: Record<string, CachedSolveResult>
  solveJobs: Record<string, ActiveSolveJob>

  // Training
  trainResults: Record<string, CachedTrainResult>
  trainJobs: Record<string, ActiveTrainJob>

  // Column cache — keyed by "nodeId:source", cached across panel mounts
  columnCache: Record<string, { columns: ColumnInfo[]; graphVersion: number }>

  // Graph version — bumped on any node/edge change
  graphVersion: number

  // ── Column cache actions ──
  setColumns: (sourceNodeId: string, columns: ColumnInfo[], graphVersion: number, source?: string) => void
  getColumns: (sourceNodeId: string, source?: string) => { columns: ColumnInfo[]; fresh: boolean } | null

  // ── Preview actions ──
  setPreview: (nodeId: string, data: PreviewData, graphVersion: number) => void
  /** Returns cached preview, or null if no entry exists. Caller checks graphVersion for staleness. */
  getPreview: (nodeId: string) => CachedPreview | null
  bumpGraphVersion: () => void

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
  getModellingPreview: (nodeId: string) => { result: TrainResult; jobId: string; nodeLabel: string; configHash: string } | null

  // ── Cleanup ──
  clearNode: (nodeId: string) => void
}

const useNodeResultsStore = create<NodeResultsState>()((set, get) => ({
  previews: {},
  columnCache: {},
  solveResults: {},
  solveJobs: {},
  trainResults: {},
  trainJobs: {},
  graphVersion: 0,

  // ── Column cache ──

  setColumns: (sourceNodeId, columns, graphVersion, source) => {
    const key = source ? `${sourceNodeId}:${source}` : sourceNodeId
    set((s) => ({
      columnCache: { ...s.columnCache, [key]: { columns, graphVersion } },
    }))
  },

  getColumns: (sourceNodeId, source) => {
    const key = source ? `${sourceNodeId}:${source}` : sourceNodeId
    const entry = get().columnCache[key]
    if (!entry) return null
    return { columns: entry.columns, fresh: entry.graphVersion === get().graphVersion }
  },

  // ── Preview ──

  setPreview: (nodeId, data, graphVersion) =>
    set((s) => ({
      previews: { ...s.previews, [nodeId]: { data, graphVersion } },
    })),

  getPreview: (nodeId) => get().previews[nodeId] ?? null,

  bumpGraphVersion: () => set((s) => ({ graphVersion: s.graphVersion + 1 })),

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
        ? { points: rawFrontier.points, n_points: rawFrontier.n_points, constraint_names: rawFrontier.constraint_names }
        : null
      return {
        solveJobs: remainingJobs,
        solveResults: {
          ...s.solveResults,
          [nodeId]: {
            result,
            originalResult: result,
            jobId: job.jobId,
            configHash: job.configHash,
            constraints: job.constraints,
            nodeLabel: job.nodeLabel,
            frontier,
            selectedPointIndex: null,
          },
        },
      }
    }),

  failSolveJob: (nodeId, error) =>
    set((s) => {
      const job = s.solveJobs[nodeId]
      if (!job) return s
      const { [nodeId]: _removedJob, ...remainingJobs } = s.solveJobs; void _removedJob
      return {
        solveJobs: remainingJobs,
        solveResults: {
          ...s.solveResults,
          [nodeId]: {
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
          },
        },
      }
    }),

  selectFrontierPoint: (nodeId, pointIndex) =>
    set((s) => {
      const cached = s.solveResults[nodeId]
      if (!cached) return s
      return {
        solveResults: {
          ...s.solveResults,
          [nodeId]: {
            ...cached,
            selectedPointIndex: pointIndex,
            // Revert to original result when deselecting
            ...(pointIndex === null ? { result: cached.originalResult } : {}),
          },
        },
      }
    }),

  updateFrontierAfterSelect: (nodeId, pointIndex, selectResult) =>
    set((s) => {
      const cached = s.solveResults[nodeId]
      if (!cached) return s
      return {
        solveResults: {
          ...s.solveResults,
          [nodeId]: {
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
          },
        },
      }
    }),

  // ── Training ──

  startTrainJob: (nodeId, jobId, nodeLabel, configHash) =>
    set((s) => ({
      trainJobs: {
        ...s.trainJobs,
        [nodeId]: { jobId, nodeId, nodeLabel, progress: null, error: null, configHash },
      },
    })),

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
      return {
        trainJobs: job ? remainingJobs : s.trainJobs,
        trainResults: {
          ...s.trainResults,
          [nodeId]: {
            result,
            jobId: job?.jobId ?? "",
            configHash: job?.configHash ?? "",
          },
        },
      }
    }),

  failTrainJob: (nodeId, error) =>
    set((s) => {
      const job = s.trainJobs[nodeId]
      if (!job) return s
      const { [nodeId]: _removedJob, ...remainingJobs } = s.trainJobs; void _removedJob
      return {
        trainJobs: remainingJobs,
        trainResults: {
          ...s.trainResults,
          [nodeId]: {
            result: { status: "error", error, metrics: {}, feature_importance: [], model_path: "", train_rows: 0, test_rows: 0 } as TrainResult,
            jobId: job.jobId,
            configHash: job.configHash,
          },
        },
      }
    }),

  // ── Derived ──

  getOptimiserPreview: (nodeId) => {
    const cached = get().solveResults[nodeId]
    if (!cached) return null
    return {
      result: cached.result,
      jobId: cached.jobId,
      constraints: cached.constraints,
      nodeLabel: cached.nodeLabel,
      frontier: cached.frontier,
      selectedPointIndex: cached.selectedPointIndex,
    }
  },

  getModellingPreview: (nodeId) => {
    const cached = get().trainResults[nodeId]
    if (!cached) return null
    // Only show completed (non-error) results in the preview panel
    if (cached.result.status === "error") return null
    const job = get().trainJobs[nodeId]
    return {
      result: cached.result,
      jobId: cached.jobId,
      nodeLabel: job?.nodeLabel ?? "Model",
      configHash: cached.configHash,
    }
  },

  // ── Cleanup ──

  clearNode: (nodeId) =>
    set((s) => {
      const { [nodeId]: _rp, ...previews } = s.previews; void _rp
      const columnCache = Object.fromEntries(
        Object.entries(s.columnCache).filter(([k]) => k !== nodeId && !k.startsWith(`${nodeId}:`))
      )
      const { [nodeId]: _rsr, ...solveResults } = s.solveResults; void _rsr
      const { [nodeId]: _rsj, ...solveJobs } = s.solveJobs; void _rsj
      const { [nodeId]: _rtr, ...trainResults } = s.trainResults; void _rtr
      const { [nodeId]: _rtj, ...trainJobs } = s.trainJobs; void _rtj
      return { previews, columnCache, solveResults, solveJobs, trainResults, trainJobs }
    }),
}))

export default useNodeResultsStore
