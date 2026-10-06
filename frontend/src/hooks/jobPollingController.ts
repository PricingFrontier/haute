const BASE_INTERVAL_MS = 500
const PROGRESS_INTERVAL_MS = 1_000
const MAX_INTERVAL_MS = 5_000
const MAX_LIFETIME_MS = 24 * 60 * 60 * 1_000
const POLL_TIMEOUT_MS = 30_000
const CONSECUTIVE_FAILURES_FOR_TOAST = 5

export interface JobPollingConfig<TJob, TStatus> {
  jobs: Record<string, TJob>
  pollFn: (jobId: string, signal: AbortSignal) => Promise<TStatus>
  onProgress: (nodeId: string, status: TStatus) => void
  progressThrottleMs?: number
  /**
   * Opts into progress-aware backoff: the signature of the progress a status
   * shows. While it changes from one poll to the next the interval stays at
   * or below `PROGRESS_INTERVAL_MS`; it backs off to `MAX_INTERVAL_MS` only
   * while unchanged or after errors. Without it every poll backs off.
   */
  progressKey?: (status: TStatus) => string
  onComplete: (nodeId: string, result: TStatus) => void
  onFail: (nodeId: string, errorMsg: string, terminalStatus?: TStatus) => void
  labelFn: (job: TJob) => string
  jobIdFn: (job: TJob) => string
  isComplete: (status: TStatus) => boolean
  isError: (status: TStatus) => boolean
  getResult: (status: TStatus) => TStatus | undefined
  getErrorMessage: (status: TStatus) => string
  getTerminalPollErrorMessage?: (error: unknown) => string | undefined
  addToast: (type: "success" | "error" | "warning" | "info", text: string) => void
  successLabel: string
  failLabel: string
}

/** An awaited job wait passed its deadline without a terminal status. */
export class JobWaitTimeoutError extends Error {
  override name = "JobWaitTimeout"
  readonly timeoutMs: number

  constructor(timeoutMs: number) {
    super(`The job did not finish within ${Math.round(timeoutMs / 1_000)} seconds.`)
    this.timeoutMs = timeoutMs
  }
}

export interface WaitForJobOptions<TStatus> {
  /** Read the job's status; the signal ends with the wait. */
  poll: (signal: AbortSignal) => Promise<TStatus>
  isTerminal: (status: TStatus) => boolean
  /** Delay between one response and the next request. */
  intervalMs: number
  /** The lifetime of the component or request that owns the wait. */
  signal?: AbortSignal
  /** Defaults to the polling lifetime shared with the controller. */
  timeoutMs?: number
  /** Each non-terminal status; it may abort `signal` or throw to end the wait. */
  onStatus?: (status: TStatus) => void
}

function jobWaitAborted(): DOMException {
  return new DOMException("The job wait was aborted.", "AbortError")
}

/**
 * Wait for one job that an operation started and awaits, with no store entry
 * to track it. Rejects on the first poll error, on `signal`, and at the
 * deadline; the job itself is left to the caller that owns it.
 */
export function waitForJob<TStatus>({
  poll,
  isTerminal,
  intervalMs,
  signal,
  timeoutMs = MAX_LIFETIME_MS,
  onStatus,
}: WaitForJobOptions<TStatus>): Promise<TStatus> {
  if (signal?.aborted) return Promise.reject(jobWaitAborted())
  return new Promise<TStatus>((resolve, reject) => {
    const requests = new AbortController()
    let settled = false
    let pollTimeoutId: ReturnType<typeof setTimeout> | undefined
    const settle = (finish: () => void) => {
      if (settled) return
      settled = true
      clearTimeout(pollTimeoutId)
      clearTimeout(deadlineId)
      signal?.removeEventListener("abort", onAbort)
      requests.abort()
      finish()
    }
    const onAbort = () => settle(() => reject(jobWaitAborted()))
    const deadlineId = setTimeout(
      () => settle(() => reject(new JobWaitTimeoutError(timeoutMs))),
      timeoutMs,
    )
    signal?.addEventListener("abort", onAbort, { once: true })

    const request = () => {
      Promise.resolve()
        .then(() => poll(requests.signal))
        .then((status) => {
          if (settled) return
          if (isTerminal(status)) {
            settle(() => resolve(status))
            return
          }
          onStatus?.(status)
          if (!settled) pollTimeoutId = setTimeout(request, intervalMs)
        })
        .catch((error: unknown) => settle(() => reject(error)))
    }
    request()
  })
}

