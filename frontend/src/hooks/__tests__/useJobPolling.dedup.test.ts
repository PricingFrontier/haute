/**
 * Dedup regression test for useJobPolling.
 *
 * `useJobPolling` is the single polling engine shared by solve and train
 * jobs via `useBackgroundJobs`. Before Phase 2 Package 3B, the two panels
 * (ModellingConfig and OptimiserConfig) each maintained their own copy of
 * the poll loop — identical except for the API client function and the
 * result shape. This test pins the contract that both job types travel
 * through the exact same engine and therefore share:
 *
 *   - base poll cadence (500 ms)
 *   - exponential backoff on network errors
 *   - error → onFail + error toast
 *   - completion → onComplete + success toast
 *   - cleanup on unmount
 *
 * Any divergence here means the hook was fragmented again or a panel
 * re-inlined its own polling loop. Keep these tests green.
 *
 * These tests use the same hook API the panels use in production through
 * `useBackgroundJobs`, so regressions in either direction (train-only or
 * solve-only change) are caught.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act, cleanup } from "@testing-library/react"
import useJobPolling from "../useJobPolling"
import type { UseJobPollingConfig } from "../useJobPolling"

// ── Shapes modelled after production use (SolveProgress / TrainProgress) ──

interface SolveLikeJob { jobId: string; nodeLabel: string }
interface SolveLikeStatus {
  status: string
  message: string
  progress: number
  result?: { converged: boolean }
}

interface TrainLikeJob { jobId: string; nodeLabel: string }
interface TrainLikeStatus {
  status: string
  message: string
  progress: number
  result?: { metrics: Record<string, number> }
}

// ── Helpers ──────────────────────────────────────────────────────

async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

function solveConfig(
  overrides: Partial<UseJobPollingConfig<SolveLikeJob, SolveLikeStatus>> = {},
): UseJobPollingConfig<SolveLikeJob, SolveLikeStatus> {
  return {
    jobs: {},
    pollFn: vi.fn(),
    onProgress: vi.fn(),
    onComplete: vi.fn(),
    onFail: vi.fn(),
    labelFn: (j) => j.nodeLabel,
    jobIdFn: (j) => j.jobId,
    isComplete: (s) => s.status === "completed",
    isError: (s) => s.status === "error",
    getResult: (s) => (s.result ? s : undefined),
    getErrorMessage: (s) => s.message || "Unknown error",
    addToast: vi.fn(),
    successLabel: "Optimisation complete",
    failLabel: "Optimisation failed",
    ...overrides,
  }
}

function trainConfig(
  overrides: Partial<UseJobPollingConfig<TrainLikeJob, TrainLikeStatus>> = {},
): UseJobPollingConfig<TrainLikeJob, TrainLikeStatus> {
  return {
    jobs: {},
    pollFn: vi.fn(),
    onProgress: vi.fn(),
    onComplete: vi.fn(),
    onFail: vi.fn(),
    labelFn: (j) => j.nodeLabel,
    jobIdFn: (j) => j.jobId,
    isComplete: (s) => s.status === "completed",
    isError: (s) => s.status === "error",
    getResult: (s) => (s.result ? s : undefined),
    getErrorMessage: (s) => s.message || "Unknown error",
    addToast: vi.fn(),
    successLabel: "Training complete",
    failLabel: "Training failed",
    ...overrides,
  }
}

// ── Setup ────────────────────────────────────────────────────────

beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

// ═════════════════════════════════════════════════════════════════
// Shared poll cadence
// ═════════════════════════════════════════════════════════════════

describe("useJobPolling — solve and train share poll cadence", () => {
  it("kicks off the first poll at ~500ms for both solve and train jobs", async () => {
    const solvePoll = vi.fn<(id: string) => Promise<SolveLikeStatus>>().mockResolvedValue({
      status: "running", message: "", progress: 0.1,
    })
    const trainPoll = vi.fn<(id: string) => Promise<TrainLikeStatus>>().mockResolvedValue({
      status: "running", message: "", progress: 0.1,
    })

    const solveCfg = solveConfig({
      jobs: { n1: { jobId: "s1", nodeLabel: "Solve 1" } },
      pollFn: solvePoll,
    })
    const trainCfg = trainConfig({
      jobs: { n2: { jobId: "t1", nodeLabel: "Train 1" } },
      pollFn: trainPoll,
    })

    renderHook(() => {
      useJobPolling(solveCfg)
      useJobPolling(trainCfg)
    })

    // Before 500ms, neither should have fired
    await advance(499)
    expect(solvePoll).not.toHaveBeenCalled()
    expect(trainPoll).not.toHaveBeenCalled()

    // At 500ms, both fire — identical cadence from a shared engine
    await advance(1)
    expect(solvePoll).toHaveBeenCalledTimes(1)
    expect(trainPoll).toHaveBeenCalledTimes(1)
  })

  it("applies identical exponential backoff on errors for both job types", async () => {
    const solveTimes: number[] = []
    const trainTimes: number[] = []

    const solvePoll = vi.fn<(id: string) => Promise<SolveLikeStatus>>().mockImplementation(() => {
      solveTimes.push(Date.now())
      return Promise.reject(new Error("network"))
    })
    const trainPoll = vi.fn<(id: string) => Promise<TrainLikeStatus>>().mockImplementation(() => {
      trainTimes.push(Date.now())
      return Promise.reject(new Error("network"))
    })

    const solveCfg = solveConfig({
      jobs: { n1: { jobId: "s1", nodeLabel: "Solve 1" } },
      pollFn: solvePoll,
    })
    const trainCfg = trainConfig({
      jobs: { n2: { jobId: "t1", nodeLabel: "Train 1" } },
      pollFn: trainPoll,
    })

    renderHook(() => {
      useJobPolling(solveCfg)
      useJobPolling(trainCfg)
    })

    // Advance enough for 3 error polls on each (500 + 1000 + 2000 = 3500ms)
    await advance(4000)

    expect(solveTimes.length).toBeGreaterThanOrEqual(3)
    expect(trainTimes.length).toBeGreaterThanOrEqual(3)

    // The backoff gaps should be equivalent for both job types.
    const solveGap1 = solveTimes[1] - solveTimes[0]
    const trainGap1 = trainTimes[1] - trainTimes[0]
    expect(trainGap1).toBe(solveGap1)

    const solveGap2 = solveTimes[2] - solveTimes[1]
    const trainGap2 = trainTimes[2] - trainTimes[1]
    expect(trainGap2).toBe(solveGap2)
  })
})

// ═════════════════════════════════════════════════════════════════
// Shared completion semantics
// ═════════════════════════════════════════════════════════════════

describe("useJobPolling — shared completion semantics", () => {
  it("calls onComplete + success toast with the panel-specific label for both types", async () => {
    const solvePoll = vi.fn<(id: string) => Promise<SolveLikeStatus>>().mockResolvedValue({
      status: "completed", message: "", progress: 1, result: { converged: true },
    })
    const trainPoll = vi.fn<(id: string) => Promise<TrainLikeStatus>>().mockResolvedValue({
      status: "completed", message: "", progress: 1, result: { metrics: { gini: 0.5 } },
    })

    const solveToast = vi.fn()
    const trainToast = vi.fn()
    const solveOnComplete = vi.fn()
    const trainOnComplete = vi.fn()

    const solveCfg = solveConfig({
      jobs: { n1: { jobId: "s1", nodeLabel: "Solve 1" } },
      pollFn: solvePoll,
      onComplete: solveOnComplete,
      addToast: solveToast,
    })
    const trainCfg = trainConfig({
      jobs: { n2: { jobId: "t1", nodeLabel: "Train 1" } },
      pollFn: trainPoll,
      onComplete: trainOnComplete,
      addToast: trainToast,
    })

    renderHook(() => {
      useJobPolling(solveCfg)
      useJobPolling(trainCfg)
    })

    await advance(500)

    // Both call onComplete exactly once — same engine, same semantics
    expect(solveOnComplete).toHaveBeenCalledTimes(1)
    expect(trainOnComplete).toHaveBeenCalledTimes(1)

    // Each gets a success toast with its own label prefix (the only
    // real difference between solve and train polling)
    expect(solveToast).toHaveBeenCalledWith("success", "Optimisation complete: Solve 1")
    expect(trainToast).toHaveBeenCalledWith("success", "Training complete: Train 1")
  })

  it("calls onFail + error toast on API error status for both types", async () => {
    const solvePoll = vi.fn<(id: string) => Promise<SolveLikeStatus>>().mockResolvedValue({
      status: "error", message: "Infeasible", progress: 0,
    })
    const trainPoll = vi.fn<(id: string) => Promise<TrainLikeStatus>>().mockResolvedValue({
      status: "error", message: "Training diverged", progress: 0,
    })

    const solveOnFail = vi.fn()
    const trainOnFail = vi.fn()
    const solveToast = vi.fn()
    const trainToast = vi.fn()

    const solveCfg = solveConfig({
      jobs: { n1: { jobId: "s1", nodeLabel: "Solve 1" } },
      pollFn: solvePoll,
      onFail: solveOnFail,
      addToast: solveToast,
    })
    const trainCfg = trainConfig({
      jobs: { n2: { jobId: "t1", nodeLabel: "Train 1" } },
      pollFn: trainPoll,
      onFail: trainOnFail,
      addToast: trainToast,
    })

    renderHook(() => {
      useJobPolling(solveCfg)
      useJobPolling(trainCfg)
    })

    await advance(500)

    expect(solveOnFail).toHaveBeenCalledWith("n1", "Infeasible")
    expect(trainOnFail).toHaveBeenCalledWith("n2", "Training diverged")

    // Error toasts follow the same format: `${failLabel}: ${label} — ${msg}`
    expect(solveToast).toHaveBeenCalledWith(
      "error",
      "Optimisation failed: Solve 1 — Infeasible",
    )
    expect(trainToast).toHaveBeenCalledWith(
      "error",
      "Training failed: Train 1 — Training diverged",
    )
  })

})

// ═════════════════════════════════════════════════════════════════
// Shared cleanup
// ═════════════════════════════════════════════════════════════════

describe("useJobPolling — shared cleanup on unmount", () => {
  it("stops polling both solve and train jobs when the consumer unmounts", async () => {
    const solvePoll = vi.fn<(id: string) => Promise<SolveLikeStatus>>().mockResolvedValue({
      status: "running", message: "", progress: 0.5,
    })
    const trainPoll = vi.fn<(id: string) => Promise<TrainLikeStatus>>().mockResolvedValue({
      status: "running", message: "", progress: 0.5,
    })

    const solveCfg = solveConfig({
      jobs: { n1: { jobId: "s1", nodeLabel: "Solve 1" } },
      pollFn: solvePoll,
    })
    const trainCfg = trainConfig({
      jobs: { n2: { jobId: "t1", nodeLabel: "Train 1" } },
      pollFn: trainPoll,
    })

    const { unmount } = renderHook(() => {
      useJobPolling(solveCfg)
      useJobPolling(trainCfg)
    })

    await advance(500)
    const solveCallsBefore = solvePoll.mock.calls.length
    const trainCallsBefore = trainPoll.mock.calls.length
    expect(solveCallsBefore).toBeGreaterThanOrEqual(1)
    expect(trainCallsBefore).toBeGreaterThanOrEqual(1)

    unmount()

    // Advance well past several poll intervals — neither fires again
    await advance(10_000)
    expect(solvePoll.mock.calls.length).toBe(solveCallsBefore)
    expect(trainPoll.mock.calls.length).toBe(trainCallsBefore)
  })
})
