import { useCallback, useEffect, useRef } from "react"

import { cancelNodeData, getNodeDataProfile } from "../api/client"
import type { ExecutionMetrics, NodeDataProfile } from "../api/types"
import {
  captureDocumentExecutionFence,
  isDocumentExecutionFenceCurrent,
} from "../stores/useDocumentStatusStore"
import useNodeDataStore from "../stores/useNodeDataStore"
import useSettingsStore from "../stores/useSettingsStore"
import useToastStore from "../stores/useToastStore"
import { buildGraph } from "../utils/buildGraph"
import type { SimpleEdge, SimpleNode } from "../panels/editors"
import type { NodeDataCache } from "./useNodeDataCache"

export interface NodeDataProfileState {
  /** The profile of the data version the consumer currently reads, if any. */
  profile: NodeDataProfile | null
  executionMetrics: ExecutionMetrics | null
  /** True while the shared profile job for this data is running. */
  profiling: boolean
  message: string
  /**
   * Why the last attempt at this data's profile failed, if it did. While it is
   * set nothing asks again on its own, so the panel offers the retry below
   * instead of leaving its panes empty with no way forward.
   */
  error: string | null
  /** Ask for the profile again, recomputing it for the current data version. */
  refresh: () => Promise<void>
  /** Stop the running profile job, whichever consumer started it. */
  cancel: () => Promise<void>
}

export interface UseNodeDataProfileInput {
  node: SimpleNode | null
  allNodes: SimpleNode[]
  edges: SimpleEdge[]
  submodels?: Record<string, unknown>
  preamble?: string
  cache: NodeDataCache
  /** Set false while nothing is showing the profile, to ask for nothing. */
  enabled?: boolean
}

/**
 * The shared `profile` analysis of the data a consumer node reads.
 *
 * The profile belongs to one data version of one point, so it is asked for
 * only while that point is `current`, is stored per slot, and is recomputed by
 * the backend whenever the data version changes. The job that computes it is
 * polled once for the whole application, like a build.
 */
export default function useNodeDataProfile({
  node,
  allNodes,
  edges,
  submodels,
  preamble,
  cache,
  enabled = true,
}: UseNodeDataProfileInput): NodeDataProfileState {
  const nodeId = node?.id ?? null
  const nodeLabel = node ? String(node.data.label || node.id) : ""
  const activeSource = useSettingsStore((s) => s.activeSource)
  const addToast = useToastStore((s) => s.addToast)
  const observeProfile = useNodeDataStore((s) => s.observeProfile)
  const startProfileJob = useNodeDataStore((s) => s.startProfileJob)
  const reportProfileFailure = useNodeDataStore((s) => s.reportProfileFailure)
  const clearProfileFailure = useNodeDataStore((s) => s.clearProfileFailure)
  const slotKey = cache.point?.slot_key ?? null
  const entry = useNodeDataStore((s) => (slotKey ? s.profiles[slotKey] : undefined))
  const job = useNodeDataStore((s) => (slotKey ? s.profileJobs[slotKey] : undefined))
  const recordedFailure = useNodeDataStore((s) => (slotKey ? s.profileFailures[slotKey] : undefined))
  const dataVersion = cache.dataVersion
  // What this consumer has already asked for, so one mount asks once per data
  // version. It never affects a render, so it is a ref rather than state.
  const asked = useRef<string | null>(null)
  const profile = entry && entry.dataVersion === dataVersion ? entry.profile : null
  // A failure describes one attempt on one data version; rebuilt data is asked
  // for again by itself.
  const failure =
    recordedFailure && recordedFailure.dataVersion === dataVersion ? recordedFailure.message : null

  const ask = useCallback(
    async (askedVersion: string, fence = captureDocumentExecutionFence()) => {
      if (!nodeId || !slotKey) return
      const response = await getNodeDataProfile({
        graph: buildGraph(allNodes, edges, submodels, preamble),
        node_id: nodeId,
        source: activeSource,
      })
      if (!isDocumentExecutionFenceCurrent(fence)) return
      if (response.status === "completed" && response.result) {
        observeProfile(slotKey, response.result)
        return
      }
      if ((response.status === "started" || response.status === "joined") && response.job_id) {
        startProfileJob(slotKey, {
          jobId: response.job_id,
          message: response.message || "Profiling data",
          startedByLabel: nodeLabel,
          // The version this job profiles, so its outcome is never attributed
          // to a generation published while it ran. The response's point is
          // authoritative; a started job always has one.
          dataVersion: response.point.data_version ?? askedVersion,
        })
      }
    },
    [
      activeSource,
      allNodes,
      edges,
      nodeId,
      nodeLabel,
      observeProfile,
      preamble,
      slotKey,
      startProfileJob,
      submodels,
    ],
  )

  useEffect(() => {
    // The profile describes the whole dataset, so it is asked for only once the
    // point is current, and once per data version.
    if (!enabled || !slotKey || !dataVersion || cache.availability !== "current") return
    if (profile || job || failure || asked.current === `${slotKey}:${dataVersion}`) return
    const fence = captureDocumentExecutionFence()
    if (!isDocumentExecutionFenceCurrent(fence)) return
    asked.current = `${slotKey}:${dataVersion}`
    void ask(dataVersion, fence).catch((err: unknown) => {
      if (!isDocumentExecutionFenceCurrent(fence)) return
      const message = err instanceof Error ? err.message : String(err)
      // Recorded as well as announced: the failure is what the retry action is
      // offered from, so a failed request is never a silent dead end.
      reportProfileFailure(slotKey, dataVersion, message)
      addToast("error", `Reading the data profile failed: ${message}`)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [addToast, cache.availability, dataVersion, enabled, failure, job, profile, slotKey])

  const refresh = useCallback(async () => {
    asked.current = null
    if (!dataVersion) return
    if (slotKey) clearProfileFailure(slotKey)
    await ask(dataVersion).catch((err: unknown) => {
      const message = err instanceof Error ? err.message : String(err)
      if (slotKey) reportProfileFailure(slotKey, dataVersion, message)
      addToast("error", `Reading the data profile failed: ${message}`)
    })
  }, [addToast, ask, clearProfileFailure, dataVersion, reportProfileFailure, slotKey])

  const cancel = useCallback(async () => {
    // The poller moves the cancelled job to its terminal state, so this only
    // asks; it never writes the shared store itself.
    if (!job) return
    try {
      await cancelNodeData(job.jobId)
    } catch (err: unknown) {
      addToast(
        "error",
        `Cancelling the data profile failed: ${err instanceof Error ? err.message : String(err)}`,
      )
    }
  }, [addToast, job])

  return {
    profile,
    executionMetrics: profile ? entry?.executionMetrics ?? null : null,
    profiling: Boolean(job),
    message: job?.message ?? "",
    error: failure,
    refresh,
    cancel,
  }
}
