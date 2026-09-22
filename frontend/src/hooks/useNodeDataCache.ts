import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import {
  cancelNodeData,
  clearNodeData,
  clearInputCache,
  deleteJsonCache,
  getNodeDataPoint,
  runNodeData,
} from "../api/client"
import { nextOperationToken } from "../utils/operationToken"
import type { NodeDataColumns, NodeDataPointResponse, NodeDataRetention } from "../api/types"
import type { SimpleEdge, SimpleNode } from "../panels/editors"
import { buildNodeDataCacheIdentity } from "../panels/dataPointIdentity"
import useNodeDataStore, { columnsCoverDemand } from "../stores/useNodeDataStore"
import { hashConfig } from "../stores/useNodeResultsStore"
import {
  captureDocumentExecutionFence,
  isDocumentExecutionFenceCurrent,
  type DocumentExecutionFence,
} from "../stores/useDocumentStatusStore"
import useSettingsStore from "../stores/useSettingsStore"
import useToastStore from "../stores/useToastStore"
import { buildGraph } from "../utils/buildGraph"

/**
 * What one consumer can say about the data it reads. `checking` means no
 * authoritative answer has arrived yet for the current identity, so a consumer
 * shows it as "working on it" rather than claiming the data is missing.
 */
export type NodeDataAvailability =
  | "checking"
  | "current"
  | "partial"
  | "stale"
  | "missing"
  | "building"
  | "corrupt"

export interface NodeDataCache {
  point: NodeDataPointResponse | null
  availability: NodeDataAvailability
  /** Progress of the build of this point, whichever consumer started it. */
  progress: number
  message: string
  jobId: string | null
  busy: boolean
  rowCount: number | null
  sizeBytes: number | null
  retention: NodeDataRetention | null
  dataVersion: string | null
  columns: NodeDataColumns | null
  demand: NodeDataColumns | null
  /** True when this consumer has no build action: the point is read directly. */
  readsDirectly: boolean
  canBuild: boolean
  run: () => Promise<void>
  refresh: () => Promise<void>
  cancel: () => Promise<void>
  clear: () => Promise<void>
}

export interface UseNodeDataCacheInput {
  node: SimpleNode | null
  allNodes: SimpleNode[]
  edges: SimpleEdge[]
  submodels?: Record<string, unknown>
  preamble?: string
  /** Set false to stop asking the backend, for example while a panel is closed. */
  enabled?: boolean
}

/**
 * Derive one consumer's availability from the point's shared facts and this
 * consumer's own column demand: a generation that covers the demand is
 * `current` even when it holds fewer columns than another consumer needs.
 */
export function deriveAvailability(
  point: NodeDataPointResponse | null,
  building: boolean,
): NodeDataAvailability {
  if (building) return "building"
  if (!point) return "checking"
  if (point.kind !== "node_output") return point.state
  const generation = point.generation
  if (!generation) return point.state === "building" ? "building" : point.state
  if (!generation.fresh) return "stale"
  return columnsCoverDemand(generation.columns, point.demand) ? "current" : "partial"
}

/**
 * The consumers reading each node's data, so the node's Refresh button can ask
 * them to bring it up to date.
 *
 * This is deliberately not React state: pressing Refresh is a user event, and
 * the build belongs in that event rather than in a render that reacts to it.
 * A node can have more than one consumer open, so every one of them is asked;
 * the backend joins builds of the same data.
 */
const nodeDataRefreshHandlers = new Map<string, Set<() => void>>()

function registerNodeDataRefresh(nodeId: string, handler: () => void): () => void {
  const handlers = nodeDataRefreshHandlers.get(nodeId) ?? new Set<() => void>()
  handlers.add(handler)
  nodeDataRefreshHandlers.set(nodeId, handlers)
  return () => {
    handlers.delete(handler)
    if (handlers.size === 0) nodeDataRefreshHandlers.delete(nodeId)
  }
}

/**
 * Ask whoever reads *nodeId*'s data to cache it if what they hold is missing,
 * stale, partial or unreadable. Nothing happens for a node no open panel reads,
 * or for data that is already current.
 */
export function refreshNodeDataCache(nodeId: string): void {
  for (const handler of nodeDataRefreshHandlers.get(nodeId) ?? []) handler()
}

function producerNode(allNodes: SimpleNode[], point: NodeDataPointResponse | null): SimpleNode | null {
  if (!point) return null
  return allNodes.find((candidate) => candidate.id === point.point.producer_node_id) ?? null
}

