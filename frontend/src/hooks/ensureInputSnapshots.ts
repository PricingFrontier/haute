import type { Node } from "@xyflow/react"
import {
  ApiError,
  buildInputCache,
  cancelInputCacheJob,
  deleteJsonCache,
  getInputCacheJob,
  getInputCacheStatus,
  buildJsonCache,
  getJsonCacheStatusForSchema,
  getJsonCacheProgress,
} from "../api/client"
import { TERMINAL_JOB_STATUSES } from "../api/types"
import { dataInputIsDirect } from "../utils/dataInputMode"
import { NODE_TYPES } from "../utils/nodeTypes"
import { JobWaitTimeoutError, waitForJob } from "./jobPollingController"

const POLL_INTERVAL_MS = 800
// A cancelled build is waited for, because a point reports itself as building
// until its job is terminal and nothing else is polling it by then.
const CANCELLATION_WAIT_MS = 60 * POLL_INTERVAL_MS

/**
 * A cancellation that the server did not accept, or whose build never reached a
 * terminal state. The caller cancelled, so this is the outcome it must report:
 * work is still running that the user asked to stop.
 */
export class CancellationFailedError extends Error {
  override name = "CancellationFailed"
}

export interface EnsureInputSnapshotsOptions {
  /** Called at most once when this ensure pass starts or joins any build. */
  onBuildStart?: () => void
  signal?: AbortSignal
  /** Current Quote Input cache preparation phase; null when preparation ends. */
  onProgress?: (message: string | null) => void
  /**
   * Rebuild every snapshot and cache this pass covers, even one that is already
   * served. Without it a ready — including a stale — snapshot and a cached table
   * are left as they are, which is what a preview wants; a user asking for a
   * refresh wants the data recomputed.
   */
  force?: boolean
  /**
   * The id of each input-snapshot build this pass started or joined, so a
   * caller can cancel that build again itself if a cancellation fails.
   */
  onJobStarted?: (jobId: string) => void
}

function snapshotConfigs(nodes: Node[]): Record<string, unknown>[] {
  return nodes.flatMap((node) => {
    const data = node.data as {
      nodeType?: unknown
      config?: unknown
    }
    if (
      data.nodeType !== NODE_TYPES.DATA_INPUT ||
      typeof data.config !== "object" ||
      data.config === null ||
      Array.isArray(data.config)
    ) {
      return []
    }
    const config = data.config as Record<string, unknown>
    if (dataInputIsDirect(config)) return []
    return [config]
  })
}

function quoteInputConfigs(nodes: Node[]): Record<string, unknown>[] {
  return nodes.flatMap((node) => {
    const { nodeType, config } = node.data
    if (nodeType !== NODE_TYPES.API_INPUT || !config || typeof config !== "object" || Array.isArray(config)) return []
    const value = config as Record<string, unknown>
    return Object.prototype.hasOwnProperty.call(value, "tables") &&
      typeof value.path === "string" && /\.(json|jsonl|ndjson|xml)$/i.test(value.path)
      ? [value] : []
  })
}

async function ensureQuoteInputCache(
  config: Record<string, unknown>,
  options: EnsureInputSnapshotsOptions,
  notifyBuildStart: () => void,
): Promise<void> {
  if (options.signal?.aborted) throw abortError()
  const path = config.path as string
  const payload = { path, volatile_schema: config }
  const report = (message: string | null) => {
    if (!options.signal?.aborted) options.onProgress?.(message)
  }
  report("Checking Quote Input cache…")
  try {
    if (options.force) {
      // The build endpoint answers a valid working cache with no work at all,
      // so a forced rebuild removes that layer first. The committed layer of a
      // saved pipeline is untouched; the working layer is what a read serves.
      report("Replacing the Quote Input cache…")
      await deleteJsonCache(path, options.signal ? { signal: options.signal } : undefined)
      if (options.signal?.aborted) throw abortError()
    }
    if (!options.force) {
      const status = options.signal
        ? await getJsonCacheStatusForSchema(payload, { signal: options.signal })
        : await getJsonCacheStatusForSchema(payload)
      if (options.signal?.aborted) throw abortError()
      if (status.cached) return
    }
    notifyBuildStart()
    report("Caching Quote Input as Parquet…")
    const controller = new AbortController()
    const onAbort = () => controller.abort()
    options.signal?.addEventListener("abort", onAbort, { once: true })
    const pollProgress = async () => {
      try {
        // eslint-disable-next-line no-restricted-syntax -- JSON-cache progress, removed with the json-cache routes in CACHE-S08
        for (;;) {
          await waitForNextPoll(controller.signal)
          const progress = await getJsonCacheProgress(path, { signal: controller.signal })
          if (!controller.signal.aborted && progress.active) {
            const rowCount = progress.rows ?? 0
            const rows = rowCount > 0 ? ` · ${rowCount.toLocaleString()} rows` : ""
            report(`Caching Quote Input as Parquet…${rows} · ${Math.floor(progress.elapsed ?? 0)}s`)
          }
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          // Progress display is presentational; only the build outcome may
          // decide this preparation. Stop polling and keep the last message.
          console.warn("Quote Input cache progress polling stopped:", error)
        }
      }
    }
    try {
      await Promise.all([
        buildJsonCache(payload, { signal: controller.signal }).finally(() => controller.abort()),
        pollProgress(),
      ])
      if (options.signal?.aborted) throw abortError()
    } finally {
      controller.abort()
      options.signal?.removeEventListener("abort", onAbort)
    }
  } finally {
    report(null)
  }
}

