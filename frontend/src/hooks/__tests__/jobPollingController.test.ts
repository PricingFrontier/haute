import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  JobPollingController,
  JobWaitTimeoutError,
  waitForJob,
  type JobPollingConfig,
} from "../jobPollingController"

interface Job { jobId: string; nodeLabel: string }
interface Status { status: string; progress: number; iteration?: number }

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

describe("JobPollingController backoff", () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  const running = (iteration: number): Status => ({ status: "running", progress: 0, iteration })
  const byIteration = (status: Status) => String(status.iteration)

  /** The delay before each of the first `statuses.length` polls; the last status repeats after that. */
  async function pollGaps(statuses: readonly Status[], progressKey?: (status: Status) => string): Promise<number[]> {
    const times: number[] = []
    const pollFn = vi.fn(() => {
      times.push(Date.now())
      return Promise.resolve(statuses[Math.min(times.length, statuses.length) - 1])
    })
    const controller = new JobPollingController(config({ pollFn, progressKey }))
    const startedAt = Date.now()
    controller.reconcile()
    await advance(30_000)
    controller.dispose()
    return times.slice(0, statuses.length).map((time, i) => time - (i === 0 ? startedAt : times[i - 1]))
  }

  it("polls at least once a second while the progress key advances on every poll", async () => {
    const gaps = await pollGaps([1, 2, 3, 4, 5, 6, 7, 8].map(running), byIteration)

    expect(gaps).toEqual([500, 1_000, 1_000, 1_000, 1_000, 1_000, 1_000, 1_000])
  })

  it("backs off to 5 s while the progress key holds, and returns to the base interval when it moves", async () => {
    const gaps = await pollGaps([1, 1, 1, 1, 1, 2, 3, 4].map(running), byIteration)

    expect(gaps).toEqual([500, 1_000, 2_000, 4_000, 5_000, 5_000, 500, 1_000])
  })

  it("backs off to 5 s without a progress key, even while progress advances", async () => {
    const gaps = await pollGaps([1, 2, 3, 4, 5, 6].map(running))

    expect(gaps).toEqual([500, 1_000, 2_000, 4_000, 5_000, 5_000])
  })
})

describe("waitForJob", () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  const isTerminal = (status: Status) => status.status !== "running"

  it("polls at once, then at the interval, and resolves with the terminal status", async () => {
    const onStatus = vi.fn()
    const poll = vi.fn()
      .mockResolvedValueOnce({ status: "running", progress: 0.1 })
      .mockResolvedValueOnce({ status: "running", progress: 0.6 })
      .mockResolvedValueOnce({ status: "completed", progress: 1 })
    const wait = waitForJob({ poll, isTerminal, intervalMs: 800, onStatus })

    await advance(0)
    expect(poll).toHaveBeenCalledTimes(1)
    await advance(799)
    expect(poll).toHaveBeenCalledTimes(1)
    await advance(1)
    expect(poll).toHaveBeenCalledTimes(2)
    await advance(800)

    await expect(wait).resolves.toEqual({ status: "completed", progress: 1 })
    expect(onStatus.mock.calls.map(([status]) => status.progress)).toEqual([0.1, 0.6])
    expect(vi.getTimerCount()).toBe(0)
  })

  it("rejects with an AbortError, aborts the request in flight and polls no more", async () => {
    const signals: AbortSignal[] = []
    const poll = vi.fn((signal: AbortSignal) => {
      signals.push(signal)
      return new Promise<Status>(() => {})
    })
    const owner = new AbortController()
    const wait = waitForJob({ poll, isTerminal, intervalMs: 800, signal: owner.signal })
    await advance(0)

    owner.abort()

    await expect(wait).rejects.toMatchObject({ name: "AbortError" })
    expect(signals[0].aborted).toBe(true)
    await advance(10_000)
    expect(poll).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it("does not poll for an already aborted signal", async () => {
    const poll = vi.fn()
    const owner = new AbortController()
    owner.abort()

    await expect(
      waitForJob({ poll, isTerminal, intervalMs: 800, signal: owner.signal }),
    ).rejects.toMatchObject({ name: "AbortError" })
    expect(poll).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
  })

  it("rejects with JobWaitTimeoutError at its deadline and stops polling", async () => {
    const signals: AbortSignal[] = []
    const poll = vi.fn((signal: AbortSignal) => {
      signals.push(signal)
      return Promise.resolve({ status: "running", progress: 0 })
    })
    const wait = waitForJob({ poll, isTerminal, intervalMs: 800, timeoutMs: 2_000 })
    const outcome = wait.catch((error: unknown) => error)

    await advance(2_000)

    const error = await outcome
    expect(error).toBeInstanceOf(JobWaitTimeoutError)
    expect((error as JobWaitTimeoutError).timeoutMs).toBe(2_000)
    const polls = poll.mock.calls.length
    await advance(10_000)
    expect(poll).toHaveBeenCalledTimes(polls)
    expect(signals.every((signal) => signal.aborted)).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
  })

  it("is bounded by the controller's lifetime when the caller gives no deadline", async () => {
    const poll = vi.fn(() => new Promise<Status>(() => {}))
    const outcome = waitForJob({ poll, isTerminal, intervalMs: 800 }).catch((error: unknown) => error)

    await advance(24 * 60 * 60 * 1_000)

    expect(await outcome).toBeInstanceOf(JobWaitTimeoutError)
    expect(vi.getTimerCount()).toBe(0)
  })

  it("rejects with the poll's error without retrying", async () => {
    const failure = new Error("status unavailable")
    const poll = vi.fn().mockRejectedValue(failure)

    await expect(waitForJob({ poll, isTerminal, intervalMs: 800 })).rejects.toBe(failure)
    await advance(10_000)
    expect(poll).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it("stops when onStatus aborts the owner's signal", async () => {
    const owner = new AbortController()
    const poll = vi.fn().mockResolvedValue({ status: "running", progress: 0 })

    const wait = waitForJob({
      poll, isTerminal, intervalMs: 800, signal: owner.signal,
      onStatus: () => owner.abort(),
    })

    await expect(wait).rejects.toMatchObject({ name: "AbortError" })
    await advance(10_000)
    expect(poll).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it("rejects with an error that onStatus throws", async () => {
    const failure = new Error("not current")
    const poll = vi.fn().mockResolvedValue({ status: "running", progress: 0 })

    await expect(waitForJob({
      poll, isTerminal, intervalMs: 800,
      onStatus: () => { throw failure },
    })).rejects.toBe(failure)
    expect(vi.getTimerCount()).toBe(0)
  })
})
