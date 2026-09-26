/**
 * Tests for useNodeResultsStore — preview cache, solve/train job lifecycle,
 * config hashing, column cache, and cleanup.
 */
import { describe, it, expect, beforeEach } from "vitest"
import useNodeResultsStore, {
  MAX_CACHED_PREVIEWS,
  MAX_CACHED_SOLVE_RESULTS,
  MAX_CACHED_TRAIN_RESULTS,
  MAX_CACHED_EXPLORE_PIVOT_RESULTS,
  MAX_CACHED_OPTIMISER_APPLY,
  effectiveConstraintBounds,
  optimiserApplyIdentityFor,
  optimiserApplyKey,
  explorePivotResultKey,
  hashConfig,
  resetNodeResultsDerivedCaches,
} from "../../stores/useNodeResultsStore.ts"
import useGraphStore from "../../stores/useGraphStore.ts"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore.ts"
import type { PreviewData } from "../../panels/DataPreview.tsx"
import type { ApplyOptimiserResponse, FrontierPoint, FrontierPointSummary, OptimiserSolveResult } from "../../api/types.ts"
import type { ExplorePivotResult, ExplorePivotStatusResponse } from "../../api/types.ts"
import { makeExecutionMetricsFixture } from "../../testSupport/executionMetricsFixture.ts"
import {
  makeTrainResult,
  makeSolveResult as makeSolveResultFactory,
  makeFrontier,
  makeHistoryEntry,
  makeFrontierSelect,
} from "../../test-utils/factories.ts"
import { makePipelineEditorDocument } from "../../testSupport/pipelineDocumentFixture.ts"
import { makeOnlineFrontierPoint } from "../../panels/optimiser/__tests__/fixtures.ts"

const NON_CONVERGED_WARNING = "Solver did not converge. Consider increasing max_iter or relaxing tolerance."

// ── Helpers ──────────────────────────────────────────────────────

function resetStore() {
  resetNodeResultsDerivedCaches()
  useGraphStore.setState({ structuralVersion: 0 })
  useDocumentStatusStore.getState().reset()
  useNodeResultsStore.setState({
    previews: {},
    pinnedPreviewNodeId: null,
    columnCache: {},
    solveResults: {},
    solveJobs: {},
    trainResults: {},
    trainJobs: {},
    pivotResults: {},
    pivotJobs: {},
    optimiserApplyCache: [],
  })
}

function makePreviewData(overrides: Partial<PreviewData> = {}): PreviewData {
  return {
    nodeId: "node-1",
    nodeLabel: "Test Node",
    status: "ok",
    row_count: 10,
    column_count: 2,
    columns: [
      { name: "col_a", dtype: "float64" },
      { name: "col_b", dtype: "int64" },
    ],
    preview: [{ col_a: 1.5, col_b: 42 }],
    error: null,
    ...overrides,
  }
}

function makeSolveResult(
  overrides: Partial<OptimiserSolveResult> = {},
): OptimiserSolveResult {
  return makeSolveResultFactory({
    total_objective: 100,
    baseline_objective: 80,
    constraints: { premium: 50 },
    baseline_constraints: { premium: 45 },
    lambdas: { premium: 0.1 },
    converged: true,
    ...overrides,
  })
}

/** A typed frontier point: the store keeps points as they are and reads only summaries. */
function onlinePoint(totalObjective: number): FrontierPoint {
  return makeOnlineFrontierPoint(0, { total_objective: totalObjective })
}

function pointSummary(overrides: Partial<FrontierPointSummary> = {}): FrontierPointSummary {
  return {
    total_objective: 150,
    constraints: {},
    effective_bounds: {},
    lambdas: {},
    converged: true,
    iterations: null,
    cd_iterations: null,
    clamp_rate: null,
    history: null,
    scenario_value_stats: null,
    scenario_value_histogram: null,
    factor_tables: null,
    warning: null,
    frontier_error: null,
    diagnostics_errors: [],
    ...overrides,
  }
}

function makePivotResult(overrides: Partial<ExplorePivotResult> = {}): ExplorePivotResult {
  return { version: 1, node_id: "e1", pivot_id: "p1", source: "pricing", data_version: "cache", calculation_key: "calc", row_fields: [], column_fields: [], values: [], row_paths: [], column_paths: [], cells: [], warnings: [], generated_at: 1, execution_metrics: null, ...overrides }
}

function makePivotStatus(overrides: Partial<ExplorePivotStatusResponse> = {}): ExplorePivotStatusResponse {
  return { status: "error", progress: 1, message: "Pivot failed", result: null, failure: null, terminal_reason: "error", execution_metrics: null, ...overrides }
}

// ── Test suites ──────────────────────────────────────────────────

