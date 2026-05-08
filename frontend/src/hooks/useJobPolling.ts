/**
 * Generic job polling hook — manages a set of background jobs via setTimeout
 * with exponential backoff, max lifetime timeout, and automatic cleanup.
 *
 * Extracted from useBackgroundJobs to eliminate duplication: both solve and
 * train polling were structurally identical aside from type-specific callbacks.
 *
 * This hook owns the ref tracking per-job poller state and the useEffect that
 * reconciles active jobs with running pollers. Consumers pass a `UseJobPollingConfig`
 * describing how to poll, interpret results, and report outcomes.
 */
import { useEffect, useRef, type MutableRefObject } from "react"

// ── Poller configuration ──

const BASE_INTERVAL_MS = 500
const MAX_INTERVAL_MS = 5_000
const MAX_LIFETIME_MS = 24 * 60 * 60 * 1_000 // 24 hours
const CONSECUTIVE_FAILURES_FOR_TOAST = 5
const POLL_TIMEOUT_MS = 30_000

// ── Per-job polling state tracked alongside the timeout handle ──

interface JobPollerState<TStatus = unknown> {
  jobId: string
  timeoutId?: ReturnType<typeof setTimeout>
  progressTimeoutId?: ReturnType<typeof setTimeout>
  startedAt: number
  consecutiveErrors: number
  toastedWarning: boolean
  lastProgressPublishedAt: number
  hasPendingProgress: boolean
  pendingProgress?: TStatus
}

// ── Public config interface ──

export interface UseJobPollingConfig<TJob, TStatus> {
  /** Map of nodeId -> active job. When a new key appears, polling starts. When removed, polling stops. */
  jobs: Record<string, TJob>
  /** Fetch the current status for a job by its server-side job ID. */
  pollFn: (jobId: string) => Promise<TStatus>
  /** Called when a poll returns in-progress status. */
  onProgress: (nodeId: string, status: TStatus) => void
  /**
   * Minimum time between in-progress updates published to consumers.
   * Terminal completion/error states bypass this throttle.
   */
  progressThrottleMs?: number
  /** Called when a job completes successfully. */
  onComplete: (nodeId: string, result: TStatus) => void
  /** Called when a job fails (API error status or network failure). */
  onFail: (nodeId: string, errorMsg: string) => void
  /** Extract a display label from a job (for toast messages). */
  labelFn: (job: TJob) => string
  /** Extract the server-side job ID from a job object. */
  jobIdFn: (job: TJob) => string
  /** Return true if the status indicates successful completion. */
  isComplete: (status: TStatus) => boolean
  /** Return true if the status indicates an error/failure. */
  isError: (status: TStatus) => boolean
  /** Extract the result payload from a completed status, or undefined if missing. */
  getResult: (status: TStatus) => TStatus | undefined
  /** Extract a human-readable error message from an error status. */
  getErrorMessage: (status: TStatus) => string
  /**
   * Return a terminal error message for non-retryable poll exceptions.
   * Undefined keeps the existing retry/backoff behaviour for transient errors.
   */
  getTerminalPollErrorMessage?: (error: unknown) => string | undefined
  /** Show a toast notification. */
  addToast: (type: "success" | "error" | "warning" | "info", text: string) => void
  /** Label prefix for success toasts (e.g. "Training complete"). */
  successLabel: string
  /** Label prefix for failure toasts (e.g. "Training failed"). */
  failLabel: string
}

// ── Internal poller reconciliation ──

/**
 * Reconciles the current set of active jobs with running pollers.
 * Starts polling for new jobs, stops polling for removed jobs.
 *
 * Uses `setTimeout` with exponential backoff instead of `setInterval`.
 */
