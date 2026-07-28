import type { Node } from "@xyflow/react"
import {
  ApiError,
  buildInputCache,
  getInputCacheJob,
  getInputCacheStatus,
} from "../api/client"
import { TERMINAL_JOB_STATUSES } from "../api/types"
import { dataInputIsDirect } from "../utils/dataInputMode"
import { NODE_TYPES } from "../utils/nodeTypes"

const POLL_INTERVAL_MS = 800

export interface EnsureInputSnapshotsOptions {
  /** Called at most once when this ensure pass starts or joins any build. */
  onBuildStart?: () => void
  signal?: AbortSignal
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

async function waitForJob(
  jobId: string,
  signal?: AbortSignal,
): Promise<void> {
  for (;;) {
    if (signal?.aborted) throw abortError()
    const job = signal
      ? await getInputCacheJob(jobId, { signal })
      : await getInputCacheJob(jobId)
    if (job.status === "completed") return
    if (TERMINAL_JOB_STATUSES.has(job.status)) {
      throw new Error(job.message || `Input snapshot build ${job.status}.`)
    }
    await waitForNextPoll(signal)
  }
}

async function startBuild(
  config: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<string> {
  const payload = {
    schema_version: 1 as const,
    config,
    refresh: false,
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
 * A ready snapshot is served as published even when its freshness is stale.
 */
export async function ensureInputSnapshots(
  nodes: Node[],
  options: EnsureInputSnapshotsOptions = {},
): Promise<void> {
  const configs = snapshotConfigs(nodes)
  if (configs.length === 0) return

  let buildNotified = false
  const notifyBuildStart = () => {
    if (buildNotified) return
    buildNotified = true
    options.onBuildStart?.()
  }

  await Promise.all(
    configs.map(async (config) => {
      const payload = { schema_version: 1 as const, config }
      const status = options.signal
        ? await getInputCacheStatus(payload, { signal: options.signal })
        : await getInputCacheStatus(payload)
      if (status.state === "ready") return

      // The build endpoint joins an existing job for "building". Corrupt and
      // failed snapshots are known-bad and are rebuilt before execution.
      notifyBuildStart()
      await waitForJob(
        await startBuild(config, options.signal),
        options.signal,
      )
    }),
  )
}
