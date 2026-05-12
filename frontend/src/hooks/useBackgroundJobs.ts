/**
 * Background job polling hook — mounted once in App.tsx.
 *
 * Manages polling for all active optimiser and training jobs regardless of
 * which panel is open. This means clicking away from a node mid-solve no
 * longer kills the polling loop — results are captured in useNodeResultsStore
 * and a toast notifies the user on completion.
 *
 * Polling mechanics (exponential backoff, max lifetime, cleanup) are delegated
 * to the generic useJobPolling hook — this file is a thin orchestrator that
 * wires up store selectors and API functions for each job type.
 */
import { useCallback } from "react"
import { getOptimiserStatus, getTrainStatus } from "../api/client"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import type { SolveProgress, TrainProgress } from "../stores/useNodeResultsStore"
import useToastStore from "../stores/useToastStore"
import { buildExecutionFailureMessage } from "../utils/executionDiagnostics"
import useJobPolling from "./useJobPolling"

const VISIBLE_PROGRESS_INTERVAL_MS = 1_000

// HTTP statuses that mean "this job no longer exists, stop polling":
// - 404 Not Found: job was never known, or the route signals it as missing.
// - 410 Gone: job was known but has been purged (typical retention sweep).
// Both are terminal; retrying would just burn the 24-hour max-lifetime window
// for a resource that will never come back.
const TERMINAL_MISSING_JOB_STATUSES = new Set([404, 410])
const FAILED_JOB_STATUSES = new Set([
  "error",
  "cancelled",
  "superseded",
  "timed_out",
  "memory_limited",
  "contract_error",
])

function getMissingJobPollErrorMessage(error: unknown): string | undefined {
  if (!error || typeof error !== "object") return undefined
  const { status, detail, message } = error as {
    status?: unknown
    detail?: unknown
    message?: unknown
  }
  if (typeof status !== "number" || !TERMINAL_MISSING_JOB_STATUSES.has(status)) return undefined
  if (typeof detail === "string" && detail.trim()) return detail
  if (typeof message === "string" && message.trim()) return message
  return "Job not found"
}

export default function useBackgroundJobs() {
  const addToast = useToastStore((s) => s.addToast)

  // ── Optimiser job polling ──

  const solveJobs = useNodeResultsStore((s) => s.solveJobs)
  const updateSolveProgress = useNodeResultsStore((s) => s.updateSolveProgress)
  const completeSolveJob = useNodeResultsStore((s) => s.completeSolveJob)
  const failSolveJob = useNodeResultsStore((s) => s.failSolveJob)

  const solvePollFn = useCallback(
    (jobId: string) => getOptimiserStatus<SolveProgress>(jobId),
    [],
  )
  const solveOnComplete = useCallback(
    (nodeId: string, status: SolveProgress) => {
      if (!status.result) return
      completeSolveJob(nodeId, status.result, status)
    },
    [completeSolveJob],
  )

  useJobPolling<(typeof solveJobs)[string], SolveProgress>({
    jobs: solveJobs,
    pollFn: solvePollFn,
    onProgress: updateSolveProgress,
    progressThrottleMs: VISIBLE_PROGRESS_INTERVAL_MS,
    onComplete: solveOnComplete,
    onFail: failSolveJob,
    labelFn: (job) => job.nodeLabel,
    jobIdFn: (job) => job.jobId,
    isComplete: (s) => s.status === "completed",
    isError: (s) => FAILED_JOB_STATUSES.has(s.status),
    getResult: (s) => (s.result ? s : undefined),
    getErrorMessage: (s) => buildExecutionFailureMessage(s.message || "Unknown error", s.execution_metrics, {
      status: s.status,
      terminalReason: s.terminal_reason,
    }),
    getTerminalPollErrorMessage: getMissingJobPollErrorMessage,
    addToast,
    successLabel: "Optimisation complete",
    failLabel: "Optimisation failed",
  })

  // ── Training job polling ──

  const trainJobs = useNodeResultsStore((s) => s.trainJobs)
  const updateTrainProgress = useNodeResultsStore((s) => s.updateTrainProgress)
  const completeTrainJob = useNodeResultsStore((s) => s.completeTrainJob)
  const failTrainJob = useNodeResultsStore((s) => s.failTrainJob)

  const trainPollFn = useCallback(
    (jobId: string) => getTrainStatus<TrainProgress>(jobId),
    [],
  )
  const trainOnComplete = useCallback(
    (nodeId: string, status: TrainProgress) => {
      if (!status.result) return
      completeTrainJob(nodeId, status.result, status)
    },
    [completeTrainJob],
  )

  useJobPolling<(typeof trainJobs)[string], TrainProgress>({
    jobs: trainJobs,
    pollFn: trainPollFn,
    onProgress: updateTrainProgress,
    progressThrottleMs: VISIBLE_PROGRESS_INTERVAL_MS,
    onComplete: trainOnComplete,
    onFail: failTrainJob,
    labelFn: (job) => job.nodeLabel,
    jobIdFn: (job) => job.jobId,
    isComplete: (s) => s.status === "completed",
    isError: (s) => FAILED_JOB_STATUSES.has(s.status),
    getResult: (s) => (s.result ? s : undefined),
    getErrorMessage: (s) => buildExecutionFailureMessage(s.message || "Unknown error", s.execution_metrics, {
      status: s.status,
      terminalReason: s.terminal_reason,
    }),
    getTerminalPollErrorMessage: getMissingJobPollErrorMessage,
    addToast,
    successLabel: "Training complete",
    failLabel: "Training failed",
  })
}