interface JobPollerState<TStatus> {
  jobId: string
  startedAt: number
  intervalMs: number
  /** The previous status's `progressKey`; unset until the first status. */
  lastProgressKey?: string
  consecutiveErrors: number
  toastedWarning: boolean
  lastProgressPublishedAt: number
  hasPendingProgress: boolean
  pendingProgress?: TStatus
  pollTimeoutId?: ReturnType<typeof setTimeout>
  progressTimeoutId?: ReturnType<typeof setTimeout>
  requestTimeoutId?: ReturnType<typeof setTimeout>
  abortController?: AbortController
}

/** Framework-free authority for a set of independent background-job pollers. */
export class JobPollingController<TJob, TStatus> {
  private config: JobPollingConfig<TJob, TStatus>
  private readonly pollers: Record<string, JobPollerState<TStatus>> = {}
  private disposed = false

  constructor(config: JobPollingConfig<TJob, TStatus>) {
    this.config = config
  }

  updateConfig(config: JobPollingConfig<TJob, TStatus>): void {
    this.config = config
    // React StrictMode deliberately replays effect cleanup and setup. An
    // explicit new configuration is the only operation that may reactivate a
    // disposed controller; a real unmount performs no subsequent update.
    this.disposed = false
  }

  reconcile(): void {
    if (this.disposed) return
    for (const [nodeId, state] of Object.entries(this.pollers)) {
      const job = this.config.jobs[nodeId]
      if (!job || this.config.jobIdFn(job) !== state.jobId) this.retire(nodeId, state)
    }
    for (const [nodeId, job] of Object.entries(this.config.jobs)) {
      if (this.pollers[nodeId]) continue
      const state: JobPollerState<TStatus> = {
        jobId: this.config.jobIdFn(job),
        startedAt: Date.now(),
        intervalMs: BASE_INTERVAL_MS,
        consecutiveErrors: 0,
        toastedWarning: false,
        lastProgressPublishedAt: 0,
        hasPendingProgress: false,
      }
      this.pollers[nodeId] = state
      this.schedulePoll(nodeId, state)
    }
  }

  dispose(): void {
    if (this.disposed) return
    this.disposed = true
    for (const [nodeId, state] of Object.entries(this.pollers)) this.retire(nodeId, state)
  }

  private isCurrent(nodeId: string, state: JobPollerState<TStatus>): boolean {
    const job = this.config.jobs[nodeId]
    return !this.disposed && this.pollers[nodeId] === state && job != null && this.config.jobIdFn(job) === state.jobId
  }

  private clearPendingProgress(state: JobPollerState<TStatus>): void {
    clearTimeout(state.progressTimeoutId)
    state.progressTimeoutId = undefined
    state.hasPendingProgress = false
    state.pendingProgress = undefined
  }

  private retire(nodeId: string, state: JobPollerState<TStatus>): void {
    if (this.pollers[nodeId] !== state) return
    clearTimeout(state.pollTimeoutId)
    clearTimeout(state.requestTimeoutId)
    this.clearPendingProgress(state)
    state.abortController?.abort()
    state.abortController = undefined
    delete this.pollers[nodeId]
  }

  private schedulePoll(nodeId: string, state: JobPollerState<TStatus>): void {
    if (!this.isCurrent(nodeId, state)) return
    const elapsed = Date.now() - state.startedAt
    if (elapsed >= MAX_LIFETIME_MS) {
      this.failForLifetime(nodeId, state)
      return
    }
    state.pollTimeoutId = setTimeout(
      () => this.poll(nodeId, state),
      Math.min(state.intervalMs, MAX_LIFETIME_MS - elapsed),
    )
  }

  private failForLifetime(nodeId: string, state: JobPollerState<TStatus>): void {
    if (!this.isCurrent(nodeId, state)) return
    const job = this.config.jobs[nodeId]
    this.retire(nodeId, state)
    this.config.onFail(nodeId, "Job timed out after 24 hours")
    this.config.addToast("error", `${this.config.failLabel}: ${this.config.labelFn(job)} - Job timed out after 24 hours`)
  }

  private poll(nodeId: string, state: JobPollerState<TStatus>): void {
    if (!this.isCurrent(nodeId, state)) return
    if (Date.now() - state.startedAt >= MAX_LIFETIME_MS) {
      this.failForLifetime(nodeId, state)
      return
    }
    const abortController = new AbortController()
    state.abortController = abortController
    const timeoutError = new Error("Poll request timed out")
    const timeout = new Promise<never>((_, reject) => {
      state.requestTimeoutId = setTimeout(() => {
        abortController.abort()
        reject(timeoutError)
      }, POLL_TIMEOUT_MS)
    })
    const request = Promise.resolve().then(
      () => this.config.pollFn(state.jobId, abortController.signal),
    )
    void Promise.race([request, timeout])
      .then((status) => this.handleStatus(nodeId, state, status))
      .catch((error: unknown) => this.handlePollError(nodeId, state, error))
      .finally(() => {
        clearTimeout(state.requestTimeoutId)
        state.requestTimeoutId = undefined
        if (state.abortController === abortController) state.abortController = undefined
      })
  }

