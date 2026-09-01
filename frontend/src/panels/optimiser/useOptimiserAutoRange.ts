import { useCallback, useEffect, useReducer, useRef } from "react"

import {
  cancelOptimiserFrontierAutoRange,
  getOptimiserFrontierAutoRangeStatus,
  startOptimiserFrontierAutoRange,
} from "../../api/client"
import type {
  ExecutionMetrics,
  FrontierAutoRangeStatusResponse,
  GraphPayload,
} from "../../api/types"
import useDocumentStatusStore, {
  captureDocumentExecutionFence,
  isDocumentExecutionFenceCurrent,
  type DocumentExecutionFence,
} from "../../stores/useDocumentStatusStore"
import useGraphStore from "../../stores/useGraphStore"
import useSettingsStore from "../../stores/useSettingsStore"
import {
  buildExecutionFailureMessage,
  executionErrorDetailMessage,
  executionJobStatusFromReason,
  executionMetricsFromError,
  executionTerminalReasonFromError,
} from "../../utils/executionDiagnostics"
import type { OnUpdateConfig } from "../editors"

const POLL_INTERVAL_MS = 1_000

type FrontierRange = { min: number; max: number }

type AutoRangeState = {
  generation: number
  scopeKey: string | null
  loading: boolean
  error: string | null
  terminalMetrics: ExecutionMetrics | null
  terminalStatus: string | null
  terminalReason: string | null
  terminalErrorCode: string | null
}

type AutoRangeAction =
  | { type: "begin"; generation: number; scopeKey: string }
  | {
      type: "terminal"
      generation: number
      status: FrontierAutoRangeStatusResponse
      message: string
    }
  | {
      type: "failure"
      generation: number
      error: string
      metrics: ExecutionMetrics | null
      status: string
      reason: string | null
    }
  | {
      type: "completed"
      generation: number
      scopeKey: string
      warning: string | null
    }

const initialState: AutoRangeState = {
  generation: 0,
  scopeKey: null,
  loading: false,
  error: null,
  terminalMetrics: null,
  terminalStatus: null,
  terminalReason: null,
  terminalErrorCode: null,
}

function reducer(state: AutoRangeState, action: AutoRangeAction): AutoRangeState {
  if (action.type === "begin") {
    return {
      ...initialState,
      generation: action.generation,
      scopeKey: action.scopeKey,
      loading: true,
    }
  }
  if (action.generation !== state.generation) return state

  switch (action.type) {
    case "terminal":
      return {
        ...state,
        loading: false,
        error: action.message,
        terminalMetrics: action.status.execution_metrics ?? null,
        terminalStatus: action.status.status,
        terminalReason: action.status.terminal_reason ?? action.status.status,
        terminalErrorCode: action.status.error_code ?? null,
      }
    case "failure":
      return {
        ...state,
        loading: false,
        error: action.error,
        terminalMetrics: action.metrics,
        terminalStatus: action.status,
        terminalReason: action.reason,
        terminalErrorCode: null,
      }
    case "completed":
      return {
        ...initialState,
        generation: action.generation,
        scopeKey: action.scopeKey,
        error: action.warning,
      }
  }
}

function statusDetail(detail: unknown): string | null {
  if (typeof detail === "string" && detail.trim()) return detail
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) return null

  const fields = detail as Record<string, unknown>
  for (const key of ["message", "detail", "reason", "error_code"]) {
    const value = fields[key]
    if (typeof value === "string" && value.trim()) return value
  }
  return null
}

function statusFailureMessage(status: FrontierAutoRangeStatusResponse): string {
  const message = status.message.trim()
  const detail = statusDetail(status.error_detail)
  const fallback = status.error_code?.trim()
    ? `Auto range failed (${status.error_code})`
    : "Auto range failed"
  const baseMessage = message || detail || fallback
  const memoryLimited = status.status === "memory_limited"
    || status.terminal_reason === "memory_limited"
    || status.error_code === "memory_limit"
    || status.error_code === "memory_limited"

  if (!memoryLimited) return baseMessage
  return buildExecutionFailureMessage(baseMessage, status.execution_metrics, {
    prefix: "Auto range failed",
    status: status.status,
    terminalReason: status.terminal_reason,
    errorCode: status.error_code,
  })
}

