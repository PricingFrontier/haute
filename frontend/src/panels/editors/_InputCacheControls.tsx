import { useEffect, useState } from "react"
import {
  buildInputCache,
  cancelInputCacheJob,
  clearInputCache,
  getInputCacheJob,
  getInputCacheStatus,
} from "../../api/client"
import type {
  InputCacheJobStatusResponse,
  InputCacheSnapshotResponse,
} from "../../api/types"
import { INPUT_STYLE } from "./_shared"

type CacheView = {
  identity: string
  snapshot: InputCacheSnapshotResponse | null
  jobId: string | null
  job: InputCacheJobStatusResponse | null
  message: string | null
  cancelling: boolean
  action: "build" | "clear" | null
}

function initialView(identity: string): CacheView {
  return {
    identity,
    snapshot: null,
    jobId: null,
    job: null,
    message: null,
    cancelling: false,
    action: null,
  }
}

function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof Error ? caught.message : fallback
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`
}

export default function InputCacheControls({
  config,
  cacheable,
  admittedEager,
  requiredReady,
}: {
  config: Record<string, unknown>
  cacheable: boolean
  admittedEager: boolean
  requiredReady: boolean
}) {
  const identity = JSON.stringify(config)
  const [view, setView] = useState<CacheView>(() => initialView(identity))

  if (view.identity !== identity) {
    setView(initialView(identity))
  }

  useEffect(() => {
    if (!cacheable) return
    let cancelled = false
    const requestIdentity = identity

    void getInputCacheStatus({ schema_version: 1, config })
      .then((snapshot) => {
        if (cancelled) return
        setView((current) =>
          current.identity === requestIdentity
            ? { ...current, snapshot, message: null }
            : current,
        )
      })
      .catch((caught: unknown) => {
        if (cancelled) return
        setView((current) =>
          current.identity === requestIdentity
            ? {
                ...current,
                message: errorMessage(caught, "Cache status failed."),
              }
            : current,
        )
      })

    return () => {
      cancelled = true
    }
  }, [cacheable, config, identity])

  useEffect(() => {
    if (!cacheable || !view.jobId) return
    let cancelled = false
    let timer: number | undefined
    const requestIdentity = identity
    const jobId = view.jobId

    const poll = async () => {
      try {
        const job = await getInputCacheJob(jobId)
        if (cancelled) return
        setView((current) =>
          current.identity === requestIdentity && current.jobId === jobId
            ? {
                ...current,
                job,
                message: job.message,
                cancelling:
                  job.status === "running" ? current.cancelling : false,
              }
            : current,
        )

        if (job.status === "running") {
          timer = window.setTimeout(() => void poll(), 800)
          return
        }

        setView((current) =>
          current.identity === requestIdentity && current.jobId === jobId
            ? {
                ...current,
                jobId: null,
                snapshot: job.snapshot ?? current.snapshot,
                cancelling: false,
              }
            : current,
        )
        const snapshot = await getInputCacheStatus({
          schema_version: 1,
          config,
        })
        if (cancelled) return
        setView((current) =>
          current.identity === requestIdentity
            ? { ...current, snapshot }
            : current,
        )
      } catch (caught) {
        if (cancelled) return
        setView((current) =>
          current.identity === requestIdentity && current.jobId === jobId
            ? {
                ...current,
                jobId: null,
                cancelling: false,
                message: errorMessage(caught, "Cache job status failed."),
              }
            : current,
        )
      }
    }

    void poll()
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [cacheable, config, identity, view.jobId])

  if (!cacheable) return null

  const snapshot = view.snapshot
  const generation = snapshot?.generation
  const jobRunning = view.jobId !== null
  const readiness = jobRunning ? "building" : (snapshot?.state ?? "unknown")
  const freshness = snapshot?.freshness ?? "unknown"

  const build = async (refresh: boolean) => {
    const requestIdentity = identity
    setView((current) =>
      current.identity === requestIdentity
        ? { ...current, action: "build", message: null }
        : current,
    )
    try {
      const response = await buildInputCache({
        schema_version: 1,
        config,
        refresh,
        profile: admittedEager ? "preview_eager" : "lazy_sink",
      })
      setView((current) =>
        current.identity === requestIdentity
          ? {
              ...current,
              action: null,
              jobId: response.job_id,
              job: null,
              message: response.joined
                ? "Joined the active snapshot build."
                : "Snapshot build started.",
            }
          : current,
      )
    } catch (caught) {
      setView((current) =>
        current.identity === requestIdentity
          ? {
              ...current,
              action: null,
              message: errorMessage(caught, "Cache build failed."),
            }
          : current,
      )
    }
  }

  const cancel = async () => {
    if (!view.jobId) return
    const requestIdentity = identity
    const jobId = view.jobId
    setView((current) =>
      current.identity === requestIdentity && current.jobId === jobId
        ? {
            ...current,
            cancelling: true,
            message: "Requesting cancellation...",
          }
        : current,
    )
    try {
      const response = await cancelInputCacheJob(jobId)
      setView((current) =>
        current.identity === requestIdentity && current.jobId === jobId
          ? {
              ...current,
              message: response.cancellation_requested
                ? "Cancellation requested. Waiting for the builder to stop."
                : `The job is already ${response.status}.`,
            }
          : current,
      )
    } catch (caught) {
      setView((current) =>
        current.identity === requestIdentity && current.jobId === jobId
          ? {
              ...current,
              cancelling: false,
              message: errorMessage(caught, "Cancellation failed."),
            }
          : current,
      )
    }
  }

  const clear = async () => {
    const requestIdentity = identity
    setView((current) =>
      current.identity === requestIdentity
        ? { ...current, action: "clear", message: null }
        : current,
    )
    try {
      const next = await clearInputCache({ schema_version: 1, config })
      setView((current) =>
        current.identity === requestIdentity
          ? {
              ...current,
              action: null,
              snapshot: next,
              message: "Snapshot cleared.",
            }
          : current,
      )
    } catch (caught) {
      setView((current) =>
        current.identity === requestIdentity
          ? {
              ...current,
              action: null,
              message: errorMessage(caught, "Cache clear failed."),
            }
          : current,
      )
    }
  }

  return (
    <section
      className="rounded-lg p-2.5 space-y-2"
      style={{
        border: "1px solid var(--border)",
        background: "var(--bg-elevated)",
      }}
    >
      <h3 className="text-xs font-semibold">Snapshot cache</h3>

      <div className="grid grid-cols-2 gap-2 text-[11px]">
        <div>
          <span style={{ color: "var(--text-muted)" }}>Local readiness</span>
          <div
            data-testid="cache-readiness"
            style={{
              color:
                readiness === "ready"
                  ? "var(--success)"
                  : readiness === "corrupt" || readiness === "failed"
                    ? "var(--danger-text)"
                    : "var(--text-secondary)",
            }}
          >
            {readiness}
          </div>
        </div>
        <div>
          <span style={{ color: "var(--text-muted)" }}>External freshness</span>
          <div
            data-testid="cache-freshness"
            style={{
              color:
                freshness === "fresh"
                  ? "var(--success)"
                  : freshness === "stale"
                    ? "var(--warning-strong)"
                    : "var(--text-secondary)",
            }}
          >
            {freshness}
          </div>
        </div>
      </div>

      {generation && (
        <div className="text-[11px]" style={{ color: "var(--text-muted)" }}>
          Generation {generation.generation_id}:{" "}
          {generation.row_count.toLocaleString()} rows,{" "}
          {generation.column_count.toLocaleString()} columns,{" "}
          {formatBytes(generation.size_bytes)}
        </div>
      )}

      {view.job && view.job.status === "running" && (
        <div className="text-[11px]" style={{ color: "var(--text-secondary)" }}>
          {view.job.progress.phase}:{" "}
          {view.job.progress.rows.toLocaleString()} rows in{" "}
          {view.job.progress.batches.toLocaleString()} batches
        </div>
      )}

      {admittedEager && (
        <p className="text-[11px]" style={{ color: "var(--warning-strong)" }}>
          This format builds its snapshot eagerly and is subject to memory
          admission.
        </p>
      )}

      <div className="flex gap-2">
        <button
          type="button"
          disabled={!requiredReady || jobRunning || view.action !== null}
          onClick={() => void build(snapshot?.state === "ready")}
          className="px-2 py-1 rounded text-xs disabled:opacity-50"
          style={INPUT_STYLE}
        >
          {view.action === "build"
            ? "Starting..."
            : snapshot?.state === "ready"
              ? "Refresh"
              : "Build"}
        </button>
        {jobRunning && (
          <button
            type="button"
            disabled={view.cancelling}
            onClick={() => void cancel()}
            className="px-2 py-1 rounded text-xs disabled:opacity-50"
            style={INPUT_STYLE}
          >
            {view.cancelling ? "Cancelling..." : "Cancel"}
          </button>
        )}
        {snapshot?.state === "ready" && (
          <button
            type="button"
            disabled={jobRunning || view.action !== null}
            onClick={() => void clear()}
            className="px-2 py-1 rounded text-xs disabled:opacity-50"
            style={INPUT_STYLE}
          >
            {view.action === "clear" ? "Clearing..." : "Clear"}
          </button>
        )}
      </div>

      {!requiredReady && (
        <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>
          Complete the required source fields to build a snapshot.
        </p>
      )}
      {view.message && (
        <p
          role="status"
          className="text-[11px]"
          style={{ color: "var(--text-secondary)" }}
        >
          {view.message}
        </p>
      )}
    </section>
  )
}
