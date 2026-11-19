import { useRequestScope, useResultKey } from "../useGraph"
import { useCallback, useState } from "react"

import { cancelExplorePivot, runExplorePivot } from "../../api/client"
import type {
  ExecutionMetrics,
  ExplorePivotFailure,
  ExplorePivotStatusResponse,
  JobStatus,
} from "../../api/types"
import useGraphStore from "../../stores/useGraphStore"
import { executionPart } from "../../stores/useDocumentStatusStore"
import useNodeResultsStore, {
  explorePivotResultKey,
} from "../../stores/useNodeResultsStore"
import useSettingsStore from "../../stores/useSettingsStore"
import { canvasTarget, prepareNodeRequest } from "../../utils/requestGateway"
import {
  executionJobStatusFromReason,
  executionMetricsFromError,
  executionTerminalReasonFromError,
} from "../../utils/executionDiagnostics"
import { apiErrorMessage } from "../../api/errors"
import { answer, ask, watch } from "../../utils/answers"
import type { SimpleEdge, SimpleNode } from "../editors/_shared"
import {
  pivotCalculationIdentity,
  isPivotConfigured,
  type ExplorePivotConfig,
} from "./pivotConfig"

type ExplorePivotActionNotice = {
  message: string
  failure?: ExplorePivotFailure | null
}

/** By a node's result key, then by pivot id. */
type ByNode<T> = Readonly<Record<string, Readonly<Record<string, T>>>>

const NO_ENTRIES: Readonly<Record<string, never>> = Object.freeze({})

