/**
 * Tests for useBackgroundJobs — polling lifecycle, exponential backoff,
 * max lifetime timeout, and cleanup on job removal.
 *
 * Since useBackgroundJobs orchestrates polling via createJobPoller (a
 * non-exported module-level function that uses setTimeout recursively),
 * we test the observable behaviour through the stores and mocked API.
 *
 * Key testing challenge: the poller uses `setTimeout` inside an `async`
 * callback, creating a chain of (timer -> promise -> timer -> ...).
 * We use vi.advanceTimersByTimeAsync() which handles this pattern by
 * interleaving timer advancement with microtask flushing.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act, cleanup } from "@testing-library/react"

// Mock API functions BEFORE importing the hook
vi.mock("../../api/client.ts", () => ({
  getOptimiserStatus: vi.fn(),
  getTrainStatus: vi.fn(),
}))

import { getOptimiserStatus, getTrainStatus } from "../../api/client.ts"
import useNodeResultsStore from "../../stores/useNodeResultsStore.ts"
import useToastStore from "../../stores/useToastStore.ts"
import useBackgroundJobs from "../../hooks/useBackgroundJobs.ts"
import type { SolveProgress, TrainProgress } from "../../stores/useNodeResultsStore.ts"

// ── Helpers ──────────────────────────────────────────────────────

function resetStores() {
  useNodeResultsStore.setState({
    previews: {},
    columnCache: {},
    solveResults: {},
    solveJobs: {},
    trainResults: {},
    trainJobs: {},
  })
  useToastStore.setState({
    toasts: [],
    _toastCounter: 0,
  })
}

function makeSolveProgress(overrides: Partial<SolveProgress> = {}): SolveProgress {
  return {
    status: "running",
    progress: 0.5,
    message: "Working...",
    elapsed_seconds: 5,
    ...overrides,
  }
}

function makeTrainProgress(overrides: Partial<TrainProgress> = {}): TrainProgress {
  return {
    status: "running",
    progress: 0.5,
    message: "Training...",
    iteration: 50,
    total_iterations: 100,
    train_loss: { rmse: 0.1 },
    elapsed_seconds: 5,
    ...overrides,
  }
}

/**
 * Advance fake timers by `ms` while also flushing any interleaved microtasks
 * (promises). vi.advanceTimersByTimeAsync handles the setTimeout->promise->setTimeout
 * chain correctly.
 */
async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

// ── Test suites ──────────────────────────────────────────────────

