import type { ReactNode } from "react"

interface TooltipProps {
  /** Tooltip text. */
  label: string
  /** The hover target. */
  children: ReactNode
  /** Which side to render on (default top). */
  side?: "top" | "bottom"
  /** Extra classes on the wrapper (e.g. layout). */
  className?: string
}

/**
 * A zero-delay, CSS-only tooltip (S38: native `title` delay is too slow for the
 * tiny change icons). Shows on hover via `group-hover` — no JS timers, no portal.
 * Colours come from CSS vars so the theme-regression guards stay satisfied.
 */
export default function Tooltip({ label, children, side = "top", className }: TooltipProps) {
  return (
    <span className={`relative inline-flex group/tt ${className ?? ""}`}>
      {children}
      <span
        role="tooltip"
        className={
          "pointer-events-none absolute left-1/2 -translate-x-1/2 z-50 hidden " +
          "group-hover/tt:block w-max max-w-[220px] whitespace-normal rounded " +
          "px-1.5 py-1 text-[10px] font-normal leading-snug shadow-lg text-left " +
          (side === "top" ? "bottom-full mb-1" : "top-full mt-1")
        }
        style={{
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
