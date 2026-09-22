import { Database, Loader2, XCircle } from "lucide-react"

import type { NodeDataAvailability, NodeDataCache } from "../hooks/useNodeDataCache"
import { PREVIEW_PANEL_ACTION_BUTTON_CLASS } from "../panels/previewPanelLayout"
import { dataCacheDetail } from "./dataCacheLabels"

export interface DataCacheStatusProps {
  cache: NodeDataCache
  /** Appends the snapshot's size and retention to the detail line. */
  showDetails?: boolean
  className?: string
}

const AVAILABILITY_LABELS: Record<NodeDataAvailability, string> = {
  checking: "Checking cache",
  current: "Cached",
  partial: "Cached for some columns",
  stale: "Cache out of date",
  missing: "Not cached",
  building: "Caching",
  corrupt: "Cache unreadable",
}

// Each says what is true and what Refresh would do about it, because Refresh
// is now the only way to act on any of them.
const AVAILABILITY_TITLES: Record<NodeDataAvailability, string> = {
  checking: "Asking the server about this data",
  current: "The whole dataset is cached; Refresh leaves it alone",
  partial: "Cached without every column this node reads; Refresh caches the rest",
  stale: "The cached data is out of date; Refresh caches it again",
  missing: "This data is not cached yet; Refresh caches it",
  building: "This data is being cached",
  corrupt: "The cached data is unreadable; Refresh caches it again",
}

function statusColor(availability: NodeDataAvailability): string {
  if (availability === "current") return "var(--success)"
  if (availability === "stale" || availability === "partial") return "var(--warning-strong)"
  if (availability === "checking") return "var(--text-muted)"
  return "var(--danger)"
}

/**
 * What every consumer of a data point shows about it: its state for this
 * consumer's demand, the progress of whichever consumer's build is running,
 * and cancel while it runs.
 *
 * Nothing here starts a build. The node's own Refresh button does that, for
 * the same reason it re-runs the preview: one control for "bring this node up
 * to date" rather than one per thing that might be out of date.
 *
 * A point that is read directly from its file has nothing to cache, so the
 * state is replaced by that statement.
 */
export default function DataCacheStatus({ cache, showDetails = false, className }: DataCacheStatusProps) {
  const detail = showDetails ? dataCacheDetail(cache) : null

  if (cache.readsDirectly) {
    return (
      <span
        className={`text-[11px] ${className ?? ""}`}
        style={{ color: "var(--text-muted)" }}
        data-testid="data-cache-direct"
      >
        Reads Parquet directly
      </span>
    )
  }

  if (cache.availability === "building") {
    const percent = Math.min(Math.max(cache.progress * 100, 0), 100)
    return (
      <span className={`inline-flex items-center gap-1 ${className ?? ""}`}>
        <button
          type="button"
          onClick={() => void cache.cancel()}
          className={PREVIEW_PANEL_ACTION_BUTTON_CLASS}
          style={{
            color: "var(--danger)",
            background: "var(--danger-soft)",
            border: "1px solid var(--danger-border)",
          }}
          title={`${cache.message || "Caching"} (${Math.round(percent)}%)`}
          data-testid="data-cache-cancel"
        >
          <XCircle size={12} className="shrink-0" />
          <span className="truncate">Cancel</span>
        </button>
        <span
          role="progressbar"
          aria-label="Data caching progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(percent)}
          className="h-1 w-10 overflow-hidden rounded"
          style={{ background: "var(--accent-soft)" }}
          data-testid="data-cache-progress"
        >
          <span
            className="block h-full transition-all duration-300"
            style={{ width: `${Math.max(percent, 2)}%`, background: "var(--accent)" }}
          />
        </span>
      </span>
    )
  }

  return (
    <span className={`inline-flex items-center gap-1 ${className ?? ""}`}>
      <span
        className="inline-flex items-center gap-1 text-[11px] font-medium"
        style={{ color: statusColor(cache.availability) }}
        title={AVAILABILITY_TITLES[cache.availability]}
        data-testid="data-cache-status"
      >
        {cache.busy || cache.availability === "checking" ? (
          <Loader2 size={12} className="shrink-0 animate-spin" />
        ) : (
          <Database size={12} className="shrink-0" />
        )}
        <span className="truncate">{AVAILABILITY_LABELS[cache.availability]}</span>
      </span>
      {detail ? (
        <span className="text-[11px] truncate" style={{ color: "var(--text-muted)" }} data-testid="data-cache-detail">
          {detail}
        </span>
      ) : null}
    </span>
  )
}
