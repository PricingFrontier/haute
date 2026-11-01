import { useLayoutEffect, useRef, useState } from "react"
import useClickOutside from "../hooks/useClickOutside"
import { useMenuKeyboard } from "../hooks/useMenuKeyboard"
import { CONNECTION_DROP_TYPES, NODE_TYPE_META, type NodeTypeValue } from "../utils/nodeTypes"

const VIEWPORT_MARGIN = 8

type ConnectionDropMenuProps = {
  /** Client coordinates of the connection release point. */
  x: number
  y: number
  onClose: () => void
} & ({ inputChoices?: undefined; onSelect: (type: NodeTypeValue) => void }
  | { inputChoices: string[]; onSelect: (input: string) => void })

/** Node or public-input choices at the connection's release point. */
export default function ConnectionDropMenu({
  x,
  y,
  onSelect,
  onClose,
  inputChoices,
}: ConnectionDropMenuProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [position, setPosition] = useState({ left: x, top: y })

  useClickOutside(ref, onClose, true)
  useMenuKeyboard(ref, onClose)

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

  return (
    <div
      ref={ref}
      data-testid="connection-drop-menu"
      role="menu"
      aria-label={inputChoices ? "Inputs" : "Add node"}
      className="fixed z-50 rounded-lg shadow-2xl py-1 min-w-[190px] max-h-[calc(100vh-16px)] overflow-y-auto animate-fade-in"
      style={{ left: position.left, top: position.top, background: "var(--bg-panel)", border: "1px solid var(--border-bright)" }}
    >
      <div
        className="px-3 py-1.5 text-[9px] font-bold uppercase tracking-[0.1em] mb-0.5"
        style={{ color: "var(--text-muted)", borderBottom: "1px solid var(--border)" }}
      >
        {inputChoices ? "Inputs" : "Add node"}
      </div>
      {inputChoices ? inputChoices.map((name, i) => (
        <button key={name} type="button" role="menuitem" tabIndex={i === 0 ? 0 : -1}
          onClick={() => onSelect(name)} className="w-full text-left px-3 py-1.5 text-[12px] hover-chrome">
          {name}
        </button>
      )) : CONNECTION_DROP_TYPES.map((type, i) => {
        const meta = NODE_TYPE_META[type]
        const Icon = meta.icon
        return (
          <button
            key={type}
            type="button"
            role="menuitem"
            tabIndex={i === 0 ? 0 : -1}
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
