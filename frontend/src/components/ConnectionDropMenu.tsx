import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react"
import useClickOutside from "../hooks/useClickOutside"
import { CONNECTION_DROP_TYPES, NODE_TYPE_META, type NodeTypeValue } from "../utils/nodeTypes"

const VIEWPORT_MARGIN = 8

interface ConnectionDropMenuProps {
  /** Client coordinates of the connection release point. */
  x: number
  y: number
  onSelect: (type: NodeTypeValue) => void
  onClose: () => void
}

/** Add node menu shown where a connection from an output was released on empty canvas. */
export default function ConnectionDropMenu({
  x,
  y,
  onSelect,
  onClose,
}: ConnectionDropMenuProps) {
  const ref = useRef<HTMLDivElement>(null)
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([])
  const [position, setPosition] = useState({ left: x, top: y })

  useClickOutside(ref, onClose, true)

  // Keep the whole list on screen when the release point is near an edge. The
  // offset size ignores the fade-in's scale transform, unlike the client rect.
  useLayoutEffect(() => {
    const menu = ref.current
    if (!menu) return
    setPosition({
      left: Math.max(VIEWPORT_MARGIN, Math.min(x, window.innerWidth - menu.offsetWidth - VIEWPORT_MARGIN)),
      top: Math.max(VIEWPORT_MARGIN, Math.min(y, window.innerHeight - menu.offsetHeight - VIEWPORT_MARGIN)),
    })
  }, [x, y])

  useLayoutEffect(() => {
    itemRefs.current[0]?.focus()
  }, [])

  // Escape closes the menu wherever focus is. The document listener runs
  // before the window-level shortcuts, which skip a prevented event.
  useEffect(() => {
    const handler = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape") return
      event.preventDefault()
      onClose()
    }
    document.addEventListener("keydown", handler)
    return () => document.removeEventListener("keydown", handler)
  }, [onClose])

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return
    event.preventDefault()
    const items = itemRefs.current.filter((item): item is HTMLButtonElement => !!item)
    if (items.length === 0) return
    const current = items.findIndex((item) => item === document.activeElement)
    const step = event.key === "ArrowDown" ? 1 : -1
    const next = current === -1 ? 0 : (current + step + items.length) % items.length
    items[next].focus()
  }

  return (
    <div
      ref={ref}
      data-testid="connection-drop-menu"
      role="menu"
      aria-label="Add node"
      onKeyDown={onKeyDown}
      className="fixed z-50 rounded-lg shadow-2xl py-1 min-w-[190px] max-h-[calc(100vh-16px)] overflow-y-auto animate-fade-in"
      style={{ left: position.left, top: position.top, background: "var(--bg-panel)", border: "1px solid var(--border-bright)" }}
    >
      <div
        className="px-3 py-1.5 text-[9px] font-bold uppercase tracking-[0.1em] mb-0.5"
        style={{ color: "var(--text-muted)", borderBottom: "1px solid var(--border)" }}
      >
        Add node
      </div>
      {CONNECTION_DROP_TYPES.map((type, i) => {
        const meta = NODE_TYPE_META[type]
        const Icon = meta.icon
        return (
          <button
            key={type}
            ref={(el) => { itemRefs.current[i] = el }}
            type="button"
            role="menuitem"
            data-testid={`connection-drop-item-${type}`}
            title={meta.description}
            onClick={() => onSelect(type)}
            className="w-full flex items-center gap-2.5 px-3 py-1.5 text-[12px] hover-chrome"
          >
            <span className="w-5 h-5 rounded-md flex items-center justify-center shrink-0" style={{ background: `${meta.color}18` }}>
              <Icon size={12} style={{ color: meta.color }} aria-hidden="true" />
            </span>
            {meta.name}
          </button>
        )
      })}
    </div>
  )
}
