import { Loader2, Play, Table2, XCircle } from "lucide-react"
import { useCallback, useState } from "react"

import { cancelExplorePivot, runExplorePivot } from "../../api/client"
import type {
  ExecutionMetrics,
  ExploreCacheReport,
  ExplorePivotFailure,
  ExplorePivotStatusResponse,
  JobStatus,
} from "../../api/types"
import useGraphStore from "../../stores/useGraphStore"
import useNodeResultsStore, {
  explorePivotResultKey,
} from "../../stores/useNodeResultsStore"
import useSettingsStore from "../../stores/useSettingsStore"
import { NODE_GROUP_COLORS } from "../../theme/colors"
import { buildGraph } from "../../utils/buildGraph"
import {
  executionErrorDetailMessage,
  executionJobStatusFromReason,
  executionMetricsFromError,
  executionTerminalReasonFromError,
} from "../../utils/executionDiagnostics"
import type { SimpleEdge, SimpleNode } from "../editors"
import PivotTableGrid from "./PivotTableGrid"
import {
  parseExplorePivots,
  pivotCalculationIdentity,
  type ExplorePivotConfig,
} from "./pivotConfig"

type ExplorePivotsPaneProps = {
  node: SimpleNode
  allNodes: SimpleNode[]
  edges: SimpleEdge[]
  submodels?: Record<string, unknown>
  preamble?: string
  report: ExploreCacheReport | null
}

