import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { JobPollingController, type JobPollingConfig } from "../jobPollingController"

interface Job { jobId: string; nodeLabel: string }
interface Status { status: string; progress: number }

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void
  return { promise: new Promise<T>((done) => { resolve = done }), resolve }
}

function config(overrides: Partial<JobPollingConfig<Job, Status>> = {}): JobPollingConfig<Job, Status> {
  return {
    jobs: { node: { jobId: "old", nodeLabel: "Old" } },
    pollFn: vi.fn(), onProgress: vi.fn(), onComplete: vi.fn(), onFail: vi.fn(),
    labelFn: (job) => job.nodeLabel, jobIdFn: (job) => job.jobId,
    isComplete: (status) => status.status === "completed", isError: (status) => status.status === "error",
    getResult: () => undefined, getErrorMessage: () => "Unknown error", addToast: vi.fn(),
    successLabel: "Complete", failLabel: "Failed", ...overrides,
  }
}

async function advance(ms: number): Promise<void> { await vi.advanceTimersByTimeAsync(ms) }

describe("JobPollingController", () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it("aborts a replaced job and ignores its late resolve", async () => {
    const oldPoll = deferred<Status>()
    const signals: AbortSignal[] = []
    const pollFn = vi.fn((_: string, signal: AbortSignal) => {
      signals.push(signal)
      return signals.length === 1 ? oldPoll.promise : Promise.resolve({ status: "running", progress: 0.2 })
    })
    const onProgress = vi.fn()
    const controller = new JobPollingController(config({ pollFn, onProgress }))
    controller.reconcile()
    await advance(500)

    controller.updateConfig(config({ pollFn, onProgress, jobs: { node: { jobId: "new", nodeLabel: "New" } } }))
    controller.reconcile()
    expect(signals[0].aborted).toBe(true)

    oldPoll.resolve({ status: "running", progress: 0.9 })
    await Promise.resolve()
    expect(onProgress).not.toHaveBeenCalled()

    await advance(500)
    expect(onProgress).toHaveBeenCalledWith("node", { status: "running", progress: 0.2 })
  })

  it("aborts a removed job and drops pending throttled progress", async () => {
    const signals: AbortSignal[] = []
    const activePoll = deferred<Status>()
    const onProgress = vi.fn()
    const pollFn = vi.fn((_: string, signal: AbortSignal) => {
      signals.push(signal)
      if (signals.length === 3) return activePoll.promise
      return Promise.resolve({ status: "running", progress: signals.length / 10 })
    })
    const controller = new JobPollingController(config({ pollFn, onProgress, progressThrottleMs: 5_000 }))
    controller.reconcile()
    await advance(500)
    await advance(1_000)
    expect(onProgress).toHaveBeenCalledTimes(1)
    await advance(2_000)

    controller.updateConfig(config({ pollFn, onProgress, progressThrottleMs: 5_000, jobs: {} }))
    controller.reconcile()
    expect(signals[2].aborted).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
    await advance(5_000)
    expect(onProgress).toHaveBeenCalledTimes(1)
  })

  it("aborts every active request and clears all timers on dispose", async () => {
    const signals: AbortSignal[] = []
    const pollFn = vi.fn((_: string, signal: AbortSignal) => {
      signals.push(signal)
      return new Promise<Status>(() => {})
    })
    const controller = new JobPollingController(config({ pollFn, jobs: {
      first: { jobId: "first", nodeLabel: "First" }, second: { jobId: "second", nodeLabel: "Second" },
    } }))
    controller.reconcile()
    await advance(500)
    controller.dispose()

    expect(signals).toHaveLength(2)
    expect(signals.every((signal) => signal.aborted)).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
  })
})
