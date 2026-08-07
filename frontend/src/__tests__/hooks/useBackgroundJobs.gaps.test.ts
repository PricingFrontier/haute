/**
 * Gap tests for useBackgroundJobs — covers:
 *
 * 1. Multiple concurrent jobs (solve + train running simultaneously)
 * 2. Train job failure toast
 * 3. API returning unexpected shape (missing fields)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act, cleanup } from "@testing-library/react"

vi.mock("../../api/client.ts", () => ({
  getOptimiserStatus: vi.fn(),
  getTrainStatus: vi.fn(),
}))

import { getOptimiserStatus, getTrainStatus } from "../../api/client.ts"
import useNodeResultsStore from "../../stores/useNodeResultsStore.ts"
import useToastStore from "../../stores/useToastStore.ts"
import useBackgroundJobs from "../../hooks/useBackgroundJobs.ts"
import { makeTrainResult } from "../../test-utils/factories.ts"

function resetStores() {
  useNodeResultsStore.setState({
    previews: {},
    columnCache: {},
    solveResults: {},
    solveJobs: {},
    trainResults: {},
    trainJobs: {},
  })
}

async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

describe("useBackgroundJobs — gap tests", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    resetStores()
    vi.clearAllMocks()
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  // ────────────────────────────────────────────────────────────────
  // 1. Multiple concurrent jobs
  // ────────────────────────────────────────────────────────────────

  describe("multiple concurrent jobs", () => {
    it("polls solve and train jobs independently at the same time", async () => {
      // Catches: if job pollers share state or interfere, running both
      // an optimiser and a training job simultaneously would cause one
      // to stop polling or receive the other's results.
      const mockSolve = vi.mocked(getOptimiserStatus)
      const mockTrain = vi.mocked(getTrainStatus)

      const solveResult = {
        total_objective: 100,
        baseline_objective: 80,
        constraints: {},
        baseline_constraints: {},
        lambdas: {},
        converged: true,
      }
      const trainResult = makeTrainResult({
        final_test_metrics: { rmse: 0.01 },
        feature_importance: [],
        model_path: "/m.pkl",
        development_rows: 100,
        final_test_rows: 20,
      })

      // Solve: running → completed
      mockSolve.mockResolvedValueOnce({
        status: "running",
        progress: 0.5,
        message: "Solving...",
        elapsed_seconds: 2,
      })
      mockSolve.mockResolvedValueOnce({
        status: "completed",
        progress: 1.0,
        message: "Done",
        elapsed_seconds: 5,
        result: solveResult,
      })

      // Train: running → completed
      mockTrain.mockResolvedValueOnce({
        status: "running",
        progress: 0.3,
        message: "Training...",
        iteration: 30,
        total_iterations: 100,
        train_loss: { rmse: 0.5 },
        elapsed_seconds: 2,
      })
      mockTrain.mockResolvedValueOnce({
        status: "completed",
        progress: 1.0,
        message: "Done",
        iteration: 100,
        total_iterations: 100,
        train_loss: { rmse: 0.01 },
        elapsed_seconds: 10,
        result: trainResult,
      })

      // Start both jobs
      act(() => {
        useNodeResultsStore.getState().startSolveJob("s1", "solve-1", "Solve Node", {}, "h1", "live", 0)
        useNodeResultsStore.getState().startTrainJob("t1", "train-1", "Train Node", "h2", "live", 0)
      })

      renderHook(() => useBackgroundJobs())

      // First poll — both should fire at 500ms
      await advance(500)
      expect(mockSolve).toHaveBeenCalledTimes(1)
      expect(mockTrain).toHaveBeenCalledTimes(1)

      // Healthy polling ramps after the first response, so both complete
      // on their second poll at 1500ms.
      await advance(1000)
      expect(mockSolve).toHaveBeenCalledTimes(2)
      expect(mockTrain).toHaveBeenCalledTimes(2)

      const state = useNodeResultsStore.getState()
      // Both should be in results, removed from jobs
      expect(state.solveResults["s1"]).toBeDefined()
      expect(state.solveResults["s1"].result.converged).toBe(true)
      expect(state.trainResults["t1"]).toBeDefined()
      expect(state.trainResults["t1"].result.final_test_metrics.rmse).toBe(0.01)
      expect(state.solveJobs["s1"]).toBeUndefined()
      expect(state.trainJobs["t1"]).toBeUndefined()
    })

    it("two solve jobs for different nodes poll independently", async () => {
      // Catches: if the poller used a single timer for all jobs,
      // completing one job would stop polling for the other.
      const mockSolve = vi.mocked(getOptimiserStatus)

      // Job A: completes on first poll
      // Job B: still running on first poll, completes on second
      let callCount = 0
      mockSolve.mockImplementation((jobId: string) => {
        callCount++
        if (jobId === "job-a") {
          return Promise.resolve({
            status: "completed",
            progress: 1,
            message: "Done",
            elapsed_seconds: 1,
            result: { total_objective: 1, baseline_objective: 0, constraints: {}, baseline_constraints: {}, lambdas: {}, converged: true },
          })
        }
        // job-b: running on first call, completed on second
        if (callCount <= 2) {
          return Promise.resolve({ status: "running", progress: 0.5, message: "...", elapsed_seconds: 1 })
        }
        return Promise.resolve({
          status: "completed",
          progress: 1,
          message: "Done",
          elapsed_seconds: 2,
          result: { total_objective: 2, baseline_objective: 0, constraints: {}, baseline_constraints: {}, lambdas: {}, converged: true },
        })
      })

      act(() => {
        useNodeResultsStore.getState().startSolveJob("a", "job-a", "Node A", {}, "h", "live", 0)
        useNodeResultsStore.getState().startSolveJob("b", "job-b", "Node B", {}, "h", "live", 0)
      })

      renderHook(() => useBackgroundJobs())

      // First poll: A completes, B still running
      await advance(500)
      expect(useNodeResultsStore.getState().solveResults["a"]).toBeDefined()
      expect(useNodeResultsStore.getState().solveJobs["b"]).toBeDefined()

      // Second poll: B completes
      await advance(1000)
      expect(useNodeResultsStore.getState().solveResults["b"]).toBeDefined()
    })
  })

  // ────────────────────────────────────────────────────────────────
  // 2. Train job failure toast
  // ────────────────────────────────────────────────────────────────

  describe("train job failure", () => {
    it("shows error toast with 'Training failed' when train job returns error", async () => {
      // Catches: if the train polling config's failLabel were wrong or
      // the onFail callback didn't fire, training failures would be
      // silently swallowed with no user feedback.
      const mockTrain = vi.mocked(getTrainStatus)
      mockTrain.mockResolvedValueOnce({
        status: "error",
        progress: 0,
        message: "CUDA out of memory",
        iteration: 10,
        total_iterations: 100,
        train_loss: {},
        elapsed_seconds: 30,
      })

      act(() => {
        useNodeResultsStore.getState().startTrainJob("t1", "tj-1", "GLM Node", "th", "live", 0)
      })

      renderHook(() => useBackgroundJobs())

      await advance(500)

      // Job should be removed from store (prevents infinite restart loop)
      expect(useNodeResultsStore.getState().trainJobs["t1"]).toBeUndefined()

      // Toast should contain "Training failed"
      const toasts = useToastStore.getState().toasts
      expect(toasts.some((t) => t.type === "error" && t.text.includes("Training failed"))).toBe(true)
      expect(toasts.some((t) => t.text.includes("CUDA out of memory"))).toBe(true)
    })
  })

  // ────────────────────────────────────────────────────────────────
  // 3. API returning unexpected shape
  // ────────────────────────────────────────────────────────────────

  describe("API returning unexpected shape", () => {
    it("handles missing message field gracefully (uses 'Unknown error')", async () => {
      // Catches: if the API returns an error status without a message
      // field, getErrorMessage should fall back to "Unknown error"
      // instead of showing "undefined" in the toast.
      const mockSolve = vi.mocked(getOptimiserStatus)
      mockSolve.mockResolvedValueOnce({
        status: "error",
        progress: 0,
        elapsed_seconds: 1,
        // message is intentionally missing
      })

      act(() => {
        useNodeResultsStore.getState().startSolveJob("n1", "job-1", "Node 1", {}, "h", "live", 0)
      })

      renderHook(() => useBackgroundJobs())

      await advance(500)

      // Job should be removed from store
      expect(useNodeResultsStore.getState().solveJobs["n1"]).toBeUndefined()

      const toasts = useToastStore.getState().toasts
      expect(toasts.some((t) => t.text.includes("Unknown error"))).toBe(true)
    })

    it("treats network error during poll as retryable (does not fail job)", async () => {
      // Catches: if network errors were treated the same as API error
      // status, a single dropped packet would permanently fail the job
      // instead of retrying with backoff.
      const mockSolve = vi.mocked(getOptimiserStatus)

      // First call: network error. Second call: still running. Third: completed.
      mockSolve
        .mockRejectedValueOnce(new Error("fetch failed"))
        .mockResolvedValueOnce({
          status: "running",
          progress: 0.5,
          message: "Working...",
          elapsed_seconds: 3,
        })
        .mockResolvedValueOnce({
          status: "completed",
          progress: 1,
          message: "Done",
          elapsed_seconds: 5,
          result: { total_objective: 1, baseline_objective: 0, constraints: {}, baseline_constraints: {}, lambdas: {}, converged: true },
        })

      act(() => {
        useNodeResultsStore.getState().startSolveJob("n1", "job-1", "Node 1", {}, "h", "live", 0)
      })

      renderHook(() => useBackgroundJobs())

      // First poll: network error at 500ms — job should NOT be failed
      await advance(500)
      expect(useNodeResultsStore.getState().solveJobs["n1"]).toBeDefined()
      expect(useNodeResultsStore.getState().solveJobs["n1"]?.error).toBeFalsy()

      // Second poll: running (after backoff: 500 * 2^1 = 1000ms)
      await advance(1000)
      expect(useNodeResultsStore.getState().solveJobs["n1"]?.progress?.progress).toBe(0.5)

      // A successful response clears the error counter but keeps the per-job
      // ramp, so the third poll arrives after the next 2s interval.
      await advance(2000)
      expect(useNodeResultsStore.getState().solveResults["n1"]).toBeDefined()
    })
  })
})
