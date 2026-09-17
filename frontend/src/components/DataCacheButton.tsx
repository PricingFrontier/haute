import { Database, Loader2, XCircle } from "lucide-react"

import type { NodeDataAvailability, NodeDataCache } from "../hooks/useNodeDataCache"
import { PREVIEW_PANEL_ACTION_BUTTON_CLASS } from "../panels/previewPanelLayout"
import { dataCacheDetail } from "./dataCacheLabels"

export interface DataCacheButtonProps {
  cache: NodeDataCache
  /** Appends the snapshot's size and retention to the button's detail line. */
  showDetails?: boolean
  className?: string
}

const AVAILABILITY_LABELS: Record<NodeDataAvailability, string> = {
  checking: "Checking cache",
  current: "Re-cache",
  partial: "Cached for some columns",
  stale: "Re-cache",
  missing: "Needs caching",
  building: "Caching",
  corrupt: "Re-cache",
}

const AVAILABILITY_TITLES: Record<NodeDataAvailability, string> = {
  checking: "Asking the server about this data",
  current: "The whole dataset is cached; cache it again to recompute it",
  partial: "Cached without every column this node reads; cache it again for all of them",
  stale: "The cached data is out of date; cache it again",
  missing: "This data is not cached yet",
  building: "This data is being cached",
  corrupt: "The cached data is unreadable; cache it again",
}

function buttonStyle(availability: NodeDataAvailability): { color: string; background: string } {
  if (availability === "current") {
    return { color: "var(--text-on-accent)", background: "var(--success-fill)" }
  }
  if (availability === "stale" || availability === "partial") {
    return { color: "var(--text-on-light-accent)", background: "var(--warning-strong)" }
  }
  if (availability === "checking") {
    return { color: "var(--text-muted)", background: "var(--accent-soft)" }
  }
  return { color: "var(--text-on-accent)", background: "var(--danger-solid)" }
}

/**
 * The one cache control every consumer of a data point shows: its state for
 * this consumer's demand, the progress of whichever consumer's build is
 * running, and cancel while it runs.
 *
 * A point that is read directly from its file has nothing to build, so the
 * button is replaced by that statement.
 */
export default function DataCacheButton({ cache, showDetails = false, className }: DataCacheButtonProps) {
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

  const disabled = !cache.canBuild || cache.busy || cache.availability === "checking"
  return (
    <span className={`inline-flex items-center gap-1 ${className ?? ""}`}>
      <button
        type="button"
        onClick={() => void (cache.availability === "missing" ? cache.run() : cache.refresh())}
        disabled={disabled}
        className={`${PREVIEW_PANEL_ACTION_BUTTON_CLASS} disabled:opacity-45 disabled:cursor-not-allowed`}
        style={buttonStyle(cache.availability)}
        title={AVAILABILITY_TITLES[cache.availability]}
        data-testid="data-cache-button"
      >
        {cache.busy || cache.availability === "checking" ? (
          <Loader2 size={12} className="shrink-0 animate-spin" />
        ) : (
          <Database size={12} className="shrink-0" />
        )}
        <span className="truncate">{AVAILABILITY_LABELS[cache.availability]}</span>
      </button>
      {detail ? (
        <span className="text-[11px] truncate" style={{ color: "var(--text-muted)" }} data-testid="data-cache-detail">
          {detail}
        </span>
      ) : null}
    </span>
  )
}
