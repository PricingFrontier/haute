import { X } from "lucide-react"
import { useEffect, useLayoutEffect, useRef, useState, type ButtonHTMLAttributes, type CSSProperties, type MouseEvent, type ReactNode } from "react"

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

/**
 * The Close button every side panel closes by, a white X on red: the kit's header's, and
 * the one a panel drawing its own header ends it with. Other props, such as a test id or
 * a label, pass through.
 */
export function SidePanelClose(props: Omit<ButtonHTMLAttributes<HTMLButtonElement>, "type" | "className" | "children">) {
  return (
    <button type="button" title="Close" {...props} className="side-panel-close">
      <X size={14} strokeWidth={2.5} />
    </button>
  )
}

/** A side panel's header: its icon, title and subtitle, any actions, and Close. */
export function SidePanelHeader({ title, onClose, icon, subtitle, actions }: SidePanelHeaderProps) {
  return (
    <div className="side-panel-header">
      {icon}
      <div className="side-panel-heading">
        {typeof title === "string" ? <span className="side-panel-title">{title}</span> : title}
        {subtitle}
      </div>
      {actions}
      <SidePanelClose onClick={onClose} />
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
  /** The side of the editor it opens on; right unless said otherwise. */
  side?: "left" | "right"
}

/**
 * A panel on the right of the editor, or the left, resized by dragging its inner
 * edge, which slides in from its side when it opens. Its styles are side-panel.css.
 */
export function SidePanel({ children, width, onWidthChange, minWidth, maxWidth, style, testId, side = "right", ...heading }: SidePanelProps) {
  const panel = useRef<HTMLDivElement>(null)
  const dragging = useRef(false)
  const start = useRef({ x: 0, width })
  const current = useRef(width)
  // Mirrors `dragging` so the handle keeps its accent while the pointer strays off it.
  const [dragActive, setDragActive] = useState(false)
  // The latest limits, side and callback, for the window listeners added once.
  const latest = useRef({ minWidth, maxWidth, onWidthChange, side })
  useLayoutEffect(() => {
    latest.current = { minWidth, maxWidth, onWidthChange, side }
  })

  useEffect(() => {
    // One routine for every way a resize ends: a release, a release outside the window (a
    // move with no button down), the window losing the focus, and the panel going.
    const finish = () => {
      if (!dragging.current) return
      dragging.current = false
      document.body.style.cursor = ""
      document.body.style.userSelect = ""
      latest.current.onWidthChange(current.current)
      setDragActive(false)
    }
    const onMouseMove = (e: globalThis.MouseEvent) => {
      if (!dragging.current) return
      if (e.buttons === 0) {
        finish()
        return
      }
      const { minWidth: min, maxWidth: max, side: from } = latest.current
      // Dragged outwards, away from the panel's side, it widens.
      const moved = from === "left" ? e.clientX - start.current.x : start.current.x - e.clientX
      const next = Math.min(max(), Math.max(min, start.current.width + moved))
      current.current = next
      // Straight to the element while dragging; the host hears once, at the end.
      if (panel.current) panel.current.style.width = `${next}px`
    }
    window.addEventListener("mousemove", onMouseMove)
    window.addEventListener("mouseup", finish)
    window.addEventListener("blur", finish)
    return () => {
      window.removeEventListener("mousemove", onMouseMove)
      window.removeEventListener("mouseup", finish)
      window.removeEventListener("blur", finish)
      finish()
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

  const handle = (
    <div
      onMouseDown={onDragStart}
      data-testid="panel-resize-handle"
      className={dragActive ? "panel-drag-handle dragging" : "panel-drag-handle"}
      style={{ background: "var(--chrome-border)" }}
    />
  )
  return (
    <div ref={panel} data-testid={testId} className={side === "left" ? "side-panel left" : "side-panel"} style={{ width, background: "var(--bg-panel)", ...style }}>
      {side === "right" && handle}
      <div className="side-panel-body">
        {heading.title !== undefined && <SidePanelHeader {...heading} />}
        {children}
      </div>
      {side === "left" && handle}
    </div>
  )
}
