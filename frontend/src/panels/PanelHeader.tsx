import type { ReactNode } from "react"
import { X } from "lucide-react"

export interface PanelHeaderProps {
  title: string | ReactNode
  onClose: () => void
  /** Optional icon to show before the title */
  icon?: ReactNode
  /** Optional subtitle displayed below the title */
  subtitle?: ReactNode
  /** Optional right-side action buttons (rendered before the close button) */
  actions?: ReactNode
}

/**
 * Shared header bar + close button for right-side panels.
 * Eliminates repeated header/close markup across UtilityPanel,
 * ImportsPanel, GitPanel, and TracePanel.
 *
 * NodePanel is intentionally excluded — its header has an editable
 * label input, node ID badge, and refresh button that don't fit
 * this pattern.
 */
export default function PanelHeader({
  title,
  onClose,
  icon,
  subtitle,
  actions,
}: PanelHeaderProps) {
  return (
    <div
      className="px-3 py-2.5 flex items-center gap-2 shrink-0"
      style={{ borderBottom: "1px solid var(--border)" }}
    >
      {icon}
      <div className="flex-1 min-w-0">
        {typeof title === "string" ? (
          <span
            className="text-[13px] font-semibold block"
            style={{ color: "var(--text-primary)" }}
          >
            {title}
          </span>
        ) : (
          title
        )}
        {subtitle}
      </div>
      {actions}
      <button
        onClick={onClose}
        className="p-1 rounded shrink-0 transition-colors hover-bg"
        style={{ color: "var(--text-muted)" }}
        title="Close"
      >
        <X size={14} />
      </button>
    </div>
  )
}
