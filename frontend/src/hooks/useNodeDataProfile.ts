import { useCallback, useEffect, useRef, useState } from "react"

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
  /** True while a profile job this consumer started is running: its node's Stop covers it. */
  ownedProfiling: boolean
  message: string
  /**
   * Why the last attempt at this data's profile failed, if it did. While it is
   * set nothing asks again on its own, so the panel offers the retry below
   * instead of leaving its panes empty with no way forward.
   */
  error: string | null
  /** Ask for the profile again, recomputing it for the current data version. */
  refresh: () => Promise<void>
  /**
   * Stop this consumer's profiling: cancel a job it started (one joined from
   * elsewhere is left running), cancel one whose id has not arrived yet as soon
   * as it does, and ask for no profile on its own until `resume` or `refresh`.
   */
  cancel: () => Promise<void>
  /** Let this consumer ask for the profile on its own again after a stop. */
  resume: () => void
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
  const epoch = useNodeDataStore((s) => s.epoch)
  const slotKey = cache.point?.slot_key ?? null
  const entry = useNodeDataStore((s) => (slotKey ? s.profiles[slotKey] : undefined))
  const job = useNodeDataStore((s) => (slotKey ? s.profileJobs[slotKey] : undefined))
  const recordedFailure = useNodeDataStore((s) => (slotKey ? s.profileFailures[slotKey] : undefined))
  const dataVersion = cache.dataVersion
  // The request currently owned by this consumer. Store resets retain the data
  // version but advance the epoch, so they deliberately create a new identity.
  const asked = useRef<string | null>(null)
  // The node whose profiling Stop paused: a job id that arrives afterwards is
  // cancelled at once, and nothing asks for that node's profile on its own
  // until it resumes. Kept by node, so another node opened in the same panel
  // profiles as usual; kept in state as well, so resuming asks again.
  const [stoppedNodes, setStoppedNodes] = useState<ReadonlySet<string>>(() => new Set())
  const stoppedNodesRef = useRef<ReadonlySet<string>>(stoppedNodes)
  const stopped = nodeId !== null && stoppedNodes.has(nodeId)
  const setStopped = useCallback((node: string | null, isStopped: boolean) => {
    if (node === null) return
    const next = new Set(stoppedNodesRef.current)
    if (isStopped) next.add(node)
    else next.delete(node)
    stoppedNodesRef.current = next
    setStoppedNodes(next)
  }, [])
  const profile = entry && entry.dataVersion === dataVersion ? entry.profile : null
  // A failure describes one attempt on one data version; rebuilt data is asked
  // for again by itself.
  const failure =
    recordedFailure && recordedFailure.dataVersion === dataVersion ? recordedFailure.message : null

  const ask = useCallback(
    async (
      askedVersion: string,
      askedEpoch: number,
      fence = captureDocumentExecutionFence(),
    ) => {
      if (!nodeId || !slotKey) return
      const response = await getNodeDataProfile({
        graph: buildGraph(allNodes, edges, submodels, preamble),
        node_id: nodeId,
        source: activeSource,
      })
      if (!isDocumentExecutionFenceCurrent(fence) || askedEpoch !== useNodeDataStore.getState().epoch) return
      if (response.status === "completed" && response.result) {
        observeProfile(slotKey, response.result)
        return
      }
      if ((response.status === "started" || response.status === "joined") && response.job_id) {
        if (response.status === "started") {
          if (nodeId !== null && stoppedNodesRef.current.has(nodeId)) {
            // Stop was pressed while this request was in flight.
            void cancelNodeData(response.job_id).catch((err: unknown) => {
              addToast(
                "error",
                `Cancelling the data profile failed: ${err instanceof Error ? err.message : String(err)}`,
              )
            })
          }
        }
        startProfileJob(slotKey, {
          jobId: response.job_id,
          message: response.message || "Profiling data",
          startedByLabel: nodeLabel,
          // The version this job profiles, so its outcome is never attributed
          // to a generation published while it ran. The response's point is
          // authoritative; a started job always has one.
          dataVersion: response.point.data_version ?? askedVersion,
          // Only a job this tab started is its to stop.
          startedHere: response.status === "started",
        })
      }
    },
    [
      activeSource,
      addToast,
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
    // A stopped consumer asks for nothing on its own until it resumes.
    if (stopped) return
    const requestKey = `${slotKey}:${dataVersion}:${epoch}`
    if (profile || job || failure || asked.current === requestKey) return
    const fence = captureDocumentExecutionFence()
    if (!isDocumentExecutionFenceCurrent(fence)) return
    asked.current = requestKey
    void ask(dataVersion, epoch, fence).catch((err: unknown) => {
      if (!isDocumentExecutionFenceCurrent(fence) || epoch !== useNodeDataStore.getState().epoch) return
      const message = err instanceof Error ? err.message : String(err)
      // Recorded as well as announced: the failure is what the retry action is
      // offered from, so a failed request is never a silent dead end.
      reportProfileFailure(slotKey, dataVersion, message)
      addToast("error", `Reading the data profile failed: ${message}`)
    }).finally(() => {
      if (asked.current === requestKey) asked.current = null
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [addToast, cache.availability, dataVersion, enabled, epoch, failure, job, profile, slotKey, stopped])

  const refresh = useCallback(async () => {
    setStopped(nodeId, false)
    if (!dataVersion) return
    if (slotKey) clearProfileFailure(slotKey)
    const fence = captureDocumentExecutionFence()
    const requestEpoch = useNodeDataStore.getState().epoch
    const requestKey = slotKey ? `${slotKey}:${dataVersion}:${requestEpoch}` : null
    asked.current = requestKey
    await ask(dataVersion, requestEpoch, fence).catch((err: unknown) => {
      if (!isDocumentExecutionFenceCurrent(fence) || requestEpoch !== useNodeDataStore.getState().epoch) return
      const message = err instanceof Error ? err.message : String(err)
      if (slotKey) reportProfileFailure(slotKey, dataVersion, message)
      addToast("error", `Reading the data profile failed: ${message}`)
    }).finally(() => {
      if (asked.current === requestKey) asked.current = null
    })
  }, [addToast, ask, clearProfileFailure, dataVersion, nodeId, reportProfileFailure, setStopped, slotKey])

  const cancel = useCallback(async () => {
    setStopped(nodeId, true)
    // The poller moves the cancelled job to its terminal state, so this only
    // asks; it never writes the shared store itself.
    if (!job?.startedHere) return
    try {
      await cancelNodeData(job.jobId)
    } catch (err: unknown) {
      addToast(
        "error",
        `Cancelling the data profile failed: ${err instanceof Error ? err.message : String(err)}`,
      )
    }
  }, [addToast, job, nodeId, setStopped])

  const resume = useCallback(() => {
    setStopped(nodeId, false)
  }, [nodeId, setStopped])

  return {
    profile,
    executionMetrics: profile ? entry?.executionMetrics ?? null : null,
    profiling: Boolean(job),
    ownedProfiling: Boolean(job?.startedHere),
    message: job?.message ?? "",
    error: failure,
    refresh,
    cancel,
    resume,
  }
}
