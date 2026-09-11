import type { Node } from "@xyflow/react"
import {
  ApiError,
  buildInputCache,
  getInputCacheJob,
  getInputCacheStatus,
  buildJsonCache,
  getJsonCacheStatusForSchema,
  getJsonCacheProgress,
} from "../api/client"
import { TERMINAL_JOB_STATUSES } from "../api/types"
import { dataInputIsDirect } from "../utils/dataInputMode"
import { NODE_TYPES } from "../utils/nodeTypes"

const POLL_INTERVAL_MS = 800

export interface EnsureInputSnapshotsOptions {
  /** Called at most once when this ensure pass starts or joins any build. */
  onBuildStart?: () => void
  signal?: AbortSignal
  /** Current Quote Input cache preparation phase; null when preparation ends. */
  onProgress?: (message: string | null) => void
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
    const status = options.signal
      ? await getJsonCacheStatusForSchema(payload, { signal: options.signal })
      : await getJsonCacheStatusForSchema(payload)
    if (options.signal?.aborted) throw abortError()
    if (status.cached) return
    notifyBuildStart()
    report("Caching Quote Input as Parquet…")
    const controller = new AbortController()
    const onAbort = () => controller.abort()
    options.signal?.addEventListener("abort", onAbort, { once: true })
    const pollProgress = async () => {
      try {
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
