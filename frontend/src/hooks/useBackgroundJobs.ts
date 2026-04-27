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
import useJobPolling from "./useJobPolling"

const VISIBLE_PROGRESS_INTERVAL_MS = 1_000

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
      completeSolveJob(nodeId, status.result)
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
    isError: (s) => s.status === "error",
    getResult: (s) => (s.result ? s : undefined),
    getErrorMessage: (s) => s.message || "Unknown error",
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
      completeTrainJob(nodeId, status.result)
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
    isError: (s) => s.status === "error",
    getResult: (s) => (s.result ? s : undefined),
    getErrorMessage: (s) => s.message || "Unknown error",
    addToast,
    successLabel: "Training complete",
    failLabel: "Training failed",
  })
}
