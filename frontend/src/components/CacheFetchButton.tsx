import { useState, useEffect, useRef } from "react"
import { Loader2, HardDriveDownload, Trash2, XCircle, AlertCircle } from "lucide-react"
import { ApiError } from "../api/client"
import { formatBytes } from "../utils/formatBytes"
import { formatTime } from "../utils/formatTime"

// ─── Types ───────────────────────────────────────────────────────

/** Minimal cache status shape – consumers extend with their own fields. */
export type BaseCacheStatus = {
  cached: boolean
  row_count: number
  column_count: number
  size_bytes: number
}

type ProgressPayload = { active: boolean; rows?: number; elapsed?: number; phase?: string }

export type CacheFetchButtonProps<TStatus extends BaseCacheStatus> = {
  /** The key that identifies the resource (path, table name, etc.). */
  resourceKey: string

  /** API: check current cache status. */
  getStatus: (key: string) => Promise<TStatus>
  /** API: kick off a fetch / build. */
  startFetch: (key: string) => Promise<TStatus>
  /** API: poll progress while building. */
  getProgress: (key: string) => Promise<ProgressPayload>
  /** API: delete the cached data. */
  deleteCache: (key: string) => Promise<TStatus>
  /** API: cancel an in-progress build. Optional — when absent, no cancel button shown. */
  cancelFetch?: (key: string) => Promise<unknown>

  /** Field on TStatus that holds the "cached at" unix timestamp. */
  timestampField: keyof TStatus

  /** Labels */
  labels: {
    /** Button text when nothing is cached (e.g. "Cache as Parquet"). */
    fetchLabel: string
    /** Button text when cache exists (e.g. "Refresh Cache"). */
    refreshLabel: string
    /** Hint shown below the button when not yet cached. */
    notCachedHint: string
    /** Text shown while waiting for the first progress tick. */
    pendingLabel: string
  }

  /** Called after a successful fetch or when the initial status load finds a cache. */
  onCacheReady?: (status: TStatus) => void

  /** External disabled flag — when true the button renders inactive
   * (no click handler fires, distinct visual style via the existing
   * `disabled:opacity-40` class). Pairs with `disabledReason` for the
   * hover tooltip.
   */
  disabled?: boolean
  /** Tooltip shown when the button is disabled by `disabled=true`. */
  disabledReason?: string
}

// ─── Component ───────────────────────────────────────────────────

