import { useCallback, useRef, useState } from "react"

import { cancelExplorePivot, runExplorePivot } from "../../api/client"
import type {
  ExecutionMetrics,
  ExplorePivotFailure,
  ExplorePivotStatusResponse,
  JobStatus,
} from "../../api/types"
import useGraphStore from "../../stores/useGraphStore"
import useNodeResultsStore, {
  explorePivotResultKey,
} from "../../stores/useNodeResultsStore"
import useSettingsStore from "../../stores/useSettingsStore"
import { buildGraph } from "../../utils/buildGraph"
import {
  executionErrorDetailMessage,
  executionJobStatusFromReason,
  executionMetricsFromError,
  executionTerminalReasonFromError,
} from "../../utils/executionDiagnostics"
import type { SimpleEdge, SimpleNode } from "../editors"
import {
  pivotCalculationIdentity,
  type ExplorePivotConfig,
} from "./pivotConfig"

export type ExplorePivotActionNotice = {
  message: string
  failure?: ExplorePivotFailure | null
}

type UseExplorePivotActionsInput = {
  node: SimpleNode
  allNodes: SimpleNode[]
  edges: SimpleEdge[]
  submodels?: Record<string, unknown>
  preamble?: string
}

function terminalStatus(
  status: JobStatus,
  message: string,
  failure: ExplorePivotFailure | null = null,
  executionMetrics: ExecutionMetrics | null = null,
): ExplorePivotStatusResponse {
  return {
    status,
    progress: 1,
    message,
    result: null,
    failure,
    terminal_reason: status,
    execution_metrics: executionMetrics,
  }
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message
  return executionErrorDetailMessage(error) ?? String(error)
}

/**
 * Shared Pivot execution lifecycle used by both Pivot tables and PivotCharts.
 * Charts own no execution endpoint, job, or cache.
 */