function requestErrorDetail(error: unknown): string {
  const detailMessage = executionErrorDetailMessage(error)
  if (detailMessage) return detailMessage
  if (
    error
    && typeof error === "object"
    && "detail" in error
    && typeof error.detail === "string"
  ) {
    return error.detail
  }
  return error instanceof Error ? error.message : String(error)
}

function abortableDelay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Aborted", "AbortError"))
      return
    }

    const onAbort = () => {
      clearTimeout(timeoutId)
      reject(new DOMException("Aborted", "AbortError"))
    }
    const timeoutId = setTimeout(() => {
      signal.removeEventListener("abort", onAbort)
      resolve()
    }, ms)
    signal.addEventListener("abort", onAbort, { once: true })
  })
}

function validateRanges(
  status: FrontierAutoRangeStatusResponse,
  constraintNames: readonly string[],
): Record<string, FrontierRange> {
  if (!status.result) throw new Error("Auto range completed without ranges")

  const ranges: Record<string, FrontierRange> = {}
  const missing: string[] = []
  for (const name of constraintNames) {
    const range = status.result.ranges[name]
    if (range && Number.isFinite(range.min) && Number.isFinite(range.max)) {
      ranges[name] = { min: range.min, max: range.max }
    } else {
      missing.push(name)
    }
  }
  if (missing.length > 0) {
    throw new Error(`No ranges returned for: ${missing.join(", ")}`)
  }
  if (Object.keys(ranges).length === 0) {
    throw new Error("No ranges returned for the selected constraints")
  }
  return ranges
}

type ActiveRun = {
  generation: number
  scopeKey: string
  graphVersion: number
  controller: AbortController
  jobId: string | null
  cancelledJobId: string | null
  documentFence: DocumentExecutionFence
}

export type UseOptimiserAutoRangeOptions = {
  nodeId: string
  constraintNames: readonly string[]
  buildGraph: () => GraphPayload
  onUpdate: OnUpdateConfig
}

function documentScopeKey(state: ReturnType<typeof useDocumentStatusStore.getState>): string {
  return JSON.stringify([
    state.sourceFile,
    state.sourceRevision,
    state.loadStatus,
    state.capabilities?.can_execute === true,
    state.graphSynchronized,
  ])
}

function autoRangeScopeKey(
  nodeId: string,
  graphVersion: number,
  documentKey: string,
  constraintNames: readonly string[],
): string {
  return JSON.stringify([nodeId, graphVersion, documentKey, constraintNames])
}