type CardNotice = {
  message: string
  failure?: ExplorePivotFailure | null
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

function EmptyPivots({ children }: { children: string }) {
  return (
    <div className="flex flex-1 items-center justify-center p-4">
      <div className="max-w-md text-center">
        <Table2
          size={24}
          className="mx-auto mb-2"
          aria-hidden="true"
          style={{ color: NODE_GROUP_COLORS.explore }}
        />
        <div
          className="text-xs font-semibold"
          style={{ color: "var(--text-secondary)" }}
        >
          {children}
        </div>
      </div>
    </div>
  )
}

export default function ExplorePivotsPane({
  node,
  allNodes,
  edges,
  submodels,
  preamble,
  report,
}: ExplorePivotsPaneProps) {
  const activeSource = useSettingsStore((state) => state.activeSource)
  const streamingChunkSize = useSettingsStore(
    (state) => state.streamingChunkSize,
  )
  const structuralVersion = useGraphStore((state) => state.structuralVersion)

  const pivotResults = useNodeResultsStore((state) => state.pivotResults)
  const pivotJobs = useNodeResultsStore((state) => state.pivotJobs)
  const startJob = useNodeResultsStore((state) => state.startExplorePivotJob)
  const updateProgress = useNodeResultsStore(
    (state) => state.updateExplorePivotProgress,
  )
  const completeJob = useNodeResultsStore(
    (state) => state.completeExplorePivotJob,
  )
  const failJob = useNodeResultsStore(
    (state) => state.failExplorePivotJob,
  )

  const [notices, setNotices] = useState<Record<string, CardNotice>>({})
  const [submitting, setSubmitting] = useState<Record<string, boolean>>({})
  const parsed = parseExplorePivots(node.data.config ?? {})
  const nodeLabel = String(node.data.label || node.id)

  const setNotice = useCallback(
    (pivotId: string, notice: CardNotice | null) => {
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

  const startStoredJob = useCallback(
    (
      pivot: ExplorePivotConfig,
      key: string,
      jobId: string,
      calculationIdentity: string,
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
      failure: ExplorePivotFailure | null = null,
      executionMetrics: ExecutionMetrics | null = null,
    ) => {
      startStoredJob(
        pivot,
        key,
        `failed:${key}:${Date.now()}`,
        calculationIdentity,
      )
      failJob(
        key,
        message,
        terminalStatus(status, message, failure, executionMetrics),
      )
    },
    [failJob, startStoredJob],
  )

  const update = useCallback(
    async (pivot: ExplorePivotConfig) => {
      if (pivot.values.length === 0) return

      const key = explorePivotResultKey(node.id, pivot.id)
      const calculationIdentity = pivotCalculationIdentity(pivot)
      setNotice(pivot.id, null)
      setCardSubmitting(pivot.id, true)

      try {
        const response = await runExplorePivot({
          graph: buildGraph(allNodes, edges, submodels, preamble),
          node_id: node.id,
          pivot,
          source: activeSource,
          streamingChunkSize,
        })

        if (response.status === "cache_required") {
          const message = response.failure?.message ?? response.message
          persistStartFailure(
            pivot,
            key,
            calculationIdentity,
            message,
            "contract_error",
            response.failure,
          )
          return
        }

        if (response.status === "completed") {
          if (!response.result) {
            throw new Error("Pivot completed without a result")
          }
          const jobId = response.job_id ?? `cached:${key}`
          startStoredJob(pivot, key, jobId, calculationIdentity)
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
        startStoredJob(pivot, key, response.job_id, calculationIdentity)
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
        const terminalReason = executionTerminalReasonFromError(error)
        persistStartFailure(
          pivot,
          key,
          calculationIdentity,
          errorMessage(error),
          executionJobStatusFromReason(terminalReason),
          null,
          executionMetricsFromError(error),
        )
      } finally {
        setCardSubmitting(pivot.id, false)
      }
    },
    [
      activeSource,
      allNodes,
      completeJob,
      edges,
      node.id,
      persistStartFailure,
      preamble,
      setCardSubmitting,
      setNotice,
      startStoredJob,
      streamingChunkSize,
      submodels,
      updateProgress,
    ],
  )

  const cancel = useCallback(
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
        // A failed cancellation request does not prove that the calculation
        // stopped. Keep the active job so background polling can resolve it.
        setNotice(pivot.id, { message: errorMessage(error) })
      }
    },
    [completeJob, failJob, node.id, setNotice],
  )

  if (!parsed.ok) {
    return (
      <div data-testid="explore-pivots-pane" className="flex-1 p-4">
        <div
          role="alert"
          className="rounded-lg px-3 py-2 text-xs leading-relaxed"
          style={{
            color: "var(--danger)",
            background: "var(--danger-soft)",
            border: "1px solid var(--danger-border)",
          }}
        >
          {parsed.error}
        </div>
      </div>
    )
  }

  if (parsed.pivots.length === 0) {
    return (
      <div data-testid="explore-pivots-pane" className="flex flex-1">
        <EmptyPivots>Add a pivot from the Pivots settings pane.</EmptyPivots>
      </div>
    )
  }

  const enabledPivots = parsed.pivots.filter((pivot) => pivot.enabled)
  if (enabledPivots.length === 0) {
    return (
      <div data-testid="explore-pivots-pane" className="flex flex-1">
        <EmptyPivots>No pivots are currently shown.</EmptyPivots>
      </div>
    )
  }

  return (
    <div data-testid="explore-pivots-pane" className="flex-1 overflow-auto p-3">
      <div className="flex flex-col gap-3">
        {enabledPivots.map((pivot) => {
          const key = explorePivotResultKey(node.id, pivot.id)
          const cached = pivotResults[key]
          const job = pivotJobs[key]
          const isSubmitting = submitting[pivot.id] === true
          const notice = notices[pivot.id]
          const currentIdentity = pivotCalculationIdentity(pivot)
          const fresh =
            cached?.result?.dataframe_cache_key === report?.dataframe_cache_key
            && cached.calculationIdentity === currentIdentity
          const status = job?.progress
          const failure =
            status?.failure
            ?? (!job && !isSubmitting ? cached?.terminalStatus?.failure : null)
          const storedError = !job && !isSubmitting ? cached?.error : undefined
          const alertMessage = notice?.message ?? failure?.message ?? storedError
          const alertFailure = notice?.failure ?? failure

          return (
            <section
              key={pivot.id}
              role="region"
              aria-label={pivot.name}
              className="min-h-32 overflow-hidden rounded-lg"
              style={{
                background: "var(--bg-input)",
                border: "1px solid var(--border)",
              }}
            >
              <div
                className="flex items-center gap-2 px-3 py-2"
                style={{ borderBottom: "1px solid var(--border)" }}
              >
                <Table2
                  size={14}
                  aria-hidden="true"
                  style={{ color: NODE_GROUP_COLORS.explore }}
                />
                <h3
                  className="mr-auto text-xs font-semibold"
                  style={{ color: "var(--text-primary)" }}
                >
                  {pivot.name}
                </h3>
                {job ? (
                  <button
                    type="button"
                    onClick={() => void cancel(pivot, job.jobId)}
                    className="inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] font-semibold"
                    style={{
                      color: "var(--danger)",
                      border: "1px solid var(--danger-border)",
                    }}
                  >
                    <XCircle size={12} aria-hidden="true" />
                    Cancel
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => void update(pivot)}
                    disabled={pivot.values.length === 0 || isSubmitting}
                    className="inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] font-semibold disabled:opacity-45"
                    style={{
                      color: "var(--text-on-accent)",
                      background: NODE_GROUP_COLORS.explore,
                    }}
                  >
                    {isSubmitting ? (
                      <Loader2
                        size={12}
                        className="animate-spin"
                        aria-hidden="true"
                      />
                    ) : (
                      <Play size={12} aria-hidden="true" />
                    )}
                    {isSubmitting ? "Starting" : "Update"}
                  </button>
                )}
              </div>

              {pivot.values.length === 0 ? (
                <div
                  className="px-3 py-5 text-center text-[11px]"
                  style={{ color: "var(--text-muted)" }}
                >
                  Add at least one Value in this pivot&apos;s configuration.
                </div>
              ) : (
                <>
                  {job && (
                    <div
                      role="status"
                      className="flex items-center gap-2 px-3 py-2 text-[11px]"
                      style={{ color: "var(--text-muted)" }}
                    >
                      <Loader2
                        size={13}
                        className="animate-spin"
                        aria-hidden="true"
                      />
                      {status?.message || "Calculating pivot"}
                    </div>
                  )}

                  {cached?.result && (
                    <>
                      <div
                        className="px-3 py-2 text-[11px]"
                        style={{
                          color: fresh ? "var(--text-muted)" : "var(--warning)",
                        }}
                      >
                        {fresh
                          ? "Current result"
                          : "Result is out of date. Update to recalculate it."}
                      </div>
                      <PivotTableGrid result={cached.result} pivot={pivot} />
                    </>
                  )}

                  {alertMessage && (
                    <div
                      role="alert"
                      className="m-3 rounded px-3 py-2 text-[11px]"
                      style={{
                        color: "var(--danger)",
                        background: "var(--danger-soft)",
                      }}
                    >
                      {alertMessage}
                      {alertFailure?.remediation
                        ? ` ${alertFailure.remediation}`
                        : ""}
                    </div>
                  )}

                  {!cached?.result && !job && !isSubmitting && !alertMessage && (
                    <div
                      className="px-3 py-5 text-center text-[11px]"
                      style={{ color: "var(--text-muted)" }}
                    >
                      Update this pivot to calculate its full-data result.
                    </div>
                  )}
                </>
              )}
            </section>
          )
        })}
      </div>
    </div>
  )
}