export default function useExplorePivotActions({
  node,
  allNodes,
  edges,
  submodels,
  preamble,
}: UseExplorePivotActionsInput) {
  const activeSource = useSettingsStore((state) => state.activeSource)
  const streamingChunkSize = useSettingsStore(
    (state) => state.streamingChunkSize,
  )
  const structuralVersion = useGraphStore((state) => state.structuralVersion)
  const startJob = useNodeResultsStore((state) => state.startExplorePivotJob)
  const updateProgress = useNodeResultsStore(
    (state) => state.updateExplorePivotProgress,
  )
  const completeJob = useNodeResultsStore(
    (state) => state.completeExplorePivotJob,
  )
  const failJob = useNodeResultsStore((state) => state.failExplorePivotJob)

  const [notices, setNotices] = useState<
    Record<string, ExplorePivotActionNotice>
  >({})
  const [submitting, setSubmitting] = useState<Record<string, boolean>>({})
  const nodeLabel = String(node.data.label || node.id)

  const setNotice = useCallback(
    (pivotId: string, notice: ExplorePivotActionNotice | null) => {
      setNotices((current) => {
        if (notice) return { ...current, [pivotId]: notice }
        const { [pivotId]: _removed, ...remaining } = current
        void _removed
        return remaining
      })
    },
    [],
  )

  const setCardSubmitting = useCallback(
    (pivotId: string, value: boolean) => {
      setSubmitting((current) => {
        if (value) return { ...current, [pivotId]: true }
        const { [pivotId]: _removed, ...remaining } = current
        void _removed
        return remaining
      })
    },
    [],
  )

  // A superseding submission can overlap the one it replaced. The visible
  // submitting flag belongs to the LATEST submission generation only: an
  // obsolete request settling (in either order) never touches the flag, and
  // the latest request settling clears it immediately — so a superseded
  // request can neither strand the flag on nor clear it prematurely.
  const submissionGenerations = useRef<Record<string, number>>({})
  const beginSubmitting = useCallback(
    (pivotId: string): number => {
      const generation = (submissionGenerations.current[pivotId] ?? 0) + 1
      submissionGenerations.current[pivotId] = generation
      setCardSubmitting(pivotId, true)
      return generation
    },
    [setCardSubmitting],
  )
  const endSubmitting = useCallback(
    (pivotId: string, generation: number) => {
      // The counter is monotonic and never reset: deleting it here would let
      // a later submission reuse an obsolete in-flight request's generation.
      if (submissionGenerations.current[pivotId] !== generation) return
      setCardSubmitting(pivotId, false)
    },
    [setCardSubmitting],
  )

  const startStoredJob = useCallback(
    (
      pivot: ExplorePivotConfig,
      key: string,
      jobId: string,
      calculationIdentity: string,
      requestedDataframeCacheKey: string | null,
    ) => {
      startJob(
        key,
        jobId,
        node.id,
        pivot.id,
        nodeLabel,
        pivot.name,
        calculationIdentity,
        activeSource,
        structuralVersion,
        requestedDataframeCacheKey,
      )
    },
    [activeSource, node.id, nodeLabel, startJob, structuralVersion],
  )

  const persistStartFailure = useCallback(
    (
      pivot: ExplorePivotConfig,
      key: string,
      calculationIdentity: string,
      message: string,
      status: JobStatus,
      requestedDataframeCacheKey: string | null,
      failure: ExplorePivotFailure | null = null,
      executionMetrics: ExecutionMetrics | null = null,
    ) => {
      startStoredJob(
        pivot,
        key,
        `failed:${key}:${Date.now()}`,
        calculationIdentity,
        requestedDataframeCacheKey,
      )
      failJob(
        key,
        message,
        terminalStatus(status, message, failure, executionMetrics),
      )
    },
    [failJob, startStoredJob],
  )

  const updatePivot = useCallback(
    async (
      pivot: ExplorePivotConfig,
      requestedDataframeCacheKey: string | null = null,
      autoClaimToken?: number,
    ) => {
      if (pivot.values.length === 0) return

      const key = explorePivotResultKey(node.id, pivot.id)
      const calculationIdentity = pivotCalculationIdentity(pivot)
      const startToken = autoClaimToken
        ?? useNodeResultsStore
          .getState()
          .claimExplorePivotManual(
            key,
            requestedDataframeCacheKey,
            calculationIdentity,
          )
      // Every submission owns a claim generation. A newer automatic target or
      // manual Retry replaces it, so a stale response never overwrites newer
      // work shared by another mounted consumer.
      const claimCurrent = () =>
        useNodeResultsStore.getState().pivotStartClaims[key]?.token
          === startToken
      setNotice(pivot.id, null)
      const submissionGeneration = beginSubmitting(pivot.id)

      try {
        const response = await runExplorePivot({
          graph: buildGraph(allNodes, edges, submodels, preamble),
          node_id: node.id,
          pivot,
          source: activeSource,
          streamingChunkSize,
        })
        if (!claimCurrent()) return

        if (response.status === "cache_required") {
          const message = response.failure?.message ?? response.message
          persistStartFailure(
            pivot,
            key,
            calculationIdentity,
            message,
            "contract_error",
            requestedDataframeCacheKey,
            response.failure,
          )
          return
        }

        if (response.status === "completed") {
          if (!response.result) {
            throw new Error("Pivot completed without a result")
          }
          const jobId = response.job_id ?? `cached:${key}`
          startStoredJob(
            pivot,
            key,
            jobId,
            calculationIdentity,
            requestedDataframeCacheKey,
          )
          completeJob(key, response.result, {
            status: "completed",
            progress: 1,
            message: response.message || "Completed",
            result: response.result,
            failure: null,
            terminal_reason: "completed",
            execution_metrics: response.result.execution_metrics,
          })
          return
        }

        if (!response.job_id) {
          throw new Error("Pivot job did not return a job id")
        }
        startStoredJob(
          pivot,
          key,
          response.job_id,
          calculationIdentity,
          requestedDataframeCacheKey,
        )
        updateProgress(key, {
          status: "running",
          progress: 0.05,
          message: response.message || "Starting",
          result: null,
          failure: null,
          terminal_reason: null,
          execution_metrics: null,
        })
      } catch (error) {
        if (!claimCurrent()) return
        const terminalReason = executionTerminalReasonFromError(error)
        persistStartFailure(
          pivot,
          key,
          calculationIdentity,
          errorMessage(error),
          executionJobStatusFromReason(terminalReason),
          requestedDataframeCacheKey,
          null,
          executionMetricsFromError(error),
        )
      } finally {
        useNodeResultsStore.getState().releaseExplorePivotStart(key, startToken)
        endSubmitting(pivot.id, submissionGeneration)
      }
    },
    [
      activeSource,
      allNodes,
      beginSubmitting,
      completeJob,
      edges,
      endSubmitting,
      node.id,
      persistStartFailure,
      preamble,
      setNotice,
      startStoredJob,
      streamingChunkSize,
      submodels,
      updateProgress,
    ],
  )

  const cancelPivot = useCallback(
    async (pivot: ExplorePivotConfig, jobId: string) => {
      const key = explorePivotResultKey(node.id, pivot.id)
      setNotice(pivot.id, null)
      try {
        const response = await cancelExplorePivot(jobId)
        if (response.status === "completed" && response.result) {
          completeJob(key, response.result, response)
        } else {
          failJob(key, response.message || "Cancelled", response)
        }
      } catch (error) {
        // A failed cancellation request does not prove the calculation stopped.
        // Keep the active job so background polling can resolve it.
        setNotice(pivot.id, { message: errorMessage(error) })
      }
    },
    [completeJob, failJob, node.id, setNotice],
  )

  return { cancelPivot, notices, submitting, updatePivot }
}