/** Owns optimiser frontier auto-range identity, polling, cancellation, and terminal UI state. */
export function useOptimiserAutoRange({
  nodeId,
  constraintNames,
  buildGraph,
  onUpdate,
}: UseOptimiserAutoRangeOptions) {
  const [state, dispatch] = useReducer(reducer, initialState)
  const graphVersion = useGraphStore((current) => current.structuralVersion)
  const streamingChunkSize = useSettingsStore((current) => current.streamingChunkSize)
  const currentDocumentKey = useDocumentStatusStore(documentScopeKey)
  const scopeKey = autoRangeScopeKey(
    nodeId,
    graphVersion,
    currentDocumentKey,
    constraintNames,
  )
  const generationRef = useRef(0)
  const activeRef = useRef<ActiveRun | null>(null)
  const unmountedRef = useRef(false)

  const retire = useCallback((active: ActiveRun) => {
    if (activeRef.current === active) activeRef.current = null
    active.controller.abort()

    const jobId = active.jobId
    if (!jobId || active.cancelledJobId === jobId) return
    active.cancelledJobId = jobId
    void cancelOptimiserFrontierAutoRange(jobId).catch((error) => {
      // WHY no toast: best-effort cancellation of an abandoned job; the user
      // has already moved on and the server expires the job either way.
      console.warn("cancel_optimiser_frontier_auto_range_failed", { jobId, error })
    })
  }, [])

  const isCurrent = useCallback((active: ActiveRun) => (
    !unmountedRef.current
    && activeRef.current === active
    && generationRef.current === active.generation
    && !active.controller.signal.aborted
    && useGraphStore.getState().structuralVersion === active.graphVersion
    && isDocumentExecutionFenceCurrent(active.documentFence)
  ), [])

  useEffect(() => {
    unmountedRef.current = false
    return () => {
      unmountedRef.current = true
      const active = activeRef.current
      if (active) retire(active)
    }
  }, [retire])

  useEffect(() => {
    const active = activeRef.current
    if (active && active.scopeKey !== scopeKey) retire(active)
  }, [retire, scopeKey])

  const executeRun = useCallback(async (active: ActiveRun): Promise<void> => {
    try {
      const start = await startOptimiserFrontierAutoRange({
        graph: buildGraph(),
        node_id: nodeId,
        streamingChunkSize,
        signal: active.controller.signal,
      })
      if (start.status === "started" && start.job_id) active.jobId = start.job_id
      if (!isCurrent(active)) {
        retire(active)
        return
      }
      if (start.status === "error" || !start.job_id) {
        throw new Error(start.error || "Auto range failed to start")
      }

      const jobId = start.job_id
      let status = await getOptimiserFrontierAutoRangeStatus(jobId, {
        signal: active.controller.signal,
      })
      while (status.status === "running") {
        await abortableDelay(POLL_INTERVAL_MS, active.controller.signal)
        if (!isCurrent(active)) {
          retire(active)
          return
        }
        status = await getOptimiserFrontierAutoRangeStatus(jobId, {
          signal: active.controller.signal,
        })
      }
      if (!isCurrent(active)) {
        retire(active)
        return
      }

      if (status.status === "cancelled" || status.status === "superseded") {
        dispatch({
          type: "terminal",
          generation: active.generation,
          status,
          message: status.message
            || statusDetail(status.error_detail)
            || "Auto range was cancelled",
        })
        return
      }
      if (status.status !== "completed") {
        dispatch({
          type: "terminal",
          generation: active.generation,
          status,
          message: statusFailureMessage(status),
        })
        return
      }

      const ranges = validateRanges(status, constraintNames)
      if (!isCurrent(active)) return
      const updateResult = onUpdate({ frontier_ranges: ranges })
      if (!updateResult.ok) throw new Error(updateResult.error)

      const publishedScopeKey = autoRangeScopeKey(
        nodeId,
        useGraphStore.getState().structuralVersion,
        documentScopeKey(useDocumentStatusStore.getState()),
        constraintNames,
      )
      dispatch({
        type: "completed",
        generation: active.generation,
        scopeKey: publishedScopeKey,
        warning: status.result?.warning ?? null,
      })
    } catch (error) {
      if (!isCurrent(active)) return
      const metrics = executionMetricsFromError(error)
      const reason = executionTerminalReasonFromError(error)
      dispatch({
        type: "failure",
        generation: active.generation,
        error: buildExecutionFailureMessage(requestErrorDetail(error), metrics, {
          prefix: "Auto range failed",
          terminalReason: reason,
        }),
        metrics: metrics ?? null,
        status: executionJobStatusFromReason(reason),
        reason,
      })
    } finally {
      if (activeRef.current === active) activeRef.current = null
    }
  }, [buildGraph, constraintNames, isCurrent, nodeId, onUpdate, retire, streamingChunkSize])

  const run = useCallback(() => {
    const documentFence = captureDocumentExecutionFence()
    if (!isDocumentExecutionFenceCurrent(documentFence)) return

    const previous = activeRef.current
    if (previous) retire(previous)
    const active: ActiveRun = {
      generation: ++generationRef.current,
      scopeKey,
      graphVersion,
      controller: new AbortController(),
      jobId: null,
      cancelledJobId: null,
      documentFence,
    }
    activeRef.current = active
    dispatch({ type: "begin", generation: active.generation, scopeKey })
    void executeRun(active)
  }, [executeRun, graphVersion, retire, scopeKey])

  const visibleState = state.scopeKey === scopeKey ? state : initialState
  return {
    autoRangeLoading: visibleState.loading,
    autoRangeError: visibleState.error,
    autoRangeTerminalMetrics: visibleState.terminalMetrics,
    autoRangeTerminalStatus: visibleState.terminalStatus,
    autoRangeTerminalReason: visibleState.terminalReason,
    autoRangeTerminalErrorCode: visibleState.terminalErrorCode,
    run,
  }
}
