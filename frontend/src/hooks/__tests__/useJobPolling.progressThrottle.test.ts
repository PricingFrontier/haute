import { act, cleanup, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import useJobPolling, { type UseJobPollingConfig } from "../useJobPolling"

interface Job {
  jobId: string
  nodeLabel: string
}

interface Status {
  status: string
  message: string
  progress: number
  result?: { converged: boolean }
}

function config(overrides: Partial<UseJobPollingConfig<Job, Status>>): UseJobPollingConfig<Job, Status> {
  return {
    jobs: { n1: { jobId: "job-1", nodeLabel: "Job 1" } },
    pollFn: vi.fn(),
    onProgress: vi.fn(),
    onComplete: vi.fn(),
    onFail: vi.fn(),
    labelFn: (job) => job.nodeLabel,
    jobIdFn: (job) => job.jobId,
    isComplete: (status) => status.status === "completed",
    isError: (status) => status.status === "error",
    getResult: (status) => (status.result ? status : undefined),
    getErrorMessage: (status) => status.message || "Unknown error",
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

describe("useJobPolling progress throttling", () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it("coalesces in-progress updates to the configured visible-progress interval", async () => {
    const onProgress = vi.fn()
    const pollFn = vi.fn<(id: string) => Promise<Status>>()
      .mockResolvedValueOnce({ status: "running", message: "10%", progress: 0.1 })
      .mockResolvedValueOnce({ status: "running", message: "20%", progress: 0.2 })
      .mockResolvedValueOnce({ status: "running", message: "30%", progress: 0.3 })

    renderHook(() => useJobPolling(config({
      pollFn,
      onProgress,
      progressThrottleMs: 1_000,
    })))

    await advance(500)
    expect(onProgress).toHaveBeenCalledTimes(1)
    expect(onProgress).toHaveBeenLastCalledWith("n1", expect.objectContaining({ progress: 0.1 }))

    await advance(500)
    expect(onProgress).toHaveBeenCalledTimes(1)

    await advance(500)
    expect(onProgress).toHaveBeenCalledTimes(2)
    expect(onProgress).toHaveBeenLastCalledWith("n1", expect.objectContaining({ progress: 0.3 }))
  })

  it("drops pending throttled progress when the job is removed", async () => {
    const onProgress = vi.fn()
    const pollFn = vi.fn<(id: string) => Promise<Status>>()
      .mockResolvedValueOnce({ status: "running", message: "10%", progress: 0.1 })
      .mockResolvedValueOnce({ status: "running", message: "20%", progress: 0.2 })

    const initialConfig = config({
      pollFn,
      onProgress,
      progressThrottleMs: 5_000,
    })
    const { rerender } = renderHook(
      (pollingConfig: UseJobPollingConfig<Job, Status>) => useJobPolling(pollingConfig),
      { initialProps: initialConfig },
    )

    await advance(500)
    expect(onProgress).toHaveBeenCalledTimes(1)
    expect(onProgress).toHaveBeenLastCalledWith("n1", expect.objectContaining({ progress: 0.1 }))

    await advance(500)
    expect(onProgress).toHaveBeenCalledTimes(1)

    rerender({
      ...initialConfig,
      jobs: {},
    })

    expect(vi.getTimerCount()).toBe(0)

    await advance(5_000)
    expect(onProgress).toHaveBeenCalledTimes(1)
  })

  it("ignores in-flight progress returned by a superseded job", async () => {
    const onProgress = vi.fn()
    let resolveOldPoll: (status: Status) => void = () => {}
    const oldPoll = new Promise<Status>((resolve) => {
      resolveOldPoll = resolve
    })
    const pollFn = vi.fn<(id: string) => Promise<Status>>()
      .mockImplementationOnce(() => oldPoll)
      .mockResolvedValue({ status: "running", message: "new job", progress: 0.2 })

    const initialConfig = config({
      pollFn,
      onProgress,
      jobs: { n1: { jobId: "job-1", nodeLabel: "Job 1" } },
    })
    const { rerender } = renderHook(
      (pollingConfig: UseJobPollingConfig<Job, Status>) => useJobPolling(pollingConfig),
      { initialProps: initialConfig },
    )

    await advance(500)
    expect(pollFn).toHaveBeenCalledWith("job-1")

    rerender({
      ...initialConfig,
      jobs: { n1: { jobId: "job-2", nodeLabel: "Job 2" } },
    })

    await act(async () => {
      resolveOldPoll({ status: "running", message: "old job", progress: 0.9 })
      await oldPoll
    })

    expect(onProgress).not.toHaveBeenCalled()

    await advance(500)
    expect(pollFn).toHaveBeenCalledWith("job-2")
    expect(onProgress).toHaveBeenCalledTimes(1)
    expect(onProgress).toHaveBeenLastCalledWith("n1", expect.objectContaining({
      message: "new job",
      progress: 0.2,
    }))
  })

  it("does not delay completion behind the in-progress throttle window", async () => {
    const onProgress = vi.fn()
    const onComplete = vi.fn()
    const pollFn = vi.fn<(id: string) => Promise<Status>>()
      .mockResolvedValueOnce({ status: "running", message: "10%", progress: 0.1 })
      .mockResolvedValueOnce({ status: "running", message: "20%", progress: 0.2 })
      .mockResolvedValueOnce({
        status: "completed",
        message: "Done",
        progress: 1,
        result: { converged: true },
      })

    renderHook(() => useJobPolling(config({
      pollFn,
      onProgress,
      onComplete,
      progressThrottleMs: 5_000,
    })))

    await advance(500)
    await advance(500)
    expect(onProgress).toHaveBeenCalledTimes(1)
    expect(onComplete).not.toHaveBeenCalled()

    await advance(500)
    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete).toHaveBeenCalledWith("n1", expect.objectContaining({ status: "completed" }))
    expect(onProgress).toHaveBeenCalledTimes(1)
  })

  it("does not delay API error status behind the in-progress throttle window", async () => {
    const onProgress = vi.fn()
    const onFail = vi.fn()
    const pollFn = vi.fn<(id: string) => Promise<Status>>()
      .mockResolvedValueOnce({ status: "running", message: "10%", progress: 0.1 })
      .mockResolvedValueOnce({ status: "running", message: "20%", progress: 0.2 })
      .mockResolvedValueOnce({ status: "error", message: "Infeasible", progress: 0.2 })

    renderHook(() => useJobPolling(config({
      pollFn,
      onProgress,
      onFail,
      progressThrottleMs: 5_000,
    })))

    await advance(500)
    await advance(500)
    expect(onProgress).toHaveBeenCalledTimes(1)
    expect(onFail).not.toHaveBeenCalled()

    await advance(500)
    expect(onFail).toHaveBeenCalledTimes(1)
    expect(onFail).toHaveBeenCalledWith("n1", "Infeasible", {
      status: "error",
      message: "Infeasible",
      progress: 0.2,
    })
    expect(onProgress).toHaveBeenCalledTimes(1)
  })
})
