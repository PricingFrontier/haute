import { useEffect, useRef, useState, useMemo } from "react"
import { Trash2, Copy, Type, Ungroup, Link2 } from "lucide-react"

interface ContextMenuProps {
  x: number
  y: number
  nodeId: string
  nodeLabel: string
  isSubmodel?: boolean
  isSingleton?: boolean
  onClose: () => void
  onDelete: (id: string) => void
  onDuplicate: (id: string) => void
  onRename: (id: string) => void
  onCreateInstance?: (id: string) => void
  onDissolveSubmodel?: (name: string) => void
}

export default function ContextMenu({
  x,
  y,
  nodeLabel,
  onClose,
  onDelete,
  onDuplicate,
  onRename,
  onCreateInstance,
  onDissolveSubmodel,
  isSubmodel,
  isSingleton,
  nodeId,
}: ContextMenuProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [focusIndex, setFocusIndex] = useState(0)
  const buttonRefs = useRef<(HTMLButtonElement | null)[]>([])

  const items = useMemo(() => {
    const list: { label: string; icon: typeof Type; action: () => void; danger?: boolean }[] = [
      { label: "Rename", icon: Type, action: () => onRename(nodeId) },
    ]
    if (!isSingleton) {
      list.push({ label: "Duplicate", icon: Copy, action: () => onDuplicate(nodeId) })
    }
    if (onCreateInstance && !isSubmodel) {
      list.push({ label: "Create Instance", icon: Link2, action: () => onCreateInstance(nodeId) })
    }
    if (isSubmodel && onDissolveSubmodel) {
      const smName = nodeId.startsWith("submodel__") ? nodeId.slice("submodel__".length) : nodeId
      list.push({ label: "Dissolve Submodel", icon: Ungroup, action: () => onDissolveSubmodel(smName), danger: true })
    }
    list.push({ label: "Delete", icon: Trash2, action: () => onDelete(nodeId), danger: true })
    return list
  }, [nodeId, isSubmodel, isSingleton, onRename, onDuplicate, onDelete, onCreateInstance, onDissolveSubmodel])

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
      role="menu"
      aria-label={`Actions for ${nodeLabel}`}
      className="fixed z-50 rounded-lg shadow-2xl py-1 min-w-[160px] animate-fade-in"
      style={{ left: x, top: y, background: "var(--bg-panel)", border: "1px solid var(--border-bright)" }}
    >
      <div
        className="px-3 py-1.5 text-[9px] font-bold uppercase tracking-[0.1em] mb-0.5"
        style={{ color: "var(--text-muted)", borderBottom: "1px solid var(--border)" }}
      >
        {nodeLabel}
      </div>
      {items.map((item, i) => {
        const Icon = item.icon
        return (
          <button
            key={item.label}
            ref={(el) => { buttonRefs.current[i] = el }}
            role="menuitem"
            tabIndex={i === focusIndex ? 0 : -1}
            onClick={() => {
              item.action()
              onClose()
            }}
            className={`w-full flex items-center gap-2.5 px-3 py-1.5 text-[12px] hover-chrome${item.danger ? " menu-item--danger" : ""}`}
          >
            <Icon size={13} aria-hidden="true" />
            {item.label}
          </button>
        )
      })}
    </div>
  )
}