  private handleStatus(nodeId: string, state: JobPollerState<TStatus>, status: TStatus): void {
    if (!this.isCurrent(nodeId, state)) return
    state.consecutiveErrors = 0
    state.toastedWarning = false
    const job = this.config.jobs[nodeId]
    if (this.config.isComplete(status) || this.config.isError(status)) {
      this.retire(nodeId, state)
      if (this.config.isComplete(status) && this.config.getResult(status)) {
        this.config.onComplete(nodeId, this.config.getResult(status)!)
        this.config.addToast("success", `${this.config.successLabel}: ${this.config.labelFn(job)}`)
      } else {
        const message = this.config.getErrorMessage(status) || "Unknown error"
        this.config.onFail(nodeId, message, status)
        this.config.addToast("error", `${this.config.failLabel}: ${this.config.labelFn(job)} - ${message}`)
      }
      return
    }
    state.intervalMs = this.nextStatusInterval(state, status)
    this.schedulePoll(nodeId, state)
    this.queueProgress(nodeId, state, status)
  }

  /**
   * Every status doubles the interval up to `MAX_INTERVAL_MS`, unless the
   * caller keys progress and it moved: then the interval doubles only up to
   * `PROGRESS_INTERVAL_MS`, and one that had backed off beyond that returns to
   * the base interval.
   */
  private nextStatusInterval(state: JobPollerState<TStatus>, status: TStatus): number {
    const progressKey = this.config.progressKey
    if (!progressKey) return Math.min(state.intervalMs * 2, MAX_INTERVAL_MS)
    const key = progressKey(status)
    const moved = key !== state.lastProgressKey
    state.lastProgressKey = key
    if (!moved) return Math.min(state.intervalMs * 2, MAX_INTERVAL_MS)
    if (state.intervalMs > PROGRESS_INTERVAL_MS) return BASE_INTERVAL_MS
    return Math.min(state.intervalMs * 2, PROGRESS_INTERVAL_MS)
  }

  private handlePollError(nodeId: string, state: JobPollerState<TStatus>, error: unknown): void {
    if (!this.isCurrent(nodeId, state)) return
    const job = this.config.jobs[nodeId]
    const terminalMessage = this.config.getTerminalPollErrorMessage?.(error)
    if (terminalMessage) {
      this.retire(nodeId, state)
      this.config.onFail(nodeId, terminalMessage)
      this.config.addToast("error", `${this.config.failLabel}: ${this.config.labelFn(job)} - ${terminalMessage}`)
      return
    }
    state.consecutiveErrors += 1
    // WHY no toast: transient poll errors self-heal on the next interval; a
    // warning toast fires below once failures persist past the threshold.
    console.warn(`${this.config.failLabel} poll failed (attempt ${state.consecutiveErrors}, will retry):`, error)
    if (state.consecutiveErrors >= CONSECUTIVE_FAILURES_FOR_TOAST && !state.toastedWarning) {
      state.toastedWarning = true
      this.config.addToast("warning", `Polling is struggling for ${this.config.labelFn(job)} - ${state.consecutiveErrors} consecutive errors`)
    }
    state.intervalMs = Math.min(state.intervalMs * 2, MAX_INTERVAL_MS)
    this.schedulePoll(nodeId, state)
  }

  private queueProgress(nodeId: string, state: JobPollerState<TStatus>, status: TStatus): void {
    const throttleMs = this.config.progressThrottleMs ?? 0
    const elapsed = Date.now() - state.lastProgressPublishedAt
    if (throttleMs <= 0 || state.lastProgressPublishedAt === 0 || elapsed >= throttleMs) {
      this.publishProgress(nodeId, state, status)
      return
    }
    state.pendingProgress = status
    state.hasPendingProgress = true
    if (state.progressTimeoutId) return
    state.progressTimeoutId = setTimeout(() => {
      state.progressTimeoutId = undefined
      if (state.hasPendingProgress) {
        this.publishProgress(nodeId, state, state.pendingProgress as TStatus)
      }
    }, throttleMs - elapsed)
  }

  private publishProgress(nodeId: string, state: JobPollerState<TStatus>, status: TStatus): void {
    this.clearPendingProgress(state)
    if (!this.isCurrent(nodeId, state)) return
    state.lastProgressPublishedAt = Date.now()
    this.config.onProgress(nodeId, status)
  }
}
