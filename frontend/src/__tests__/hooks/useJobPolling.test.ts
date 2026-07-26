/**
 * Tests for useJobPolling — the generic polling hook extracted from
 * useBackgroundJobs.
 *
 * These tests exercise the hook directly (not through the orchestrator)
 * to verify: start/stop lifecycle, exponential backoff, max lifetime
 * timeout, cleanup on unmount, and cleanup on job removal.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act, cleanup } from "@testing-library/react"
import useJobPolling from "../../hooks/useJobPolling"
import type { UseJobPollingConfig } from "../../hooks/useJobPolling"

// ── Types for test jobs ──────────────────────────────────────────

interface TestJob {
  jobId: string
  nodeLabel: string
}

interface TestStatus {
  status: string
  progress: number
  message: string
  result?: { value: number }
}

// ── Helpers ──────────────────────────────────────────────────────

function makeConfig(
  overrides: Partial<UseJobPollingConfig<TestJob, TestStatus>> = {},
): UseJobPollingConfig<TestJob, TestStatus> {
  return {
    jobs: {},
    pollFn: vi.fn().mockResolvedValue({ status: "running", progress: 0.5, message: "Working" }),
    onProgress: vi.fn(),
    onComplete: vi.fn(),
    onFail: vi.fn(),
    labelFn: (job) => job.nodeLabel,
    jobIdFn: (job) => job.jobId,
    isComplete: (s) => s.status === "completed",
    isError: (s) => s.status === "error",
    getResult: (s) => (s.result ? s : undefined),
    getErrorMessage: (s) => s.message || "Unknown error",
    addToast: vi.fn(),
    successLabel: "Job complete",
    failLabel: "Job failed",
    ...overrides,
  }
}

async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

// ── Test suites ──────────────────────────────────────────────────

describe("useJobPolling", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  // ────────────────────────────────────────────────────────────────
  // Basic lifecycle
  // ────────────────────────────────────────────────────────────────

  describe("basic lifecycle", () => {
    it("starts polling when a job appears and calls onProgress", async () => {
      const pollFn = vi.fn<(jobId: string) => Promise<TestStatus>>().mockResolvedValue({
        status: "running",
        progress: 0.5,
        message: "Working",
      })
      const onProgress = vi.fn()

      const config = makeConfig({
        jobs: { n1: { jobId: "j1", nodeLabel: "Node 1" } },
        pollFn,
        onProgress,
      })

      renderHook(() => useJobPolling(config))

      // First poll at 500ms
      await advance(500)

      expect(pollFn).toHaveBeenCalledWith("j1")
      expect(onProgress).toHaveBeenCalledWith("n1", {
        status: "running",
        progress: 0.5,
        message: "Working",
      })
    })

    it("calls onComplete and shows success toast when job completes", async () => {
      const result = { value: 42 }
      const pollFn = vi.fn<(jobId: string) => Promise<TestStatus>>().mockResolvedValue({
        status: "completed",
        progress: 1.0,
        message: "Done",
        result,
      })
      const onComplete = vi.fn()
      const addToast = vi.fn()

      const config = makeConfig({
        jobs: { n1: { jobId: "j1", nodeLabel: "Node 1" } },
        pollFn,
        onComplete,
        addToast,
      })

      renderHook(() => useJobPolling(config))

      await advance(500)

      expect(onComplete).toHaveBeenCalledTimes(1)
      expect(onComplete).toHaveBeenCalledWith("n1", expect.objectContaining({ result }))
      expect(addToast).toHaveBeenCalledWith("success", "Job complete: Node 1")
    })

    it("calls onFail and shows error toast when API returns error status", async () => {
      const pollFn = vi.fn<(jobId: string) => Promise<TestStatus>>().mockResolvedValue({
        status: "error",
        progress: 0,
        message: "Infeasible",
      })
      const onFail = vi.fn()
      const addToast = vi.fn()

      const config = makeConfig({
        jobs: { n1: { jobId: "j1", nodeLabel: "Node 1" } },
        pollFn,
        onFail,
        addToast,
      })

      renderHook(() => useJobPolling(config))

      await advance(500)

      expect(onFail).toHaveBeenCalledWith("n1", "Infeasible", {
        status: "error",
        progress: 0,
        message: "Infeasible",
      })
      expect(addToast).toHaveBeenCalledWith("error", "Job failed: Node 1 — Infeasible")
    })

    it("stops polling after job completes (no more polls scheduled)", async () => {
      const pollFn = vi.fn<(jobId: string) => Promise<TestStatus>>().mockResolvedValue({
        status: "completed",
        progress: 1.0,
        message: "Done",
        result: { value: 1 },
      })

      const config = makeConfig({
        jobs: { n1: { jobId: "j1", nodeLabel: "Node 1" } },
        pollFn,
      })

      renderHook(() => useJobPolling(config))

      await advance(500)
      expect(pollFn).toHaveBeenCalledTimes(1)

      // Advance well past several poll intervals
      await advance(5000)
      expect(pollFn).toHaveBeenCalledTimes(1) // no additional calls
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Exponential backoff
  // ────────────────────────────────────────────────────────────────

  describe("progress throttling", () => {
    it("coalesces frequent running progress updates and emits the latest pending status", async () => {
      let progress = 0
      const pollFn = vi.fn<(jobId: string) => Promise<TestStatus>>()
        .mockImplementation(() => {
          progress = Math.round((progress + 0.1) * 10) / 10
          return Promise.resolve({
            status: "running",
            progress,
            message: `Progress ${progress}`,
          })
        })
      const onProgress = vi.fn()

      const config = makeConfig({
        jobs: { n1: { jobId: "j1", nodeLabel: "Node 1" } },
        pollFn,
        onProgress,
        progressThrottleMs: 2000,
      })

      renderHook(() => useJobPolling(config))

      await advance(500)
      expect(onProgress).toHaveBeenCalledTimes(1)
      expect(onProgress).toHaveBeenLastCalledWith("n1", expect.objectContaining({ progress: 0.1 }))

      await advance(1500)
      expect(pollFn.mock.calls.length).toBeGreaterThan(1)
      expect(onProgress).toHaveBeenCalledTimes(1)

      await advance(2000)
      expect(onProgress.mock.calls.length).toBeGreaterThanOrEqual(2)
      expect(onProgress.mock.calls.length).toBeLessThan(pollFn.mock.calls.length)
      expect(onProgress).toHaveBeenLastCalledWith("n1", expect.objectContaining({
        progress: expect.any(Number),
      }))
      expect(onProgress.mock.lastCall?.[1].progress).toBeGreaterThan(0.1)
    })
    it("does not delay completion behind a pending throttled progress update", async () => {
      const result = { value: 42 }
      const pollFn = vi.fn<(jobId: string) => Promise<TestStatus>>()
        .mockResolvedValueOnce({ status: "running", progress: 0.1, message: "Starting" })
        .mockResolvedValueOnce({ status: "running", progress: 0.2, message: "Almost there" })
        .mockResolvedValueOnce({
          status: "completed",
          progress: 1,
          message: "Done",
          result,
        })
      const onProgress = vi.fn()
      const onComplete = vi.fn()

      const config = makeConfig({
        jobs: { n1: { jobId: "j1", nodeLabel: "Node 1" } },
        pollFn,
        onProgress,
        onComplete,
        progressThrottleMs: 5000,
      })

      renderHook(() => useJobPolling(config))

      await advance(500)
      expect(onProgress).toHaveBeenCalledTimes(1)

      await advance(1000)
      expect(onProgress).toHaveBeenCalledTimes(1)
      expect(onComplete).not.toHaveBeenCalled()

      await advance(2000)
      expect(onComplete).toHaveBeenCalledTimes(1)
      expect(onComplete).toHaveBeenCalledWith("n1", expect.objectContaining({ result }))

      await advance(2000)
      expect(onProgress).toHaveBeenCalledTimes(1)
    })

    it("does not delay error statuses behind a pending throttled progress update", async () => {
      const pollFn = vi.fn<(jobId: string) => Promise<TestStatus>>()
        .mockResolvedValueOnce({ status: "running", progress: 0.1, message: "Starting" })
        .mockResolvedValueOnce({ status: "running", progress: 0.2, message: "Almost there" })
        .mockResolvedValueOnce({ status: "error", progress: 0.2, message: "Failed" })
      const onProgress = vi.fn()
      const onFail = vi.fn()

      const config = makeConfig({
        jobs: { n1: { jobId: "j1", nodeLabel: "Node 1" } },
        pollFn,
        onProgress,
        onFail,
        progressThrottleMs: 5000,
      })

      renderHook(() => useJobPolling(config))

      await advance(1500)
      expect(onProgress).toHaveBeenCalledTimes(1)
      expect(onFail).not.toHaveBeenCalled()

      await advance(2000)
      expect(onFail).toHaveBeenCalledTimes(1)
      expect(onFail).toHaveBeenCalledWith("n1", "Failed", {
        status: "error",
        progress: 0.2,
        message: "Failed",
      })

      await advance(2000)
      expect(onProgress).toHaveBeenCalledTimes(1)
    })
  })

  describe("exponential backoff", () => {
    it("ramps healthy in-progress polling to a 5 second steady-state interval", async () => {
      const callTimes: number[] = []
      const pollFn = vi.fn<(jobId: string) => Promise<TestStatus>>().mockImplementation(() => {
        callTimes.push(Date.now())
        return Promise.resolve({
          status: "running",
          progress: 0.5,
          message: "Working",
        })
      })
      const config = makeConfig({
        jobs: { n1: { jobId: "j1", nodeLabel: "Node 1" } },
        pollFn,
      })

      const startedAt = Date.now()
      renderHook(() => useJobPolling(config))
      await advance(12_500)

      expect(callTimes).toHaveLength(5)
      expect(callTimes.map((time, index) => (
        index === 0 ? time - startedAt : time - callTimes[index - 1]
      ))).toEqual([500, 1_000, 2_000, 4_000, 5_000])
    })

    it("increases delay after network errors", async () => {
      const callTimes: number[] = []
      const pollFn = vi.fn<(jobId: string) => Promise<TestStatus>>().mockImplementation(() => {
        callTimes.push(Date.now())
        if (callTimes.length <= 3) {
          return Promise.reject(new Error("Network error"))
        }
        return Promise.resolve({
          status: "completed",
          progress: 1.0,
          message: "Done",
          result: { value: 1 },
        })
      })

      const config = makeConfig({
        jobs: { n1: { jobId: "j1", nodeLabel: "Node 1" } },
        pollFn,
      })

      renderHook(() => useJobPolling(config))

      // Error 1 at +500ms, Error 2 at +500+1000=+1500ms, Error 3 at +1500+2000=+3500ms
      // Success at +3500+4000=+7500ms
      await advance(8000)

      expect(callTimes.length).toBe(4)

      // Verify backoff: gap between call 1->2 should be >= 1000ms (500 * 2^1)
      const gap1 = callTimes[1] - callTimes[0]
      expect(gap1).toBeGreaterThanOrEqual(1000)

      // Gap 2->3 should be >= 2000ms (500 * 2^2)
      const gap2 = callTimes[2] - callTimes[1]
      expect(gap2).toBeGreaterThanOrEqual(2000)
    })

    it("shows warning toast after 5 consecutive failures", async () => {
      const pollFn = vi.fn<(jobId: string) => Promise<TestStatus>>().mockRejectedValue(
        new Error("Network error"),
      )
      const addToast = vi.fn()

      const config = makeConfig({
        jobs: { n1: { jobId: "j1", nodeLabel: "Node 1" } },
        pollFn,
        addToast,
      })

      renderHook(() => useJobPolling(config))

      // Advance enough for 5 error polls:
      // Poll 1: +500, Poll 2: +1500, Poll 3: +3500, Poll 4: +7500, Poll 5: +12500
      await advance(13000)

      const warningCalls = addToast.mock.calls.filter(
        ([type]: unknown[]) => type === "warning",
      )
      expect(warningCalls.length).toBe(1)
      expect(warningCalls[0][1]).toContain("Polling is struggling")
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Max lifetime timeout
  // ────────────────────────────────────────────────────────────────

  describe("max lifetime timeout", () => {
    it("fails job after 24 hours", async () => {
      const pollFn = vi.fn<(jobId: string) => Promise<TestStatus>>().mockResolvedValue({
        status: "running",
        progress: 0.5,
        message: "Working",
      })
      const onFail = vi.fn()
      const addToast = vi.fn()

      const config = makeConfig({
        jobs: { n1: { jobId: "j1", nodeLabel: "Node 1" } },
        pollFn,
        onFail,
        addToast,
      })

      renderHook(() => useJobPolling(config))

      // MAX_LIFETIME_MS = 24 * 60 * 60 * 1000
      await advance(86_400_500)

      expect(onFail).toHaveBeenCalledWith("n1", "Job timed out after 24 hours")
      expect(addToast).toHaveBeenCalledWith(
        "error",
        "Job failed: Node 1 — Job timed out after 24 hours",
      )
    }, 120_000)
  })

  // ────────────────────────────────────────────────────────────────
  // Cleanup
  // ────────────────────────────────────────────────────────────────

  describe("cleanup", () => {
    it("does not publish stale progress when a nodeId is removed and re-added before an old poll resolves", async () => {
      let resolveOldPoll!: (status: TestStatus) => void
      const pollFn = vi.fn<(jobId: string) => Promise<TestStatus>>()
        .mockImplementationOnce(() => new Promise((resolve) => { resolveOldPoll = resolve }))
        .mockResolvedValue({ status: "running", progress: 0.9, message: "New job progress" })
      const onProgress = vi.fn()

      const { rerender } = renderHook(
        (props: { config: UseJobPollingConfig<TestJob, TestStatus> }) =>
          useJobPolling(props.config),
        {
          initialProps: {
            config: makeConfig({
              jobs: { n1: { jobId: "old-job", nodeLabel: "Node 1" } },
              pollFn,
              onProgress,
            }),
          },
        },
      )

      await advance(500)
      expect(pollFn).toHaveBeenCalledWith("old-job")

      rerender({
        config: makeConfig({
          jobs: {},
          pollFn,
          onProgress,
        }),
      })
      rerender({
        config: makeConfig({
          jobs: { n1: { jobId: "new-job", nodeLabel: "Node 1" } },
          pollFn,
          onProgress,
        }),
      })

      await act(async () => {
        resolveOldPoll({ status: "running", progress: 0.1, message: "Old job progress" })
        await Promise.resolve()
      })

      expect(onProgress).not.toHaveBeenCalledWith("n1", expect.objectContaining({
        message: "Old job progress",
      }))

      await advance(500)
      expect(pollFn).toHaveBeenCalledWith("new-job")
      expect(onProgress).toHaveBeenCalledWith("n1", expect.objectContaining({
        message: "New job progress",
      }))
    })

    it("restarts polling when the same nodeId is assigned a different jobId", async () => {
      let resolveOldPoll!: (status: TestStatus) => void
      const pollFn = vi.fn<(jobId: string) => Promise<TestStatus>>()
        .mockImplementationOnce(() => new Promise((resolve) => { resolveOldPoll = resolve }))
        .mockResolvedValue({ status: "running", progress: 0.8, message: "Replacement job progress" })
      const onProgress = vi.fn()

      const { rerender } = renderHook(
        (props: { config: UseJobPollingConfig<TestJob, TestStatus> }) =>
          useJobPolling(props.config),
        {
          initialProps: {
            config: makeConfig({
              jobs: { n1: { jobId: "old-job", nodeLabel: "Node 1" } },
              pollFn,
              onProgress,
            }),
          },
        },
      )

      await advance(500)
      expect(pollFn).toHaveBeenCalledWith("old-job")

      rerender({
        config: makeConfig({
          jobs: { n1: { jobId: "new-job", nodeLabel: "Node 1" } },
          pollFn,
          onProgress,
        }),
      })

      await act(async () => {
        resolveOldPoll({ status: "running", progress: 0.1, message: "Old job progress" })
        await Promise.resolve()
      })

      expect(onProgress).not.toHaveBeenCalledWith("n1", expect.objectContaining({
        message: "Old job progress",
      }))

      await advance(500)
      expect(pollFn).toHaveBeenCalledWith("new-job")
      expect(onProgress).toHaveBeenCalledWith("n1", expect.objectContaining({
        message: "Replacement job progress",
      }))
    })

    it("does not publish stale warning toasts when an old poll rejects after a nodeId is re-added", async () => {
      let rejectOldPoll!: (error: Error) => void
      const pollFn = vi.fn<(jobId: string) => Promise<TestStatus>>()
        .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectOldPoll = reject }))
        .mockResolvedValue({ status: "running", progress: 0.9, message: "New job progress" })
      const addToast = vi.fn()

      const { rerender } = renderHook(
        (props: { config: UseJobPollingConfig<TestJob, TestStatus> }) =>
          useJobPolling(props.config),
        {
          initialProps: {
            config: makeConfig({
              jobs: { n1: { jobId: "old-job", nodeLabel: "Old label" } },
              pollFn,
              addToast,
            }),
          },
        },
      )

      await advance(500)
      expect(pollFn).toHaveBeenCalledWith("old-job")

      rerender({
        config: makeConfig({
          jobs: {},
          pollFn,
          addToast,
        }),
      })
      rerender({
        config: makeConfig({
          jobs: { n1: { jobId: "new-job", nodeLabel: "New label" } },
          pollFn,
          addToast,
        }),
      })

      await act(async () => {
        rejectOldPoll(new Error("old poll failed"))
        await Promise.resolve()
      })

      expect(addToast).not.toHaveBeenCalled()

      await advance(500)
      expect(pollFn).toHaveBeenCalledWith("new-job")
      expect(addToast).not.toHaveBeenCalled()
    })

    it("stops polling when job is removed from the jobs map via rerender", async () => {
      let callCount = 0
      const pollFn = vi.fn<(jobId: string) => Promise<TestStatus>>().mockImplementation(() => {
        callCount++
        return Promise.resolve({ status: "running", progress: 0.5, message: "Working" })
      })

      const initialConfig = makeConfig({
        jobs: { n1: { jobId: "j1", nodeLabel: "Node 1" } },
        pollFn,
      })

      const { rerender } = renderHook(
        (props: { config: UseJobPollingConfig<TestJob, TestStatus> }) =>
          useJobPolling(props.config),
        { initialProps: { config: initialConfig } },
      )

      // Let first poll happen
      await advance(500)
      const afterFirst = callCount
      expect(afterFirst).toBeGreaterThanOrEqual(1)

      // Remove the job
      const updatedConfig = makeConfig({ jobs: {}, pollFn })
      rerender({ config: updatedConfig })

      const afterCleanup = callCount

      // Advance significantly -- no more polls
      await advance(5000)
      expect(callCount).toBeLessThanOrEqual(afterCleanup + 1)
    })

    it("clears all timeouts on unmount", async () => {
      const pollFn = vi.fn<(jobId: string) => Promise<TestStatus>>().mockResolvedValue({
        status: "running",
        progress: 0.5,
        message: "Working",
      })

      const config = makeConfig({
        jobs: { n1: { jobId: "j1", nodeLabel: "Node 1" } },
        pollFn,
      })

      const { unmount } = renderHook(() => useJobPolling(config))

      await advance(500)
      const callsBeforeUnmount = pollFn.mock.calls.length

      unmount()

      // Advance well past several intervals
      await advance(10_000)

      // No new calls after unmount
      expect(pollFn.mock.calls.length).toBe(callsBeforeUnmount)
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Multiple independent jobs
  // ────────────────────────────────────────────────────────────────

  describe("multiple jobs", () => {
    it("polls multiple jobs independently", async () => {
      const pollCalls: string[] = []
      const pollFn = vi.fn<(jobId: string) => Promise<TestStatus>>().mockImplementation((jobId) => {
        pollCalls.push(jobId)
        return Promise.resolve({ status: "running", progress: 0.5, message: "Working" })
      })

      const config = makeConfig({
        jobs: {
          n1: { jobId: "j1", nodeLabel: "Node 1" },
          n2: { jobId: "j2", nodeLabel: "Node 2" },
        },
        pollFn,
      })

      renderHook(() => useJobPolling(config))

      await advance(500)

      // Both jobs should have been polled
      expect(pollCalls).toContain("j1")
      expect(pollCalls).toContain("j2")
    })
  })
})