export function CacheFetchButton<TStatus extends BaseCacheStatus>({
  resourceKey,
  getStatus,
  startFetch,
  getProgress,
  deleteCache: deleteCacheFn,
  cancelFetch: cancelFetchFn,
  timestampField,
  labels,
  onCacheReady,
  disabled: externalDisabled,
  disabledReason,
}: CacheFetchButtonProps<TStatus>) {
  const [cache, setCache] = useState<TStatus | null>(null)
  const [building, setBuilding] = useState(false)
  const [progress, setProgress] = useState<{ rows: number; elapsed: number; phase: string } | null>(null)
  const [error, setError] = useState("")
  const [statusError, setStatusError] = useState("")

  // Keep a ref for onCacheReady to avoid stale closure in useEffect
  const onCacheReadyRef = useRef(onCacheReady)
  onCacheReadyRef.current = onCacheReady

  // Load initial status
  useEffect(() => {
    if (!resourceKey) return
    setStatusError("")
    getStatus(resourceKey)
      .then((data) => {
        setCache(data)
        setStatusError("")
        if (data.cached) onCacheReadyRef.current?.(data)
      })
      .catch((e: unknown) => {
        console.warn("cache status fetch failed", e)
        setCache(null)
        const msg = e instanceof ApiError ? e.detail || e.message : e instanceof Error ? e.message : String(e)
        setStatusError(`Unable to check cache status: ${msg}`)
      })
  // eslint-disable-next-line react-hooks/exhaustive-deps -- stable callback props, including would restart polling
  }, [resourceKey])

  // Poll progress while building
  useEffect(() => {
    if (!building || !resourceKey) return
    const id = setInterval(() => {
      getProgress(resourceKey)
        .then((data) => {
          if (data.active) {
            setProgress({ rows: data.rows || 0, elapsed: data.elapsed || 0, phase: data.phase || "" })
          } else {
            setBuilding(false)
          }
        })
        .catch((e) => { console.warn("progress poll failed", e) })
    }, 1000)
    return () => { clearInterval(id); setProgress(null) }
  // eslint-disable-next-line react-hooks/exhaustive-deps -- stable callback prop, including would restart interval
  }, [building, resourceKey])

  const doFetch = () => {
    if (!resourceKey) return
    setBuilding(true)
    setError("")
    setStatusError("")
    startFetch(resourceKey)
      .then((data) => {
        setCache(data)
        setBuilding(false)
        onCacheReady?.(data)
      })
      .catch((e: Error) => {
        const msg = e instanceof ApiError ? e.detail || e.message : e.message
        // Don't show cancellation as an error
        if (msg === "Cache build cancelled") {
          setError("")
        } else {
          setError(msg)
        }
        setBuilding(false)
      })
  }

  const doCancel = () => {
    if (!resourceKey || !cancelFetchFn) return
    cancelFetchFn(resourceKey).catch((e) => { console.warn("cancel request failed", e) })
  }

  const doDelete = () => {
    if (!resourceKey) return
    deleteCacheFn(resourceKey)
      .then((data) => setCache(data))
      .catch((e: Error) => setError(e instanceof ApiError ? e.detail || e.message : e.message))
  }

  const cachedAt = cache ? (cache[timestampField] as number) : 0
  const hasStatusError = !!statusError && !cache?.cached && !building
  const visibleError = error || statusError

  return (
    <div>
      <button
        onClick={building && cancelFetchFn ? doCancel : doFetch}
        disabled={!resourceKey || (building && !cancelFetchFn) || !!externalDisabled}
        title={externalDisabled ? disabledReason : undefined}
        className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs font-medium transition-colors disabled:opacity-40"
        style={{
          background: (building && cancelFetchFn) || hasStatusError ? 'var(--danger-soft)' : cache?.cached ? 'var(--success-soft)' : 'var(--accent-soft)',
          border: (building && cancelFetchFn) || hasStatusError ? '1px solid var(--danger-border-strong)' : cache?.cached ? '1px solid var(--success-border-strong)' : '1px solid var(--accent)',
          color: (building && cancelFetchFn) || hasStatusError ? 'var(--danger)' : cache?.cached ? 'var(--success)' : 'var(--accent)',
        }}
      >
        {building ? (
          cancelFetchFn ? (
            <><XCircle size={14} /> Cancel {progress ? `(${progress.phase ? `${progress.phase}… ` : ""}${progress.rows.toLocaleString()} rows \u00b7 ${progress.elapsed}s)` : ""}</>
          ) : (
            <><Loader2 size={14} className="animate-spin" /> {progress ? `${progress.phase ? `${progress.phase}… ` : ""}${progress.rows.toLocaleString()} rows \u00b7 ${progress.elapsed}s` : labels.pendingLabel}</>
          )
        ) : cache?.cached ? (
          <><HardDriveDownload size={14} /> {labels.refreshLabel}</>
        ) : hasStatusError ? (
          <><AlertCircle size={14} /> Cache status unavailable</>
        ) : (
          <><HardDriveDownload size={14} /> {labels.fetchLabel}</>
        )}
      </button>

      {cache?.cached && (
        <div className="mt-1.5 flex items-center gap-2 text-[10px] px-1" style={{ color: 'var(--text-muted)' }}>
          <span>{cache.row_count.toLocaleString()} rows</span>
          <span>&middot;</span>
          <span>{cache.column_count} cols</span>
          <span>&middot;</span>
          <span>{formatBytes(cache.size_bytes)}</span>
          {cachedAt > 0 && (
            <><span>&middot;</span><span>{formatTime(cachedAt)}</span></>
          )}
          <span>&middot;</span>
          <button
            onClick={doDelete}
            className="inline-flex items-center gap-0.5 hover:opacity-70 transition-opacity"
            style={{ color: 'var(--danger)' }}
            title="Delete cached data"
          >
            <Trash2 size={10} /> clear
          </button>
        </div>
      )}

      {!cache?.cached && resourceKey && !building && !statusError && (
        <div className="mt-1.5 text-[10px] px-1" style={{ color: 'var(--warning-strong)' }}>
          {labels.notCachedHint}
        </div>
      )}

      {visibleError && (
        <div
          role="alert"
          className="mt-1.5 text-[10px] px-2 py-1 rounded"
          style={{ background: 'var(--danger-soft)', color: 'var(--danger)' }}
        >
          {visibleError}
        </div>
      )}
    </div>
  )
}