function reconcilePollers<TJob, TStatus>(
  configRef: MutableRefObject<UseJobPollingConfig<TJob, TStatus>>,
  stateRef: React.MutableRefObject<Record<string, JobPollerState<TStatus>>>,
): void {
  const { jobs } = configRef.current

  const activeNodeIds = Object.keys(jobs)
  const pollingNodeIds = Object.keys(stateRef.current)

  function clearPendingProgress(state: JobPollerState<TStatus>): void {
    // Removal and terminal cleanup both drop queued throttled progress.
    // Stale in-progress updates must not publish after the job is gone.
    clearTimeout(state.progressTimeoutId)
    state.progressTimeoutId = undefined
    state.hasPendingProgress = false
    state.pendingProgress = undefined
  }

  // Start polling for new jobs
  for (const nodeId of activeNodeIds) {
    const job = jobs[nodeId]
    const jobId = configRef.current.jobIdFn(job)
    const currentState = stateRef.current[nodeId]
    if (currentState?.jobId === jobId) continue // already polling this exact job
    if (currentState) {
      clearTimeout(currentState.timeoutId)
      clearPendingProgress(currentState)
      delete stateRef.current[nodeId]
    }

    const now = Date.now()
    const isCurrentJob = (state: JobPollerState<TStatus>) => {
      const currentJob = configRef.current.jobs[nodeId]
      return (
        stateRef.current[nodeId] === state &&
        currentJob != null &&
        configRef.current.jobIdFn(currentJob) === state.jobId
      )
    }

    function publishProgress(state: JobPollerState<TStatus>, status: TStatus): void {
      clearPendingProgress(state)
      if (!isCurrentJob(state)) return
      state.lastProgressPublishedAt = Date.now()
      configRef.current.onProgress(nodeId, status)
    }

    function queueProgress(state: JobPollerState<TStatus>, status: TStatus): void {
      const throttleMs = configRef.current.progressThrottleMs ?? 0
      if (throttleMs <= 0) {
        publishProgress(state, status)
        return
      }

      const now = Date.now()
      const elapsed = now - state.lastProgressPublishedAt
      if (state.lastProgressPublishedAt === 0 || elapsed >= throttleMs) {
        publishProgress(state, status)
        return
      }

      state.pendingProgress = status
      state.hasPendingProgress = true
      if (state.progressTimeoutId) return

      state.progressTimeoutId = setTimeout(() => {
        state.progressTimeoutId = undefined
        if (!state.hasPendingProgress) return
        publishProgress(state, state.pendingProgress as TStatus)
      }, throttleMs - elapsed)
    }

    function schedulePoll(state: JobPollerState<TStatus>): void {
      const { pollFn, isComplete, isError, getResult, getErrorMessage, getTerminalPollErrorMessage, onComplete, onFail, labelFn, addToast, successLabel, failLabel } = configRef.current
      const job = configRef.current.jobs[nodeId]
      if (!job || !isCurrentJob(state)) return
      const elapsed = Date.now() - state.startedAt

      // ── Max lifetime check ──
      if (elapsed >= MAX_LIFETIME_MS) {
        clearPendingProgress(state)
        delete stateRef.current[nodeId]
        onFail(nodeId, "Job timed out after 24 hours")
        addToast("error", `${failLabel}: ${labelFn(job)} — Job timed out after 24 hours`)
        return
      }

      // Compute delay: base interval with exponential backoff on errors
      const delay =
        state.consecutiveErrors === 0
          ? BASE_INTERVAL_MS
          : Math.min(BASE_INTERVAL_MS * Math.pow(2, state.consecutiveErrors), MAX_INTERVAL_MS)

      state.timeoutId = setTimeout(async () => {
        if (!isCurrentJob(state)) return
        let pollTimeoutId: ReturnType<typeof setTimeout> | undefined
        try {
          const status = await Promise.race([
            pollFn(state.jobId),
            new Promise<never>((_, reject) => {
              pollTimeoutId = setTimeout(() => reject(new Error("Poll request timed out")), POLL_TIMEOUT_MS)
            }),
          ])
          clearTimeout(pollTimeoutId)
          if (!isCurrentJob(state)) return

          // Reset backoff on successful network call
          state.consecutiveErrors = 0
          state.toastedWarning = false

          if (isComplete(status) || isError(status)) {
            clearPendingProgress(state)
            delete stateRef.current[nodeId]
            if (isComplete(status) && getResult(status)) {
              onComplete(nodeId, getResult(status)!)
              addToast("success", `${successLabel}: ${labelFn(job)}`)
            } else {
              const msg = getErrorMessage(status) || "Unknown error"
              onFail(nodeId, msg)
              addToast("error", `${failLabel}: ${labelFn(job)} — ${msg}`)
            }
            return
          }

          // Still in progress. The awaited poll may have been superseded by a
          // newer job for the same node; stale statuses must not publish or
          // enqueue another poll.
          if (!isCurrentJob(state)) return
          schedulePoll(state)
          queueProgress(state, status)
          return
        } catch (e) {
          clearTimeout(pollTimeoutId)
          if (!isCurrentJob(state)) return
          const terminalMessage = getTerminalPollErrorMessage?.(e)
          if (terminalMessage) {
            clearPendingProgress(state)
            delete stateRef.current[nodeId]
            onFail(nodeId, terminalMessage)
            addToast("error", `${failLabel}: ${labelFn(job)} — ${terminalMessage}`)
            return
          }
          state.consecutiveErrors += 1
          console.warn(`${failLabel} poll failed (attempt ${state.consecutiveErrors}, will retry):`, e)

          if (state.consecutiveErrors >= CONSECUTIVE_FAILURES_FOR_TOAST && !state.toastedWarning) {
            state.toastedWarning = true
            addToast("warning", `Polling is struggling for ${labelFn(job)} — ${state.consecutiveErrors} consecutive errors`)
          }
        }

        // Schedule next poll (whether success-in-progress or error)
        if (isCurrentJob(state)) {
          schedulePoll(state)
        }
      }, delay)
    }

    const initialState: JobPollerState<TStatus> = {
      jobId,
      startedAt: now,
      consecutiveErrors: 0,
      toastedWarning: false,
      lastProgressPublishedAt: 0,
      hasPendingProgress: false,
    }
    stateRef.current[nodeId] = initialState
    schedulePoll(initialState)
  }

  // Stop polling for jobs that are no longer active (completed/cleared)
  for (const nodeId of pollingNodeIds) {
    if (!jobs[nodeId]) {
      clearTimeout(stateRef.current[nodeId].timeoutId)
      clearPendingProgress(stateRef.current[nodeId])
      delete stateRef.current[nodeId]
    }
  }
}

