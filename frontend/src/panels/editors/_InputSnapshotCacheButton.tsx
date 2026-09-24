import { useEffect, useRef, useState } from "react"
import {
  CacheFetchButton,
  PARQUET_CACHE_LABELS,
} from "../../components/CacheFetchButton"
import {
  buildInputCache,
  cancelInputCacheJob,
  clearInputCache,
  getInputCacheJob,
  getInputCacheStatus,
} from "../../api/client"
import {
  TERMINAL_JOB_STATUSES,
  type InputCacheJobStatusResponse,
  type InputCacheSnapshotResponse,
} from "../../api/types"
import { waitForJob } from "../../hooks/jobPollingController"

type SnapshotButtonStatus = {
  cached: boolean
  row_count: number
  column_count: number
  size_bytes: number
  created_at: number
  freshness: InputCacheSnapshotResponse["freshness"]
}

function toButtonStatus(snapshot: InputCacheSnapshotResponse): SnapshotButtonStatus {
  const tables = snapshot.tables
  if (tables) {
    // A structured API Input: its emitting tables together, as one control.
    const generations = tables.flatMap((table) => (table.generation ? [table.generation] : []))
    return {
      cached: snapshot.state === "ready",
      row_count: generations.reduce((total, generation) => total + generation.row_count, 0),
      column_count: generations.reduce((total, generation) => total + generation.column_count, 0),
      size_bytes: generations.reduce((total, generation) => total + generation.size_bytes, 0),
      created_at: generations.reduce((latest, generation) => Math.max(latest, generation.created_at), 0),
      freshness: snapshot.freshness,
    }
  }
  const generation = snapshot.generation
  return {
    cached: snapshot.state === "ready",
    row_count: generation?.row_count ?? 0,
    column_count: generation?.column_count ?? 0,
    size_bytes: generation?.size_bytes ?? 0,
    created_at: generation?.created_at ?? 0,
    freshness: snapshot.freshness,
  }
}

const BUILD_POLL_INTERVAL_MS = 800

export default function InputSnapshotCacheButton({
  config,
  admittedEager,
  requiredReady,
  nodeType = "dataInput",
  disabledReason = "Complete the required source fields to cache as Parquet.",
}: {
  config: Record<string, unknown>
  admittedEager: boolean
  requiredReady: boolean
  /** A structured API Input caches every emitting table of the node together. */
  nodeType?: "dataInput" | "apiInput"
  disabledReason?: string
}) {
  const resourceKey = requiredReady ? JSON.stringify(config) : ""
  const activeJobRef = useRef<{
    resourceKey: string
    jobId: string
  } | null>(null)
  const cachedRef = useRef({ resourceKey, cached: false })
  // The build wait belongs to this configuration: unmounting or changing it
  // stops polling, while the server build runs on for whoever needs it next.
  const buildWaitRef = useRef<AbortController | null>(null)
  useEffect(() => () => buildWaitRef.current?.abort(), [resourceKey])
  const [trackedStatus, setTrackedStatus] = useState<{
    resourceKey: string
    cached: boolean
    freshness: SnapshotButtonStatus["freshness"]
  }>({ resourceKey, cached: false, freshness: "unknown" })
  const payload =
    nodeType === "apiInput"
      ? { schema_version: 1 as const, node_type: nodeType, config }
      : { schema_version: 1 as const, config }
  const activeStatus =
    trackedStatus.resourceKey === resourceKey
      ? trackedStatus
      : { resourceKey, cached: false, freshness: "unknown" as const }

  const track = (status: SnapshotButtonStatus): SnapshotButtonStatus => {
    cachedRef.current = { resourceKey, cached: status.cached }
    setTrackedStatus({
      resourceKey,
      cached: status.cached,
      freshness: status.freshness,
    })
    return status
  }

  return (
    <div>
      <CacheFetchButton<SnapshotButtonStatus>
        resourceKey={resourceKey}
        getStatus={() => getInputCacheStatus(payload).then(toButtonStatus).then(track)}
        startFetch={async (_key, onProgress) => {
          const buildWait = new AbortController()
          buildWaitRef.current = buildWait
          const refresh =
            cachedRef.current.resourceKey === resourceKey &&
            cachedRef.current.cached
          const started = await buildInputCache({
            ...payload,
            refresh,
            profile: admittedEager ? "preview_eager" : "lazy_sink",
          })
          const activeJob = { resourceKey, jobId: started.job_id }
          activeJobRef.current = activeJob
          let job: InputCacheJobStatusResponse
          try {
            job = await waitForJob({
              poll: (signal) => getInputCacheJob(started.job_id, { signal }),
              isTerminal: (current) => TERMINAL_JOB_STATUSES.has(current.status),
              intervalMs: BUILD_POLL_INTERVAL_MS,
              signal: buildWait.signal,
              // The wait's own statuses are the button's progress: no second poll.
              onStatus: (current) => {
                if (current.status !== "running") return
                onProgress({
                  rows: current.progress.rows,
                  elapsed: Math.round(current.progress.elapsed_seconds),
                  phase: current.progress.phase,
                })
              },
            })
          } finally {
            if (buildWaitRef.current === buildWait) buildWaitRef.current = null
            if (
              activeJobRef.current?.resourceKey === activeJob.resourceKey &&
              activeJobRef.current.jobId === activeJob.jobId
            ) {
              activeJobRef.current = null
            }
          }
          if (job.status !== "completed" || !job.snapshot) {
            throw new Error(job.message || "Snapshot build failed.")
          }
          return track(toButtonStatus(job.snapshot))
        }}
        cancelFetch={() => {
          const activeJob = activeJobRef.current
          return activeJob?.resourceKey === resourceKey
            ? cancelInputCacheJob(activeJob.jobId)
            : Promise.resolve(null)
        }}
        deleteCache={() => clearInputCache(payload).then(toButtonStatus).then(track)}
        timestampField="created_at"
        labels={{
          ...PARQUET_CACHE_LABELS,
          notCachedHint: "No cache yet - the first run creates it automatically",
        }}
        disabled={!requiredReady}
        disabledReason={disabledReason}
      />
      {activeStatus.cached &&
        activeStatus.freshness === "stale" && (
          <p
            className="mt-1 text-[10px] px-1"
            style={{ color: "var(--warning-strong)" }}
          >
            Source changed since cache - Refresh to update.
          </p>
        )}
    </div>
  )
}
