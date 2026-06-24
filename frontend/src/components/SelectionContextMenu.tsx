import { useEffect, useRef, useState, useMemo } from "react"
import { Boxes, Trash2, type LucideIcon } from "lucide-react"

interface SelectionContextMenuProps {
  x: number
  y: number
  /** Ids of the currently-selected nodes the actions apply to. */
  nodeIds: string[]
  onClose: () => void
  /** Group the selection into a submodel (mirrors Ctrl+G). */
  onGroup: (ids: string[]) => void
  /** Delete the selected nodes (mirrors the Delete-key path). */
  onDelete: (ids: string[]) => void
  /**
   * Why "Group into wrapper" is unavailable, or null when it's allowed. When
   * set, that item is greyed out and inert with the reason as its tooltip
   * (same rule as the action guard — see utils/groupIntoWrapper).
   */
  groupDisabledReason?: string | null
}

/**
 * Right-click menu for a multi-node selection. Sibling to the per-node
 * ContextMenu (Rename/Peek/Duplicate/Delete) — this variant offers only the
 * selection-level actions (Group into submodel, Delete). Shares the per-node
 * menu's styling, keyboard navigation, and close-on-outside-click behaviour.
 */
export default function SelectionContextMenu({
  x,
  y,
  nodeIds,
  onClose,
  onGroup,
  onDelete,
  groupDisabledReason,
}: SelectionContextMenuProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [focusIndex, setFocusIndex] = useState(0)
  const buttonRefs = useRef<(HTMLButtonElement | null)[]>([])

  const items = useMemo(() => {
    const list: {
      label: string
      icon: LucideIcon
      action: () => void
      danger?: boolean
      testId: string
      disabled?: boolean
      title?: string
    }[] = [
      {
        label: "Group into wrapper",
        icon: Boxes,
        action: () => onGroup(nodeIds),
        testId: "context-menu-group-submodel",
        disabled: !!groupDisabledReason,
        title: groupDisabledReason ?? undefined,
      },
      {
        label: "Delete",
        icon: Trash2,
        action: () => onDelete(nodeIds),
        danger: true,
        testId: "context-menu-delete-selected",
      },
    ]
    return list
  }, [nodeIds, onGroup, onDelete, groupDisabledReason])

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as HTMLElement)) {
        onClose()
      }
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [onClose])

  // Auto-focus first item
  useEffect(() => {
    buttonRefs.current[0]?.focus()
  }, [])

  // Keyboard navigation: Escape, ArrowDown, ArrowUp
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") { onClose(); return }
      if (e.key === "ArrowDown") {
        e.preventDefault()
        setFocusIndex((prev) => {
          const next = (prev + 1) % items.length
          buttonRefs.current[next]?.focus()
          return next
        })
        return
      }
      if (e.key === "ArrowUp") {
        e.preventDefault()
        setFocusIndex((prev) => {
          const next = (prev - 1 + items.length) % items.length
          buttonRefs.current[next]?.focus()
          return next
        })
      }
    }
    document.addEventListener("keydown", handler)
    return () => document.removeEventListener("keydown", handler)
  }, [onClose, items.length])

  return (
    <div
      ref={ref}
      data-testid="selection-context-menu"
      role="menu"
      aria-label={`Actions for ${nodeIds.length} selected nodes`}
      className="fixed z-50 rounded-lg shadow-2xl py-1 min-w-[160px] animate-fade-in"
      style={{ left: x, top: y, background: "var(--bg-panel)", border: "1px solid var(--border-bright)" }}
    >
      <div
        className="px-3 py-1.5 text-[9px] font-bold uppercase tracking-[0.1em] mb-0.5"
        style={{ color: "var(--text-muted)", borderBottom: "1px solid var(--border)" }}
      >
        {nodeIds.length} selected
      </div>
      {items.map((item, i) => {
        const Icon = item.icon
        return (
          <button
            key={item.label}
            ref={(el) => { buttonRefs.current[i] = el }}
            role="menuitem"
            data-testid={item.testId}
            aria-disabled={item.disabled || undefined}
            data-disabled={item.disabled || undefined}
            title={item.title}
            tabIndex={i === focusIndex ? 0 : -1}
            onClick={() => {
              if (item.disabled) return // inert: greyed out, reason in the tooltip
              item.action()
              onClose()
            }}
            className={`w-full flex items-center gap-2.5 px-3 py-1.5 text-[12px]${item.disabled ? "" : " hover-chrome"}${item.danger ? " menu-item--danger" : ""}`}
            style={item.disabled ? { opacity: 0.4, cursor: "not-allowed" } : undefined}
          >
            <Icon size={13} aria-hidden="true" />
            {item.label}
          </button>
        )
      })}
    </div>
  )
}