describe("useNodeResultsStore", () => {
  beforeEach(() => {
    resetStore()
  })

  it("preserves every active job type across saves and rejects a later external revision", () => {
    const document = useDocumentStatusStore.getState()
    document.loadDocumentStatus(makePipelineEditorDocument({ source_file: "main.py", source_revision: "r1" }))
    const store = useNodeResultsStore.getState()
    store.startTrainJob("n1", "train", "Train", "config", "live", 7)
    store.startSolveJob("n1", "solve", "Solve", {}, "config", "live", 7)
    store.startExplorePivotJob("n1", "pivot", "n1", "p1", "Explore", "Pivot", "calculation", "live", 7)
    const progress = { status: "running" as const, progress: 0.5, message: "Still working", elapsed_seconds: 5, iteration: 3, total_iterations: 6, train_loss: {}, failure: null, terminal_reason: null, execution_metrics: null }
    const update = () => {
      store.updateTrainProgress("n1", progress)
      store.updateSolveProgress("n1", progress)
      store.updateExplorePivotProgress("n1", { ...progress, result: null })
    }
    const collections = ["trainJobs", "solveJobs", "pivotJobs"] as const
    document.acknowledgeSave("r2")
    document.acknowledgeSave("r3")
    update()
    for (const key of collections) {
      expect(useNodeResultsStore.getState()[key].n1).toMatchObject({ progress, source: "live", structuralVersion: 7 })
    }
    document.setSourceRevision("external")
    update()
    for (const key of collections) expect(useNodeResultsStore.getState()[key].n1).toBeUndefined()
  })

  // ────────────────────────────────────────────────────────────────
  // Solve job lifecycle
  // ────────────────────────────────────────────────────────────────

  describe("solve job lifecycle", () => {
    it("startSolveJob creates an active job entry", () => {
      const { startSolveJob } = useNodeResultsStore.getState()
      startSolveJob("n1", "job-1", "Node 1", { premium: { min: 0, max: 100 } }, "hash-a", "live", 0)

      const { solveJobs } = useNodeResultsStore.getState()
      expect(solveJobs["n1"]).toBeDefined()
      expect(solveJobs["n1"].jobId).toBe("job-1")
      expect(solveJobs["n1"].nodeLabel).toBe("Node 1")
      expect(solveJobs["n1"].configHash).toBe("hash-a")
      expect(solveJobs["n1"].progress).toBeNull()
      expect(solveJobs["n1"].error).toBeNull()
    })

    it("updateSolveProgress attaches progress to active job", () => {
      const state = useNodeResultsStore.getState()
      state.startSolveJob("n1", "job-1", "Node 1", {}, "h", "live", 0)
      state.updateSolveProgress("n1", {
        status: "running",
        progress: 0.5,
        message: "Iterating",
        elapsed_seconds: 3,
      })

      const job = useNodeResultsStore.getState().solveJobs["n1"]
      expect(job.progress).not.toBeNull()
      expect(job.progress!.progress).toBe(0.5)
      expect(job.progress!.message).toBe("Iterating")
    })

    it("updateSolveProgress is a no-op for unknown node", () => {
      const state = useNodeResultsStore.getState()
      state.updateSolveProgress("unknown", {
        status: "running",
        progress: 0.5,
        message: "x",
        elapsed_seconds: 1,
      })
      expect(useNodeResultsStore.getState().solveJobs["unknown"]).toBeUndefined()
    })

    it("discards a completion captured under an obsolete document fence", () => {
      useDocumentStatusStore.getState().loadDocumentStatus(
        makePipelineEditorDocument({
          source_file: "rating/main.py",
          source_revision: "r1",
        }),
      )
      const state = useNodeResultsStore.getState()
      state.startSolveJob("n1", "job-1", "Node 1", {}, "h", "live", 0)

      useDocumentStatusStore.getState().loadLiveDocumentStatus(
        makePipelineEditorDocument({
          load_status: "degraded",
          source_file: "rating/main.py",
          source_revision: "r2",
        }),
        true,
        "live-fingerprint",
      )
      state.completeSolveJob("n1", makeSolveResult())

      expect(useNodeResultsStore.getState().solveJobs.n1).toBeUndefined()
      expect(useNodeResultsStore.getState().solveResults.n1).toBeUndefined()
    })

    it("completeSolveJob moves result to solveResults and removes the job", () => {
      const state = useNodeResultsStore.getState()
      state.startSolveJob("n1", "job-1", "Node 1", { premium: { min: 0, max: 100 } }, "hash-a", "live", 0)

      const result = makeSolveResult()
      state.completeSolveJob("n1", result)

      const updated = useNodeResultsStore.getState()
      // Job removed
      expect(updated.solveJobs["n1"]).toBeUndefined()
      // Result stored
      expect(updated.solveResults["n1"]).toBeDefined()
      expect(updated.solveResults["n1"].result).toEqual(result)
      expect(updated.solveResults["n1"].jobId).toBe("job-1")
      expect(updated.solveResults["n1"].configHash).toBe("hash-a")
      expect(updated.solveResults["n1"].constraints).toEqual({ premium: { min: 0, max: 100 } })
      expect(updated.solveResults["n1"].nodeLabel).toBe("Node 1")
    })

    it("completeSolveJob is a no-op when there is no active job", () => {
      const state = useNodeResultsStore.getState()
      state.completeSolveJob("n1", makeSolveResult())
      const updated = useNodeResultsStore.getState()
      // No result should be stored because there was no matching job
      expect(updated.solveResults["n1"]).toBeUndefined()
    })

    it("full lifecycle: start → update → complete", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", { c: { min: 0, max: 1 } }, "h1", "live", 0)
      s.updateSolveProgress("n1", {
        status: "running",
        progress: 0.5,
        message: "halfway",
        elapsed_seconds: 5,
      })
      const result = makeSolveResult({ converged: true, iterations: 42 })
      s.completeSolveJob("n1", result)

      const final = useNodeResultsStore.getState()
      expect(Object.keys(final.solveJobs)).toHaveLength(0)
      expect(final.solveResults["n1"].result?.iterations).toBe(42)
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Solve job failure
  // ────────────────────────────────────────────────────────────────

  describe("failSolveJob", () => {
    it("removes the job from solveJobs on failure", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h", "live", 0)
      s.updateSolveProgress("n1", {
        status: "running",
        progress: 0.3,
        message: "working",
        elapsed_seconds: 2,
      })

      s.failSolveJob("n1", "Solver diverged")

      // Job is removed from solveJobs
      expect(useNodeResultsStore.getState().solveJobs["n1"]).toBeUndefined()
      // Error is stored in solveResults
      const failedResult = useNodeResultsStore.getState().solveResults["n1"]
      expect(failedResult).toBeDefined()
      expect(failedResult.error).toBe("Solver diverged")
    })

    it("retains structured terminal solve diagnostics on failure", () => {
      const s = useNodeResultsStore.getState()
      const executionMetrics = makeExecutionMetricsFixture({ profile: "optimiser_setup", terminal_reason: "memory_limited" })
      const terminalStatus = {
        status: "memory_limited" as const,
        progress: 1,
        message: "Stopped",
        elapsed_seconds: 9,
        terminal_reason: "memory_limited",
        execution_metrics: executionMetrics,
      }

      s.startSolveJob("n1", "j1", "Node 1", {}, "h", "live", 0)
      s.failSolveJob("n1", "Stopped", terminalStatus)

      const failedResult = useNodeResultsStore.getState().solveResults["n1"]
      expect(failedResult.terminalStatus).toEqual(terminalStatus)
      expect(failedResult.terminalStatus?.execution_metrics).toBe(executionMetrics)
    })

    it("caches no fabricated result for a failed solve with no earlier result", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h", "live", 0)

      s.failSolveJob("n1", "Solver diverged")

      const failed = useNodeResultsStore.getState().solveResults["n1"]
      expect(failed.error).toBe("Solver diverged")
      // No zero objective or baseline stands in for a result that does not exist.
      expect(failed.result).toBeNull()
      expect(failed.originalResult).toBeNull()
      expect(useNodeResultsStore.getState().getOptimiserPreview("n1")).toBeNull()
      // Selecting a point on it changes nothing.
      s.selectFrontierPoint("n1", 0)
      expect(useNodeResultsStore.getState().solveResults["n1"]).toBe(failed)
    })

    it("keeps the earlier result when a later solve fails", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h", "live", 0)
      s.completeSolveJob("n1", makeSolveResult({ total_objective: 42 }))
      s.startSolveJob("n1", "j2", "Node 1", {}, "h", "live", 0)

      s.failSolveJob("n1", "Solver diverged")

      const failed = useNodeResultsStore.getState().solveResults["n1"]
      expect(failed.error).toBe("Solver diverged")
      expect(failed.result?.total_objective).toBe(42)
      expect(useNodeResultsStore.getState().getOptimiserPreview("n1")?.result?.total_objective).toBe(42)
    })

    it("is a no-op for unknown node", () => {
      useNodeResultsStore.getState().failSolveJob("ghost", "oops")
      expect(useNodeResultsStore.getState().solveJobs["ghost"]).toBeUndefined()
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Train job lifecycle
  // ────────────────────────────────────────────────────────────────

  describe("train job lifecycle", () => {
    it("startTrainJob creates an active job entry", () => {
      useNodeResultsStore.getState().startTrainJob("t1", "tj-1", "Train Node", "cfg-hash", "live", 0)
      const job = useNodeResultsStore.getState().trainJobs["t1"]
      expect(job).toBeDefined()
      expect(job.jobId).toBe("tj-1")
      expect(job.nodeLabel).toBe("Train Node")
      expect(job.configHash).toBe("cfg-hash")
      expect(job.progress).toBeNull()
      expect(job.error).toBeNull()
    })

    it("updateTrainProgress attaches progress to active job", () => {
      const s = useNodeResultsStore.getState()
      s.startTrainJob("t1", "tj-1", "Train Node", "h", "live", 0)
      s.updateTrainProgress("t1", {
        status: "running",
        progress: 0.7,
        message: "Training...",
        iteration: 70,
        total_iterations: 100,
        train_loss: { rmse: 0.1 },
        elapsed_seconds: 10,
      })

      const job = useNodeResultsStore.getState().trainJobs["t1"]
      expect(job.progress!.progress).toBe(0.7)
      expect(job.progress!.iteration).toBe(70)
    })

    it("updateTrainProgress is a no-op for unknown node", () => {
      useNodeResultsStore.getState().updateTrainProgress("nope", {
        status: "running",
        progress: 0.5,
        message: "x",
        iteration: 50,
        total_iterations: 100,
        train_loss: {},
        elapsed_seconds: 1,
      })
      expect(useNodeResultsStore.getState().trainJobs["nope"]).toBeUndefined()
    })

    it("completeTrainJob moves result to trainResults and removes the job", () => {
      const s = useNodeResultsStore.getState()
      s.startTrainJob("t1", "tj-1", "Train Node", "cfg-hash", "live", 0)
      const result = makeTrainResult()
      s.completeTrainJob("t1", result)

      const updated = useNodeResultsStore.getState()
      expect(updated.trainJobs["t1"]).toBeUndefined()
      expect(updated.trainResults["t1"]).toBeDefined()
      expect(updated.trainResults["t1"].result).toEqual(result)
      expect(updated.trainResults["t1"].jobId).toBe("tj-1")
      expect(updated.trainResults["t1"].configHash).toBe("cfg-hash")
    })

    it("completeTrainJob works even without an active job (direct completion)", () => {
      const s = useNodeResultsStore.getState()
      const result = makeTrainResult()
      s.completeTrainJob("t1", result)

      const updated = useNodeResultsStore.getState()
      // Should still be stored with empty jobId/configHash
      expect(updated.trainResults["t1"]).toBeDefined()
      expect(updated.trainResults["t1"].result).toEqual(result)
      expect(updated.trainResults["t1"].jobId).toBe("")
      expect(updated.trainResults["t1"].configHash).toBe("")
    })

    it("full lifecycle: start → update → complete", () => {
      const s = useNodeResultsStore.getState()
      s.startTrainJob("t1", "tj-1", "Train Node", "h", "live", 0)
      s.updateTrainProgress("t1", {
        status: "running",
        progress: 0.5,
        message: "Training...",
        iteration: 50,
        total_iterations: 100,
        train_loss: { rmse: 0.1 },
        elapsed_seconds: 5,
      })
      const result = makeTrainResult({ final_test_metrics: { rmse: 0.02 } })
      s.completeTrainJob("t1", result)

      const final = useNodeResultsStore.getState()
      expect(Object.keys(final.trainJobs)).toHaveLength(0)
      expect(final.trainResults["t1"].result?.final_test_metrics.rmse).toBe(0.02)
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Train job failure
  // ────────────────────────────────────────────────────────────────

  describe("failTrainJob", () => {
    it("removes job from trainJobs on failure", () => {
      const s = useNodeResultsStore.getState()
      s.startTrainJob("t1", "tj-1", "Train Node", "h", "live", 0)
      s.updateTrainProgress("t1", {
        status: "running",
        progress: 0.3,
        message: "training",
        iteration: 30,
        total_iterations: 100,
        train_loss: {},
        elapsed_seconds: 3,
      })

      s.failTrainJob("t1", "Out of memory")

      // Job is removed from the map (prevents infinite poll-restart loop)
      expect(useNodeResultsStore.getState().trainJobs["t1"]).toBeUndefined()
      // Error is stored in trainResults
      const failedResult = useNodeResultsStore.getState().trainResults["t1"]
      expect(failedResult).toBeDefined()
      expect(failedResult.result?.error).toBe("Out of memory")
      expect(failedResult.result?.status).toBe("error")
    })

    it("retains structured terminal training diagnostics on failure", () => {
      const s = useNodeResultsStore.getState()
      const executionMetrics = makeExecutionMetricsFixture({ profile: "training_prep", terminal_reason: "memory_limited" })
      const terminalStatus = {
        status: "memory_limited" as const,
        progress: 1,
        message: "Stopped",
        iteration: 0,
        total_iterations: 0,
        train_loss: {},
        elapsed_seconds: 8,
        terminal_reason: "memory_limited",
        execution_metrics: executionMetrics,
      }

      s.startTrainJob("t1", "tj-1", "Train Node", "h", "live", 0)
      s.failTrainJob("t1", "Stopped", terminalStatus)

      const failedResult = useNodeResultsStore.getState().trainResults["t1"]
      expect(failedResult.terminalStatus).toEqual(terminalStatus)
      expect(failedResult.terminalStatus?.execution_metrics).toBe(executionMetrics)
    })

    it("is a no-op for unknown node", () => {
      useNodeResultsStore.getState().failTrainJob("ghost", "oops")
      expect(useNodeResultsStore.getState().trainJobs["ghost"]).toBeUndefined()
    })
  })

  describe("explore pivot job lifecycle", () => {
    const start = (nodeId: string, pivotId: string, jobId = `${nodeId}-${pivotId}`) => {
      const key = explorePivotResultKey(nodeId, pivotId)
      useNodeResultsStore.getState().startExplorePivotJob(key, jobId, nodeId, pivotId, `Node ${nodeId}`, `Pivot ${pivotId}`, `identity-${pivotId}`, "pricing", 2)
      return key
    }

    it("keeps pivot IDs on the same node independent and completes only the matching job", () => {
      const first = start("e1", "p1")
      const second = start("e1", "p2")
      useNodeResultsStore.getState().completeExplorePivotJob(first, makePivotResult({ pivot_id: "p1" }))
      const state = useNodeResultsStore.getState()
      expect(state.pivotResults[first]?.result?.pivot_id).toBe("p1")
      expect(state.pivotJobs[first]).toBeUndefined()
      expect(state.pivotJobs[second]).toBeDefined()
    })

    it("retains a prior result when a refresh fails and records terminal failure metadata", () => {
      const key = start("e1", "p1", "job-1")
      const previous = makePivotResult({ pivot_id: "p1", calculation_key: "old" })
      useNodeResultsStore.getState().completeExplorePivotJob(key, previous)
      useNodeResultsStore.getState().startExplorePivotJob(
        key,
        "job-2",
        "e1",
        "p1",
        "Node e1",
        "Pivot p1",
        "new-identity",
        "other-source",
        3,
        "new-dataframe-cache",
      )
      const terminal = makePivotStatus({ message: "No memory" })
      useNodeResultsStore.getState().failExplorePivotJob(key, "No memory", terminal)
      expect(useNodeResultsStore.getState().pivotResults[key]).toMatchObject({
        result: previous,
        error: "No memory",
        terminalStatus: terminal,
        jobId: "job-2",
        calculationIdentity: "identity-p1",
        lastAttemptedCalculationIdentity: "new-identity",
        lastAttemptedDataframeCacheKey: "new-dataframe-cache",
        source: "pricing",
        structuralVersion: 2,
      })
    })

    it("drops pivot completions and failures captured under an obsolete document fence", () => {
      useDocumentStatusStore.getState().loadDocumentStatus(
        makePipelineEditorDocument({
          source_file: "rating/main.py",
          source_revision: "r1",
        }),
      )
      const completedKey = start("e1", "completed")
      const failedKey = start("e1", "failed")

      useDocumentStatusStore.getState().loadLiveDocumentStatus(
        makePipelineEditorDocument({
          load_status: "degraded",
          source_file: "rating/main.py",
          source_revision: "r2",
        }),
        true,
        "live-fingerprint",
      )
      const state = useNodeResultsStore.getState()
      state.completeExplorePivotJob(completedKey, makePivotResult({ pivot_id: "completed" }))
      state.failExplorePivotJob(failedKey, "obsolete failure")

      expect(useNodeResultsStore.getState().pivotJobs).toEqual({})
      expect(useNodeResultsStore.getState().pivotResults).toEqual({})
    })

    it("clearNode removes only pivot entries owned by that node", () => {
      const first = start("e1", "p1")
      const second = start("e1", "p2")
      const other = start("e10", "p1")
      useNodeResultsStore.getState().completeExplorePivotJob(first, makePivotResult())
      useNodeResultsStore.getState().completeExplorePivotJob(second, makePivotResult({ pivot_id: "p2" }))
      useNodeResultsStore.getState().completeExplorePivotJob(other, makePivotResult({ node_id: "e10" }))
      const pending = start("e1", "pending")
      const pendingOther = start("e10", "pending")
      useNodeResultsStore.getState().clearNode("e1")
      const state = useNodeResultsStore.getState()
      expect(state.pivotResults[first]).toBeUndefined()
      expect(state.pivotResults[second]).toBeUndefined()
      expect(state.pivotResults[other]).toBeDefined()
      expect(state.pivotJobs[pending]).toBeUndefined()
      expect(state.pivotJobs[pendingOther]).toBeDefined()
    })

    it("enforces the pivot result LRU cap", () => {
      for (let index = 0; index < MAX_CACHED_EXPLORE_PIVOT_RESULTS + 1; index += 1) {
        const key = start(`e${index}`, "p1")
        useNodeResultsStore.getState().completeExplorePivotJob(key, makePivotResult({ node_id: `e${index}` }))
      }
      expect(Object.keys(useNodeResultsStore.getState().pivotResults)).toHaveLength(MAX_CACHED_EXPLORE_PIVOT_RESULTS)
    })
  })

  describe("hashConfig", () => {
    it("returns the same hash for the same config", () => {
      const config = { solver: "glpk", tolerance: 0.01 }
      expect(hashConfig(config)).toBe(hashConfig({ ...config }))
    })

    it("returns different hashes for different configs", () => {
      const a = { solver: "glpk", tolerance: 0.01 }
      const b = { solver: "glpk", tolerance: 0.02 }
      expect(hashConfig(a)).not.toBe(hashConfig(b))
    })

    it("strips _nodeId, _columns, _schemaWarnings, and _availableColumns before hashing", () => {
      const base = { solver: "glpk", tolerance: 0.01 }
      const withInternals = {
        ...base,
        _nodeId: "n-42",
        _columns: [{ name: "x", dtype: "float64" }],
        _schemaWarnings: [{ column: "x", status: "missing" }],
        _availableColumns: ["x", "y", "z"],
      }
      expect(hashConfig(base)).toBe(hashConfig(withInternals))
    })

    it("keeps reserved key spellings semantic below the root config", () => {
      expect(hashConfig({
        nested: { _columns: ["premium"] },
      })).not.toBe(hashConfig({
        nested: { _columns: ["discount"] },
      }))
    })

    it("uses ordinary JSON semantics for undefined and non-finite values", () => {
      expect(hashConfig({ optional: undefined })).toBe(hashConfig({}))
      expect(hashConfig({ value: Number.NaN })).toBe(hashConfig({ value: null }))
      expect(hashConfig({
        values: [undefined, Number.POSITIVE_INFINITY],
      })).toBe(hashConfig({
        values: [null, null],
      }))
    })

    it("accepts nested values with a JSON toJSON contract", () => {
      expect(hashConfig({
        value: { toJSON: () => ({ b: 2, a: 1 }) },
      })).toBe(hashConfig({
        value: { a: 1, b: 2 },
      }))
    })

    it("still rejects genuinely cyclic configuration", () => {
      const cyclic: Record<string, unknown> = {}
      cyclic.self = cyclic

      expect(() => hashConfig(cyclic)).toThrow()
    })

    it("normalizes nested objects inside arrays when hashing", () => {
      const first = { constraints: [{ name: "premium", bounds: { min: 0, max: 100 } }] }
      const second = { constraints: [{ bounds: { max: 100, min: 0 }, name: "premium" }] }

      expect(hashConfig(first)).toBe(hashConfig(second))
    })

    it("returns a non-empty string", () => {
      const hash = hashConfig({ a: 1 })
      expect(hash.length).toBeGreaterThan(0)
    })

    it("uses the same identity for different object key orders", () => {
      const a = { x: 1, y: 2 }
      const b = { y: 2, x: 1 }

      expect(hashConfig(a)).toBe(hashConfig(b))
    })

    it("preserves array order in the identity", () => {
      expect(hashConfig({ columns: ["premium", "discount"] })).not.toBe(
        hashConfig({ columns: ["discount", "premium"] }),
      )
    })

    it("does not collide for a known DJB2 collision", () => {
      expect(hashConfig({ value: "10ry7jgv0xesb" })).not.toBe(
        hashConfig({ value: "n2mwwma6ztkt" }),
      )
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Preview cache / structural version
  // ────────────────────────────────────────────────────────────────

  describe("preview cache and structural version", () => {
    it("setPreview then getPreview returns cached data", () => {
      const s = useNodeResultsStore.getState()
      const preview = makePreviewData()
      s.setPreview("n1", preview, 0)

      const cached = s.getPreview("n1")
      expect(cached).not.toBeNull()
      expect(cached!.data).toEqual(preview)
      expect(cached!.structuralVersion).toBe(0)
    })

    it("getPreview returns null for unknown node", () => {
      expect(useNodeResultsStore.getState().getPreview("unknown")).toBeNull()
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Column cache
  // ────────────────────────────────────────────────────────────────

  describe("column cache", () => {
    it("setColumns then getColumns returns columns with fresh=true", () => {
      const s = useNodeResultsStore.getState()
      const columns = [{ name: "a", dtype: "float64" }, { name: "b", dtype: "int64" }]
      s.setColumns("src-1", columns, 0)

      const result = useNodeResultsStore.getState().getColumns("src-1")
      expect(result).not.toBeNull()
      expect(result!.columns).toEqual(columns)
      expect(result!.fresh).toBe(true)
    })

    it("getColumns returns null for unknown source", () => {
      expect(useNodeResultsStore.getState().getColumns("nope")).toBeNull()
    })

    it("columns become stale when structuralVersion changes", () => {
      const s = useNodeResultsStore.getState()
      s.setColumns("src-1", [{ name: "a", dtype: "float64" }], 0)
      useGraphStore.setState({ structuralVersion: 1 })

      const result = useNodeResultsStore.getState().getColumns("src-1")
      expect(result).not.toBeNull()
      expect(result!.fresh).toBe(false)
    })

    it("columns set at current structural version are fresh", () => {
      const s = useNodeResultsStore.getState()
      useGraphStore.setState({ structuralVersion: 1 })
      s.setColumns("src-1", [{ name: "a", dtype: "float64" }], 1)

      const result = useNodeResultsStore.getState().getColumns("src-1")
      expect(result).not.toBeNull()
      expect(result!.fresh).toBe(true)
    })
  })

  // ────────────────────────────────────────────────────────────────
  // clearNode
  // ────────────────────────────────────────────────────────────────

  describe("clearNode", () => {
    it("removes all data for a given node", () => {
      const s = useNodeResultsStore.getState()

      // Set up data across all caches
      s.setPreview("n1", makePreviewData(), 0)
      s.setColumns("n1", [{ name: "x", dtype: "float64" }], 0)
      s.startSolveJob("n1", "sj1", "Node 1", { c: { min: 0, max: 1 } }, "h1", "live", 0)
      s.startTrainJob("n1", "tj1", "Train 1", "th1", "live", 0)

      // Also set up a solve result (complete a second job to create it)
      s.startSolveJob("n1b", "sj2", "Node 1b", {}, "h2", "live", 0)
      // We'll directly inject a solve result for n1
      useNodeResultsStore.setState((prev) => ({
        solveResults: { ...prev.solveResults, n1: { result: makeSolveResult(), originalResult: makeSolveResult(), jobId: "sj-old", configHash: "h-old", source: "live", structuralVersion: 0, constraints: {}, nodeLabel: "N1", frontier: null, selectedPointIndex: null } },
        trainResults: { ...prev.trainResults, n1: { result: makeTrainResult(), jobId: "tj-old", configHash: "th-old", source: "live", structuralVersion: 0 } },
      }))

      // Verify all caches have data
      expect(useNodeResultsStore.getState().getPreview("n1")).not.toBeNull()
      expect(useNodeResultsStore.getState().getColumns("n1")).not.toBeNull()
      expect(useNodeResultsStore.getState().solveJobs["n1"]).toBeDefined()
      expect(useNodeResultsStore.getState().solveResults["n1"]).toBeDefined()
      expect(useNodeResultsStore.getState().trainJobs["n1"]).toBeDefined()
      expect(useNodeResultsStore.getState().trainResults["n1"]).toBeDefined()

      // Clear
      useNodeResultsStore.getState().clearNode("n1")

      const after = useNodeResultsStore.getState()
      expect(after.getPreview("n1")).toBeNull()
      expect(after.getColumns("n1")).toBeNull()
      expect(after.solveJobs["n1"]).toBeUndefined()
      expect(after.solveResults["n1"]).toBeUndefined()
      expect(after.trainJobs["n1"]).toBeUndefined()
      expect(after.trainResults["n1"]).toBeUndefined()
    })

    it("clearNode removes source-keyed column cache entries", () => {
      const s = useNodeResultsStore.getState()
      s.setColumns("n1", [{ name: "a", dtype: "float64" }], 0, "live")
      s.setColumns("n1", [{ name: "b", dtype: "float64" }], 0, "staging")
      s.setColumns("n2", [{ name: "c", dtype: "float64" }], 0, "live")

      expect(useNodeResultsStore.getState().getColumns("n1", "live")).not.toBeNull()
      expect(useNodeResultsStore.getState().getColumns("n1", "staging")).not.toBeNull()

      useNodeResultsStore.getState().clearNode("n1")

      expect(useNodeResultsStore.getState().getColumns("n1", "live")).toBeNull()
      expect(useNodeResultsStore.getState().getColumns("n1", "staging")).toBeNull()
      expect(useNodeResultsStore.getState().getColumns("n2", "live")).not.toBeNull()
    })

    it("does not affect other nodes", () => {
      const s = useNodeResultsStore.getState()
      s.setPreview("n1", makePreviewData(), 0)
      s.setPreview("n2", makePreviewData({ nodeId: "n2" }), 0)

      s.clearNode("n1")

      expect(useNodeResultsStore.getState().getPreview("n1")).toBeNull()
      expect(useNodeResultsStore.getState().getPreview("n2")).not.toBeNull()
    })

    it("clears pinnedPreviewNodeId when clearing the pinned node", () => {
      const s = useNodeResultsStore.getState()
      s.setPinnedPreviewNodeId("n1")

      s.clearNode("n1")

      expect(useNodeResultsStore.getState().pinnedPreviewNodeId).toBeNull()
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Bounded result caches
  // ────────────────────────────────────────────────────────────────

  describe("bounded result caches", () => {
    it("evicts the oldest cached previews when the preview cap is exceeded", () => {
      const s = useNodeResultsStore.getState()

      for (let i = 0; i < MAX_CACHED_PREVIEWS + 2; i += 1) {
        s.setPreview(`p${i}`, makePreviewData({ nodeId: `p${i}` }), 0)
      }

      const { previews } = useNodeResultsStore.getState()
      expect(Object.keys(previews)).toHaveLength(MAX_CACHED_PREVIEWS)
      expect(previews.p0).toBeUndefined()
      expect(previews.p1).toBeUndefined()
      expect(previews[`p${MAX_CACHED_PREVIEWS + 1}`]).toBeDefined()
    })

    it("keeps recently read previews when evicting", () => {
      const s = useNodeResultsStore.getState()

      for (let i = 0; i < MAX_CACHED_PREVIEWS; i += 1) {
        s.setPreview(`p${i}`, makePreviewData({ nodeId: `p${i}` }), 0)
      }
      expect(s.getPreview("p0")).not.toBeNull()
      s.setPreview("p-new", makePreviewData({ nodeId: "p-new" }), 0)

      const { previews } = useNodeResultsStore.getState()
      expect(Object.keys(previews)).toHaveLength(MAX_CACHED_PREVIEWS)
      expect(previews.p0).toBeDefined()
      expect(previews.p1).toBeUndefined()
      expect(previews["p-new"]).toBeDefined()
    })

    it("keeps the pinned preview node when evicting by recency", () => {
      const s = useNodeResultsStore.getState()

      for (let i = 0; i < MAX_CACHED_PREVIEWS; i += 1) {
        s.setPreview(`p${i}`, makePreviewData({ nodeId: `p${i}` }), 0)
      }
      s.setPinnedPreviewNodeId("p0")
      s.setPreview("p-new-1", makePreviewData({ nodeId: "p-new-1" }), 0)
      s.setPreview("p-new-2", makePreviewData({ nodeId: "p-new-2" }), 0)

      const { previews } = useNodeResultsStore.getState()
      expect(Object.keys(previews)).toHaveLength(MAX_CACHED_PREVIEWS)
      expect(previews.p0).toBeDefined()
      expect(previews.p1).toBeUndefined()
      expect(previews.p2).toBeUndefined()
      expect(previews["p-new-1"]).toBeDefined()
      expect(previews["p-new-2"]).toBeDefined()
    })

    it("allows the previously pinned preview node to be evicted after clearing the pin", () => {
      const s = useNodeResultsStore.getState()

      for (let i = 0; i < MAX_CACHED_PREVIEWS; i += 1) {
        s.setPreview(`p${i}`, makePreviewData({ nodeId: `p${i}` }), 0)
      }
      s.setPinnedPreviewNodeId("p0")
      s.setPreview("p-new-1", makePreviewData({ nodeId: "p-new-1" }), 0)
      s.setPinnedPreviewNodeId(null)
      s.setPreview("p-new-2", makePreviewData({ nodeId: "p-new-2" }), 0)

      const { previews } = useNodeResultsStore.getState()
      expect(Object.keys(previews)).toHaveLength(MAX_CACHED_PREVIEWS)
      expect(previews.p0).toBeUndefined()
      expect(previews.p1).toBeUndefined()
      expect(previews.p2).toBeDefined()
      expect(previews["p-new-1"]).toBeDefined()
      expect(previews["p-new-2"]).toBeDefined()
    })

    it("leaves the pin unchanged when setting the same pinned preview node", () => {
      const s = useNodeResultsStore.getState()
      s.setPinnedPreviewNodeId("p0")

      s.setPinnedPreviewNodeId("p0")

      expect(useNodeResultsStore.getState().pinnedPreviewNodeId).toBe("p0")
    })

    it("evicts the oldest cached solve results without removing active solve jobs", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("active-solve", "active-job", "Still Running", {}, "active-hash", "live", 0)

      for (let i = 0; i < MAX_CACHED_SOLVE_RESULTS; i += 1) {
        s.startSolveJob(`s${i}`, `job-${i}`, `Solve ${i}`, {}, `hash-${i}`, "live", 0)
        s.completeSolveJob(`s${i}`, makeSolveResult({ total_objective: i }))
      }
      expect(s.getOptimiserPreview("s0")).not.toBeNull()
      s.startSolveJob(`s${MAX_CACHED_SOLVE_RESULTS}`, `job-${MAX_CACHED_SOLVE_RESULTS}`, "Solve new", {}, "hash-new", "live", 0)
      s.completeSolveJob(`s${MAX_CACHED_SOLVE_RESULTS}`, makeSolveResult({ total_objective: MAX_CACHED_SOLVE_RESULTS }))

      const { solveJobs, solveResults } = useNodeResultsStore.getState()
      expect(solveJobs["active-solve"]).toBeDefined()
      expect(Object.keys(solveResults)).toHaveLength(MAX_CACHED_SOLVE_RESULTS)
      expect(solveResults.s0).toBeUndefined()
      expect(solveResults[`s${MAX_CACHED_SOLVE_RESULTS}`]?.result?.total_objective).toBe(MAX_CACHED_SOLVE_RESULTS)
    })

    it("evicts cached optimiser previews when failed solve results push out old entries", () => {
      const s = useNodeResultsStore.getState()

      for (let i = 0; i < MAX_CACHED_SOLVE_RESULTS; i += 1) {
        s.startSolveJob(`s${i}`, `job-${i}`, `Solve ${i}`, {}, `hash-${i}`, "live", 0)
        s.completeSolveJob(`s${i}`, makeSolveResult({ total_objective: i }))
      }
      expect(s.getOptimiserPreview("s0")).not.toBeNull()
      s.startSolveJob("s-new", "job-new", "Solve new", {}, "hash-new", "live", 0)
      s.failSolveJob("s-new", "Solver failed")

      const { solveResults } = useNodeResultsStore.getState()
      expect(Object.keys(solveResults)).toHaveLength(MAX_CACHED_SOLVE_RESULTS)
      expect(solveResults.s0).toBeUndefined()
      expect(solveResults["s-new"].error).toBe("Solver failed")
    })

    it("keeps explicitly touched solve results when evicting", () => {
      const s = useNodeResultsStore.getState()

      for (let i = 0; i < MAX_CACHED_SOLVE_RESULTS; i += 1) {
        s.startSolveJob(`s${i}`, `job-${i}`, `Solve ${i}`, {}, `hash-${i}`, "live", 0)
        s.completeSolveJob(`s${i}`, makeSolveResult({ total_objective: i }))
      }
      expect(s.getOptimiserPreview("s0")).not.toBeNull()
      s.touchOptimiserPreview("s0")
      s.startSolveJob("s-new", "job-new", "Solve new", {}, "hash-new", "live", 0)
      s.completeSolveJob("s-new", makeSolveResult({ total_objective: 999 }))

      const { solveResults } = useNodeResultsStore.getState()
      expect(Object.keys(solveResults)).toHaveLength(MAX_CACHED_SOLVE_RESULTS)
      expect(solveResults.s0).toBeDefined()
      expect(solveResults.s1).toBeUndefined()
      expect(solveResults["s-new"]?.result?.total_objective).toBe(999)
    })

    it("keeps the pinned optimiser preview result when evicting by recency", () => {
      const s = useNodeResultsStore.getState()

      for (let i = 0; i < MAX_CACHED_SOLVE_RESULTS; i += 1) {
        s.startSolveJob(`s${i}`, `job-${i}`, `Solve ${i}`, {}, `hash-${i}`, "live", 0)
        s.completeSolveJob(`s${i}`, makeSolveResult({ total_objective: i }))
      }
      s.setPinnedPreviewNodeId("s0")
      s.startSolveJob("s-new-1", "job-new-1", "Solve new 1", {}, "hash-new-1", "live", 0)
      s.completeSolveJob("s-new-1", makeSolveResult({ total_objective: 998 }))
      s.startSolveJob("s-new-2", "job-new-2", "Solve new 2", {}, "hash-new-2", "live", 0)
      s.completeSolveJob("s-new-2", makeSolveResult({ total_objective: 999 }))

      const { solveResults } = useNodeResultsStore.getState()
      expect(Object.keys(solveResults)).toHaveLength(MAX_CACHED_SOLVE_RESULTS)
      expect(solveResults.s0).toBeDefined()
      expect(solveResults.s1).toBeUndefined()
      expect(solveResults.s2).toBeUndefined()
      expect(solveResults["s-new-1"]?.result?.total_objective).toBe(998)
      expect(solveResults["s-new-2"]?.result?.total_objective).toBe(999)
    })

    it("evicts the oldest cached train results without removing active train jobs", () => {
      const s = useNodeResultsStore.getState()
      s.startTrainJob("active-train", "active-job", "Still Running", "active-hash", "live", 0)

      for (let i = 0; i < MAX_CACHED_TRAIN_RESULTS + 1; i += 1) {
        s.startTrainJob(`t${i}`, `job-${i}`, `Train ${i}`, `hash-${i}`, "live", 0)
        s.completeTrainJob(`t${i}`, makeTrainResult({ final_test_metrics: { rmse: i } }))
      }

      const { trainJobs, trainResults } = useNodeResultsStore.getState()
      expect(trainJobs["active-train"]).toBeDefined()
      expect(Object.keys(trainResults)).toHaveLength(MAX_CACHED_TRAIN_RESULTS)
      expect(trainResults.t0).toBeUndefined()
      expect(trainResults[`t${MAX_CACHED_TRAIN_RESULTS}`]?.result.final_test_metrics.rmse).toBe(MAX_CACHED_TRAIN_RESULTS)
    })

    it("evicts cached modelling previews when failed train results push out old entries", () => {
      const s = useNodeResultsStore.getState()

      for (let i = 0; i < MAX_CACHED_TRAIN_RESULTS; i += 1) {
        s.startTrainJob(`t${i}`, `job-${i}`, `Train ${i}`, `hash-${i}`, "live", 0)
        s.completeTrainJob(`t${i}`, makeTrainResult({ final_test_metrics: { rmse: i } }))
      }
      expect(s.getModellingPreview("t0")).not.toBeNull()
      s.startTrainJob("t-new", "job-new", "Train new", "hash-new", "live", 0)
      s.failTrainJob("t-new", "Training failed")

      const { trainResults } = useNodeResultsStore.getState()
      expect(Object.keys(trainResults)).toHaveLength(MAX_CACHED_TRAIN_RESULTS)
      expect(trainResults.t0).toBeUndefined()
      expect(trainResults["t-new"].result?.status).toBe("error")
    })

    it("keeps explicitly touched train results when evicting", () => {
      const s = useNodeResultsStore.getState()

      for (let i = 0; i < MAX_CACHED_TRAIN_RESULTS; i += 1) {
        s.startTrainJob(`t${i}`, `job-${i}`, `Train ${i}`, `hash-${i}`, "live", 0)
        s.completeTrainJob(`t${i}`, makeTrainResult({ final_test_metrics: { rmse: i } }))
      }
      expect(s.getModellingPreview("t0")).not.toBeNull()
      s.touchModellingPreview("t0")
      s.startTrainJob("t-new", "job-new", "Train new", "hash-new", "live", 0)
      s.completeTrainJob("t-new", makeTrainResult({ final_test_metrics: { rmse: 999 } }))

      const { trainResults } = useNodeResultsStore.getState()
      expect(Object.keys(trainResults)).toHaveLength(MAX_CACHED_TRAIN_RESULTS)
      expect(trainResults.t0).toBeDefined()
      expect(trainResults.t1).toBeUndefined()
      expect(trainResults["t-new"]?.result.final_test_metrics.rmse).toBe(999)
    })

    it("keeps the pinned modelling preview result when evicting by recency", () => {
      const s = useNodeResultsStore.getState()

      for (let i = 0; i < MAX_CACHED_TRAIN_RESULTS; i += 1) {
        s.startTrainJob(`t${i}`, `job-${i}`, `Train ${i}`, `hash-${i}`, "live", 0)
        s.completeTrainJob(`t${i}`, makeTrainResult({ final_test_metrics: { rmse: i } }))
      }
      s.setPinnedPreviewNodeId("t0")
      s.startTrainJob("t-new-1", "job-new-1", "Train new 1", "hash-new-1", "live", 0)
      s.completeTrainJob("t-new-1", makeTrainResult({ final_test_metrics: { rmse: 998 } }))
      s.startTrainJob("t-new-2", "job-new-2", "Train new 2", "hash-new-2", "live", 0)
      s.completeTrainJob("t-new-2", makeTrainResult({ final_test_metrics: { rmse: 999 } }))

      const { trainResults } = useNodeResultsStore.getState()
      expect(Object.keys(trainResults)).toHaveLength(MAX_CACHED_TRAIN_RESULTS)
      expect(trainResults.t0).toBeDefined()
      expect(trainResults.t1).toBeUndefined()
      expect(trainResults.t2).toBeUndefined()
      expect(trainResults["t-new-1"]?.result.final_test_metrics.rmse).toBe(998)
      expect(trainResults["t-new-2"]?.result.final_test_metrics.rmse).toBe(999)
    })

    it("clearNode removes preview eviction bookkeeping for the cleared node", () => {
      const s = useNodeResultsStore.getState()

      for (let i = 0; i < MAX_CACHED_PREVIEWS; i += 1) {
        s.setPreview(`p${i}`, makePreviewData({ nodeId: `p${i}` }), 0)
      }
      s.clearNode("p0")
      s.setPreview("p-new-1", makePreviewData({ nodeId: "p-new-1" }), 0)
      s.setPreview("p-new-2", makePreviewData({ nodeId: "p-new-2" }), 0)

      const { previews } = useNodeResultsStore.getState()
      expect(Object.keys(previews)).toHaveLength(MAX_CACHED_PREVIEWS)
      expect(previews.p0).toBeUndefined()
      expect(previews.p1).toBeUndefined()
      expect(previews["p-new-1"]).toBeDefined()
      expect(previews["p-new-2"]).toBeDefined()
    })

    it("touch helpers tolerate missing and error cached results", () => {
      const s = useNodeResultsStore.getState()

      s.touchOptimiserPreview("missing-solve")
      s.touchModellingPreview("missing-train")

      s.startTrainJob("t-error", "job-error", "Train error", "hash-error", "live", 0)
      s.failTrainJob("t-error", "Training failed")
      s.touchModellingPreview("t-error")

      expect(useNodeResultsStore.getState().solveResults["missing-solve"]).toBeUndefined()
      expect(useNodeResultsStore.getState().trainResults["missing-train"]).toBeUndefined()
      expect(useNodeResultsStore.getState().trainResults["t-error"].result?.status).toBe("error")
    })
  })

  // ────────────────────────────────────────────────────────────────
  // getOptimiserPreview
  // ────────────────────────────────────────────────────────────────

  describe("optimiser apply cache", () => {
    function applyResponse(rowCount: number): ApplyOptimiserResponse {
      return {
        status: "ok",
        total_objective: 100,
        constraints: { premium: 50 },
        from_artifact: false,
        preview: [{ quote_id: `Q${rowCount}` }],
        row_count: rowCount,
        preview_row_count: 1,
        preview_row_limit: 100,
        preview_truncated: rowCount > 1,
        error: null,
      }
    }

    function frontierResult(generation: number): OptimiserSolveResult {
      return makeSolveResult({
        frontier_generation: generation,
        frontier: makeFrontier({
          points: [onlinePoint(150), onlinePoint(160)],
          point_summaries: [pointSummary(), pointSummary({ total_objective: 160 })],
          n_points: 2,
          points_returned: 2,
          frontier_generation: generation,
        }),
      })
    }

    function solve(nodeId: string, jobId: string, result: OptimiserSolveResult) {
      const s = useNodeResultsStore.getState()
      s.startSolveJob(nodeId, jobId, "Optimiser", {}, "h", "live", 0)
      s.completeSolveJob(nodeId, result)
    }

    function currentIdentity(nodeId: string) {
      const cached = useNodeResultsStore.getState().solveResults[nodeId]
      if (cached.result === null) throw new Error(`Node ${nodeId} has no solve result`)
      return optimiserApplyIdentityFor(cached, {})
    }

    function cachedKeys(): string[] {
      return useNodeResultsStore.getState().optimiserApplyCache.map((entry) => entry.key)
    }

    it("derives the node's current identity from its job, frontier generation and selection", () => {
      solve("n1", "j1", frontierResult(3))
      expect(currentIdentity("n1")).toEqual({ jobId: "j1", frontierGeneration: 3, target: 0, query: {} })

      useNodeResultsStore.getState().selectFrontierPoint("n1", null)
      expect(currentIdentity("n1")).toEqual({ jobId: "j1", frontierGeneration: 3, target: "solved", query: {} })
    })

    it("keys every part of the identity", () => {
      const base = { jobId: "j1", frontierGeneration: 0, target: "solved" as const, query: {} }
      const keys = new Set([
        optimiserApplyKey(base),
        optimiserApplyKey({ ...base, jobId: "j2" }),
        optimiserApplyKey({ ...base, frontierGeneration: 1 }),
        optimiserApplyKey({ ...base, target: 0 }),
      ])
      expect(keys.size).toBe(4)
    })

    it("stores a response for the current identity and drops one for a target no longer shown", () => {
      solve("n1", "j1", frontierResult(0))
      const s = useNodeResultsStore.getState()
      const pointZero = currentIdentity("n1")

      expect(s.recordOptimiserApply("n1", pointZero, applyResponse(1))).toBe(true)
      expect(cachedKeys()).toEqual([optimiserApplyKey(pointZero)])

      s.selectFrontierPoint("n1", 1)
      expect(s.recordOptimiserApply("n1", pointZero, applyResponse(2))).toBe(false)
      expect(useNodeResultsStore.getState().optimiserApplyCache[0].response.row_count).toBe(1)
    })

    it("drops a late response from an earlier job or frontier generation", () => {
      solve("n1", "j1", frontierResult(0))
      const earlierJob = currentIdentity("n1")
      solve("n1", "j1", frontierResult(1))
      const earlierGeneration = currentIdentity("n1")
      solve("n1", "j2", frontierResult(1))
      const s = useNodeResultsStore.getState()

      expect(s.recordOptimiserApply("n1", earlierJob, applyResponse(1))).toBe(false)
      expect(s.recordOptimiserApply("n1", earlierGeneration, applyResponse(1))).toBe(false)
      expect(s.recordOptimiserApply("n2", currentIdentity("n1"), applyResponse(1))).toBe(false)
      expect(cachedKeys()).toEqual([])
    })

    it("keeps the node's entries for the same job and generation, and clears them on a new job or recompute", () => {
      solve("n1", "j1", frontierResult(0))
      solve("n2", "j9", frontierResult(0))
      const s = useNodeResultsStore.getState()
      const n1 = currentIdentity("n1")
      const n2 = currentIdentity("n2")
      s.recordOptimiserApply("n1", n1, applyResponse(1))
      s.recordOptimiserApply("n2", n2, applyResponse(2))

      // Re-installing the same job and generation keeps the entry.
      solve("n1", "j1", frontierResult(0))
      expect(cachedKeys()).toEqual([optimiserApplyKey(n1), optimiserApplyKey(n2)])

      // A recompute (same job, next generation) clears only that node.
      solve("n1", "j1", frontierResult(1))
      expect(cachedKeys()).toEqual([optimiserApplyKey(n2)])

      s.recordOptimiserApply("n1", currentIdentity("n1"), applyResponse(3))
      solve("n1", "j3", frontierResult(0))
      expect(cachedKeys()).toEqual([optimiserApplyKey(n2)])
    })

    it("clears a node's entries when its solve fails or its results are cleared", () => {
      solve("n1", "j1", frontierResult(0))
      solve("n2", "j2", frontierResult(0))
      const s = useNodeResultsStore.getState()
      s.recordOptimiserApply("n1", currentIdentity("n1"), applyResponse(1))
      s.recordOptimiserApply("n2", currentIdentity("n2"), applyResponse(2))

      s.startSolveJob("n1", "j3", "Optimiser", {}, "h", "live", 0)
      s.failSolveJob("n1", "boom")
      expect(cachedKeys()).toEqual([optimiserApplyKey(currentIdentity("n2"))])

      s.clearNode("n2")
      expect(cachedKeys()).toEqual([])
    })

    it("keeps at most the 16 most recently used entries", () => {
      expect(MAX_CACHED_OPTIMISER_APPLY).toBe(16)
      const pointCount = MAX_CACHED_OPTIMISER_APPLY + 1
      solve("n1", "j1", makeSolveResult({
        frontier: makeFrontier({
          points: Array.from({ length: pointCount }, (_, i) => (onlinePoint(i))),
          point_summaries: Array.from({ length: pointCount }, (_, i) => pointSummary({ total_objective: i })),
          n_points: pointCount,
          points_returned: pointCount,
        }),
      }))
      const s = useNodeResultsStore.getState()
      const identities = []
      for (let index = 0; index < MAX_CACHED_OPTIMISER_APPLY; index += 1) {
        s.selectFrontierPoint("n1", index)
        identities.push(currentIdentity("n1"))
        s.recordOptimiserApply("n1", currentIdentity("n1"), applyResponse(index))
      }
      // Reading point 0 again makes it the most recently used.
      s.touchOptimiserApply(optimiserApplyKey(identities[0]))
      s.selectFrontierPoint("n1", MAX_CACHED_OPTIMISER_APPLY)
      s.recordOptimiserApply("n1", currentIdentity("n1"), applyResponse(99))

      const keys = cachedKeys()
      expect(keys).toHaveLength(MAX_CACHED_OPTIMISER_APPLY)
      expect(keys).not.toContain(optimiserApplyKey(identities[1]))
      expect(keys).toContain(optimiserApplyKey(identities[0]))
      expect(keys.at(-1)).toBe(optimiserApplyKey(currentIdentity("n1")))
    })

    it("refuses a result whose frontier reports a different generation", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Optimiser", {}, "h", "live", 0)
      const result = frontierResult(2)
      expect(() => s.completeSolveJob("n1", { ...result, frontier: { ...result.frontier!, frontier_generation: 1 } }))
        .toThrow("Optimiser frontier generation 1 does not match its result's generation 2")
    })
  })

  describe("getOptimiserPreview", () => {
    it("returns null when no solve result exists", () => {
      expect(useNodeResultsStore.getState().getOptimiserPreview("n1")).toBeNull()
    })

    it("builds correct shape from completed result", () => {
      const s = useNodeResultsStore.getState()
      const constraints = { premium: { min: 0, max: 100 } }
      s.startSolveJob("n1", "j1", "Optim Node", constraints, "h", "live", 0)
      const result = makeSolveResult({ converged: true, iterations: 15 })
      s.completeSolveJob("n1", result)

      const preview = useNodeResultsStore.getState().getOptimiserPreview("n1")
      expect(preview).not.toBeNull()
      expect(preview!.result).toEqual(result)
      expect(preview!.solvedResult).toBe(preview!.result)
      expect(preview!.jobId).toBe("j1")
      expect(preview!.constraints).toEqual(constraints)
      expect(preview!.nodeLabel).toBe("Optim Node")
    })

    it("carries the as-solved result beside the selected point's", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Optim Node", {}, "h", "live", 0)
      const result = makeSolveResult({
        history: [makeHistoryEntry({ iteration: 1 })],
        frontier: makeFrontier({
          points: [onlinePoint(150)],
          point_summaries: [pointSummary()],
          n_points: 1,
          points_returned: 1,
        }),
      })
      s.completeSolveJob("n1", result)

      const preview = useNodeResultsStore.getState().getOptimiserPreview("n1")!
      expect(preview.selectedPointIndex).toBe(0)
      expect(preview.result?.total_objective).toBe(150)
      expect(preview.result?.history).toBeUndefined()
      expect(preview.solvedResult).toBe(result)
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Frontier actions
  // ────────────────────────────────────────────────────────────────

  describe("Frontier actions", () => {
    it("completeSolveJob selects and displays the first frontier point immediately", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", { vol: { min: 0.9 } }, "h1", "live", 0)

      const frontier = makeFrontier({
        status: "ok",
        points: [
          onlinePoint(123),
          onlinePoint(135),
        ],
        point_summaries: [
          pointSummary({ total_objective: 123, constraints: { vol: 0.95 }, lambdas: { vol: 0.01 } }),
          pointSummary({ total_objective: 135, constraints: { vol: 1.05 }, lambdas: { vol: 0.03 } }),
        ],
        n_points: 2,
        points_returned: 2,
        constraint_names: ["vol"],
        points_limit: 2000,
        points_truncated: false,
      })
      const result = makeSolveResult({
        total_objective: 100,
        constraints: { vol: 0.9 },
        baseline_constraints: { vol: 0.88 },
        lambdas: { vol: 0 },
        frontier,
      })
      s.completeSolveJob("n1", result)

      const cached = useNodeResultsStore.getState().solveResults["n1"]
      expect(cached).toBeDefined()
      expect(cached.frontier).not.toBeNull()
      expect(cached.frontier!.points).toHaveLength(2)
      expect(cached.frontier!.n_points).toBe(2)
      expect(cached.frontier!.points_returned).toBe(2)
      expect(cached.frontier!.constraint_names).toEqual(["vol"])
      expect(cached.selectedPointIndex).toBe(0)
      expect(cached.result?.total_objective).toBe(123)
      expect(cached.result?.constraints).toEqual({ vol: 0.95 })
      // Summaries carry no baseline; it stays the as-solved one.
      expect(cached.result?.baseline_constraints).toEqual({ vol: 0.88 })
      expect(cached.result?.lambdas).toEqual({ vol: 0.01 })
      expect(cached.originalResult?.total_objective).toBe(100)
    })

    it("completeSolveJob sets null frontier when points empty", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1", "live", 0)

      const frontier = makeFrontier({
        status: "ok",
        points: [],
        point_summaries: [],
        n_points: 0,
        points_returned: 0,
        constraint_names: [],
        points_limit: 2000,
        points_truncated: false,
      })
      const result = makeSolveResult({ frontier })
      s.completeSolveJob("n1", result)

      const cached = useNodeResultsStore.getState().solveResults["n1"]
      expect(cached).toBeDefined()
      expect(cached.frontier).toBeNull()
    })

    it("completeSolveJob sets null frontier when the result has none", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1", "live", 0)

      const result = makeSolveResult()
      // The server sends a null frontier when the solve has none.
      expect(result.frontier).toBeNull()
      s.completeSolveJob("n1", result)

      const cached = useNodeResultsStore.getState().solveResults["n1"]
      expect(cached).toBeDefined()
      expect(cached.frontier).toBeNull()
    })

    it("selectFrontierPoint sets index", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1", "live", 0)
      s.completeSolveJob("n1", makeSolveResult({
        frontier: makeFrontier({
          status: "ok",
          points: [onlinePoint(150)],
          point_summaries: [pointSummary({ total_objective: 150, constraints: { premium: 70 }, lambdas: { premium: 0.25 } })],
          n_points: 1,
          points_returned: 1,
          constraint_names: ["premium"],
          points_limit: 2000,
          points_truncated: false,
        }),
      }))

      s.selectFrontierPoint("n1", 0)

      const cached = useNodeResultsStore.getState().solveResults["n1"]
      expect(cached.selectedPointIndex).toBe(0)
    })

    it("selectFrontierPoint shows the server's summary for the point, not values from the frontier row", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1", "live", 0)
      const frontier = makeFrontier({
        status: "ok",
        points: [
          // The row's own values differ from the summary's: only the summary is shown.
          makeOnlineFrontierPoint(0, {
            total_objective: 150,
            totals: { premium: 70, loss: 33 },
            lambdas: { premium: 0.25, loss: 0.4 },
            converged: false,
          }),
        ],
        point_summaries: [
          pointSummary({
            total_objective: 151,
            constraints: { premium: 71, loss: 34 },
            lambdas: { premium: 0.99, loss: 0.77 },
            converged: true,
          }),
        ],
        n_points: 1,
        points_returned: 1,
        constraint_names: ["premium", "loss"],
        points_limit: 2000,
        points_truncated: false,
      })
      s.completeSolveJob("n1", makeSolveResult({
        mode: "ratebook",
        total_objective: 100,
        constraints: { premium: 50, loss: 20 },
        baseline_constraints: { premium: 45, loss: 18 },
        lambdas: { premium: 0.1, loss: 0.2 },
        n_quotes: 4321,
        frontier,
      }))

      s.selectFrontierPoint("n1", 0)

      const cached = useNodeResultsStore.getState().solveResults["n1"]
      expect(cached.selectedPointIndex).toBe(0)
      expect(cached.result?.total_objective).toBe(151)
      expect(cached.result?.constraints).toEqual({ premium: 71, loss: 34 })
      expect(cached.result?.lambdas).toEqual({ premium: 0.99, loss: 0.77 })
      expect(cached.result?.converged).toBe(true)
      // Fields the summary does not carry come from the as-solved result.
      expect(cached.result?.mode).toBe("ratebook")
      expect(cached.result?.baseline_objective).toBe(80)
      expect(cached.result?.baseline_constraints).toEqual({ premium: 45, loss: 18 })
      expect(cached.result?.n_quotes).toBe(4321)
      expect(cached.result?.baseline_objective).toBe(cached.originalResult?.baseline_objective)
    })

    it("selectFrontierPoint shows the summary's diagnostics and clears the fields it nulls", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1", "live", 0)
      const pointStats = {
        mean: 1.08,
        std: 0.03,
        min: 0.95,
        max: 1.2,
        p5: 0.99,
        p25: 1.03,
        p50: 1.07,
        p75: 1.12,
        p95: 1.18,
        pct_increase: 0.8,
        pct_decrease: 0.2,
      }
      const frontier = makeFrontier({
        status: "ok",
        points: [onlinePoint(150)],
        point_summaries: [
          pointSummary({
            iterations: 19,
            cd_iterations: 4,
            clamp_rate: 0.01,
            scenario_value_stats: pointStats,
            history: null,
            scenario_value_histogram: null,
            factor_tables: null,
            warning: null,
            frontier_error: null,
          }),
        ],
        n_points: 1,
        points_returned: 1,
        constraint_names: ["premium"],
        points_limit: 2000,
        points_truncated: false,
      })
      s.completeSolveJob("n1", makeSolveResult({
        iterations: 42,
        history: [
          makeHistoryEntry({ iteration: 1, total_objective: 100, max_lambda_change: 0.1, all_constraints_satisfied: false }),
        ],
        scenario_value_histogram: { counts: [1, 2, 3], edges: [0.9, 1, 1.1, 1.2] },
        factor_tables: { region: [{ __factor_group__: "North", optimal_scenario_value: 1.05, quote_count: 10 }] },
        warning: "base warning should not leak",
        frontier,
      }))

      s.selectFrontierPoint("n1", 0)

      const selected = useNodeResultsStore.getState().solveResults["n1"].result!
      expect(selected.iterations).toBe(19)
      expect(selected.cd_iterations).toBe(4)
      expect(selected.clamp_rate).toBe(0.01)
      expect(selected.scenario_value_stats).toEqual(pointStats)
      expect(selected.history).toBeUndefined()
      expect(selected.scenario_value_histogram).toBeUndefined()
      // A point without tables has none, as the server's result reports it.
      expect(selected.factor_tables).toEqual({})
      expect(selected.warning).toBeUndefined()
    })

    it("selectFrontierPoint uses a point-specific warning for non-converged frontier point summaries", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1", "live", 0)
      const frontier = makeFrontier({
        status: "ok",
        points: [
          onlinePoint(150),
        ],
        point_summaries: [pointSummary({ converged: false, warning: NON_CONVERGED_WARNING })],
        n_points: 1,
        points_returned: 1,
        constraint_names: ["premium"],
        points_limit: 2000,
        points_truncated: false,
      })
      s.completeSolveJob("n1", makeSolveResult({
        converged: true,
        warning: "base warning should not leak",
        frontier,
      }))

      s.selectFrontierPoint("n1", 0)

      expect(useNodeResultsStore.getState().solveResults.n1.result?.warning).toBe(NON_CONVERGED_WARNING)
    })

    it("selectFrontierPoint null deselects and reverts result", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1", "live", 0)
      const original = makeSolveResult({
        total_objective: 100,
        frontier: makeFrontier({
          status: "ok",
          points: [onlinePoint(200)],
          point_summaries: [pointSummary({ total_objective: 200, constraints: { premium: 60 }, lambdas: { premium: 0.2 } })],
          n_points: 1,
          points_returned: 1,
          constraint_names: ["premium"],
          points_limit: 2000,
          points_truncated: false,
        }),
      })
      s.completeSolveJob("n1", original)

      s.selectFrontierPoint("n1", 0)

      // The result should now reflect the frontier point
      expect(useNodeResultsStore.getState().solveResults["n1"].result?.total_objective).toBe(200)

      // Deselect — set back to null
      s.selectFrontierPoint("n1", null)
      const cached = useNodeResultsStore.getState().solveResults["n1"]
      expect(cached.selectedPointIndex).toBeNull()
      expect(cached.result?.total_objective).toBe(100)
      expect(cached.result?.constraints).toEqual(original.constraints)
      expect(cached.result?.lambdas).toEqual(original.lambdas)
      // The original result is preserved in originalResult for the caller to use
      expect(cached.originalResult?.total_objective).toBe(100)
    })

    it("effectiveConstraintBounds reads the displayed result's backend bounds, never the config", () => {
      const s = useNodeResultsStore.getState()
      // The node's configured bound (0.8) is not what either result was solved at.
      s.startSolveJob("n1", "j1", "Node 1", { premium: { min: 0.8 } }, "h1", "live", 0)
      s.completeSolveJob("n1", makeSolveResult({
        constraints: { premium: 50, loss: 20 },
        effective_bounds: {
          premium: { kind: "min", bound: 48 },
          loss: { kind: "max", bound: 25 },
        },
        lambdas: { premium: 0.1, loss: 0 },
        frontier: makeFrontier({
          points: [onlinePoint(150)],
          point_summaries: [pointSummary({
            constraints: { premium: 55, loss: 21 },
            effective_bounds: {
              premium: { kind: "min", bound: 52 },
              loss: { kind: "max", bound: 25 },
            },
            lambdas: { premium: 0.3, loss: 0 },
          })],
          n_points: 1,
          points_returned: 1,
          constraint_names: ["premium", "loss"],
          swept_axes: ["premium"],
        }),
      }))

      // A completed frontier solve displays point 0.
      const point = useNodeResultsStore.getState().getOptimiserPreview("n1")!
      expect(effectiveConstraintBounds(point.result)).toEqual({
        premium: { kind: "min", bound: 52 },
        loss: { kind: "max", bound: 25 },
      })

      s.selectFrontierPoint("n1", null)
      const solved = useNodeResultsStore.getState().getOptimiserPreview("n1")!
      expect(effectiveConstraintBounds(solved.result)).toEqual({
        premium: { kind: "min", bound: 48 },
        loss: { kind: "max", bound: 25 },
      })
    })

    it("effectiveConstraintBounds throws when a constraint has no backend bound", () => {
      expect(() => effectiveConstraintBounds(makeSolveResult({
        constraints: { premium: 50 },
        effective_bounds: {},
      }))).toThrow(/premium/)
      expect(() => effectiveConstraintBounds(makeSolveResult({
        constraints: { premium: 50 },
        effective_bounds: { premium: { kind: "min", bound: Number.NaN } },
      }))).toThrow(/premium/)
      expect(() => effectiveConstraintBounds(makeSolveResult({
        constraints: {},
        effective_bounds: { premium: { kind: "min", bound: 1 } },
      }))).toThrow(/premium/)
    })

    it("selectFrontierPoint noop for unknown node", () => {
      const s = useNodeResultsStore.getState()
      // Should not crash
      s.selectFrontierPoint("ghost", 5)
      expect(useNodeResultsStore.getState().solveResults["ghost"]).toBeUndefined()
    })

    it("updateFrontierAfterSelect updates result metrics", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1", "live", 0)
      s.completeSolveJob("n1", makeSolveResult({
        total_objective: 100,
        constraints: { premium: 50 },
        lambdas: { premium: 0.1 },
        converged: true,
      }))

      s.updateFrontierAfterSelect("n1", 2, makeFrontierSelect({
        status: "ok",
        total_objective: 250,
        constraints: { premium: 70 },
        baseline_objective: 90,
        baseline_constraints: { premium: 48 },
        lambdas: { premium: 0.3 },
        converged: false,
        error: null,
      }))

      const cached = useNodeResultsStore.getState().solveResults["n1"]
      expect(cached.selectedPointIndex).toBe(2)
      expect(cached.result?.total_objective).toBe(250)
      expect(cached.result?.constraints).toEqual({ premium: 70 })
      // The point summary carries no baseline; it stays the as-solved one.
      expect(cached.result?.baseline_objective).toBe(80)
      expect(cached.result?.baseline_constraints).toEqual({ premium: 45 })
      expect(cached.result?.lambdas).toEqual({ premium: 0.3 })
      expect(cached.result?.converged).toBe(false)
    })

    it("updateFrontierAfterSelect uses the backend non-convergence warning for select responses", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1", "live", 0)
      s.completeSolveJob("n1", makeSolveResult({
        converged: false,
        warning: "Stale original warning",
      }))

      s.updateFrontierAfterSelect("n1", 2, makeFrontierSelect({
        status: "ok",
        total_objective: 250,
        constraints: { premium: 70 },
        baseline_objective: 90,
        baseline_constraints: { premium: 48 },
        lambdas: { premium: 0.3 },
        converged: false,
        warning: NON_CONVERGED_WARNING,
        error: null,
      }))

      expect(useNodeResultsStore.getState().solveResults.n1.result?.warning).toBe(NON_CONVERGED_WARNING)
    })

    it("updateFrontierAfterSelect stores materialised ratebook factor tables on the selected point", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1", "live", 0)
      const frontier = makeFrontier({
        status: "ok",
        points: [
          onlinePoint(240),
          onlinePoint(260),
        ],
        point_summaries: [
          pointSummary({ total_objective: 240, constraints: { premium: 68 }, lambdas: { premium: 0.25 } }),
          pointSummary({ total_objective: 260, constraints: { premium: 72 }, lambdas: { premium: 0.32 } }),
        ],
        n_points: 2,
        points_returned: 2,
        constraint_names: ["premium"],
        points_limit: 2000,
        points_truncated: false,
      })
      const factorTables = {
        region: [{ __factor_group__: "North", optimal_scenario_value: 1.08, quote_count: 10 }],
      }
      s.completeSolveJob("n1", makeSolveResult({ mode: "ratebook", frontier }))

      s.updateFrontierAfterSelect("n1", 0, makeFrontierSelect({
        status: "ok",
        total_objective: 250,
        constraints: { premium: 70 },
        baseline_objective: 90,
        baseline_constraints: { premium: 48 },
        lambdas: { premium: 0.3 },
        converged: true,
        cd_iterations: 5,
        clamp_rate: 0.04,
        factor_tables: factorTables,
        error: null,
      }))

      let cached = useNodeResultsStore.getState().solveResults.n1
      expect(cached.result?.factor_tables).toEqual(factorTables)
      expect(cached.result?.cd_iterations).toBe(5)
      expect(cached.result?.clamp_rate).toBe(0.04)

      // Move to another point, then back: point 0's tables come from its
      // stored summary with no further select response.
      s.selectFrontierPoint("n1", 1)
      cached = useNodeResultsStore.getState().solveResults.n1
      expect(cached.result?.factor_tables).toEqual({})
      expect(cached.result?.total_objective).toBe(260)
      s.selectFrontierPoint("n1", 0)
      cached = useNodeResultsStore.getState().solveResults.n1
      expect(cached.frontier!.point_summaries[0].factor_tables).toEqual(factorTables)
      expect(cached.result?.total_objective).toBe(250)
      expect(cached.result?.factor_tables).toEqual(factorTables)
      expect(cached.result?.cd_iterations).toBe(5)
      expect(cached.result?.clamp_rate).toBe(0.04)
    })

    it("updateFrontierAfterSelect enriches only the selected frontier point with optional detail", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1", "live", 0)
      const secondSummary = pointSummary({ total_objective: 260, constraints: { premium: 72 }, lambdas: { premium: 0.32 } })
      const frontier = makeFrontier({
        status: "ok",
        points: [
          onlinePoint(240),
          onlinePoint(260),
        ],
        point_summaries: [
          pointSummary({ total_objective: 240, constraints: { premium: 68 }, lambdas: { premium: 0.28 } }),
          secondSummary,
        ],
        n_points: 2,
        points_returned: 2,
        constraint_names: ["premium"],
        points_limit: 2000,
        points_truncated: false,
      })
      const history = [
        makeHistoryEntry({ iteration: 1, total_objective: 250, max_lambda_change: 0.04, all_constraints_satisfied: true }),
      ]
      const scenario_value_stats = {
        mean: 1.08,
        std: 0.03,
        min: 0.95,
        max: 1.2,
        p5: 0.99,
        p25: 1.03,
        p50: 1.07,
        p75: 1.12,
        p95: 1.18,
        pct_increase: 0.8,
        pct_decrease: 0.2,
      }
      const scenario_value_histogram = { counts: [4, 5], edges: [0.95, 1.05, 1.2] }
      const factor_tables = {
        region: [{ __factor_group__: "North", optimal_scenario_value: 1.08, quote_count: 10 }],
      }
      s.completeSolveJob("n1", makeSolveResult({ mode: "ratebook", frontier }))

      s.updateFrontierAfterSelect("n1", 0, makeFrontierSelect({
        status: "ok",
        total_objective: 250,
        constraints: { premium: 70 },
        baseline_objective: 90,
        baseline_constraints: { premium: 48 },
        lambdas: { premium: 0.3 },
        converged: true,
        iterations: 18,
        cd_iterations: 5,
        clamp_rate: 0.04,
        history,
        scenario_value_stats,
        scenario_value_histogram,
        factor_tables,
        error: null,
      }))

      const cached = useNodeResultsStore.getState().solveResults.n1
      expect(cached.frontier!.point_summaries[0]).toEqual(pointSummary({
        total_objective: 250,
        constraints: { premium: 70 },
        lambdas: { premium: 0.3 },
        iterations: 18,
        cd_iterations: 5,
        clamp_rate: 0.04,
        history,
        scenario_value_stats,
        scenario_value_histogram,
        factor_tables,
      }))
      expect(cached.frontier!.point_summaries[1]).toBe(secondSummary)
      expect(cached.result?.iterations).toBe(18)
      expect(cached.result?.history).toEqual(history)
      expect(cached.result?.scenario_value_stats).toEqual(scenario_value_stats)
      expect(cached.result?.scenario_value_histogram).toEqual(scenario_value_histogram)
      expect(cached.result?.factor_tables).toEqual(factor_tables)
    })

    it("updateFrontierAfterSelect drops a response when the user has moved to a different point", () => {
      // Race: the user clicks point 0, the request fires, then they click
      // point 1 (which lands first).  When point 0's response finally arrives,
      // it must not overwrite the now-current point-1 state.  The stale
      // response is still allowed to enrich point-0's stored summary in the
      // frontier so re-selecting point 0 later benefits from the data.
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1", "live", 0)
      const frontier = makeFrontier({
        status: "ok",
        points: [
          onlinePoint(240),
          onlinePoint(260),
        ],
        point_summaries: [
          pointSummary({ total_objective: 240, constraints: { premium: 68 }, lambdas: { premium: 0.28 } }),
          pointSummary({ total_objective: 260, constraints: { premium: 72 }, lambdas: { premium: 0.32 } }),
        ],
        n_points: 2,
        points_returned: 2,
        constraint_names: ["premium"],
        points_limit: 2000,
        points_truncated: false,
      })
      s.completeSolveJob("n1", makeSolveResult({ frontier }))
      // Mirror the OptimiserPreview flow: the click sets selectedPointIndex
      // synchronously, then the fetch fires.  Click point 0 first…
      s.selectFrontierPoint("n1", 0)
      // …then the user clicks point 1 before point 0's response lands.
      s.selectFrontierPoint("n1", 1)
      s.updateFrontierAfterSelect("n1", 1, makeFrontierSelect({
        status: "ok",
        point_index: 1,
        total_objective: 260,
        constraints: { premium: 72 },
        baseline_objective: 90,
        baseline_constraints: { premium: 48 },
        lambdas: { premium: 0.32 },
        converged: true,
        error: null,
      }))
      const pointOneSummary = useNodeResultsStore.getState().solveResults["n1"].frontier!.point_summaries[1]

      // The late response from point 0 arrives.  Frontend must not regress
      // selectedPointIndex/result back to point 0.
      s.updateFrontierAfterSelect("n1", 0, makeFrontierSelect({
        status: "ok",
        point_index: 0,
        total_objective: 240,
        constraints: { premium: 68 },
        baseline_objective: 90,
        baseline_constraints: { premium: 48 },
        lambdas: { premium: 0.28 },
        converged: true,
        iterations: 11,
        history: [
          makeHistoryEntry({ iteration: 1, total_objective: 240, max_lambda_change: 0.02, all_constraints_satisfied: true }),
        ],
        error: null,
      }))

      const cached = useNodeResultsStore.getState().solveResults["n1"]
      expect(cached.selectedPointIndex).toBe(1)
      expect(cached.result?.total_objective).toBe(260)
      expect(cached.result?.constraints).toEqual({ premium: 72 })
      // Point 0's summary still takes the late response so a subsequent
      // select reuses it.
      expect(cached.frontier!.point_summaries[0]).toEqual(expect.objectContaining({ iterations: 11 }))
      // Point 1's summary stays untouched by the late response.
      expect(cached.frontier!.point_summaries[1]).toBe(pointOneSummary)
      expect(cached.frontier!.point_summaries[1].iterations).toBeNull()
    })

    it("updateFrontierAfterSelect rejects responses whose point_index does not match the requested index", () => {
      // The backend response carries the canonical point_index.  If it ever
      // disagrees with the index the store was asked to update, that is a
      // contract violation — surface it loudly rather than mutating state.
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1", "live", 0)
      s.completeSolveJob("n1", makeSolveResult({
        frontier: makeFrontier({
          status: "ok",
          points: [
            onlinePoint(240),
            onlinePoint(260),
          ],
          point_summaries: [
            pointSummary({ total_objective: 240, constraints: { premium: 68 }, lambdas: { premium: 0.28 } }),
            pointSummary({ total_objective: 260, constraints: { premium: 72 }, lambdas: { premium: 0.32 } }),
          ],
          n_points: 2,
          points_returned: 2,
          constraint_names: ["premium"],
          points_limit: 2000,
          points_truncated: false,
        }),
      }))

      expect(() =>
        s.updateFrontierAfterSelect("n1", 0, makeFrontierSelect({
          status: "ok",
          point_index: 1,
          total_objective: 260,
          constraints: { premium: 72 },
          baseline_objective: 90,
          baseline_constraints: { premium: 48 },
          lambdas: { premium: 0.32 },
          converged: true,
          error: null,
        })),
      ).toThrow(/point_index/)
    })

    it("updateFrontierAfterSelect clears point diagnostics that are not in the selected point", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1", "live", 0)
      const original = makeSolveResult({
        iterations: 42,
        cd_iterations: 9,
        clamp_rate: 0.02,
        n_quotes: 5000,
        history: [
          makeHistoryEntry({ iteration: 1, total_objective: 100, max_lambda_change: 0.1, all_constraints_satisfied: false }),
        ],
        scenario_value_stats: {
          mean: 1,
          std: 0.02,
          min: 0.9,
          max: 1.1,
          p5: 0.94,
          p25: 0.98,
          p50: 1,
          p75: 1.04,
          p95: 1.08,
          pct_increase: 0.55,
          pct_decrease: 0.45,
        },
        scenario_value_histogram: { counts: [1, 2], edges: [0.9, 1, 1.1] },
        factor_tables: { region: [{ __factor_group__: "North", optimal_scenario_value: 1.05, quote_count: 10 }] },
      })
      s.completeSolveJob("n1", original)

      s.updateFrontierAfterSelect("n1", 1, makeFrontierSelect({
        status: "ok",
        total_objective: 999,
        constraints: { premium: 99 },
        baseline_objective: 88,
        baseline_constraints: { premium: 44 },
        lambdas: { premium: 0.9 },
        converged: true,
        error: null,
      }))

      const cached = useNodeResultsStore.getState().solveResults["n1"]
      expect(cached.result?.total_objective).toBe(999)
      expect(cached.result?.n_quotes).toBe(5000)
      expect(cached.result?.iterations).toBeUndefined()
      expect(cached.result?.cd_iterations).toBeUndefined()
      expect(cached.result?.clamp_rate).toBeUndefined()
      expect(cached.result?.history).toBeUndefined()
      expect(cached.result?.scenario_value_stats).toBeUndefined()
      expect(cached.result?.scenario_value_histogram).toBeUndefined()
      // The select response always carries its point's factor tables, empty here.
      expect(cached.result?.factor_tables).toEqual({})
    })

    // ────────────────────────────────────────────────────────────
    // getModellingPreview — error-status filtering
    // Catches: if the error filter is removed, the panel would try
    // to render charts from a failed training result with missing
    // fields (feature_importance, metrics, etc.), causing a crash.
    // ────────────────────────────────────────────────────────────

    it("getModellingPreview returns null when train result has error status", () => {
      const s = useNodeResultsStore.getState()
      s.startTrainJob("t1", "tj-1", "Model Node", "cfg-h", "live", 0)
      s.completeTrainJob("t1", makeTrainResult({ status: "error", error: "OOM" }))

      const preview = useNodeResultsStore.getState().getModellingPreview("t1")
      expect(preview).toBeNull()
    })

    it("getModellingPreview returns result when status is 'completed'", () => {
      const s = useNodeResultsStore.getState()
      s.startTrainJob("t1", "tj-1", "Model Node", "cfg-h", "live", 0)
      s.completeTrainJob("t1", makeTrainResult({ status: "completed" }))

      const preview = useNodeResultsStore.getState().getModellingPreview("t1")
      expect(preview).not.toBeNull()
      expect(preview!.result.status).toBe("completed")
      expect(preview!.jobId).toBe("tj-1")
      expect(preview!.configHash).toBe("cfg-h")
    })

    it("getModellingPreview returns null when no train result exists", () => {
      expect(useNodeResultsStore.getState().getModellingPreview("ghost")).toBeNull()
    })

    it("getModellingPreview uses active job nodeLabel if still running", () => {
      const s = useNodeResultsStore.getState()
      // Start a job, complete it, then start another job for the same node
      s.startTrainJob("t1", "tj-1", "First Label", "h1", "live", 0)
      s.completeTrainJob("t1", makeTrainResult())
      // Start a new job (different config) — old result still cached
      s.startTrainJob("t1", "tj-2", "Updated Label", "h2", "live", 0)

      const preview = useNodeResultsStore.getState().getModellingPreview("t1")
      expect(preview).not.toBeNull()
      // nodeLabel should come from the active job, not the cached result
      expect(preview!.nodeLabel).toBe("Updated Label")
    })

    it("getModellingPreview falls back to 'Model' when no active job", () => {
      const s = useNodeResultsStore.getState()
      // Complete directly without a job (direct completion path)
      s.completeTrainJob("t1", makeTrainResult())

      const preview = useNodeResultsStore.getState().getModellingPreview("t1")
      expect(preview).not.toBeNull()
      expect(preview!.nodeLabel).toBe("Model")
    })

    // ────────────────────────────────────────────────────────────
    // Source-keyed columns
    // Catches: if the source key separator changes or source
    // parameter is ignored, columns from different sources would
    // overwrite each other, showing stale schema in the panel.
    // ────────────────────────────────────────────────────────────

    it("setColumns with source key isolates columns per source", () => {
      const s = useNodeResultsStore.getState()
      const liveColumns = [{ name: "premium", dtype: "float64" }]
      const stagingColumns = [{ name: "premium", dtype: "float64" }, { name: "discount", dtype: "float64" }]

      s.setColumns("src-1", liveColumns, 0, "live")
      s.setColumns("src-1", stagingColumns, 0, "staging")

      const liveResult = useNodeResultsStore.getState().getColumns("src-1", "live")
      const stagingResult = useNodeResultsStore.getState().getColumns("src-1", "staging")

      expect(liveResult!.columns).toEqual(liveColumns)
      expect(stagingResult!.columns).toEqual(stagingColumns)
      expect(liveResult!.columns).not.toEqual(stagingResult!.columns)
    })

    it("getColumns without source returns bare nodeId entry", () => {
      const s = useNodeResultsStore.getState()
      const cols = [{ name: "x", dtype: "int64" }]
      s.setColumns("src-1", cols, 0)

      // Bare key should work
      expect(useNodeResultsStore.getState().getColumns("src-1")!.columns).toEqual(cols)
      // Source-keyed lookup should not find it
      expect(useNodeResultsStore.getState().getColumns("src-1", "live")).toBeNull()
    })

    it("source-keyed columns become stale when structuralVersion changes", () => {
      const s = useNodeResultsStore.getState()
      s.setColumns("src-1", [{ name: "a", dtype: "float64" }], 0, "staging")
      useGraphStore.setState({ structuralVersion: 1 })

      const result = useNodeResultsStore.getState().getColumns("src-1", "staging")
      expect(result).not.toBeNull()
      expect(result!.fresh).toBe(false)
    })

    // ────────────────────────────────────────────────────────────
    // Concurrent solve/train for same nodeId
    // Catches: if a user kicks off an optimiser solve and a training
    // run on the same node, one should not clobber the other.
    // ────────────────────────────────────────────────────────────

    it("concurrent solve and train jobs on the same nodeId are independent", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "sj-1", "Node 1", { c: { min: 0 } }, "sh1", "live", 0)
      s.startTrainJob("n1", "tj-1", "Node 1", "th1", "live", 0)

      // Both should exist
      expect(useNodeResultsStore.getState().solveJobs["n1"]).toBeDefined()
      expect(useNodeResultsStore.getState().trainJobs["n1"]).toBeDefined()

      // Complete solve — train should still be running
      s.completeSolveJob("n1", makeSolveResult())
      expect(useNodeResultsStore.getState().solveResults["n1"]).toBeDefined()
      expect(useNodeResultsStore.getState().solveJobs["n1"]).toBeUndefined()
      expect(useNodeResultsStore.getState().trainJobs["n1"]).toBeDefined()

      // Complete train — solve result should still be there
      s.completeTrainJob("n1", makeTrainResult())
      expect(useNodeResultsStore.getState().trainResults["n1"]).toBeDefined()
      expect(useNodeResultsStore.getState().solveResults["n1"]).toBeDefined()
    })

    it("failing solve does not affect concurrent train job", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "sj-1", "Node 1", {}, "sh1", "live", 0)
      s.startTrainJob("n1", "tj-1", "Node 1", "th1", "live", 0)

      s.failSolveJob("n1", "Solver diverged")

      // Solve job is removed, but train job is untouched
      expect(useNodeResultsStore.getState().solveJobs["n1"]).toBeUndefined()
      expect(useNodeResultsStore.getState().trainJobs["n1"]).toBeDefined()
      expect(useNodeResultsStore.getState().trainJobs["n1"].progress).toBeNull()
    })

    it("multiple solve jobs on different nodes simultaneously", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "sj-1", "Node 1", { c: { min: 0 } }, "h1", "live", 0)
      s.startSolveJob("n2", "sj-2", "Node 2", { d: { max: 1 } }, "h2", "live", 0)

      expect(useNodeResultsStore.getState().solveJobs["n1"]).toBeDefined()
      expect(useNodeResultsStore.getState().solveJobs["n2"]).toBeDefined()
      expect(useNodeResultsStore.getState().solveJobs["n1"].jobId).toBe("sj-1")
      expect(useNodeResultsStore.getState().solveJobs["n2"].jobId).toBe("sj-2")

      s.updateSolveProgress("n1", { status: "running", progress: 0.5, message: "n1 halfway", elapsed_seconds: 2 })
      s.updateSolveProgress("n2", { status: "running", progress: 0.8, message: "n2 almost", elapsed_seconds: 4 })

      expect(useNodeResultsStore.getState().solveJobs["n1"].progress!.progress).toBe(0.5)
      expect(useNodeResultsStore.getState().solveJobs["n2"].progress!.progress).toBe(0.8)

      s.completeSolveJob("n1", makeSolveResult({ total_objective: 100 }))
      expect(useNodeResultsStore.getState().solveJobs["n1"]).toBeUndefined()
      expect(useNodeResultsStore.getState().solveResults["n1"].result?.total_objective).toBe(100)
      expect(useNodeResultsStore.getState().solveJobs["n2"]).toBeDefined()

      s.completeSolveJob("n2", makeSolveResult({ total_objective: 200 }))
      expect(useNodeResultsStore.getState().solveJobs["n2"]).toBeUndefined()
      expect(useNodeResultsStore.getState().solveResults["n2"].result?.total_objective).toBe(200)
      expect(useNodeResultsStore.getState().solveResults["n1"].result?.total_objective).toBe(100)
    })

    it("failing one solve does not affect other nodes' solve jobs", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "sj-1", "Node 1", {}, "h1", "live", 0)
      s.startSolveJob("n2", "sj-2", "Node 2", {}, "h2", "live", 0)

      s.failSolveJob("n1", "Diverged")

      expect(useNodeResultsStore.getState().solveJobs["n1"]).toBeUndefined()
      expect(useNodeResultsStore.getState().solveResults["n1"].error).toBe("Diverged")
      expect(useNodeResultsStore.getState().solveJobs["n2"]).toBeDefined()
      expect(useNodeResultsStore.getState().solveJobs["n2"].jobId).toBe("sj-2")
    })

    it("getOptimiserPreview includes frontier and selectedPointIndex", () => {
      const s = useNodeResultsStore.getState()
      const constraints = { vol: { min: 0.9 } }
      s.startSolveJob("n1", "j1", "Optim Node", constraints, "h1", "live", 0)

      const frontier = makeFrontier({
        status: "ok",
        points: [
          onlinePoint(100),
          onlinePoint(110),
        ],
        point_summaries: [
          pointSummary({ total_objective: 100, constraints: { vol: 0.95 }, lambdas: { vol: 0.01 } }),
          pointSummary({ total_objective: 110, constraints: { vol: 0.92 }, lambdas: { vol: 0.02 } }),
        ],
        n_points: 2,
        points_returned: 2,
        constraint_names: ["vol"],
        points_limit: 2000,
        points_truncated: false,
      })
      s.completeSolveJob("n1", makeSolveResult({ frontier }))
      s.selectFrontierPoint("n1", 1)

      const preview = useNodeResultsStore.getState().getOptimiserPreview("n1")
      expect(preview).not.toBeNull()
      expect(preview!.frontier).not.toBeNull()
      expect(preview!.frontier!.points).toHaveLength(2)
      expect(preview!.frontier!.n_points).toBe(2)
      expect(preview!.frontier!.constraint_names).toEqual(["vol"])
      expect(preview!.selectedPointIndex).toBe(1)
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Issue #13: Derived getter caching
  // ────────────────────────────────────────────────────────────────

  describe("derived getter caching (Issue #13)", () => {
    it("resetNodeResultsDerivedCaches clears module-scope derived caches and recency state", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Label", {}, "h1", "live", 0)
      s.completeSolveJob("n1", makeSolveResult())
      const stalePreview = s.getOptimiserPreview("n1")

      resetNodeResultsDerivedCaches()
      useNodeResultsStore.setState({
        solveResults: {
          n1: {
            result: makeSolveResult({ total_objective: 999 }),
            originalResult: makeSolveResult({ total_objective: 999 }),
            jobId: "j2",
            configHash: "h2",
            source: "live",
            structuralVersion: 0,
            constraints: {},
            nodeLabel: "Fresh Label",
            frontier: null,
            selectedPointIndex: null,
          },
        },
      })

      const freshPreview = useNodeResultsStore.getState().getOptimiserPreview("n1")
      expect(freshPreview).not.toBe(stalePreview)
      expect(freshPreview?.result?.total_objective).toBe(999)
      expect(freshPreview?.nodeLabel).toBe("Fresh Label")
    })

    it("getOptimiserPreview returns same reference on repeated calls", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Label", {}, "h1", "live", 0)
      s.completeSolveJob("n1", makeSolveResult())

      const a = useNodeResultsStore.getState().getOptimiserPreview("n1")
      const b = useNodeResultsStore.getState().getOptimiserPreview("n1")
      expect(a).toBe(b) // same reference, not just deep-equal
    })

    it("getOptimiserPreview returns new reference after state change", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Label", {}, "h1", "live", 0)
      s.completeSolveJob("n1", makeSolveResult())

      const a = useNodeResultsStore.getState().getOptimiserPreview("n1")
      // Trigger a state change on the same node
      useNodeResultsStore.getState().selectFrontierPoint("n1", null)
      const b = useNodeResultsStore.getState().getOptimiserPreview("n1")
      expect(a).not.toBe(b)
    })

    it("getOptimiserPreview returns null after clearNode", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Label", {}, "h1", "live", 0)
      s.completeSolveJob("n1", makeSolveResult())

      expect(useNodeResultsStore.getState().getOptimiserPreview("n1")).not.toBeNull()
      useNodeResultsStore.getState().clearNode("n1")
      expect(useNodeResultsStore.getState().getOptimiserPreview("n1")).toBeNull()
    })

    it("getModellingPreview returns same reference on repeated calls", () => {
      const s = useNodeResultsStore.getState()
      s.startTrainJob("n1", "j1", "Model", "h1", "live", 0)
      s.completeTrainJob("n1", makeTrainResult())

      const a = useNodeResultsStore.getState().getModellingPreview("n1")
      const b = useNodeResultsStore.getState().getModellingPreview("n1")
      expect(a).toBe(b)
    })

    it("getModellingPreview returns new reference after state change", () => {
      const s = useNodeResultsStore.getState()
      s.startTrainJob("n1", "j1", "Model", "h1", "live", 0)
      s.completeTrainJob("n1", makeTrainResult())

      const a = useNodeResultsStore.getState().getModellingPreview("n1")
      // Overwrite with a new result
      s.completeTrainJob("n1", makeTrainResult({ status: "completed", final_test_metrics: { rmse: 0.01 } }))
      const b = useNodeResultsStore.getState().getModellingPreview("n1")
      expect(a).not.toBe(b)
    })

    it("getModellingPreview keeps its reference across active job progress updates", () => {
      const s = useNodeResultsStore.getState()
      s.startTrainJob("n1", "j1", "Initial Label", "h1", "live", 0)
      s.completeTrainJob("n1", makeTrainResult())
      s.startTrainJob("n1", "j2", "Updated Label", "h2", "live", 0)

      const beforeProgress = useNodeResultsStore.getState().getModellingPreview("n1")
      expect(beforeProgress!.nodeLabel).toBe("Updated Label")

      s.updateTrainProgress("n1", {
        status: "running",
        progress: 0.25,
        message: "training",
        iteration: 1,
        total_iterations: 4,
        train_loss: { rmse: 0.4 },
        elapsed_seconds: 2,
      })

      const afterProgressA = useNodeResultsStore.getState().getModellingPreview("n1")
      const afterProgressB = useNodeResultsStore.getState().getModellingPreview("n1")
      expect(afterProgressA).toBe(beforeProgress)
      expect(afterProgressB).toBe(afterProgressA)
    })

    it("getModellingPreview returns null for error results", () => {
      const s = useNodeResultsStore.getState()
      s.startTrainJob("n1", "j1", "Model", "h1", "live", 0)
      s.failTrainJob("n1", "boom")

      expect(useNodeResultsStore.getState().getModellingPreview("n1")).toBeNull()
    })
  })
})