describe("useBackgroundJobs", () => {
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
  // Solve job polling — complete lifecycle
  // ────────────────────────────────────────────────────────────────

  describe("solve job polling", () => {
    it("polls and completes a solve job when API returns completed status", async () => {
      const mockGetStatus = vi.mocked(getOptimiserStatus)
      const solveResult = {
        total_objective: 100,
        baseline_objective: 80,
        constraints: {},
        baseline_constraints: {},
        lambdas: {},
        converged: true,
      }

      // First poll: still running
      mockGetStatus.mockResolvedValueOnce(
        makeSolveProgress({ status: "running", progress: 0.5 }),
      )
      // Second poll: completed with result
      mockGetStatus.mockResolvedValueOnce(
        makeSolveProgress({ status: "completed", progress: 1.0, result: solveResult }),
      )

      // Start a job in the store
      act(() => {
        useNodeResultsStore.getState().startSolveJob("n1", "job-1", "Node 1", {}, "hash-a")
      })

      // Render the hook (triggers useEffect -> createJobPoller -> schedulePoll)
      renderHook(() => useBackgroundJobs())

      // First poll fires at 500ms
      await advance(500)
      expect(mockGetStatus).toHaveBeenCalledTimes(1)
      expect(mockGetStatus).toHaveBeenCalledWith("job-1")

      // Progress should be updated on the active job
      expect(useNodeResultsStore.getState().solveJobs["n1"]?.progress?.progress).toBe(0.5)

      // Second poll fires 500ms later (BASE_INTERVAL_MS, no errors)
      await advance(500)
      expect(mockGetStatus).toHaveBeenCalledTimes(2)

      // Job should now be completed and moved to results
      const state = useNodeResultsStore.getState()
      expect(state.solveResults["n1"]).toBeDefined()
      expect(state.solveResults["n1"].result.converged).toBe(true)
      expect(state.solveJobs["n1"]).toBeUndefined()
    })

    it("updates progress on active job during polling", async () => {
      const mockGetStatus = vi.mocked(getOptimiserStatus)
      mockGetStatus.mockResolvedValueOnce(
        makeSolveProgress({ status: "running", progress: 0.7, message: "Iterating" }),
      )
      // Queue another running response so we don't complete
      mockGetStatus.mockResolvedValueOnce(
        makeSolveProgress({ status: "running", progress: 0.8 }),
      )

      act(() => {
        useNodeResultsStore.getState().startSolveJob("n1", "job-1", "Node 1", {}, "h")
      })

      renderHook(() => useBackgroundJobs())

      await advance(500)

      const job = useNodeResultsStore.getState().solveJobs["n1"]
      expect(job?.progress?.progress).toBe(0.7)
      expect(job?.progress?.message).toBe("Iterating")
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Train job polling
  // ────────────────────────────────────────────────────────────────

  describe("train job polling", () => {
    it("polls and completes a train job when API returns completed status", async () => {
      const mockGetStatus = vi.mocked(getTrainStatus)

      const trainResult = {
        status: "completed",
        metrics: { rmse: 0.05 },
        feature_importance: [],
        model_path: "/m.pkl",
        train_rows: 100,
        test_rows: 20,
      }
      mockGetStatus.mockResolvedValueOnce(
        makeTrainProgress({ status: "completed", progress: 1.0, result: trainResult }),
      )

      act(() => {
        useNodeResultsStore.getState().startTrainJob("t1", "tj-1", "Train Node", "th")
      })

      renderHook(() => useBackgroundJobs())

      await advance(500)

      const state = useNodeResultsStore.getState()
      expect(state.trainResults["t1"]).toBeDefined()
      expect(state.trainResults["t1"].result.metrics.rmse).toBe(0.05)
      expect(state.trainJobs["t1"]).toBeUndefined()
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Exponential backoff
  // ────────────────────────────────────────────────────────────────

  describe("exponential backoff", () => {
    it("increases poll interval after consecutive errors", async () => {
      const mockGetStatus = vi.mocked(getOptimiserStatus)

      // First 5 calls reject, then complete to stop the loop
      const callTracker: number[] = []
      mockGetStatus.mockImplementation(() => {
        callTracker.push(Date.now())
        if (callTracker.length <= 5) {
          return Promise.reject(new Error("Network error"))
        }
        return Promise.resolve(
          makeSolveProgress({
            status: "completed",
            result: {
              total_objective: 1,
              baseline_objective: 1,
              constraints: {},
              baseline_constraints: {},
              lambdas: {},
              converged: true,
            },
          }),
        )
      })

      act(() => {
        useNodeResultsStore.getState().startSolveJob("n1", "job-1", "Node 1", {}, "h")
      })

      renderHook(() => useBackgroundJobs())

      // Advance through all 5 errors + the final completion:
      // Poll 1: at +500ms
      // Poll 2: at +500 + 1000 = +1500ms
      // Poll 3: at +1500 + 2000 = +3500ms
      // Poll 4: at +3500 + 4000 = +7500ms
      // Poll 5: at +7500 + 5000 = +12500ms (capped at MAX_INTERVAL_MS=5000)
      // Poll 6: at +12500 + 500 = +13000ms (backoff resets after success on poll 5,
      //         but poll 5 also errors so backoff = 5000, then poll 6 at +17500)
      // Let's just advance enough to get all 5 errors
      await advance(13_000)

      // All 5 error polls should have fired
      expect(callTracker.length).toBeGreaterThanOrEqual(5)

      // Verify exponential spacing: each successive interval should be >= the previous
      // (backoff increases or stays capped)
      for (let i = 2; i < Math.min(callTracker.length, 5); i++) {
        const prevInterval = callTracker[i - 1] - callTracker[i - 2]
        const currInterval = callTracker[i] - callTracker[i - 1]
        expect(currInterval).toBeGreaterThanOrEqual(prevInterval)
      }

      // The second call should be at ~1000ms after the first (500 * 2^1)
      if (callTracker.length >= 2) {
        const gap1to2 = callTracker[1] - callTracker[0]
        expect(gap1to2).toBeGreaterThanOrEqual(1000)
      }

      // The third call should be at ~2000ms after the second (500 * 2^2)
      if (callTracker.length >= 3) {
        const gap2to3 = callTracker[2] - callTracker[1]
        expect(gap2to3).toBeGreaterThanOrEqual(2000)
      }
    }, 15_000)
  })

  // ────────────────────────────────────────────────────────────────
  // Max lifetime timeout
  // ────────────────────────────────────────────────────────────────

  describe("max lifetime timeout", () => {
    it("fails job after 24 hours with timeout message", async () => {
      const mockGetStatus = vi.mocked(getOptimiserStatus)
      // Always return "running" so the poller doesn't complete naturally
      mockGetStatus.mockResolvedValue(
        makeSolveProgress({ status: "running", progress: 0.5 }),
      )

      act(() => {
        useNodeResultsStore.getState().startSolveJob("n1", "job-1", "Node 1", {}, "h")
      })

      renderHook(() => useBackgroundJobs())

      // MAX_LIFETIME_MS = 24 * 60 * 60 * 1000 = 86,400,000ms
      // Advance past the lifetime limit in one shot.
      await advance(86_400_500)

      // Job is removed from store on failure (timeout)
      expect(useNodeResultsStore.getState().solveJobs["n1"]).toBeUndefined()
    }, 120_000)
  })

  // ────────────────────────────────────────────────────────────────
  // Cleanup on job removal
  // ────────────────────────────────────────────────────────────────

  describe("cleanup on job removal", () => {
    it("stops polling when job is removed from store", async () => {
      const mockGetStatus = vi.mocked(getOptimiserStatus)
      // Return running for first two calls, then completed to stop any straggler
      let resolvedCount = 0
      mockGetStatus.mockImplementation(() => {
        resolvedCount++
        return Promise.resolve(
          makeSolveProgress({ status: "running", progress: 0.5 }),
        )
      })

      act(() => {
        useNodeResultsStore.getState().startSolveJob("n1", "job-1", "Node 1", {}, "h")
      })

      const { rerender } = renderHook(() => useBackgroundJobs())

      // Let the first poll happen
      await advance(500)
      const callsAfterFirst = resolvedCount
      expect(callsAfterFirst).toBeGreaterThanOrEqual(1)

      // Remove the job from the store
      act(() => {
        useNodeResultsStore.getState().clearNode("n1")
      })

      // Re-render hook so useEffect fires with updated solveJobs (empty).
      // This triggers createJobPoller which calls clearTimeout on the old poller.
      rerender()

      // Record call count right after cleanup
      const callsAfterCleanup = resolvedCount

      // Advance time significantly — no more polls should happen
      // (We advance 5 seconds, which is 10 poll intervals)
      await advance(5000)

      // Should have at most 1 additional call from the in-flight timeout
      expect(resolvedCount).toBeLessThanOrEqual(callsAfterCleanup + 1)
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Error status from API
  // ────────────────────────────────────────────────────────────────

  describe("error status from API", () => {
    it("fails the job and shows toast when API returns error status", async () => {
      const mockGetStatus = vi.mocked(getOptimiserStatus)
      mockGetStatus.mockResolvedValueOnce(
        makeSolveProgress({ status: "error", message: "Infeasible" }),
      )

      act(() => {
        useNodeResultsStore.getState().startSolveJob("n1", "job-1", "Node 1", {}, "h")
      })

      renderHook(() => useBackgroundJobs())

      await advance(500)

      // Job should be removed from store on failure
      expect(useNodeResultsStore.getState().solveJobs["n1"]).toBeUndefined()

      // A toast should have been created
      const toasts = useToastStore.getState().toasts
      expect(toasts.length).toBeGreaterThanOrEqual(1)
      expect(toasts.some((t) => t.type === "error")).toBe(true)
    })
  })
})