/**
 * One consumer node's view of the data it reads: the point, its state for this
 * consumer's demand, and the actions that build, cancel, or clear it.
 *
 * Every consumer of the same point shares one slot entry in
 * {@link useNodeDataStore}, so a build started by any of them is the one they
 * all show. Snapshot-backed Data Inputs and API-input tables are built and
 * cleared through their own existing routes, which the backend names in the
 * point response; a direct-Parquet Data Input has no build action at all.
 */
export default function useNodeDataCache({
  node,
  allNodes,
  edges,
  submodels,
  preamble,
  enabled = true,
}: UseNodeDataCacheInput): NodeDataCache {
  const nodeId = node?.id ?? null
  const nodeLabel = node ? String(node.data.label || node.id) : ""
  const activeSource = useSettingsStore((s) => s.activeSource)
  const streamingChunkSize = useSettingsStore((s) => s.streamingChunkSize)
  const addToast = useToastStore((s) => s.addToast)
  const epoch = useNodeDataStore((s) => s.epoch)
  const observePoint = useNodeDataStore((s) => s.observePoint)
  const startJob = useNodeDataStore((s) => s.startJob)
  const startDelegatedBuild = useNodeDataStore((s) => s.startDelegatedBuild)
  const retainDelegatedBuild = useNodeDataStore((s) => s.retainDelegatedBuild)
  const reportDelegatedProgress = useNodeDataStore((s) => s.reportDelegatedProgress)
  const finishDelegatedBuild = useNodeDataStore((s) => s.finishDelegatedBuild)

  const identity = useMemo(
    () =>
      node
        ? buildNodeDataCacheIdentity({ node, allNodes, edges, submodels, preamble })
        : null,
    [allNodes, edges, node, preamble, submodels],
  )
  const configHash = useMemo(
    () => (identity ? hashConfig({ graph: identity, source: activeSource }) : null),
    [activeSource, identity],
  )
  // An answer is kept with the identity *and* the document it answered for, so
  // a consumer shows it only while both still hold.
  const [answered, setAnswered] = useState<{
    configHash: string
    fence: DocumentExecutionFence
    point: NodeDataPointResponse
  } | null>(null)
  const [submitting, setSubmitting] = useState(false)
  // The identity the consumer is currently asking about. Every asynchronous
  // answer is checked against it, so an answer for an identity the consumer has
  // moved past cannot replace the current one.
  const currentIdentity = useRef(configHash)
  useEffect(() => {
    currentIdentity.current = configHash
  }, [configHash])
  const point =
    answered && answered.configHash === configHash && isDocumentExecutionFenceCurrent(answered.fence)
      ? answered.point
      : null
  const slot = useNodeDataStore((s) => (point ? s.slots[point.slot_key] : undefined))
  const job = slot?.job ?? null
  const delegatedBuild = slot?.delegatedBuild ?? null
  const building = Boolean(job) || Boolean(delegatedBuild)
  const availability = deriveAvailability(point, building)
  const busy = submitting || building

  const graphPayload = useCallback(
    () => buildGraph(allNodes, edges, submodels, preamble),
    [allNodes, edges, preamble, submodels],
  )

  const fetchPoint = useCallback(
    (signal?: AbortSignal): Promise<NodeDataPointResponse | null> => {
      if (!nodeId) return Promise.resolve(null)
      return getNodeDataPoint({
        graph: graphPayload(),
        node_id: nodeId,
        source: activeSource,
        signal,
      })
    },
    [activeSource, graphPayload, nodeId],
  )

  const publish = useCallback(
    (fresh: NodeDataPointResponse, identity: string, fence: DocumentExecutionFence) => {
      // Never write another document's or another identity's answer.
      if (!isDocumentExecutionFenceCurrent(fence)) return
      if (currentIdentity.current !== identity) return
      observePoint(fresh, activeSource, identity)
      setAnswered({ configHash: identity, fence, point: fresh })
    },
    [activeSource, observePoint],
  )

  const readPoint = useCallback(
    async (fence: DocumentExecutionFence, signal?: AbortSignal): Promise<NodeDataPointResponse | null> => {
      if (!configHash) return null
      const fresh = await fetchPoint(signal)
      if (fresh) publish(fresh, configHash, fence)
      return fresh
    },
    [configHash, fetchPoint, publish],
  )

  useEffect(() => {
    if (!enabled || !nodeId || !configHash) return
    const fence = captureDocumentExecutionFence()
    if (!isDocumentExecutionFenceCurrent(fence)) return
    const controller = new AbortController()
    void fetchPoint(controller.signal)
      .then((fresh) => {
        if (!fresh || controller.signal.aborted) return
        publish(fresh, configHash, fence)
      })
      .catch((err: unknown) => {
        if (
          controller.signal.aborted ||
          !isDocumentExecutionFenceCurrent(fence) ||
          (err instanceof Error && err.name === "AbortError")
        ) {
          return
        }
        addToast(
          "error",
          `Data cache inspection failed: ${err instanceof Error ? err.message : String(err)}`,
        )
      })
    return () => controller.abort()
    // Re-asks on the consumer's identity and on the node-data epoch, which the
    // store raises whenever any consumer observes a published, widened,
    // evicted, or cleared generation. The graph objects themselves are excluded
    // deliberately: a render that keeps the same identity hash would otherwise
    // re-fire the request on unrelated edits such as a node drag.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [addToast, configHash, enabled, epoch, nodeId])

  const clearDelegatedData = useCallback(
    async (target: NodeDataPointResponse) => {
      const producer = producerNode(allNodes, target)
      const config = (producer?.data.config ?? null) as Record<string, unknown> | null
      if (!config) return
      if (target.kind === "api_input_table" && typeof config.path === "string") {
        await deleteJsonCache(config.path)
        return
      }
      await clearInputCache({ schema_version: 1, config })
    },
    [allNodes],
  )

  const buildDelegated = useCallback(
    async (target: NodeDataPointResponse, force: boolean, fence: DocumentExecutionFence) => {
      const producer = producerNode(allNodes, target)
      if (!producer) throw new Error("The delegated data point has no producer node")
      const { cancelInputSnapshotBuild, ensureInputSnapshots } = await import(
        "./ensureInputSnapshots"
      )
      const controller = new AbortController()
      const token = nextOperationToken()
      let buildJobId: string | null = null
      startDelegatedBuild(target.slot_key, {
        token,
        message: "Preparing this input",
        startedByLabel: nodeLabel,
        cancel: () => controller.abort(),
      })
      try {
        // ``force`` rebuilds the snapshot or table the ensure pass would
        // otherwise leave alone, which is what a refresh — or a point the
        // backend already reports stale — asks for. Cancelling aborts the pass,
        // which cancels the server-side build it is waiting for.
        await ensureInputSnapshots([producer as never], {
          force,
          signal: controller.signal,
          onProgress: (message) => reportDelegatedProgress(target.slot_key, token, message),
          onJobStarted: (jobId) => {
            buildJobId = jobId
          },
        })
      } catch (err) {
        if ((err as { name?: unknown } | null)?.name === "CancellationFailed" && buildJobId) {
          // The build may still be running, so the control stays live: it
          // cancels that build again instead of disappearing and leaving the
          // point building with nothing watching it.
          const jobId: string = buildJobId
          // Retained, not restarted: a pass that no longer owns this slot —
          // after a document reset and another build — changes nothing here.
          retainDelegatedBuild(target.slot_key, token, {
            message: "This build has not stopped yet; cancel it again",
            cancel: () => {
              void (async () => {
                const retryFence = captureDocumentExecutionFence()
                try {
                  await cancelInputSnapshotBuild(jobId)
                  finishDelegatedBuild(target.slot_key, token)
                  await readPoint(retryFence)
                } catch (retried) {
                  addToast(
                    "error",
                    `Cancelling the data cache failed: ${
                      retried instanceof Error ? retried.message : String(retried)
                    }`,
                  )
                }
              })()
            },
          })
          throw err
        }
        finishDelegatedBuild(target.slot_key, token)
        throw err
      }
      finishDelegatedBuild(target.slot_key, token)
      await readPoint(fence)
    },
    [
      addToast,
      allNodes,
      finishDelegatedBuild,
      nodeLabel,
      readPoint,
      reportDelegatedProgress,
      retainDelegatedBuild,
      startDelegatedBuild,
    ],
  )

  const build = useCallback(
    async (refresh: boolean) => {
      if (!nodeId || !configHash || busy) return
      const fence = captureDocumentExecutionFence()
      if (!isDocumentExecutionFenceCurrent(fence)) return
      setSubmitting(true)
      try {
        const response = await runNodeData({
          graph: graphPayload(),
          node_id: nodeId,
          source: activeSource,
          refresh,
          streamingChunkSize,
        })
        if (!isDocumentExecutionFenceCurrent(fence)) return
        publish(response.point, configHash, fence)
        if (response.status === "started" || response.status === "joined") {
          if (!response.job_id) throw new Error("Node data build did not return a job id")
          startJob(response.point.slot_key, {
            jobId: response.job_id,
            message: response.message || "Caching data",
            startedByLabel: nodeLabel,
          })
          return
        }
        if (response.status === "delegated") {
          // A snapshot-backed Data Input or API-input table is built by its own
          // route, under this slot's delegated build so every consumer of the
          // point sees it running and can cancel it.
          await buildDelegated(response.point, refresh || response.point.state === "stale", fence)
          return
        }
        if (response.cached) addToast("info", `Data already cached: ${nodeLabel}`)
      } catch (err) {
        if (!isDocumentExecutionFenceCurrent(fence)) return
        const name = (err as { name?: unknown } | null)?.name
        const detail = err instanceof Error ? err.message : String(err)
        // A cancellation the server refused leaves work running the user asked
        // to stop, so it is reported as that rather than as a failed build.
        if (name === "CancellationFailed") {
          addToast("error", `Cancelling the data cache failed: ${detail}`)
          return
        }
        // Cancelling is otherwise the outcome the user asked for, not a
        // failure. The abort arrives as a DOMException, not an Error instance.
        if (name === "AbortError") return
        addToast("error", `Data caching failed: ${detail}`)
      } finally {
        setSubmitting(false)
      }
    },
    [
      activeSource,
      addToast,
      buildDelegated,
      busy,
      configHash,
      graphPayload,
      nodeId,
      nodeLabel,
      publish,
      startJob,
      streamingChunkSize,
    ],
  )

  const run = useCallback(() => build(false), [build])
  const refresh = useCallback(() => build(true), [build])

  const canBuild = point !== null && !point.reads_directly

  /**
   * What the node's Refresh button does about this node's cached data. The
   * cost is decided here rather than at the button: data the consumer can
   * already use is left alone, so a Refresh pressed to re-read a node's
   * generated fields does not recompute a dataset that has not changed.
   * `missing` has nothing to replace, so it builds; `stale`, `partial` and
   * `corrupt` hold something unusable as it stands, so they rebuild.
   */
  // Read when Refresh is pressed rather than captured when the handler was
  // registered, so a handler registered on an early render still decides
  // against what the consumer holds now. It settles one commit after the
  // render it describes, which a user's click is never inside.
  const latest = useRef({ availability, busy, canBuild, enabled, run, refresh })
  useEffect(() => {
    latest.current = { availability, busy, canBuild, enabled, run, refresh }
  })

  const bringUpToDate = useCallback(() => {
    const { availability, busy, canBuild, enabled, run, refresh } = latest.current
    if (!enabled || !canBuild || busy) return
    // `checking` has no answer yet, so there is nothing to decide against.
    if (availability === "current" || availability === "building" || availability === "checking") {
      return
    }
    void (availability === "missing" ? run() : refresh())
  }, [])

  useEffect(() => {
    if (!nodeId) return
    return registerNodeDataRefresh(nodeId, bringUpToDate)
  }, [bringUpToDate, nodeId])

  const cancel = useCallback(async () => {
    const fence = captureDocumentExecutionFence()
    if (delegatedBuild && !job) {
      // A delegated build has no node-data job; its canceller aborts the
      // orchestration whichever consumer started it.
      delegatedBuild.cancel()
      return
    }
    if (!job) return
    try {
      await cancelNodeData(job.jobId)
      await readPoint(fence)
    } catch (err) {
      addToast("error", `Cancelling the data cache failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }, [addToast, delegatedBuild, job, readPoint])

  const clear = useCallback(async () => {
    if (!nodeId || !configHash) return
    const fence = captureDocumentExecutionFence()
    if (!isDocumentExecutionFenceCurrent(fence)) return
    try {
      const response = await clearNodeData({
        graph: graphPayload(),
        node_id: nodeId,
        source: activeSource,
      })
      if (!isDocumentExecutionFenceCurrent(fence)) return
      publish(response.point, configHash, fence)
      if (response.status === "delegated") {
        // The node-data clear removed the point's analyses; its data belongs to
        // the input-snapshot or JSON cache, cleared through its own route.
        await clearDelegatedData(response.point)
      }
      await readPoint(fence)
    } catch (err) {
      addToast("error", `Clearing the data cache failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }, [
    activeSource,
    addToast,
    clearDelegatedData,
    configHash,
    graphPayload,
    nodeId,
    publish,
    readPoint,
  ])

  return {
    point,
    availability,
    progress: job ? job.progress : availability === "current" ? 1 : 0,
    message: job?.message ?? delegatedBuild?.message ?? point?.job?.message ?? "",
    jobId: job?.jobId ?? null,
    busy,
    rowCount: point?.generation?.row_count ?? point?.row_count ?? null,
    sizeBytes: point?.generation?.size_bytes ?? point?.size_bytes ?? null,
    retention: point?.generation?.retention ?? point?.retention ?? null,
    dataVersion: point?.data_version ?? null,
    columns: point?.generation?.columns ?? null,
    demand: point?.demand ?? null,
    readsDirectly: point?.reads_directly ?? false,
    canBuild,
    run,
    refresh,
    cancel,
    clear,
  }
}