/** *byNode* with the node's entry for *pivotId* set, or removed for null. */
function withEntry<T>(byNode: ByNode<T>, nodeKey: string, pivotId: string, value: T | null): ByNode<T> {
  const entries = byNode[nodeKey] ?? {}
  if (value !== null) return { ...byNode, [nodeKey]: { ...entries, [pivotId]: value } }
  if (!(pivotId in entries)) return byNode
  const { [pivotId]: _removed, ...remaining } = entries
  void _removed
  if (Object.keys(remaining).length > 0) return { ...byNode, [nodeKey]: remaining }
  const { [nodeKey]: _node, ...others } = byNode
  void _node
  return others
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

/**
 * Shared Pivot execution lifecycle used by both Pivot tables and PivotCharts.
 * Charts own no execution endpoint, job, or cache.
 *
 * Results, jobs and claims are kept under the Explore node's result key, which
 * it returns for its consumers to read them by: inside a submodel, its
 * occurrence's, so the owner and its copies each calculate their own. The
 * starting flags (`submitting`) and notices it returns are that node's, by
 * pivot id.
 */
export default function useExplorePivotActions({
  node,
  allNodes,
  edges,
  submodels,
  preamble,
}: UseExplorePivotActionsInput) {
  const requestScope = useRequestScope()
  // The node's result key: inside a submodel, its occurrence's.
  const nodeKey = useResultKey(node.id)
  const activeSource = useSettingsStore((state) => state.activeSource)
  const structuralVersion = useGraphStore((state) => state.structuralVersion)
  const startJob = useNodeResultsStore((state) => state.startExplorePivotJob)
  const updateProgress = useNodeResultsStore(
    (state) => state.updateExplorePivotProgress,
  )
  const completeJob = useNodeResultsStore(
    (state) => state.completeExplorePivotJob,
  )
  const failJob = useNodeResultsStore((state) => state.failExplorePivotJob)
  const failStart = useNodeResultsStore((state) => state.failExplorePivotStart)

  // The panel stays mounted while another node is opened, and every node numbers
  // its own pivots, so what the hook records of the work it starts is kept by
  // the node's result key first; it returns the open node's, by pivot id.
  const [noticesByNode, setNotices] = useState<ByNode<ExplorePivotActionNotice>>({})
  const [submittingByNode, setSubmitting] = useState<ByNode<boolean>>({})
  const notices = noticesByNode[nodeKey] ?? NO_ENTRIES
  const submitting = submittingByNode[nodeKey] ?? NO_ENTRIES
  const nodeLabel = String(node.data.label || node.id)

  const setNotice = useCallback(
    (requestKey: string, pivotId: string, notice: ExplorePivotActionNotice | null) => {
      setNotices((current) => withEntry(current, requestKey, pivotId, notice))
    },
    [],
  )

  const setCardSubmitting = useCallback(
    (requestKey: string, pivotId: string, value: boolean) => {
      setSubmitting((current) => withEntry(current, requestKey, pivotId, value ? true : null))
    },
    [],
  )

  // Both are reached after the request's await, so they take the result key
  // the request was made under.
  const startStoredJob = useCallback(
    (
      pivot: ExplorePivotConfig,
      requestKey: string,
      key: string,
      jobId: string,
      calculationIdentity: string,
      requestedDataVersion: string | null,
    ) => {
      startJob(
        key,
        jobId,
        requestKey,
        pivot.id,
        nodeLabel,
        pivot.name,
        calculationIdentity,
        activeSource,
        structuralVersion,
        requestedDataVersion,
      )
    },
    [activeSource, nodeLabel, startJob, structuralVersion],
  )

  const persistStartFailure = useCallback(
    (
      pivot: ExplorePivotConfig,
      requestKey: string,
      key: string,
      calculationIdentity: string,
      message: string,
      status: JobStatus,
      requestedDataVersion: string | null,
      failure: ExplorePivotFailure | null = null,
      executionMetrics: ExecutionMetrics | null = null,
    ) => {
      failStart(
        key,
        {
          nodeId: requestKey,
          pivotId: pivot.id,
          nodeLabel,
          pivotName: pivot.name,
          calculationIdentity,
          requestedDataVersion,
          source: activeSource,
          structuralVersion,
        },
        message,
        terminalStatus(status, message, failure, executionMetrics),
      )
    },
    [activeSource, failStart, nodeLabel, structuralVersion],
  )

  const updatePivot = useCallback(
    async (
      pivot: ExplorePivotConfig,
      requestedDataVersion: string | null = null,
      autoClaimToken?: number,
    ) => {
      // Taken before the request and used after it.
      const requestKey = nodeKey
      const key = explorePivotResultKey(requestKey, pivot.id)
      const documentFence = executionPart()
      if (!isPivotConfigured(pivot) || !documentFence.holds()) {
        if (autoClaimToken !== undefined) {
          useNodeResultsStore.getState().releaseExplorePivotStart(key, autoClaimToken)
        }
        return
      }
      const calculationIdentity = pivotCalculationIdentity(pivot)
      const startToken = autoClaimToken
        ?? useNodeResultsStore
          .getState()
          .claimExplorePivotManual(
            key,
            requestKey,
            requestedDataVersion,
            calculationIdentity,
          )
      // Every submission owns a claim generation. A newer automatic target or
      // manual Retry replaces it, so a stale response never overwrites newer
      // work shared by another mounted consumer. The visible submitting flag
      // is the submission's while it is current: one replaced clears it at
      // once, before the newer one sets it, and its late answer touches nothing.
      const claimed = watch(
        useNodeResultsStore,
        (state) => state.pivotStartClaims[key]?.token,
        (_, now) => now === startToken,
      )
      setNotice(requestKey, pivot.id, null)
      // The graph reads the preamble from the store; listing it renews this callback.
      void allNodes
      void edges
      void submodels
      void preamble

      const startFailed = (error: unknown) => {
        const terminalReason = executionTerminalReasonFromError(error)
        persistStartFailure(
          pivot,
          requestKey,
          key,
          calculationIdentity,
          apiErrorMessage(error),
          executionJobStatusFromReason(terminalReason),
          requestedDataVersion,
          null,
          executionMetricsFromError(error),
        )
      }

      await answer([documentFence, claimed], async () => {
        // Inside an open submodel, the whole pipeline and the node's address.
        const target = prepareNodeRequest(requestScope, canvasTarget(node.id))
        return runExplorePivot({
          graph: target.graph,
          target: target.node,
          pivot,
          source: activeSource,
        })
      }, {
        // An answer the pivot cannot use is its start's failure, as a refused one is.
        ok: (response) => {
          try {
            if (response.status === "cache_required") {
              const message = response.failure?.message ?? response.message
              persistStartFailure(
                pivot,
                requestKey,
                key,
                calculationIdentity,
                message,
                "contract_error",
                requestedDataVersion,
                response.failure,
              )
              return
            }

            if (response.status === "completed") {
              if (!response.result) {
                startFailed(new Error("Pivot completed without a result"))
                return
              }
              const jobId = response.job_id ?? `cached:${key}`
              startStoredJob(
                pivot,
                requestKey,
                key,
                jobId,
                calculationIdentity,
                requestedDataVersion,
              )
              completeJob(key, jobId, response.result, {
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
              startFailed(new Error("Pivot job did not return a job id"))
              return
            }
            startStoredJob(
              pivot,
              requestKey,
              key,
              response.job_id,
              calculationIdentity,
              requestedDataVersion,
            )
            updateProgress(key, response.job_id, {
              status: "running",
              progress: 0.05,
              message: response.message || "Starting",
              result: null,
              failure: null,
              terminal_reason: null,
              execution_metrics: null,
            })
          } catch (error: unknown) {
            startFailed(error)
          }
        },
        failed: startFailed,
      }, {
        pending: (waiting) => setCardSubmitting(requestKey, pivot.id, waiting),
        // The start's claim is held until its answer settles, current or not.
        inFlight: (flying) => {
          if (!flying) useNodeResultsStore.getState().releaseExplorePivotStart(key, startToken)
        },
      })
    },
    [
      requestScope,
      activeSource,
      allNodes,
      completeJob,
      edges,
      node.id,
      nodeKey,
      persistStartFailure,
      preamble,
      setCardSubmitting,
      setNotice,
      startStoredJob,
      submodels,
      updateProgress,
    ],
  )

  const cancelPivot = useCallback(
    async (pivot: ExplorePivotConfig, jobId: string) => {
      // Taken before the request and used after it.
      const requestKey = nodeKey
      const key = explorePivotResultKey(requestKey, pivot.id)
      const documentFence = executionPart()
      // A failed cancellation's notice belongs to the job it cancelled: polling
      // may have retired that job and another started while it was pending.
      const job = ask([watch(useNodeResultsStore, (state) => state.pivotJobs[key]?.jobId, (_, now) => now === jobId)])
      setNotice(requestKey, pivot.id, null)
      try {
        await answer([documentFence], cancelExplorePivot(jobId), {
          ok: (response) => {
            if (response.status === "completed" && response.result) {
              completeJob(key, jobId, response.result, response)
            } else {
              failJob(key, jobId, response.message || "Cancelled", response)
            }
          },
          // A failed cancellation request does not prove the calculation stopped.
          // Keep the active job so background polling can resolve it.
          failed: (error) => job.apply(() => setNotice(requestKey, pivot.id, { message: apiErrorMessage(error) })),
        })
      } finally {
        job.release()
      }
    },
    [completeJob, failJob, nodeKey, setNotice],
  )

  return { cancelPivot, notices, resultKey: nodeKey, submitting, updatePivot }
}
