import {
  Fragment,
  cloneElement,
  isValidElement,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react"
import { createPortal } from "react-dom"

interface TooltipProps {
  /** Tooltip text. */
  label: string
  /** Preferred side (flips if it would clip; default top). */
  side?: "top" | "bottom"
  /**
   * The hover target. A single element child is described by the tooltip
   * itself; pass a function when the control to describe is nested inside the
   * child, and place the id it receives on that control's `aria-describedby`.
   */
  children: ReactNode | ((describedBy: string) => ReactNode)
  /** Extra classes on the wrapper (e.g. layout). */
  className?: string
}

const EDGE_PAD = 8
const GAP = 4

interface Placement {
  left: number
  top: number
}

/** `existing` ids plus `id`, space-separated, without duplicates. */
function withDescribedBy(existing: string | undefined, id: string): string {
  const ids = (existing ?? "").split(/\s+/).filter(Boolean)
  return (ids.includes(id) ? ids : [...ids, id]).join(" ")
}

/**
 * A zero-delay hover and focus tooltip (S38: native `title` delay is too slow
 * for the tiny change icons).
 *
 * The bubble is always rendered as `role="tooltip"`, and its id lands on the
 * trigger's `aria-describedby` so assistive technology announces it on the
 * element that actually takes focus: a single element child is cloned with the
 * id appended to any description it already has, unless its `aria-label` is
 * already exactly the tooltip text (a description would then be announced
 * twice); a function child receives the id and places it on its real control (a
 * native radio inside its `<label>`, say); only text or fragment children leave
 * it on the hover wrapper.
 *
 * The bubble is portalled into `document.body` with `position: fixed`: panels
 * are scroll containers inside `overflow-hidden` shells, so a bubble nested in
 * the anchor was cut off at the panel's edge. Closed, it carries the Tailwind
 * `hidden` class. The wrapper opens it on mouse enter or focus within and closes
 * it on mouse leave, blur, any scroll (captured on the window, so scroll
 * containers count) or a window resize — a fixed bubble would otherwise drift
 * away from its anchor.
 *
 * On opening, a layout effect places it before the first paint from the
 * anchor's viewport rectangle and the bubble's own size: centred on the anchor
 * and clamped inside the viewport horizontally, on the preferred `side` unless
 * that side would clip and the other fits. Until placed it is laid out but
 * `visibility: hidden`. It sits above modals, and long unbroken text such as a
 * workspace URL wraps. Colours come from CSS vars so the theme-regression
 * guards stay satisfied.
 */
export default function Tooltip({ label, side = "top", children, className }: TooltipProps) {
  const id = useId()
  const wrapRef = useRef<HTMLSpanElement>(null)
  const tipRef = useRef<HTMLSpanElement>(null)
  const [open, setOpen] = useState(false)
  const [placement, setPlacement] = useState<Placement | null>(null)

  const show = () => setOpen(true)
  const hide = () => {
    setOpen(false)
    setPlacement(null)
  }

  useLayoutEffect(() => {
    if (!open) return
    const anchor = wrapRef.current!.getBoundingClientRect()
    const tip = tipRef.current!
    const tw = tip.offsetWidth
    const th = tip.offsetHeight

    const centredLeft = anchor.left + anchor.width / 2 - tw / 2
    const left = Math.max(EDGE_PAD, Math.min(centredLeft, window.innerWidth - EDGE_PAD - tw))

    const fitsTop = anchor.top - GAP - th >= EDGE_PAD
    const fitsBottom = anchor.bottom + GAP + th <= window.innerHeight - EDGE_PAD
    let effSide = side
    if (side === "top" && !fitsTop && fitsBottom) effSide = "bottom"
    else if (side === "bottom" && !fitsBottom && fitsTop) effSide = "top"
    const top = effSide === "top" ? anchor.top - GAP - th : anchor.bottom + GAP

    setPlacement({ left, top })
  }, [open, side, label])

  useEffect(() => {
    if (!open) return
    const close = () => {
      setOpen(false)
      setPlacement(null)
    }
    window.addEventListener("scroll", close, true)
    window.addEventListener("resize", close)
    return () => {
      window.removeEventListener("scroll", close, true)
      window.removeEventListener("resize", close)
    }
  }, [open])

  let trigger: ReactNode
  let wrapperDescribedBy: string | undefined
  if (typeof children === "function") {
    trigger = children(id)
  } else if (
    isValidElement<{ "aria-label"?: string; "aria-describedby"?: string }>(children) &&
    children.type !== Fragment
  ) {
    trigger =
      children.props["aria-label"] === label
        ? children
        : cloneElement(children, {
            "aria-describedby": withDescribedBy(children.props["aria-describedby"], id),
          })
  } else {
    trigger = children
    wrapperDescribedBy = id
  }

  return (
    <span
      ref={wrapRef}
      className={`inline-flex ${className ?? ""}`}
      aria-describedby={wrapperDescribedBy}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
    >
      {trigger}
      {createPortal(
        <span
          ref={tipRef}
          id={id}
          role="tooltip"
          className={
            "pointer-events-none fixed z-[1000] w-max max-w-[260px] whitespace-normal " +
            "[overflow-wrap:anywhere] rounded px-1.5 py-1 text-[10px] font-normal " +
            "leading-snug shadow-lg text-left" +
            (open ? "" : " hidden")
          }
          style={{
            left: placement?.left,
            top: placement?.top,
            visibility: open && !placement ? "hidden" : undefined,
            background: "var(--bg-elevated)",
            color: "var(--text-primary)",
            border: "1px solid var(--border)",
          }}
        >
          {label}
        </span>,
        document.body,
      )}
    </span>
  )
}
