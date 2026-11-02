/**
 * Background job polling hook — mounted once in App.tsx.
 *
 * Manages polling for every active optimiser solve, training, Explore pivot,
 * node-data build and data-profile job regardless of which panel is open.
 * This means clicking away from a node mid-solve no longer kills the polling
 * loop — results are captured in useNodeResultsStore or useNodeDataStore and a
 * toast notifies the user on completion.
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
import { FAILED_JOB_STATUSES, type JobStatus } from "../api/types"
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

// What a training status shows moving: its state, phase, message, bar, round
// and fit counts. `elapsed_seconds` is left out because it moves on every poll.
function trainProgressKey(status: TrainProgress): string {
  return JSON.stringify([
    status.status,
    status.phase ?? null,
    status.message,
    status.progress,
    status.iteration,
    status.trial_index ?? null,
    status.fold_index ?? null,
    status.completed_fits ?? null,
  ])
}

/** What every background poller shares: the visible progress pace, the job id, the terminal statuses and poll errors. */
const jobPollingDefaults = {
  progressThrottleMs: VISIBLE_PROGRESS_INTERVAL_MS,
  jobIdFn: (job: { jobId: string }) => job.jobId,
  isComplete: (status: { status: string }) => status.status === "completed",
  isError: (status: { status: JobStatus }) => FAILED_JOB_STATUSES.has(status.status),
  getTerminalPollErrorMessage: getJobPollErrorMessage,
}

