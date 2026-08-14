import { Loader2, RotateCw, XCircle } from "lucide-react"
import type { LucideIcon } from "lucide-react"

import { NODE_GROUP_COLORS } from "../../theme/colors"

/** Centered empty state shared by the Pivots and Charts result panes. */
export function ExploreResultEmptyState({
  icon: Icon,
  children,
}: {
  icon: LucideIcon
  children: string
}) {
  return (
    <div className="flex flex-1 items-center justify-center p-4">
      <div className="max-w-md text-center">
        <Icon
          size={24}
          className="mx-auto mb-2"
          aria-hidden="true"
          style={{ color: NODE_GROUP_COLORS.explore }}
        />
        <div
          className="text-xs font-semibold"
          style={{ color: "var(--text-secondary)" }}
        >
          {children}
        </div>
      </div>
    </div>
  )
}

/**
 * The Cancel / Starting / Retry action cluster of one Pivot-backed result
 * card header, shared by the Pivots pane cards and PivotChart cards so the
 * two surfaces cannot drift apart.
 */
export function PivotRunStatusActions({
  activeJobId,
  submitting,
  canRetry,
  onCancel,
  onRetry,
}: {
  activeJobId: string | null
  submitting: boolean
  canRetry: boolean
  onCancel: (jobId: string) => void
  onRetry: () => void
}) {
  if (activeJobId !== null) {
    return (
      <button
        type="button"
        onClick={() => onCancel(activeJobId)}
        className="inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] font-semibold"
        style={{
          color: "var(--danger)",
          border: "1px solid var(--danger-border)",
        }}
      >
        <XCircle size={12} aria-hidden="true" />
        Cancel
      </button>
    )
  }
  if (submitting) {
    return (
      <span
        role="status"
        className="inline-flex items-center gap-1 text-[11px] font-semibold"
        style={{ color: "var(--text-muted)" }}
      >
        <Loader2 size={12} className="animate-spin" aria-hidden="true" />
        Starting calculation
      </span>
    )
  }
  if (canRetry) {
    return (
      <button
        type="button"
        onClick={onRetry}
        className="inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] font-semibold"
        style={{
          color: "var(--text-on-accent)",
          background: NODE_GROUP_COLORS.explore,
        }}
      >
        <RotateCw size={12} aria-hidden="true" />
        Retry
      </button>
    )
  }
  return null
}
