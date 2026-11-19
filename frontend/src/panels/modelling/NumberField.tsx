import type { CSSProperties } from "react"
import { NumberField as SharedNumberField } from "../../components/form/NumberField"
import type { NumberRule } from "../../utils/numberRule"

import { MODELLING_INPUT_STYLE } from "./styles"

export type NumberBounds = NumberRule & { min: number; integer: boolean }

/**
 * The modelling panes' number box: the shared number field (`components/form/NumberField`)
 * in their look. A valid, in-range value commits on blur or Enter as one undo step; text
 * refused stays on screen with its message; clearing an optional field commits `undefined`
 * and clearing a required one is refused.
 */
export function NumberField({
  label,
  value,
  placeholder,
  className = "h-7 w-14 min-w-0 rounded px-1.5 py-1 font-mono text-xs",
  style,
  onCommit,
  ...bounds
}: NumberBounds & {
  label: string
  value: number | undefined
  placeholder?: string
  className?: string
  style?: CSSProperties
  onCommit: (value: number | undefined) => void
}) {
  return (
    <SharedNumberField
      {...bounds}
      value={value}
      onCommit={(next) => onCommit(next ?? undefined)}
      aria-label={label}
      className={className}
      style={{ ...MODELLING_INPUT_STYLE, ...style, borderColor: "var(--border)" }}
      placeholder={placeholder}
    />
  )
}
