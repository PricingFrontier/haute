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
import { useCallback, useEffect, useRef } from "react"
import {
  getExplorePivotStatus,
  getNodeDataStatus,
  getOptimiserStatus,
  getTrainStatus,
} from "../api/client"
import { FAILED_JOB_STATUSES } from "../api/types"
import type { NodeDataStatusResponse } from "../api/types"
import { ApiResponseValidationError } from "../api/responseValidation"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import type { ExplorePivotProgress, SolveProgress, TrainProgress } from "../stores/useNodeResultsStore"
import useNodeDataStore from "../stores/useNodeDataStore"
import type { NodeDataSlotJob } from "../stores/useNodeDataStore"
import useDocumentStatusStore from "../stores/useDocumentStatusStore"
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

// An ApiResponseValidationError is deterministic — the same payload fails the
// same way on every poll — so it ends the job with a visible error instead of
// leaving stale progress behind a retry loop. Transport failures stay retryable.
function getJobPollErrorMessage(error: unknown): string | undefined {
  if (error instanceof ApiResponseValidationError) return error.message
  return getMissingJobPollErrorMessage(error)
}

export default function useBackgroundJobs() {
  const addToast = useToastStore((s) => s.addToast)
  const documentSourceFile = useDocumentStatusStore((s) => s.sourceFile)
  const documentExecutionGeneration = useDocumentStatusStore((s) => s.executionGeneration)
  const documentLoadStatus = useDocumentStatusStore((s) => s.loadStatus)
  const documentCanExecute = useDocumentStatusStore(
    (s) => s.capabilities?.can_execute === true,
  )
  const documentGraphSynchronized = useDocumentStatusStore(
    (s) => s.graphSynchronized,
  )
  const discardActiveJobs = useNodeResultsStore((s) => s.discardActiveJobs)
  const resetNodeData = useNodeDataStore((s) => s.reset)
  const documentFenceKey = JSON.stringify([
    documentSourceFile,
    documentExecutionGeneration,
    documentLoadStatus,
    documentCanExecute,
    documentGraphSynchronized,
  ])
  const previousDocumentFenceKey = useRef(documentFenceKey)

  useEffect(() => {
    if (previousDocumentFenceKey.current !== documentFenceKey) {
      discardActiveJobs()
      // Shared data points belong to one document and source: a new fence
      // invalidates every slot, so consumers ask again instead of showing the
      // previous document's generations.
      resetNodeData()
      previousDocumentFenceKey.current = documentFenceKey
    }
  }, [discardActiveJobs, documentFenceKey, resetNodeData])

  // ── Optimiser job polling ──

  const solveJobs = useNodeResultsStore((s) => s.solveJobs)
  const updateSolveProgress = useNodeResultsStore((s) => s.updateSolveProgress)
  const completeSolveJob = useNodeResultsStore((s) => s.completeSolveJob)
  const failSolveJob = useNodeResultsStore((s) => s.failSolveJob)

  const solvePollFn = useCallback(
    (jobId: string, signal: AbortSignal) => getOptimiserStatus<SolveProgress>(jobId, { signal }),
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
    getTerminalPollErrorMessage: getJobPollErrorMessage,
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
    (jobId: string, signal: AbortSignal) => getTrainStatus(jobId, { signal }),
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
    getTerminalPollErrorMessage: getJobPollErrorMessage,
    addToast,
    successLabel: "Training complete",
    failLabel: "Training failed",
  })

  // ── Explore pivot job polling ──

  const pivotJobs = useNodeResultsStore((s) => s.pivotJobs)
  const updateExplorePivotProgress = useNodeResultsStore((s) => s.updateExplorePivotProgress)
  const completeExplorePivotJob = useNodeResultsStore((s) => s.completeExplorePivotJob)
  const failExplorePivotJob = useNodeResultsStore((s) => s.failExplorePivotJob)
  const pivotPollFn = useCallback(
    (jobId: string, signal: AbortSignal) => getExplorePivotStatus(jobId, { signal }),
    [],
  )
  const pivotOnComplete = useCallback(
    (key: string, status: ExplorePivotProgress) => {
      if (!status.result) return
      completeExplorePivotJob(key, status.result, status)
    },
    [completeExplorePivotJob],
  )

  useJobPolling<(typeof pivotJobs)[string], ExplorePivotProgress>({
    jobs: pivotJobs,
    pollFn: pivotPollFn,
    onProgress: updateExplorePivotProgress,
    progressThrottleMs: VISIBLE_PROGRESS_INTERVAL_MS,
    onComplete: pivotOnComplete,
    onFail: failExplorePivotJob,
    labelFn: (job) => `${job.nodeLabel} - ${job.pivotName}`,
    jobIdFn: (job) => job.jobId,
    isComplete: (s) => s.status === "completed",
    isError: (s) => FAILED_JOB_STATUSES.has(s.status),
    getResult: (s) => (s.result ? s : undefined),
    getErrorMessage: (s) => buildExecutionFailureMessage(
      s.failure?.message || s.message || "Unknown error",
      s.execution_metrics,
      {
        status: s.status,
        terminalReason: s.terminal_reason,
      },
    ),
    getTerminalPollErrorMessage: getJobPollErrorMessage,
    addToast,
    successLabel: "Pivot complete",
    failLabel: "Pivot failed",
  })

  // ── Shared node-data build polling, keyed by slot ──
  //
  // One build serves every consumer of a data point, so it is polled once here
  // and each consumer reads its progress from the slot entry.

  const nodeDataJobs = useNodeDataStore((s) => s.jobs)
  const updateNodeDataProgress = useNodeDataStore((s) => s.updateJobProgress)
  const finishNodeDataJob = useNodeDataStore((s) => s.finishJob)
  const nodeDataPollFn = useCallback(
    (jobId: string, signal: AbortSignal) => getNodeDataStatus(jobId, { signal }),
    [],
  )
  const nodeDataOnComplete = useCallback(
    (slotKey: string, status: NodeDataStatusResponse) => finishNodeDataJob(slotKey, status),
    [finishNodeDataJob],
  )
  const nodeDataOnFail = useCallback(
    (slotKey: string, _message: string, status?: NodeDataStatusResponse) => {
      void _message
      finishNodeDataJob(slotKey, status ?? null)
    },
    [finishNodeDataJob],
  )

  useJobPolling<NodeDataSlotJob, NodeDataStatusResponse>({
    jobs: nodeDataJobs,
    pollFn: nodeDataPollFn,
    onProgress: updateNodeDataProgress,
    progressThrottleMs: VISIBLE_PROGRESS_INTERVAL_MS,
    onComplete: nodeDataOnComplete,
    onFail: nodeDataOnFail,
    labelFn: (job) => job.startedByLabel,
    jobIdFn: (job) => job.jobId,
    isComplete: (s) => s.status === "completed",
    isError: (s) => FAILED_JOB_STATUSES.has(s.status),
    getResult: (s) => s,
    getErrorMessage: (s) => buildExecutionFailureMessage(
      s.error || s.message || "Unknown error",
      s.execution_metrics,
      { status: s.status, terminalReason: s.terminal_reason },
    ),
    getTerminalPollErrorMessage: getJobPollErrorMessage,
    addToast,
    successLabel: "Data cached",
    failLabel: "Data caching failed",
  })

  // ── Shared data-profile polling, keyed by slot ──

  const profileJobs = useNodeDataStore((s) => s.profileJobs)
  const updateProfileProgress = useNodeDataStore((s) => s.updateProfileProgress)
  const finishProfileJob = useNodeDataStore((s) => s.finishProfileJob)
  const profileOnComplete = useCallback(
    (slotKey: string, status: NodeDataStatusResponse) => finishProfileJob(slotKey, status),
    [finishProfileJob],
  )
  const profileOnFail = useCallback(
    (slotKey: string, _message: string, status?: NodeDataStatusResponse) => {
      void _message
      finishProfileJob(slotKey, status ?? null)
    },
    [finishProfileJob],
  )

  useJobPolling<NodeDataSlotJob, NodeDataStatusResponse>({
    jobs: profileJobs,
    pollFn: nodeDataPollFn,
    onProgress: updateProfileProgress,
    progressThrottleMs: VISIBLE_PROGRESS_INTERVAL_MS,
    onComplete: profileOnComplete,
    onFail: profileOnFail,
    labelFn: (job) => job.startedByLabel,
    jobIdFn: (job) => job.jobId,
    isComplete: (s) => s.status === "completed",
    isError: (s) => FAILED_JOB_STATUSES.has(s.status),
    getResult: (s) => s,
    getErrorMessage: (s) => buildExecutionFailureMessage(
      s.error || s.message || "Unknown error",
      s.execution_metrics,
      { status: s.status, terminalReason: s.terminal_reason },
    ),
    getTerminalPollErrorMessage: getJobPollErrorMessage,
    addToast,
    successLabel: "Data profile ready",
    failLabel: "Data profile failed",
  })
}
