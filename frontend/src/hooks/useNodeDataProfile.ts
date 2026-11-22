import { useRequestScope, useResultKey } from "../panels/useGraph"
import { useCallback, useEffect, useRef, useState } from "react"

import { cancelNodeData, getNodeDataProfile } from "../api/client"
import type { ExecutionMetrics, NodeDataProfile } from "../api/types"
import {
  captureDocumentExecutionFence,
  executionPart,
  isDocumentExecutionFenceCurrent,
} from "../stores/useDocumentStatusStore"
import useNodeDataStore from "../stores/useNodeDataStore"
import useSettingsStore from "../stores/useSettingsStore"
import useToastStore from "../stores/useToastStore"
import { canvasTarget, prepareNodeRequest } from "../utils/requestGateway"
import { answer, watch } from "../utils/answers"
import type { SimpleEdge, SimpleNode } from "../panels/editors/_shared"
import type { NodeDataCache } from "./useNodeDataCache"
import { apiErrorMessage } from "../api/errors"

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
  const requestScope = useRequestScope()
  const nodeId = node?.id ?? null
  // A Stop pauses the node by its result key: inside a submodel, its occurrence's.
  const nodeKey = useResultKey(nodeId)
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
  // until it resumes. Kept by the node's result key, so another node opened in
  // the same panel, or the same child in another occurrence, profiles as usual;
  // kept in state as well, so resuming asks again.
  const [stoppedNodes, setStoppedNodes] = useState<ReadonlySet<string>>(() => new Set())
  const stoppedNodesRef = useRef<ReadonlySet<string>>(stoppedNodes)
  const stopped = nodeKey !== null && stoppedNodes.has(nodeKey)
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

  /** A failed read of the profile, recorded as well as announced: the failure is what the
   *  retry action is offered from, so a failed request is never a silent dead end. */
  const failedRead = useCallback((version: string) => (err: unknown) => {
    const message = apiErrorMessage(err)
    if (slotKey) reportProfileFailure(slotKey, version, message)
    addToast("error", `Reading the data profile failed: ${message}`)
  }, [addToast, reportProfileFailure, slotKey])

  const cancelFailed = useCallback((err: unknown) => addToast(
    "error",
    `Cancelling the data profile failed: ${apiErrorMessage(err)}`,
  ), [addToast])

  const askProfile = useCallback(
    async (askedVersion: string, askedEpoch: number, requestKey: string | null) => {
      if (!nodeId || !slotKey) return
      // The graph is read from the store; listing the panel's graph and the preamble
      // renews this callback.
      void allNodes
      void edges
      void submodels
      void preamble
      // Taken before the request: a Stop for this node, pressed while it is in
      // flight, is read by it after the answer.
      const askedKey = nodeKey
      // The profile belongs to the document as it was asked of, at the node-data epoch the
      // consumer asked at; while it is asked, the same request is not asked again.
      await answer([
        executionPart(),
        watch(useNodeDataStore, (state) => state.epoch, (_asked, now) => now === askedEpoch),
      ], () => {
        // Inside an open submodel, the whole pipeline and the node's address.
        const target = prepareNodeRequest(requestScope, canvasTarget(nodeId))
        return getNodeDataProfile({
          graph: target.graph,
          target: target.node,
          source: activeSource,
        })
      }, {
        ok: (response) => {
          if (response.status === "completed" && response.result) {
            observeProfile(slotKey, response.result)
            return
          }
          if ((response.status === "started" || response.status === "joined") && response.job_id) {
            if (response.status === "started" && askedKey !== null && stoppedNodesRef.current.has(askedKey)) {
              // Stop was pressed while this request was in flight.
              void answer([executionPart()], cancelNodeData(response.job_id), { failed: cancelFailed })
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
        failed: failedRead(askedVersion),
      }, {
        pending: (waiting) => {
          if (waiting) asked.current = requestKey
          else if (asked.current === requestKey) asked.current = null
        },
      })
    },
    [
      requestScope,
      activeSource,
      allNodes,
      cancelFailed,
      edges,
      failedRead,
      nodeId,
      nodeKey,
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
    if (!isDocumentExecutionFenceCurrent(captureDocumentExecutionFence())) return
    void askProfile(dataVersion, epoch, requestKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cache.availability, dataVersion, enabled, epoch, failure, job, profile, slotKey, stopped])

  const refresh = useCallback(async () => {
    setStopped(nodeKey, false)
    if (!dataVersion) return
    if (slotKey) clearProfileFailure(slotKey)
    const requestEpoch = useNodeDataStore.getState().epoch
    await askProfile(dataVersion, requestEpoch, slotKey ? `${slotKey}:${dataVersion}:${requestEpoch}` : null)
  }, [askProfile, clearProfileFailure, dataVersion, nodeKey, setStopped, slotKey])

  const cancel = useCallback(async () => {
    setStopped(nodeKey, true)
    // The poller moves the cancelled job to its terminal state, so this only
    // asks; it never writes the shared store itself.
    if (!job?.startedHere) return
    await answer([executionPart()], cancelNodeData(job.jobId), { failed: cancelFailed })
  }, [cancelFailed, job, nodeKey, setStopped])

  const resume = useCallback(() => {
    setStopped(nodeKey, false)
  }, [nodeKey, setStopped])

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
