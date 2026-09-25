import type { Node } from "@xyflow/react"
import {
  buildInputCache,
  cancelInputCacheJob,
  getInputCacheJob,
  getInputCacheStatus,
} from "../api/client"
import { TERMINAL_JOB_STATUSES, type InputCacheJobStatusResponse } from "../api/types"
import { inputSnapshotSource, type SnapshotSource } from "../utils/inputSnapshotSource"
import { JobWaitTimeoutError, waitForJob } from "./jobPollingController"

const POLL_INTERVAL_MS = 800
// A cancelled build is waited for, because a point reports itself as building
// until its job is terminal and nothing else is polling it by then.
const CANCELLATION_WAIT_MS = 60 * POLL_INTERVAL_MS
// How many times one snapshot asks the server again after waiting for a build
// it could not join, or after a joined build its owner stopped.
const MAX_BUILD_ATTEMPTS = 3

/**
 * A cancellation that the server did not accept, or whose build never reached a
 * terminal state. The caller cancelled, so this is the outcome it must report:
 * work is still running that the user asked to stop.
 */
export class CancellationFailedError extends Error {
  override name = "CancellationFailed"

  /** Every build that may still be running, so the caller can cancel them again. */
  readonly jobIds: string[]

  constructor(message: string, jobIds: string[]) {
    super(message)
    this.jobIds = jobIds
  }
}

function isCancellationFailed(error: unknown): error is CancellationFailedError {
  return (error as { name?: unknown } | null)?.name === "CancellationFailed"
}

/**
 * Cancel each build again and wait for it to stop. Raises a
 * `CancellationFailedError` naming only the builds that still did not stop.
 */
export async function cancelInputSnapshotBuilds(jobIds: string[]): Promise<void> {
  const outcomes = await Promise.allSettled(jobIds.map((jobId) => cancelInputSnapshotBuild(jobId)))
  const failures = outcomes.flatMap((outcome) =>
    outcome.status === "rejected" && isCancellationFailed(outcome.reason) ? [outcome.reason] : [],
  )
  const other = outcomes.find(
    (outcome): outcome is PromiseRejectedResult =>
      outcome.status === "rejected" && !isCancellationFailed(outcome.reason),
  )
  if (failures.length > 0) {
    throw new CancellationFailedError(
      failures.map((failure) => failure.message).join(" "),
      failures.flatMap((failure) => failure.jobIds),
    )
  }
  if (other) throw other.reason
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
   * The id of each input-snapshot build this pass started, so a caller can
   * cancel that build again itself if a cancellation fails. A build joined
   * from elsewhere is not reported: aborting only stops waiting for it.
   */
  onJobStarted?: (jobId: string) => void
  /**
   * Each running status of a build this pass waits for. Only a `bounded` build
   * streams its row count; an admitted-eager one reports none until it ends.
   */
  onBuildProgress?: (job: InputCacheJobStatusResponse) => void
}

function abortError(): DOMException {
  return new DOMException("Input snapshot ensure was cancelled.", "AbortError")
}

/** Wait for a build to end, and return how it ended. */
async function waitForBuild(
  jobId: string,
  signal: AbortSignal | undefined,
  owned: boolean,
  onBuildProgress?: (job: InputCacheJobStatusResponse) => void,
): Promise<InputCacheJobStatusResponse> {
  try {
    return await waitForJob({
      poll: (pollSignal) => getInputCacheJob(jobId, { signal: pollSignal }),
      isTerminal: (current) => TERMINAL_JOB_STATUSES.has(current.status),
      intervalMs: POLL_INTERVAL_MS,
      signal,
      onStatus: (current) => {
        if (current.status === "running") onBuildProgress?.(current)
      },
    })
  } catch (caught) {
    if (!signal?.aborted) throw caught
    // A build joined from another tab or consumer is theirs: this pass only
    // stops waiting for it.
    if (owned) await cancelInputSnapshotBuild(jobId)
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
      [jobId],
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
        [jobId],
      )
    }
    // The build was asked to stop but its state is unknown, which is a
    // failed cancellation rather than an ordinary build error: the caller
    // must keep offering to stop it.
    throw new CancellationFailedError(
      `The snapshot build could not be confirmed as stopped: ${
        caught instanceof Error ? caught.message : String(caught)
      }`,
      [jobId],
    )
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
  const sources = nodes.flatMap((node) => {
    const source = inputSnapshotSource(node)
    return source ? [source] : []
  })
  const quotes = sources.filter((source) => source.node_type === "apiInput")
  const dataInputs = sources.filter((source) => source.node_type !== "apiInput")
  if (sources.length === 0) return

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
  for (const source of quotes) {
    report("Checking Quote Input cache…")
    try {
      await ensureSnapshot(source, options, () => {
        notifyBuildStart()
        report("Caching Quote Input tables as Parquet…")
      })
    } finally {
      report(null)
    }
  }

  // Every build settles before this returns, so a stop whose cancellations
  // fail names every build still running, not only the first to fail.
  const outcomes = await Promise.allSettled(
    dataInputs.map((source) => ensureSnapshot(source, options, notifyBuildStart)),
  )
  const rejected = outcomes.flatMap((outcome) =>
    outcome.status === "rejected" ? [outcome.reason as unknown] : [],
  )
  const failedCancellations = rejected.filter(isCancellationFailed)
  if (failedCancellations.length > 0) {
    throw new CancellationFailedError(
      failedCancellations.map((failure) => failure.message).join(" "),
      failedCancellations.flatMap((failure) => failure.jobIds),
    )
  }
  if (rejected.length > 0) throw rejected[0]
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
  for (let attempt = 1; ; attempt += 1) {
    // The request itself is never aborted: once the server has admitted a job,
    // only its id can stop it, so an abort that arrives meanwhile is handled by
    // `waitForBuild`, which cancels the job it was handed if it is this pass's.
    const started = await buildInputCache({ ...source, refresh: options.force === true })
    // Only a build this pass started is its to cancel. A `blocked` answer names
    // a build this request may not join (one being cancelled, or an ordinary
    // build for a forced request): it is waited for, never cancelled.
    const owned = started.status === "running" && !started.joined
    if (owned) options.onJobStarted?.(started.job_id)
    const job = await waitForBuild(started.job_id, options.signal, owned, options.onBuildProgress)
    if (started.status === "running" && job.status === "completed") return
    // The build waited for did not build this request's snapshot: it was one
    // this request could not join, or a joined build its owner stopped. Ask again.
    const askAgain =
      started.status === "blocked" ||
      (!owned && (job.status === "cancelled" || job.status === "superseded"))
    if (!askAgain || attempt >= MAX_BUILD_ATTEMPTS) {
      throw new Error(
        started.status === "blocked"
          ? "Another build of this input kept running; try again when it has finished."
          : job.message || `Input snapshot build ${job.status}.`,
      )
    }
  }
}
