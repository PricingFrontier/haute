const BASE_INTERVAL_MS = 500
const MAX_INTERVAL_MS = 5_000
const MAX_LIFETIME_MS = 24 * 60 * 60 * 1_000
const POLL_TIMEOUT_MS = 30_000
const CONSECUTIVE_FAILURES_FOR_TOAST = 5

export interface JobPollingConfig<TJob, TStatus> {
  jobs: Record<string, TJob>
  pollFn: (jobId: string, signal: AbortSignal) => Promise<TStatus>
  onProgress: (nodeId: string, status: TStatus) => void
  progressThrottleMs?: number
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

interface JobPollerState<TStatus> {
  jobId: string
  startedAt: number
  intervalMs: number
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
    this.config.addToast("error", `${this.config.failLabel}: ${this.config.labelFn(job)} — Job timed out after 24 hours`)
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
        this.config.addToast("error", `${this.config.failLabel}: ${this.config.labelFn(job)} — ${message}`)
      }
      return
    }
    state.intervalMs = Math.min(state.intervalMs * 2, MAX_INTERVAL_MS)
    this.schedulePoll(nodeId, state)
    this.queueProgress(nodeId, state, status)
  }

  private handlePollError(nodeId: string, state: JobPollerState<TStatus>, error: unknown): void {
    if (!this.isCurrent(nodeId, state)) return
    const job = this.config.jobs[nodeId]
    const terminalMessage = this.config.getTerminalPollErrorMessage?.(error)
    if (terminalMessage) {
      this.retire(nodeId, state)
      this.config.onFail(nodeId, terminalMessage)
      this.config.addToast("error", `${this.config.failLabel}: ${this.config.labelFn(job)} — ${terminalMessage}`)
      return
    }
    state.consecutiveErrors += 1
    // WHY no toast: transient poll errors self-heal on the next interval; a
    // warning toast fires below once failures persist past the threshold.
    console.warn(`${this.config.failLabel} poll failed (attempt ${state.consecutiveErrors}, will retry):`, error)
    if (state.consecutiveErrors >= CONSECUTIVE_FAILURES_FOR_TOAST && !state.toastedWarning) {
      state.toastedWarning = true
      this.config.addToast("warning", `Polling is struggling for ${this.config.labelFn(job)} — ${state.consecutiveErrors} consecutive errors`)
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
