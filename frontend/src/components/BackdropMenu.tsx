import type { ReactNode } from "react"
import type { LucideIcon } from "lucide-react"

/**
 * The full-window layer under a menu or popover opened at a point (specs/frontend-git-ui):
 * a click on it closes what it covers, and so does a right-click, which never opens the
 * browser's menu over the panel.
 */
export function MenuBackdrop({ onDismiss }: { onDismiss: () => void }) {
  return (
    <div
      className="fixed inset-0 z-40"
      onClick={onDismiss}
      onContextMenu={(e) => {
        e.preventDefault()
        onDismiss()
      }}
    />
  )
}

interface BackdropMenuProps {
  testId: string
  /** The right-click's point, where the menu's top-left corner sits. */
  x: number
  y: number
  /** A line above the items naming what they act on, a branch's name. */
  title?: string
  onClose: () => void
  children: ReactNode
}

/**
 * The git surfaces' right-click menu: its `MenuBackdrop`, then the menu at the pointer,
 * the backdrop its previous sibling.
 */
export default function BackdropMenu({ testId, x, y, title, onClose, children }: BackdropMenuProps) {
  return (
    <>
      <MenuBackdrop onDismiss={onClose} />
      <div
        data-testid={testId}
        className="fixed z-50 rounded-md py-1 shadow-lg text-[12px]"
        style={{ left: x, top: y, background: "var(--bg-elevated)", border: "1px solid var(--border)" }}
      >
        {title !== undefined && (
          <div className="px-3 py-1 font-mono text-[10px] max-w-[220px] truncate" style={{ color: "var(--text-muted)" }}>
            {title}
          </div>
        )}
        {children}
      </div>
    </>
  )
}

interface MenuItemProps {
  testId: string
  icon: LucideIcon
  /** The icon's colour, the accent unless the item says otherwise. */
  iconColor?: string
  /** Delete's item: its label and icon in the danger colour. */
  danger?: boolean
  disabled?: boolean
  onClick: () => void
  children: ReactNode
}

/** One item of a `BackdropMenu`: its icon and label. */
export function MenuItem({
  testId,
  icon: Icon,
  iconColor = "var(--accent)",
  danger = false,
  disabled,
  onClick,
  children,
}: MenuItemProps) {
  return (
    <button
      data-testid={testId}
      onClick={onClick}
      disabled={disabled}
      className="flex items-center gap-1.5 px-3 py-1.5 w-full text-left hover:bg-[var(--bg-hover)] disabled:opacity-40"
      style={{ color: danger ? "var(--danger)" : "var(--text-primary)" }}
    >
      <Icon size={12} style={danger ? undefined : { color: iconColor }} /> {children}
    </button>
  )
}
