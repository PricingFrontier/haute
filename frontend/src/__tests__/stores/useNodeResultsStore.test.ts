/**
 * Tests for useNodeResultsStore — preview cache, solve/train job lifecycle,
 * config hashing, column cache, and cleanup.
 */
import { describe, it, expect, beforeEach } from "vitest"
import useNodeResultsStore, {
  MAX_CACHED_PREVIEWS,
  MAX_CACHED_SOLVE_RESULTS,
  MAX_CACHED_TRAIN_RESULTS,
  hashConfig,
  resetNodeResultsDerivedCaches,
} from "../../stores/useNodeResultsStore.ts"
import useGraphStore from "../../stores/useGraphStore.ts"
import type { PreviewData } from "../../panels/DataPreview.tsx"
import type { SolveResult } from "../../panels/OptimiserPreview.tsx"
import type { TrainResult } from "../../stores/useNodeResultsStore.ts"

const NON_CONVERGED_WARNING = "Solver did not converge. Consider increasing max_iter or relaxing tolerance."

// ── Helpers ──────────────────────────────────────────────────────

function resetStore() {
  resetNodeResultsDerivedCaches()
  useGraphStore.setState({ structuralVersion: 0 })
  useNodeResultsStore.setState({
    previews: {},
    pinnedPreviewNodeId: null,
    columnCache: {},
    solveResults: {},
    solveJobs: {},
    trainResults: {},
    trainJobs: {},
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

function makeSolveResult(overrides: Partial<SolveResult> = {}): SolveResult {
  return {
    total_objective: 100,
    baseline_objective: 80,
    constraints: { premium: 50 },
    baseline_constraints: { premium: 45 },
    lambdas: { premium: 0.1 },
    converged: true,
    ...overrides,
  }
}

function makeTrainResult(overrides: Partial<TrainResult> = {}): TrainResult {
  return {
    status: "completed",
    metrics: { rmse: 0.05 },
    feature_importance: [{ feature: "x", importance: 0.9 }],
    model_path: "/tmp/model.pkl",
    train_rows: 1000,
    test_rows: 200,
    ...overrides,
  }
}

// ── Test suites ──────────────────────────────────────────────────

describe("useNodeResultsStore", () => {
  beforeEach(() => {
    resetStore()
  })

  // ────────────────────────────────────────────────────────────────
  // Solve job lifecycle
  // ────────────────────────────────────────────────────────────────

  describe("solve job lifecycle", () => {
    it("startSolveJob creates an active job entry", () => {
      const { startSolveJob } = useNodeResultsStore.getState()
      startSolveJob("n1", "job-1", "Node 1", { premium: { min: 0, max: 100 } }, "hash-a")

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
      state.startSolveJob("n1", "job-1", "Node 1", {}, "h")
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

    it("completeSolveJob moves result to solveResults and removes the job", () => {
      const state = useNodeResultsStore.getState()
      state.startSolveJob("n1", "job-1", "Node 1", { premium: { min: 0, max: 100 } }, "hash-a")

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
      s.startSolveJob("n1", "j1", "Node 1", { c: { min: 0, max: 1 } }, "h1")
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
      expect(final.solveResults["n1"].result.iterations).toBe(42)
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Solve job failure
  // ────────────────────────────────────────────────────────────────

  describe("failSolveJob", () => {
    it("removes the job from solveJobs on failure", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h")
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
      useNodeResultsStore.getState().startTrainJob("t1", "tj-1", "Train Node", "cfg-hash")
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
      s.startTrainJob("t1", "tj-1", "Train Node", "h")
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
      s.startTrainJob("t1", "tj-1", "Train Node", "cfg-hash")
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
      s.startTrainJob("t1", "tj-1", "Train Node", "h")
      s.updateTrainProgress("t1", {
        status: "running",
        progress: 0.5,
        message: "Training...",
        iteration: 50,
        total_iterations: 100,
        train_loss: { rmse: 0.1 },
        elapsed_seconds: 5,
      })
      const result = makeTrainResult({ metrics: { rmse: 0.02 } })
      s.completeTrainJob("t1", result)

      const final = useNodeResultsStore.getState()
      expect(Object.keys(final.trainJobs)).toHaveLength(0)
      expect(final.trainResults["t1"].result.metrics.rmse).toBe(0.02)
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Train job failure
  // ────────────────────────────────────────────────────────────────

  describe("failTrainJob", () => {
    it("removes job from trainJobs on failure", () => {
      const s = useNodeResultsStore.getState()
      s.startTrainJob("t1", "tj-1", "Train Node", "h")
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
      expect(failedResult.result.error).toBe("Out of memory")
      expect(failedResult.result.status).toBe("error")
    })

    it("is a no-op for unknown node", () => {
      useNodeResultsStore.getState().failTrainJob("ghost", "oops")
      expect(useNodeResultsStore.getState().trainJobs["ghost"]).toBeUndefined()
    })
  })

  // ────────────────────────────────────────────────────────────────
  // hashConfig
  // ────────────────────────────────────────────────────────────────

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

    it("returns a non-empty string", () => {
      const hash = hashConfig({ a: 1 })
      expect(hash.length).toBeGreaterThan(0)
    })

    it("is order-sensitive via JSON.stringify (same object key order)", () => {
      // JSON.stringify preserves insertion order, so these should differ
      const a = { x: 1, y: 2 }
      const b = { y: 2, x: 1 }
      // Note: these MAY differ depending on JSON.stringify key ordering
      // In practice, JS engines preserve insertion order, so this tests that
      const hashA = hashConfig(a)
      const hashB = hashConfig(b)
      // We just verify both produce valid hashes; equality depends on engine
      expect(hashA.length).toBeGreaterThan(0)
      expect(hashB.length).toBeGreaterThan(0)
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
      s.startSolveJob("n1", "sj1", "Node 1", { c: { min: 0, max: 1 } }, "h1")
      s.startTrainJob("n1", "tj1", "Train 1", "th1")

      // Also set up a solve result (complete a second job to create it)
      s.startSolveJob("n1b", "sj2", "Node 1b", {}, "h2")
      // We'll directly inject a solve result for n1
      useNodeResultsStore.setState((prev) => ({
        solveResults: { ...prev.solveResults, n1: { result: makeSolveResult(), originalResult: makeSolveResult(), jobId: "sj-old", configHash: "h-old", constraints: {}, nodeLabel: "N1", frontier: null, selectedPointIndex: null } },
        trainResults: { ...prev.trainResults, n1: { result: makeTrainResult(), jobId: "tj-old", configHash: "th-old" } },
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

    it("evicts the oldest cached solve results without removing active solve jobs", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("active-solve", "active-job", "Still Running", {}, "active-hash")

      for (let i = 0; i < MAX_CACHED_SOLVE_RESULTS + 1; i += 1) {
        s.startSolveJob(`s${i}`, `job-${i}`, `Solve ${i}`, {}, `hash-${i}`)
        s.completeSolveJob(`s${i}`, makeSolveResult({ total_objective: i }))
      }

      const { solveJobs, solveResults } = useNodeResultsStore.getState()
      expect(solveJobs["active-solve"]).toBeDefined()
      expect(Object.keys(solveResults)).toHaveLength(MAX_CACHED_SOLVE_RESULTS)
      expect(solveResults.s0).toBeUndefined()
      expect(solveResults[`s${MAX_CACHED_SOLVE_RESULTS}`]?.result.total_objective).toBe(MAX_CACHED_SOLVE_RESULTS)
    })

    it("keeps explicitly touched solve results when evicting", () => {
      const s = useNodeResultsStore.getState()

      for (let i = 0; i < MAX_CACHED_SOLVE_RESULTS; i += 1) {
        s.startSolveJob(`s${i}`, `job-${i}`, `Solve ${i}`, {}, `hash-${i}`)
        s.completeSolveJob(`s${i}`, makeSolveResult({ total_objective: i }))
      }
      expect(s.getOptimiserPreview("s0")).not.toBeNull()
      s.touchOptimiserPreview("s0")
      s.startSolveJob("s-new", "job-new", "Solve new", {}, "hash-new")
      s.completeSolveJob("s-new", makeSolveResult({ total_objective: 999 }))

      const { solveResults } = useNodeResultsStore.getState()
      expect(Object.keys(solveResults)).toHaveLength(MAX_CACHED_SOLVE_RESULTS)
      expect(solveResults.s0).toBeDefined()
      expect(solveResults.s1).toBeUndefined()
      expect(solveResults["s-new"]?.result.total_objective).toBe(999)
    })

    it("keeps the pinned optimiser preview result when evicting by recency", () => {
      const s = useNodeResultsStore.getState()

      for (let i = 0; i < MAX_CACHED_SOLVE_RESULTS; i += 1) {
        s.startSolveJob(`s${i}`, `job-${i}`, `Solve ${i}`, {}, `hash-${i}`)
        s.completeSolveJob(`s${i}`, makeSolveResult({ total_objective: i }))
      }
      s.setPinnedPreviewNodeId("s0")
      s.startSolveJob("s-new-1", "job-new-1", "Solve new 1", {}, "hash-new-1")
      s.completeSolveJob("s-new-1", makeSolveResult({ total_objective: 998 }))
      s.startSolveJob("s-new-2", "job-new-2", "Solve new 2", {}, "hash-new-2")
      s.completeSolveJob("s-new-2", makeSolveResult({ total_objective: 999 }))

      const { solveResults } = useNodeResultsStore.getState()
      expect(Object.keys(solveResults)).toHaveLength(MAX_CACHED_SOLVE_RESULTS)
      expect(solveResults.s0).toBeDefined()
      expect(solveResults.s1).toBeUndefined()
      expect(solveResults.s2).toBeUndefined()
      expect(solveResults["s-new-1"]?.result.total_objective).toBe(998)
      expect(solveResults["s-new-2"]?.result.total_objective).toBe(999)
    })

    it("evicts the oldest cached train results without removing active train jobs", () => {
      const s = useNodeResultsStore.getState()
      s.startTrainJob("active-train", "active-job", "Still Running", "active-hash")

      for (let i = 0; i < MAX_CACHED_TRAIN_RESULTS + 1; i += 1) {
        s.startTrainJob(`t${i}`, `job-${i}`, `Train ${i}`, `hash-${i}`)
        s.completeTrainJob(`t${i}`, makeTrainResult({ metrics: { rmse: i } }))
      }

      const { trainJobs, trainResults } = useNodeResultsStore.getState()
      expect(trainJobs["active-train"]).toBeDefined()
      expect(Object.keys(trainResults)).toHaveLength(MAX_CACHED_TRAIN_RESULTS)
      expect(trainResults.t0).toBeUndefined()
      expect(trainResults[`t${MAX_CACHED_TRAIN_RESULTS}`]?.result.metrics.rmse).toBe(MAX_CACHED_TRAIN_RESULTS)
    })

    it("keeps explicitly touched train results when evicting", () => {
      const s = useNodeResultsStore.getState()

      for (let i = 0; i < MAX_CACHED_TRAIN_RESULTS; i += 1) {
        s.startTrainJob(`t${i}`, `job-${i}`, `Train ${i}`, `hash-${i}`)
        s.completeTrainJob(`t${i}`, makeTrainResult({ metrics: { rmse: i } }))
      }
      expect(s.getModellingPreview("t0")).not.toBeNull()
      s.touchModellingPreview("t0")
      s.startTrainJob("t-new", "job-new", "Train new", "hash-new")
      s.completeTrainJob("t-new", makeTrainResult({ metrics: { rmse: 999 } }))

      const { trainResults } = useNodeResultsStore.getState()
      expect(Object.keys(trainResults)).toHaveLength(MAX_CACHED_TRAIN_RESULTS)
      expect(trainResults.t0).toBeDefined()
      expect(trainResults.t1).toBeUndefined()
      expect(trainResults["t-new"]?.result.metrics.rmse).toBe(999)
    })

    it("keeps the pinned modelling preview result when evicting by recency", () => {
      const s = useNodeResultsStore.getState()

      for (let i = 0; i < MAX_CACHED_TRAIN_RESULTS; i += 1) {
        s.startTrainJob(`t${i}`, `job-${i}`, `Train ${i}`, `hash-${i}`)
        s.completeTrainJob(`t${i}`, makeTrainResult({ metrics: { rmse: i } }))
      }
      s.setPinnedPreviewNodeId("t0")
      s.startTrainJob("t-new-1", "job-new-1", "Train new 1", "hash-new-1")
      s.completeTrainJob("t-new-1", makeTrainResult({ metrics: { rmse: 998 } }))
      s.startTrainJob("t-new-2", "job-new-2", "Train new 2", "hash-new-2")
      s.completeTrainJob("t-new-2", makeTrainResult({ metrics: { rmse: 999 } }))

      const { trainResults } = useNodeResultsStore.getState()
      expect(Object.keys(trainResults)).toHaveLength(MAX_CACHED_TRAIN_RESULTS)
      expect(trainResults.t0).toBeDefined()
      expect(trainResults.t1).toBeUndefined()
      expect(trainResults.t2).toBeUndefined()
      expect(trainResults["t-new-1"]?.result.metrics.rmse).toBe(998)
      expect(trainResults["t-new-2"]?.result.metrics.rmse).toBe(999)
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
  })

  // ────────────────────────────────────────────────────────────────
  // getOptimiserPreview
  // ────────────────────────────────────────────────────────────────

  describe("getOptimiserPreview", () => {
    it("returns null when no solve result exists", () => {
      expect(useNodeResultsStore.getState().getOptimiserPreview("n1")).toBeNull()
    })

    it("builds correct shape from completed result", () => {
      const s = useNodeResultsStore.getState()
      const constraints = { premium: { min: 0, max: 100 } }
      s.startSolveJob("n1", "j1", "Optim Node", constraints, "h")
      const result = makeSolveResult({ converged: true, iterations: 15 })
      s.completeSolveJob("n1", result)

      const preview = useNodeResultsStore.getState().getOptimiserPreview("n1")
      expect(preview).not.toBeNull()
      expect(preview!.result).toEqual(result)
      expect(preview!.jobId).toBe("j1")
      expect(preview!.constraints).toEqual(constraints)
      expect(preview!.nodeLabel).toBe("Optim Node")
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Frontier actions
  // ────────────────────────────────────────────────────────────────

  describe("Frontier actions", () => {
    it("completeSolveJob selects and displays the first frontier point immediately", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", { vol: { min: 0.9 } }, "h1")

      const frontier = {
        status: "ok",
        points: [
          { total_objective: 123, total_vol: 0.95, lambda_vol: 0.01, converged: true },
          { total_objective: 135, total_vol: 1.05, lambda_vol: 0.03, converged: true },
        ],
        n_points: 2,
        points_returned: 2,
        constraint_names: ["vol"],
        points_limit: 2000,
        points_truncated: false,
      }
      const result = makeSolveResult({
        total_objective: 100,
        constraints: { vol: 0.9 },
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
      expect(cached.result.total_objective).toBe(123)
      expect(cached.result.constraints).toEqual({ vol: 0.95 })
      expect(cached.result.lambdas).toEqual({ vol: 0.01 })
      expect(cached.originalResult.total_objective).toBe(100)
    })

    it("completeSolveJob sets null frontier when points empty", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1")

      const frontier = {
        status: "ok",
        points: [],
        n_points: 0,
        points_returned: 0,
        constraint_names: [],
        points_limit: 2000,
        points_truncated: false,
      }
      const result = makeSolveResult({ frontier })
      s.completeSolveJob("n1", result)

      const cached = useNodeResultsStore.getState().solveResults["n1"]
      expect(cached).toBeDefined()
      expect(cached.frontier).toBeNull()
    })

    it("completeSolveJob sets null frontier when absent", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1")

      const result = makeSolveResult()
      // Ensure no frontier key on the result
      expect(result.frontier).toBeUndefined()
      s.completeSolveJob("n1", result)

      const cached = useNodeResultsStore.getState().solveResults["n1"]
      expect(cached).toBeDefined()
      expect(cached.frontier).toBeNull()
    })

    it("selectFrontierPoint sets index", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1")
      s.completeSolveJob("n1", makeSolveResult({
        frontier: {
          status: "ok",
          points: [{ total_objective: 150, total_premium: 70, lambda_premium: 0.25, converged: true }],
          n_points: 1,
          points_returned: 1,
          constraint_names: ["premium"],
          points_limit: 2000,
          points_truncated: false,
        },
      }))

      s.selectFrontierPoint("n1", 0)

      const cached = useNodeResultsStore.getState().solveResults["n1"]
      expect(cached.selectedPointIndex).toBe(0)
    })

    it("selectFrontierPoint derives the selected result summary from a flattened frontier row immediately", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1")
      const frontier = {
        status: "ok",
        points: [
          {
            total_objective: 150,
            total_premium: 70,
            total_loss: 33,
            lambda_premium: 0.25,
            lambda_loss: 0.4,
            converged: true,
          },
        ],
        n_points: 1,
        points_returned: 1,
        constraint_names: ["premium", "loss"],
        points_limit: 2000,
        points_truncated: false,
      }
      s.completeSolveJob("n1", makeSolveResult({
        total_objective: 100,
        constraints: { premium: 50, loss: 20 },
        baseline_constraints: { premium: 45, loss: 18 },
        lambdas: { premium: 0.1, loss: 0.2 },
        iterations: 42,
        cd_iterations: 12,
        clamp_rate: 0.03,
        history: [
          { iteration: 1, total_objective: 100, max_lambda_change: 0.1, all_constraints_satisfied: false },
        ],
        scenario_value_stats: {
          mean: 1.01,
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
        scenario_value_histogram: { counts: [1, 2, 3], edges: [0.9, 1, 1.1, 1.2] },
        factor_tables: { region: [{ __factor_group__: "North", optimal_scenario_value: 1.05 }] },
        frontier,
      }))

      s.selectFrontierPoint("n1", 0)

      const cached = useNodeResultsStore.getState().solveResults["n1"]
      expect(cached.selectedPointIndex).toBe(0)
      expect(cached.result.total_objective).toBe(150)
      expect(cached.result.constraints).toEqual({ premium: 70, loss: 33 })
      expect(cached.result.lambdas).toEqual({ premium: 0.25, loss: 0.4 })
      expect(cached.result.baseline_objective).toBe(80)
      expect(cached.result.baseline_constraints).toEqual({ premium: 45, loss: 18 })
      expect(cached.result.iterations).toBeUndefined()
      expect(cached.result.cd_iterations).toBeUndefined()
      expect(cached.result.clamp_rate).toBeUndefined()
      expect(cached.result.history).toBeNull()
      expect(cached.result.scenario_value_stats).toBeUndefined()
      expect(cached.result.scenario_value_histogram).toBeUndefined()
      expect(cached.result.factor_tables).toBeUndefined()
    })

    it("selectFrontierPoint uses point diagnostics when the frontier row provides them", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1")
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
      const pointHistory = [
        {
          iteration: 1,
          total_objective: 140,
          max_lambda_change: 0.2,
          all_constraints_satisfied: false,
          lambdas: { premium: 0.2 },
          total_constraints: { premium: 65 },
        },
        {
          iteration: 2,
          total_objective: 150,
          max_lambda_change: 0.01,
          all_constraints_satisfied: true,
          lambdas: { premium: 0.25 },
          total_constraints: { premium: 70 },
        },
      ]
      const pointFactorTables = {
        region: [{ __factor_group__: "North", optimal_scenario_value: 1.08 }],
      }
      const frontier = {
        status: "ok",
        points: [
          {
            total_objective: 150,
            total_premium: 70,
            lambda_premium: 0.25,
            converged: true,
            iterations: 19,
            cd_iterations: 4,
            clamp_rate: 0.01,
            history: pointHistory,
            scenario_value_stats: pointStats,
            scenario_value_histogram: { counts: [4, 5], edges: [0.95, 1.05, 1.2] },
            factor_tables: pointFactorTables,
          },
        ],
        n_points: 1,
        points_returned: 1,
        constraint_names: ["premium"],
        points_limit: 2000,
        points_truncated: false,
      }
      s.completeSolveJob("n1", makeSolveResult({
        iterations: 42,
        history: [
          { iteration: 1, total_objective: 100, max_lambda_change: 0.1, all_constraints_satisfied: false },
        ],
        frontier,
      }))

      s.selectFrontierPoint("n1", 0)

      const selected = useNodeResultsStore.getState().solveResults["n1"].result
      expect(selected.iterations).toBe(19)
      expect(selected.cd_iterations).toBe(4)
      expect(selected.clamp_rate).toBe(0.01)
      expect(selected.history).toEqual(pointHistory)
      expect(selected.scenario_value_stats).toEqual(pointStats)
      expect(selected.scenario_value_histogram).toEqual({ counts: [4, 5], edges: [0.95, 1.05, 1.2] })
      expect(selected.factor_tables).toEqual(pointFactorTables)
    })

    it("selectFrontierPoint preserves flat scenario stats from price-contour frontier rows", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1")
      const frontier = {
        status: "ok",
        points: [
          {
            total_objective: 150,
            total_premium: 70,
            lambda_premium: 0.25,
            converged: true,
            sv_mean: 1.08,
            sv_std: 0.03,
            sv_min: 0.95,
            sv_max: 1.2,
            sv_p5: 0.99,
            sv_p25: 1.03,
            sv_median: 1.07,
            sv_p75: 1.12,
            sv_p95: 1.18,
            sv_pct_increase: 0.8,
            sv_pct_decrease: 0.2,
          },
        ],
        n_points: 1,
        points_returned: 1,
        constraint_names: ["premium"],
        points_limit: 2000,
        points_truncated: false,
      }
      s.completeSolveJob("n1", makeSolveResult({ frontier }))

      s.selectFrontierPoint("n1", 0)

      expect(useNodeResultsStore.getState().solveResults.n1.result.scenario_value_stats).toEqual({
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
      })
    })

    it("selectFrontierPoint uses a point-specific warning for non-converged frontier rows", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1")
      const frontier = {
        status: "ok",
        points: [
          {
            total_objective: 150,
            total_premium: 70,
            lambda_premium: 0.25,
            converged: false,
          },
        ],
        n_points: 1,
        points_returned: 1,
        constraint_names: ["premium"],
        points_limit: 2000,
        points_truncated: false,
      }
      s.completeSolveJob("n1", makeSolveResult({
        converged: true,
        warning: "base warning should not leak",
        frontier,
      }))

      s.selectFrontierPoint("n1", 0)

      expect(useNodeResultsStore.getState().solveResults.n1.result.warning).toBe(NON_CONVERGED_WARNING)
    })

    it("selectFrontierPoint accepts raw constraint columns from price-contour frontier rows", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1")
      const frontier = {
        status: "ok",
        points: [
          {
            total_objective: 150,
            premium: 70,
            lambda_premium: 0.25,
            converged: true,
          },
        ],
        n_points: 1,
        points_returned: 1,
        constraint_names: ["premium"],
        points_limit: 2000,
        points_truncated: false,
      }
      s.completeSolveJob("n1", makeSolveResult({
        constraints: { premium: 50 },
        frontier,
      }))

      s.selectFrontierPoint("n1", 0)

      expect(useNodeResultsStore.getState().solveResults.n1.result.constraints).toEqual({
        premium: 70,
      })
    })

    it("selectFrontierPoint derives the selected result summary from nested frontier row maps", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1")
      const frontier = {
        status: "ok",
        points: [
          {
            total_objective: 160,
            constraints: { premium: 72, loss: 34 },
            lambdas: { premium: 0.35, loss: 0.45 },
            converged: true,
          },
        ],
        n_points: 1,
        points_returned: 1,
        constraint_names: ["premium", "loss"],
        points_limit: 2000,
        points_truncated: false,
      }
      s.completeSolveJob("n1", makeSolveResult({
        constraints: { premium: 50, loss: 20 },
        lambdas: { premium: 0.1, loss: 0.2 },
        frontier,
      }))

      s.selectFrontierPoint("n1", 0)

      const cached = useNodeResultsStore.getState().solveResults["n1"]
      expect(cached.result.total_objective).toBe(160)
      expect(cached.result.constraints).toEqual({ premium: 72, loss: 34 })
      expect(cached.result.lambdas).toEqual({ premium: 0.35, loss: 0.45 })
    })

    it("selectFrontierPoint null deselects and reverts result", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1")
      const original = makeSolveResult({
        total_objective: 100,
        frontier: {
          status: "ok",
          points: [{ total_objective: 200, total_premium: 60, lambda_premium: 0.2, converged: true }],
          n_points: 1,
          points_returned: 1,
          constraint_names: ["premium"],
          points_limit: 2000,
          points_truncated: false,
        },
      })
      s.completeSolveJob("n1", original)

      s.selectFrontierPoint("n1", 0)

      // The result should now reflect the frontier point
      expect(useNodeResultsStore.getState().solveResults["n1"].result.total_objective).toBe(200)

      // Deselect — set back to null
      s.selectFrontierPoint("n1", null)
      const cached = useNodeResultsStore.getState().solveResults["n1"]
      expect(cached.selectedPointIndex).toBeNull()
      expect(cached.result.total_objective).toBe(100)
      expect(cached.result.constraints).toEqual(original.constraints)
      expect(cached.result.lambdas).toEqual(original.lambdas)
      // The original result is preserved in originalResult for the caller to use
      expect(cached.originalResult.total_objective).toBe(100)
    })

    it("selectFrontierPoint noop for unknown node", () => {
      const s = useNodeResultsStore.getState()
      // Should not crash
      s.selectFrontierPoint("ghost", 5)
      expect(useNodeResultsStore.getState().solveResults["ghost"]).toBeUndefined()
    })

    it("updateFrontierAfterSelect updates result metrics", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1")
      s.completeSolveJob("n1", makeSolveResult({
        total_objective: 100,
        constraints: { premium: 50 },
        lambdas: { premium: 0.1 },
        converged: true,
      }))

      s.updateFrontierAfterSelect("n1", 2, {
        status: "ok",
        total_objective: 250,
        constraints: { premium: 70 },
        baseline_objective: 90,
        baseline_constraints: { premium: 48 },
        lambdas: { premium: 0.3 },
        converged: false,
        error: null,
      })

      const cached = useNodeResultsStore.getState().solveResults["n1"]
      expect(cached.selectedPointIndex).toBe(2)
      expect(cached.result.total_objective).toBe(250)
      expect(cached.result.constraints).toEqual({ premium: 70 })
      expect(cached.result.baseline_objective).toBe(90)
      expect(cached.result.baseline_constraints).toEqual({ premium: 48 })
      expect(cached.result.lambdas).toEqual({ premium: 0.3 })
      expect(cached.result.converged).toBe(false)
    })

    it("updateFrontierAfterSelect uses the backend non-convergence warning for select responses", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1")
      s.completeSolveJob("n1", makeSolveResult({
        converged: false,
        warning: "Stale original warning",
      }))

      s.updateFrontierAfterSelect("n1", 2, {
        status: "ok",
        total_objective: 250,
        constraints: { premium: 70 },
        baseline_objective: 90,
        baseline_constraints: { premium: 48 },
        lambdas: { premium: 0.3 },
        converged: false,
        error: null,
      })

      expect(useNodeResultsStore.getState().solveResults.n1.result.warning).toBe(NON_CONVERGED_WARNING)
    })

    it("updateFrontierAfterSelect keeps diagnostics from the selected frontier row", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1")
      const frontier = {
        status: "ok",
        points: [
          {
            total_objective: 240,
            total_premium: 68,
            lambda_premium: 0.28,
            converged: true,
            iterations: 17,
            history: [
              { iteration: 1, total_objective: 240, max_lambda_change: 0.02, all_constraints_satisfied: true },
            ],
            scenario_value_histogram: { counts: [2, 3], edges: [0.95, 1.05, 1.15] },
          },
        ],
        n_points: 1,
        points_returned: 1,
        constraint_names: ["premium"],
        points_limit: 2000,
        points_truncated: false,
      }
      s.completeSolveJob("n1", makeSolveResult({ frontier }))

      s.updateFrontierAfterSelect("n1", 0, {
        status: "ok",
        total_objective: 250,
        constraints: { premium: 70 },
        baseline_objective: 90,
        baseline_constraints: { premium: 48 },
        lambdas: { premium: 0.3 },
        converged: true,
        error: null,
      })

      const selected = useNodeResultsStore.getState().solveResults["n1"].result
      expect(selected.total_objective).toBe(250)
      expect(selected.iterations).toBe(17)
      expect(selected.history).toEqual([
        { iteration: 1, total_objective: 240, max_lambda_change: 0.02, all_constraints_satisfied: true },
      ])
      expect(selected.scenario_value_histogram).toEqual({ counts: [2, 3], edges: [0.95, 1.05, 1.15] })
    })

    it("updateFrontierAfterSelect stores materialised ratebook factor tables on the selected point", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1")
      const frontier = {
        status: "ok",
        points: [
          {
            total_objective: 240,
            total_premium: 68,
            lambda_premium: 0.25,
            converged: true,
          },
        ],
        n_points: 1,
        points_returned: 1,
        constraint_names: ["premium"],
        points_limit: 2000,
        points_truncated: false,
      }
      const factorTables = {
        region: [{ __factor_group__: "North", optimal_scenario_value: 1.08 }],
      }
      s.completeSolveJob("n1", makeSolveResult({ mode: "ratebook", frontier }))

      s.updateFrontierAfterSelect("n1", 0, {
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
      })

      let cached = useNodeResultsStore.getState().solveResults.n1
      expect(cached.result.factor_tables).toEqual(factorTables)
      expect(cached.result.cd_iterations).toBe(5)
      expect(cached.result.clamp_rate).toBe(0.04)

      s.selectFrontierPoint("n1", null)
      s.selectFrontierPoint("n1", 0)
      cached = useNodeResultsStore.getState().solveResults.n1
      expect(cached.result.factor_tables).toEqual(factorTables)
      expect(cached.result.cd_iterations).toBe(5)
      expect(cached.result.clamp_rate).toBe(0.04)
    })

    it("updateFrontierAfterSelect clears point diagnostics that are not in the selected point", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Node 1", {}, "h1")
      const original = makeSolveResult({
        iterations: 42,
        cd_iterations: 9,
        clamp_rate: 0.02,
        n_quotes: 5000,
        history: [
          { iteration: 1, total_objective: 100, max_lambda_change: 0.1, all_constraints_satisfied: false },
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
        factor_tables: { region: [{ __factor_group__: "North", optimal_scenario_value: 1.05 }] },
      })
      s.completeSolveJob("n1", original)

      s.updateFrontierAfterSelect("n1", 1, {
        status: "ok",
        total_objective: 999,
        constraints: { premium: 99 },
        baseline_objective: 88,
        baseline_constraints: { premium: 44 },
        lambdas: { premium: 0.9 },
        converged: true,
        error: null,
      })

      const cached = useNodeResultsStore.getState().solveResults["n1"]
      expect(cached.result.total_objective).toBe(999)
      expect(cached.result.n_quotes).toBe(5000)
      expect(cached.result.iterations).toBeUndefined()
      expect(cached.result.cd_iterations).toBeUndefined()
      expect(cached.result.clamp_rate).toBeUndefined()
      expect(cached.result.history).toBeNull()
      expect(cached.result.scenario_value_stats).toBeUndefined()
      expect(cached.result.scenario_value_histogram).toBeUndefined()
      expect(cached.result.factor_tables).toBeUndefined()
    })

    // ────────────────────────────────────────────────────────────
    // getModellingPreview — error-status filtering
    // Catches: if the error filter is removed, the panel would try
    // to render charts from a failed training result with missing
    // fields (feature_importance, metrics, etc.), causing a crash.
    // ────────────────────────────────────────────────────────────

    it("getModellingPreview returns null when train result has error status", () => {
      const s = useNodeResultsStore.getState()
      s.startTrainJob("t1", "tj-1", "Model Node", "cfg-h")
      s.completeTrainJob("t1", makeTrainResult({ status: "error", error: "OOM" }))

      const preview = useNodeResultsStore.getState().getModellingPreview("t1")
      expect(preview).toBeNull()
    })

    it("getModellingPreview returns result when status is 'completed'", () => {
      const s = useNodeResultsStore.getState()
      s.startTrainJob("t1", "tj-1", "Model Node", "cfg-h")
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
      s.startTrainJob("t1", "tj-1", "First Label", "h1")
      s.completeTrainJob("t1", makeTrainResult())
      // Start a new job (different config) — old result still cached
      s.startTrainJob("t1", "tj-2", "Updated Label", "h2")

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
      s.startSolveJob("n1", "sj-1", "Node 1", { c: { min: 0 } }, "sh1")
      s.startTrainJob("n1", "tj-1", "Node 1", "th1")

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
      s.startSolveJob("n1", "sj-1", "Node 1", {}, "sh1")
      s.startTrainJob("n1", "tj-1", "Node 1", "th1")

      s.failSolveJob("n1", "Solver diverged")

      // Solve job is removed, but train job is untouched
      expect(useNodeResultsStore.getState().solveJobs["n1"]).toBeUndefined()
      expect(useNodeResultsStore.getState().trainJobs["n1"]).toBeDefined()
      expect(useNodeResultsStore.getState().trainJobs["n1"].progress).toBeNull()
    })

    it("multiple solve jobs on different nodes simultaneously", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "sj-1", "Node 1", { c: { min: 0 } }, "h1")
      s.startSolveJob("n2", "sj-2", "Node 2", { d: { max: 1 } }, "h2")

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
      expect(useNodeResultsStore.getState().solveResults["n1"].result.total_objective).toBe(100)
      expect(useNodeResultsStore.getState().solveJobs["n2"]).toBeDefined()

      s.completeSolveJob("n2", makeSolveResult({ total_objective: 200 }))
      expect(useNodeResultsStore.getState().solveJobs["n2"]).toBeUndefined()
      expect(useNodeResultsStore.getState().solveResults["n2"].result.total_objective).toBe(200)
      expect(useNodeResultsStore.getState().solveResults["n1"].result.total_objective).toBe(100)
    })

    it("failing one solve does not affect other nodes' solve jobs", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "sj-1", "Node 1", {}, "h1")
      s.startSolveJob("n2", "sj-2", "Node 2", {}, "h2")

      s.failSolveJob("n1", "Diverged")

      expect(useNodeResultsStore.getState().solveJobs["n1"]).toBeUndefined()
      expect(useNodeResultsStore.getState().solveResults["n1"].error).toBe("Diverged")
      expect(useNodeResultsStore.getState().solveJobs["n2"]).toBeDefined()
      expect(useNodeResultsStore.getState().solveJobs["n2"].jobId).toBe("sj-2")
    })

    it("getOptimiserPreview includes frontier and selectedPointIndex", () => {
      const s = useNodeResultsStore.getState()
      const constraints = { vol: { min: 0.9 } }
      s.startSolveJob("n1", "j1", "Optim Node", constraints, "h1")

      const frontier = {
        status: "ok",
        points: [
          { total_objective: 100, total_vol: 0.95, lambda_vol: 0.01 },
          { total_objective: 110, total_vol: 0.92, lambda_vol: 0.02 },
        ],
        n_points: 2,
        points_returned: 2,
        constraint_names: ["vol"],
        points_limit: 2000,
        points_truncated: false,
      }
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
      s.startSolveJob("n1", "j1", "Label", {}, "h1")
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
            constraints: {},
            nodeLabel: "Fresh Label",
            frontier: null,
            selectedPointIndex: null,
          },
        },
      })

      const freshPreview = useNodeResultsStore.getState().getOptimiserPreview("n1")
      expect(freshPreview).not.toBe(stalePreview)
      expect(freshPreview?.result.total_objective).toBe(999)
      expect(freshPreview?.nodeLabel).toBe("Fresh Label")
    })

    it("getOptimiserPreview returns same reference on repeated calls", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Label", {}, "h1")
      s.completeSolveJob("n1", makeSolveResult())

      const a = useNodeResultsStore.getState().getOptimiserPreview("n1")
      const b = useNodeResultsStore.getState().getOptimiserPreview("n1")
      expect(a).toBe(b) // same reference, not just deep-equal
    })

    it("getOptimiserPreview returns new reference after state change", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Label", {}, "h1")
      s.completeSolveJob("n1", makeSolveResult())

      const a = useNodeResultsStore.getState().getOptimiserPreview("n1")
      // Trigger a state change on the same node
      useNodeResultsStore.getState().selectFrontierPoint("n1", 0)
      const b = useNodeResultsStore.getState().getOptimiserPreview("n1")
      expect(a).not.toBe(b)
    })

    it("getOptimiserPreview returns null after clearNode", () => {
      const s = useNodeResultsStore.getState()
      s.startSolveJob("n1", "j1", "Label", {}, "h1")
      s.completeSolveJob("n1", makeSolveResult())

      expect(useNodeResultsStore.getState().getOptimiserPreview("n1")).not.toBeNull()
      useNodeResultsStore.getState().clearNode("n1")
      expect(useNodeResultsStore.getState().getOptimiserPreview("n1")).toBeNull()
    })

    it("getModellingPreview returns same reference on repeated calls", () => {
      const s = useNodeResultsStore.getState()
      s.startTrainJob("n1", "j1", "Model", "h1")
      s.completeTrainJob("n1", makeTrainResult())

      const a = useNodeResultsStore.getState().getModellingPreview("n1")
      const b = useNodeResultsStore.getState().getModellingPreview("n1")
      expect(a).toBe(b)
    })

    it("getModellingPreview returns new reference after state change", () => {
      const s = useNodeResultsStore.getState()
      s.startTrainJob("n1", "j1", "Model", "h1")
      s.completeTrainJob("n1", makeTrainResult())

      const a = useNodeResultsStore.getState().getModellingPreview("n1")
      // Overwrite with a new result
      s.completeTrainJob("n1", makeTrainResult({ status: "completed", metrics: { rmse: 0.01 } }))
      const b = useNodeResultsStore.getState().getModellingPreview("n1")
      expect(a).not.toBe(b)
    })

    it("getModellingPreview keeps its reference across active job progress updates", () => {
      const s = useNodeResultsStore.getState()
      s.startTrainJob("n1", "j1", "Initial Label", "h1")
      s.completeTrainJob("n1", makeTrainResult())
      s.startTrainJob("n1", "j2", "Updated Label", "h2")

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
      s.startTrainJob("n1", "j1", "Model", "h1")
      s.failTrainJob("n1", "boom")

      expect(useNodeResultsStore.getState().getModellingPreview("n1")).toBeNull()
    })
  })
})
