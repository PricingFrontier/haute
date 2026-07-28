import { useRef, useState } from "react"
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

type SnapshotButtonStatus = {
  cached: boolean
  row_count: number
  column_count: number
  size_bytes: number
  created_at: number
  freshness: InputCacheSnapshotResponse["freshness"]
}

function toButtonStatus(snapshot: InputCacheSnapshotResponse): SnapshotButtonStatus {
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

async function pollJobToTerminal(jobId: string): Promise<InputCacheJobStatusResponse> {
  for (;;) {
    const job = await getInputCacheJob(jobId)
    if (TERMINAL_JOB_STATUSES.has(job.status)) return job
    await new Promise((resolve) => window.setTimeout(resolve, 800))
  }
}

export default function InputSnapshotCacheButton({
  config,
  admittedEager,
  requiredReady,
}: {
  config: Record<string, unknown>
  admittedEager: boolean
  requiredReady: boolean
}) {
  const resourceKey = requiredReady ? JSON.stringify(config) : ""
  const activeJobRef = useRef<{
    resourceKey: string
    jobId: string
  } | null>(null)
  const cachedRef = useRef({ resourceKey, cached: false })
  const [trackedStatus, setTrackedStatus] = useState<{
    resourceKey: string
    cached: boolean
    freshness: SnapshotButtonStatus["freshness"]
  }>({ resourceKey, cached: false, freshness: "unknown" })
  const payload = { schema_version: 1 as const, config }
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
        startFetch={async () => {
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
            job = await pollJobToTerminal(started.job_id)
          } finally {
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
        getProgress={async () => {
          const activeJob = activeJobRef.current
          if (!activeJob || activeJob.resourceKey !== resourceKey) {
            return { active: false }
          }
          const job = await getInputCacheJob(activeJob.jobId)
          return {
            active: job.status === "running",
            rows: job.progress.rows,
            elapsed: Math.round(job.progress.elapsed_seconds),
            phase: job.progress.phase,
          }
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
          notCachedHint: "No cache yet — the first run creates it automatically",
        }}
        disabled={!requiredReady}
        disabledReason="Complete the required source fields to cache as Parquet."
      />
      {activeStatus.cached &&
        activeStatus.freshness === "stale" && (
          <p
            className="mt-1 text-[10px] px-1"
            style={{ color: "var(--warning-strong)" }}
          >
            Source changed since cache — Refresh to update.
          </p>
        )}
    </div>
  )
}
