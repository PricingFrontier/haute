import { useRef, useState, type ReactNode } from "react"

interface TooltipProps {
  /** Tooltip text. */
  label: string
  /** Preferred side (flips if it would clip; default top). */
  side?: "top" | "bottom"
  /** The hover target. */
  children: ReactNode
  /** Extra classes on the wrapper (e.g. layout). */
  className?: string
}

const EDGE_PAD = 8
const GAP = 4

/**
 * A zero-delay, CSS-only-show tooltip (S38: native `title` delay is too slow for
 * the tiny change icons). It shows on hover via `group-hover`; on enter we
 * measure the anchor + the tooltip's intrinsic size and (a) shift it
 * horizontally so it never spills past a window edge — panels sit hard against
 * the right edge — and (b) flip top/bottom if the preferred side would clip.
 * Measurements are placement-independent (anchor rect + offsetWidth/Height), so
 * repeated hovers stay correct without imperative style mutation. Colours come
 * from CSS vars so the theme-regression guards stay satisfied.
 */
export default function Tooltip({ label, side = "top", children, className }: TooltipProps) {
  const wrapRef = useRef<HTMLSpanElement>(null)
  const tipRef = useRef<HTMLSpanElement>(null)
  const [dx, setDx] = useState(0)
  const [effSide, setEffSide] = useState<"top" | "bottom">(side)

  const place = () => {
    const tip = tipRef.current
    const wrap = wrapRef.current
    if (!tip || !wrap) return
    const w = wrap.getBoundingClientRect()
    const tw = tip.offsetWidth
    const th = tip.offsetHeight

    // Horizontal: the tooltip is centered on the anchor; nudge if either natural
    // edge would clip. Derived purely from the anchor + intrinsic width, so it
    // re-clamps identically on every hover.
    const cx = w.left + w.width / 2
    const natLeft = cx - tw / 2
    const natRight = cx + tw / 2
    let shift = 0
    if (natLeft < EDGE_PAD) shift = EDGE_PAD - natLeft
    else if (natRight > window.innerWidth - EDGE_PAD) {
      shift = window.innerWidth - EDGE_PAD - natRight
    }
    setDx(shift)

    // Vertical: prefer `side`, flip to the other side only if the preferred one
    // would clip and the other fits.
    const fitsBottom = w.bottom + GAP + th <= window.innerHeight - EDGE_PAD
    const fitsTop = w.top - GAP - th >= EDGE_PAD
    if (side === "bottom" && !fitsBottom && fitsTop) setEffSide("top")
    else if (side === "top" && !fitsTop && fitsBottom) setEffSide("bottom")
    else setEffSide(side)
  }

  return (
    <span
      ref={wrapRef}
      className={`relative inline-flex group/tt ${className ?? ""}`}
      onMouseEnter={place}
    >
      {children}
      <span
        ref={tipRef}
        role="tooltip"
        className={
          "pointer-events-none absolute left-1/2 z-50 hidden " +
          "group-hover/tt:block w-max max-w-[220px] whitespace-normal rounded " +
          "px-1.5 py-1 text-[10px] font-normal leading-snug shadow-lg text-left " +
          (effSide === "top" ? "bottom-full mb-1" : "top-full mt-1")
        }
        style={{
          transform: `translateX(calc(-50% + ${dx}px))`,
          background: "var(--bg-elevated)",
          color: "var(--text-primary)",
          border: "1px solid var(--border)",
        }}
      >
        {label}
      </span>
    </span>
  )
}
