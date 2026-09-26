/**
 * The polite live line under a chart that states the hovered or focused
 * item's exact values, so a keyboard or screen-reader user reads the same
 * numbers a pointer user sees. With nothing active it shows `placeholder`.
 * AvE bins and frontier points share it (`validation-bin-detail` in
 * validation.css).
 */

import type { ReactNode } from "react"

export default function ChartFocusDetail({
  children,
  placeholder,
}: {
  /** The active item's values, or `null` when nothing is hovered or focused. */
  children: ReactNode | null
  placeholder: string
}) {
  return (
    <div className="validation-bin-detail" role="status" aria-live="polite">
      {children ?? <span>{placeholder}</span>}
    </div>
  )
}
