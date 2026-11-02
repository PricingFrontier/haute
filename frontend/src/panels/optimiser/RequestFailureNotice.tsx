/** A failed frontier-point request (`useLatestRequest`): its message, and Retry unless it is gone. */

import { AlertCircle } from "lucide-react"
import type { RequestFailure } from "./useLatestRequest"

export default function RequestFailureNotice({
  what,
  failure,
  onRetry,
  retryLabel = "Retry",
}: {
  what: string
  failure: RequestFailure
  onRetry: () => void
  /** The Retry button's accessible name, where a pane has more than one request. */
  retryLabel?: string
}) {
  return (
    <div role="alert" className="flex items-start gap-2 text-xs px-3 py-2 rounded" style={{ background: "var(--danger-soft)", color: "var(--danger)" }}>
      <AlertCircle size={14} className="mt-0.5 shrink-0" />
      <span className="flex-1">{`${what}: ${failure.message}`}</span>
      {!failure.gone && (
        <button
          type="button"
          onClick={onRetry}
          aria-label={retryLabel}
          className="shrink-0 rounded px-2 py-0.5 text-[11px] font-medium"
          style={{ border: "1px solid var(--danger-border-strong)", color: "var(--danger)" }}
        >
          Retry
        </button>
      )}
    </div>
  )
}
