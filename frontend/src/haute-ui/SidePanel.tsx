import { X } from "lucide-react"
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type MouseEvent, type ReactNode } from "react"

export interface SidePanelHeaderProps {
  title: string | ReactNode
  onClose: () => void
  /** Before the title. */
  icon?: ReactNode
  /** Below the title. */
  subtitle?: ReactNode
  /** Buttons before the close button. */
  actions?: ReactNode
}

/** A right-hand panel's header: its icon, title and subtitle, any actions, and Close. */
export function SidePanelHeader({ title, onClose, icon, subtitle, actions }: SidePanelHeaderProps) {
  return (
    <div className="side-panel-header">
      {icon}
      <div className="side-panel-heading">
        {typeof title === "string" ? <span className="side-panel-title">{title}</span> : title}
        {subtitle}
      </div>
      {actions}
      <button type="button" onClick={onClose} className="side-panel-close" title="Close">
        <X size={14} />
      </button>
    </div>
  )
}

/** Either no header, for a panel that draws its own, or a full one: a title needs a way to close. */
type SidePanelHeading =
  | { title?: undefined; onClose?: undefined; icon?: undefined; subtitle?: undefined; actions?: undefined }
  | SidePanelHeaderProps

export type SidePanelProps = SidePanelHeading & {
  children: ReactNode
  /** Its width in pixels. The host keeps it, so it can outlive the panel. */
  width: number
  /** Called with the new width when a drag on the left edge ends. */
  onWidthChange: (width: number) => void
  /** The narrowest a drag makes it. */
  minWidth: number
  /** The widest a drag makes it, read as the drag moves. */
  maxWidth: () => number
  /** Merged over the panel's own style, such as to dim it. */
  style?: CSSProperties
  testId?: string
}

/**
 * A panel on the right of the editor, resized by dragging its left edge, which
 * slides in when it opens. Its styles are side-panel.css.
 */
export function SidePanel({ children, width, onWidthChange, minWidth, maxWidth, style, testId, ...heading }: SidePanelProps) {
  const panel = useRef<HTMLDivElement>(null)
  const dragging = useRef(false)
  const start = useRef({ x: 0, width })
  const current = useRef(width)
  // Mirrors `dragging` so the handle keeps its accent while the pointer strays off it.
  const [dragActive, setDragActive] = useState(false)
  // The latest limits and callback, for the window listeners added once.
  const latest = useRef({ minWidth, maxWidth, onWidthChange })
  useLayoutEffect(() => {
    latest.current = { minWidth, maxWidth, onWidthChange }
  })

  useEffect(() => {
    const onMouseMove = (e: globalThis.MouseEvent) => {
      if (!dragging.current) return
      const { minWidth: min, maxWidth: max } = latest.current
      const next = Math.min(max(), Math.max(min, start.current.width + start.current.x - e.clientX))
      current.current = next
      // Straight to the element while dragging; the host hears once, at the end.
      if (panel.current) panel.current.style.width = `${next}px`
    }
    const onMouseUp = () => {
      if (!dragging.current) return
      dragging.current = false
      document.body.style.cursor = ""
      document.body.style.userSelect = ""
      latest.current.onWidthChange(current.current)
      setDragActive(false)
    }
    window.addEventListener("mousemove", onMouseMove)
    window.addEventListener("mouseup", onMouseUp)
    return () => {
      window.removeEventListener("mousemove", onMouseMove)
      window.removeEventListener("mouseup", onMouseUp)
    }
  }, [])

  const onDragStart = (e: MouseEvent) => {
    dragging.current = true
    start.current = { x: e.clientX, width }
    current.current = width
    document.body.style.cursor = "col-resize"
    document.body.style.userSelect = "none"
    setDragActive(true)
  }

  return (
    <div ref={panel} data-testid={testId} className="side-panel" style={{ width, background: "var(--bg-panel)", ...style }}>
      <div
        onMouseDown={onDragStart}
        data-testid="panel-resize-handle"
        className={dragActive ? "panel-drag-handle dragging" : "panel-drag-handle"}
        style={{ background: "var(--chrome-border)" }}
      />
      <div className="side-panel-body">
        {heading.title !== undefined && <SidePanelHeader {...heading} />}
        {children}
      </div>
    </div>
  )
}
