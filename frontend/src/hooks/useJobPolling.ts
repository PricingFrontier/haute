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

interface JobPollerState {
  timeoutId?: ReturnType<typeof setTimeout>
  startedAt: number
  consecutiveErrors: number
  toastedWarning: boolean
}

// ── Public config interface ──

export interface UseJobPollingConfig<TJob, TStatus> {
  /** Map of nodeId -> active job. When a new key appears, polling starts. When removed, polling stops. */
  jobs: Record<string, TJob>
  /** Fetch the current status for a job by its server-side job ID. */
  pollFn: (jobId: string) => Promise<TStatus>
  /** Called when a poll returns in-progress status. */
  onProgress: (nodeId: string, status: TStatus) => void
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
  stateRef: React.MutableRefObject<Record<string, JobPollerState>>,
): void {
  const { jobs } = configRef.current

  const activeNodeIds = Object.keys(jobs)
  const pollingNodeIds = Object.keys(stateRef.current)

  // Start polling for new jobs
  for (const nodeId of activeNodeIds) {
    if (stateRef.current[nodeId]) continue // already polling

    const now = Date.now()

    function schedulePoll(state: JobPollerState): void {
      const { pollFn, jobIdFn, isComplete, isError, getResult, getErrorMessage, onProgress, onComplete, onFail, labelFn, addToast, successLabel, failLabel } = configRef.current
      const job = configRef.current.jobs[nodeId]
      if (!job) return
      const elapsed = Date.now() - state.startedAt

      // ── Max lifetime check ──
      if (elapsed >= MAX_LIFETIME_MS) {
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
        let pollTimeoutId: ReturnType<typeof setTimeout> | undefined
        try {
          const status = await Promise.race([
            pollFn(jobIdFn(job)),
            new Promise<never>((_, reject) => {
              pollTimeoutId = setTimeout(() => reject(new Error("Poll request timed out")), POLL_TIMEOUT_MS)
            }),
          ])
          clearTimeout(pollTimeoutId)

          // Reset backoff on successful network call
          state.consecutiveErrors = 0
          state.toastedWarning = false

          if (isComplete(status) || isError(status)) {
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

          // Still in progress
          onProgress(nodeId, status)
        } catch (e) {
          clearTimeout(pollTimeoutId)
          state.consecutiveErrors += 1
          console.warn(`${failLabel} poll failed (attempt ${state.consecutiveErrors}, will retry):`, e)

          if (state.consecutiveErrors >= CONSECUTIVE_FAILURES_FOR_TOAST && !state.toastedWarning) {
            state.toastedWarning = true
            addToast("warning", `Polling is struggling for ${labelFn(job)} — ${state.consecutiveErrors} consecutive errors`)
          }
        }

        // Schedule next poll (whether success-in-progress or error)
        if (stateRef.current[nodeId]) {
          schedulePoll(state)
        }
      }, delay)
    }

    const initialState: JobPollerState = {
      startedAt: now,
      consecutiveErrors: 0,
      toastedWarning: false,
    }
    stateRef.current[nodeId] = initialState
    schedulePoll(initialState)
  }

  // Stop polling for jobs that are no longer active (completed/cleared)
  for (const nodeId of pollingNodeIds) {
    if (!jobs[nodeId]) {
      clearTimeout(stateRef.current[nodeId].timeoutId)
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
  const pollerState = useRef<Record<string, JobPollerState>>({})
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
      }
    }
  }, [])
}