function solveJobIsActive(nodeId: string, jobId: string): boolean {
  return useNodeResultsStore.getState().solveJobs[nodeId]?.jobId === jobId
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
    (jobId: string, signal: AbortSignal) => getOptimiserStatus(jobId, { signal }),
    [],
  )
  // The solve actions address the node, so a reply applies only while the job
  // it was polled for is still the node's.
  const solveOnProgress = useCallback(
    (nodeId: string, jobId: string, status: SolveProgress) => {
      if (solveJobIsActive(nodeId, jobId)) updateSolveProgress(nodeId, status)
    },
    [updateSolveProgress],
  )
  const solveOnComplete = useCallback(
    (nodeId: string, jobId: string, status: SolveProgress) => {
      if (!status.result || !solveJobIsActive(nodeId, jobId)) return
      completeSolveJob(nodeId, status.result, status)
    },
    [completeSolveJob],
  )
  const solveOnFail = useCallback(
    (nodeId: string, jobId: string, message: string, status?: SolveProgress) => {
      if (solveJobIsActive(nodeId, jobId)) failSolveJob(nodeId, message, status)
    },
    [failSolveJob],
  )

  useJobPolling<(typeof solveJobs)[string], SolveProgress>({
    ...jobPollingDefaults,
    jobs: solveJobs,
    pollFn: solvePollFn,
    onProgress: solveOnProgress,
    onComplete: solveOnComplete,
    onFail: solveOnFail,
    labelFn: (job) => job.nodeLabel,
    getResult: (s) => (s.result ? s : undefined),
    getErrorMessage: (s) => buildExecutionFailureMessage(s.message || "Unknown error", s.execution_metrics, {
      status: s.status,
      terminalReason: s.terminal_reason,
    }),
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
    (nodeId: string, jobId: string, status: TrainProgress) => {
      if (!status.result) return
      completeTrainJob(nodeId, jobId, status.result, status)
    },
    [completeTrainJob],
  )

  useJobPolling<(typeof trainJobs)[string], TrainProgress>({
    ...jobPollingDefaults,
    jobs: trainJobs,
    pollFn: trainPollFn,
    onProgress: updateTrainProgress,
    // A training's rounds advance quickly, so it polls about once a second
    // while they do instead of backing off to five seconds.
    progressKey: trainProgressKey,
    onComplete: trainOnComplete,
    onFail: failTrainJob,
    labelFn: (job) => job.nodeLabel,
    getResult: (s) => (s.result ? s : undefined),
    getErrorMessage: (s) => buildExecutionFailureMessage(s.message || "Unknown error", s.execution_metrics, {
      status: s.status,
      terminalReason: s.terminal_reason,
    }),
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
    (key: string, jobId: string, status: ExplorePivotProgress) => {
      if (!status.result) return
      completeExplorePivotJob(key, jobId, status.result, status)
    },
    [completeExplorePivotJob],
  )

  useJobPolling<(typeof pivotJobs)[string], ExplorePivotProgress>({
    ...jobPollingDefaults,
    jobs: pivotJobs,
    pollFn: pivotPollFn,
    onProgress: updateExplorePivotProgress,
    onComplete: pivotOnComplete,
    onFail: failExplorePivotJob,
    labelFn: (job) => `${job.nodeLabel} - ${job.pivotName}`,
    getResult: (s) => (s.result ? s : undefined),
    getErrorMessage: (s) => buildExecutionFailureMessage(
      s.failure?.message || s.message || "Unknown error",
      s.execution_metrics,
      {
        status: s.status,
        terminalReason: s.terminal_reason,
      },
    ),
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
  const nodeDataOnProgress = useCallback(
    (slotKey: string, _jobId: string, status: NodeDataStatusResponse) => updateNodeDataProgress(slotKey, status),
    [updateNodeDataProgress],
  )
  const nodeDataOnComplete = useCallback(
    (slotKey: string, _jobId: string, status: NodeDataStatusResponse) => finishNodeDataJob(slotKey, status),
    [finishNodeDataJob],
  )
  const nodeDataOnFail = useCallback(
    (slotKey: string, _jobId: string, _message: string, status?: NodeDataStatusResponse) => {
      void _message
      finishNodeDataJob(slotKey, status ?? null)
    },
    [finishNodeDataJob],
  )

  useJobPolling<NodeDataSlotJob, NodeDataStatusResponse>({
    ...jobPollingDefaults,
    jobs: nodeDataJobs,
    pollFn: nodeDataPollFn,
    onProgress: nodeDataOnProgress,
    onComplete: nodeDataOnComplete,
    onFail: nodeDataOnFail,
    labelFn: (job) => job.startedByLabel,
    getResult: (s) => s,
    getErrorMessage: (s) => buildExecutionFailureMessage(
      s.error || s.message || "Unknown error",
      s.execution_metrics,
      { status: s.status, terminalReason: s.terminal_reason },
    ),
    addToast,
    successLabel: "Data cached",
    failLabel: "Data caching failed",
  })

  // ── Shared data-profile polling, keyed by slot ──

  const profileJobs = useNodeDataStore((s) => s.profileJobs)
  const updateProfileProgress = useNodeDataStore((s) => s.updateProfileProgress)
  const finishProfileJob = useNodeDataStore((s) => s.finishProfileJob)
  const profileOnProgress = useCallback(
    (slotKey: string, _jobId: string, status: NodeDataStatusResponse) => updateProfileProgress(slotKey, status),
    [updateProfileProgress],
  )
  const profileOnComplete = useCallback(
    (slotKey: string, _jobId: string, status: NodeDataStatusResponse) => finishProfileJob(slotKey, status),
    [finishProfileJob],
  )
  const profileOnFail = useCallback(
    (slotKey: string, _jobId: string, _message: string, status?: NodeDataStatusResponse) => {
      void _message
      finishProfileJob(slotKey, status ?? null)
    },
    [finishProfileJob],
  )

  useJobPolling<NodeDataSlotJob, NodeDataStatusResponse>({
    ...jobPollingDefaults,
    jobs: profileJobs,
    pollFn: nodeDataPollFn,
    onProgress: profileOnProgress,
    onComplete: profileOnComplete,
    onFail: profileOnFail,
    labelFn: (job) => job.startedByLabel,
    getResult: (s) => s,
    getErrorMessage: (s) => buildExecutionFailureMessage(
      s.error || s.message || "Unknown error",
      s.execution_metrics,
      { status: s.status, terminalReason: s.terminal_reason },
    ),
    addToast,
    successLabel: "Data profile ready",
    failLabel: "Data profile failed",
  })
}