function abortError(): DOMException {
  return new DOMException("Input snapshot ensure was cancelled.", "AbortError")
}

function waitForNextPoll(signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.reject(abortError())
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      clearTimeout(timer)
      reject(abortError())
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort)
      resolve()
    }, POLL_INTERVAL_MS)
    signal?.addEventListener("abort", onAbort, { once: true })
  })
}

async function waitForBuild(
  jobId: string,
  signal?: AbortSignal,
): Promise<void> {
  try {
    const job = await waitForJob({
      poll: (pollSignal) => getInputCacheJob(jobId, { signal: pollSignal }),
      isTerminal: (current) => TERMINAL_JOB_STATUSES.has(current.status),
      intervalMs: POLL_INTERVAL_MS,
      signal,
    })
    if (job.status !== "completed") {
      throw new Error(job.message || `Input snapshot build ${job.status}.`)
    }
  } catch (caught) {
    if (!signal?.aborted) throw caught
    await cancelInputSnapshotBuild(jobId)
    throw abortError()
  }
}

/**
 * Cancel a build and wait for it to stop.
 *
 * The endpoint only acknowledges the request — the job can still be running
 * when it answers — and a point keeps reporting itself as building until that
 * job is terminal, so the wait here is what lets the caller report a point that
 * is no longer being built. A refused cancellation, or one whose build never
 * terminates, is raised rather than passed off as a completed cancellation.
 */
export async function cancelInputSnapshotBuild(jobId: string): Promise<void> {
  let acknowledged: { status: string }
  try {
    acknowledged = await cancelInputCacheJob(jobId)
  } catch (caught) {
    throw new CancellationFailedError(
      `The snapshot build could not be cancelled: ${
        caught instanceof Error ? caught.message : String(caught)
      }`,
    )
  }
  if (TERMINAL_JOB_STATUSES.has(acknowledged.status as never)) return
  try {
    await waitForJob({
      poll: (signal) => getInputCacheJob(jobId, { signal }),
      isTerminal: (job) => TERMINAL_JOB_STATUSES.has(job.status),
      intervalMs: POLL_INTERVAL_MS,
      timeoutMs: CANCELLATION_WAIT_MS,
    })
  } catch (caught) {
    if (caught instanceof JobWaitTimeoutError) {
      throw new CancellationFailedError(
        "The snapshot build did not stop after it was cancelled; it may still be running.",
      )
    }
    // The build was asked to stop but its state is unknown, which is a
    // failed cancellation rather than an ordinary build error: the caller
    // must keep offering to stop it.
    throw new CancellationFailedError(
      `The snapshot build could not be confirmed as stopped: ${
        caught instanceof Error ? caught.message : String(caught)
      }`,
    )
  }
}

async function startBuild(
  config: Record<string, unknown>,
  signal?: AbortSignal,
  refresh = false,
): Promise<string> {
  const payload = {
    schema_version: 1 as const,
    config,
    refresh,
  }
  try {
    const request = { ...payload, profile: "lazy_sink" as const }
    return (
      signal
        ? await buildInputCache(request, { signal })
        : await buildInputCache(request)
    ).job_id
  } catch (caught) {
    const detail = caught instanceof ApiError ? caught.detail ?? "" : ""
    if (
      caught instanceof ApiError &&
      caught.status === 400 &&
      detail.startsWith("snapshot_build_unsupported")
    ) {
      const request = { ...payload, profile: "preview_eager" as const }
      return (
        signal
          ? await buildInputCache(request, { signal })
          : await buildInputCache(request)
      ).job_id
    }
    throw caught
  }
}

/**
 * Build (or join the build of) every unavailable snapshot the graph needs.
 * A ready snapshot is served as published even when its freshness is stale,
 * unless ``force`` asks for every snapshot and cache to be rebuilt.
 */
export async function ensureInputSnapshots(
  nodes: Node[],
  options: EnsureInputSnapshotsOptions = {},
): Promise<void> {
  const configs = snapshotConfigs(nodes)
  const quotes = quoteInputConfigs(nodes)
  if (configs.length === 0 && quotes.length === 0) return

  let buildNotified = false
  const notifyBuildStart = () => {
    if (buildNotified) return
    buildNotified = true
    options.onBuildStart?.()
  }

  // Structured Quote Inputs use the existing per-frame full-cache builder.
  // Keep these sequential so each schema owns its progress and publication.
  for (const config of quotes) {
    await ensureQuoteInputCache(config, options, notifyBuildStart)
  }

  await Promise.all(
    configs.map(async (config) => {
      if (!options.force) {
        const payload = { schema_version: 1 as const, config }
        const status = options.signal
          ? await getInputCacheStatus(payload, { signal: options.signal })
          : await getInputCacheStatus(payload)
        if (status.state === "ready") return
      }

      // The build endpoint joins an existing job for "building". Corrupt and
      // failed snapshots are known-bad and are rebuilt before execution.
      notifyBuildStart()
      const jobId = await startBuild(config, options.signal, options.force === true)
      options.onJobStarted?.(jobId)
      await waitForBuild(jobId, options.signal)
    }),
  )
}
