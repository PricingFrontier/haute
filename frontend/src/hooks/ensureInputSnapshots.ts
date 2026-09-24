import type { Node } from "@xyflow/react"
import {
  ApiError,
  buildInputCache,
  cancelInputCacheJob,
  getInputCacheJob,
  getInputCacheStatus,
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

function abortError(): DOMException {
  return new DOMException("Input snapshot ensure was cancelled.", "AbortError")
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

type SnapshotSource = {
  schema_version: 1
  node_type?: "apiInput"
  config: Record<string, unknown>
}

function dataInputSource(config: Record<string, unknown>): SnapshotSource {
  return { schema_version: 1, config }
}

/** A structured Quote Input: every emitting table of the node, built together. */
function quoteInputSource(config: Record<string, unknown>): SnapshotSource {
  return { schema_version: 1, node_type: "apiInput", config }
}

/**
 * Start (or join) the build and return its job id.
 *
 * The request itself is never aborted: once the server has admitted a job,
 * only its id can stop it, so an abort that arrives meanwhile is handled by
 * `waitForBuild`, which cancels the job it was handed.
 */
async function startBuild(source: SnapshotSource, refresh = false): Promise<string> {
  const payload = { ...source, refresh }
  try {
    return (await buildInputCache({ ...payload, profile: "lazy_sink" as const })).job_id
  } catch (caught) {
    const detail = caught instanceof ApiError ? caught.detail ?? "" : ""
    if (
      caught instanceof ApiError &&
      caught.status === 400 &&
      detail.startsWith("snapshot_build_unsupported")
    ) {
      return (await buildInputCache({ ...payload, profile: "preview_eager" as const })).job_id
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

  const report = (message: string | null) => {
    if (!options.signal?.aborted) options.onProgress?.(message)
  }
  // Structured Quote Inputs build their tables from one shred of the source.
  // Keep these sequential so each node owns its progress message.
  for (const config of quotes) {
    report("Checking Quote Input cache…")
    try {
      await ensureSnapshot(quoteInputSource(config), options, () => {
        notifyBuildStart()
        report("Caching Quote Input tables as Parquet…")
      })
    } finally {
      report(null)
    }
  }

  await Promise.all(
    configs.map((config) => ensureSnapshot(dataInputSource(config), options, notifyBuildStart)),
  )
}

async function ensureSnapshot(
  source: SnapshotSource,
  options: EnsureInputSnapshotsOptions,
  notifyBuildStart: () => void,
): Promise<void> {
  if (options.signal?.aborted) throw abortError()
  if (!options.force) {
    const status = options.signal
      ? await getInputCacheStatus(source, { signal: options.signal })
      : await getInputCacheStatus(source)
    // A Quote Input's stale tables are rebuilt here, in the build job, rather
    // than inside the preview's own time budget; its build writes only the
    // missing and stale tables.
    if (status.state === "ready" && !(source.node_type === "apiInput" && status.freshness === "stale")) {
      return
    }
  }

  // The build endpoint joins an existing job for "building". Corrupt and
  // failed snapshots are known-bad and are rebuilt before execution.
  notifyBuildStart()
  const jobId = await startBuild(source, options.force === true)
  options.onJobStarted?.(jobId)
  await waitForBuild(jobId, options.signal)
}