// ── Hook ──

/**
 * React hook that manages polling for a set of background jobs.
 *
 * Starts/stops pollers reactively as jobs appear/disappear. Cleans up all
 * timeouts on unmount. Each invocation manages an independent set of pollers,
 * so it can be called multiple times in the same component for different job types.
 *
 * @param config - Polling configuration including jobs map and callbacks.
 *   All callback/function fields must be stable references (e.g. from zustand
 *   selectors or useCallback) to avoid unnecessary effect re-runs.
 */
export default function useJobPolling<TJob, TStatus>(
  config: UseJobPollingConfig<TJob, TStatus>,
): void {
  const pollerState = useRef<Record<string, JobPollerState<TStatus>>>({})
  const configRef = useRef(config)
  useEffect(() => { configRef.current = config })

  useEffect(() => {
    reconcilePollers(configRef, pollerState)
  }, [
    config.jobs,
    config.pollFn,
    config.onProgress,
    config.onComplete,
    config.onFail,
    config.addToast,
    config.progressThrottleMs,
    // labelFn, jobIdFn, isComplete, isError, getResult, getErrorMessage,
    // successLabel, failLabel are typically stable literals — omitted from
    // deps to avoid unnecessary re-runs. If they change, the next jobs
    // change will pick them up.
  ])

  // Cleanup all timeouts on unmount
  useEffect(() => {
    const ref = pollerState.current
    return () => {
      for (const state of Object.values(ref)) {
        clearTimeout(state.timeoutId)
        clearTimeout(state.progressTimeoutId)
      }
    }
  }, [])
}
