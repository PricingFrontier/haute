import { useState, type CSSProperties } from "react"

import { MODELLING_INPUT_STYLE } from "./styles"

export type NumberBounds = {
  min: number
  max?: number
  integer: boolean
  required?: boolean
  exclusiveMin?: boolean
  exclusiveMax?: boolean
}

function describeRange({ min, max, integer, exclusiveMin = false, exclusiveMax = false }: NumberBounds): string {
  const kind = integer ? "an integer" : "a number"
  const lower = exclusiveMin ? `greater than ${min}` : `from ${min}`
  if (max === undefined) return `${kind} ${lower}`
  const upper = exclusiveMax ? ` and less than ${max}` : exclusiveMin ? ` and at most ${max}` : ` to ${max}`
  return `${kind} ${lower}${upper}`
}

/** Why a typed value cannot be committed, or null when it can. */
function numberDraftIssue(draft: string, bounds: NumberBounds): string | null {
  const text = draft.trim()
  if (text === "") return bounds.required ? `Enter ${describeRange(bounds)}` : null
  const parsed = Number(text)
  const inRange = Number.isFinite(parsed)
    && (!bounds.integer || Number.isInteger(parsed))
    && (bounds.exclusiveMin ? parsed > bounds.min : parsed >= bounds.min)
    && (bounds.max === undefined || (bounds.exclusiveMax ? parsed < bounds.max : parsed <= bounds.max))
  return inRange ? null : `Enter ${describeRange(bounds)}`
}

/**
 * A numeric input that keeps its draft until a valid, in-range value commits
 * on blur or Enter, so one edit is one undo step. An invalid draft stays on
 * screen with its message and never reaches the config; clearing an optional
 * field commits `undefined`, clearing a required one is refused.
 */
export function NumberField({
  label,
  value,
  step,
  placeholder,
  className = "h-7 w-14 min-w-0 rounded px-1.5 py-1 font-mono text-xs",
  style,
  onCommit,
  ...bounds
}: NumberBounds & {
  label: string
  value: number | undefined
  step: number | "any"
  placeholder?: string
  className?: string
  style?: CSSProperties
  onCommit: (value: number | undefined) => void
}) {
  const committed = value === undefined ? "" : String(value)
  const [draft, setDraft] = useState<string | null>(null)
  const [lastValue, setLastValue] = useState(committed)
  if (lastValue !== committed) {
    setLastValue(committed)
    setDraft(null)
  }
  const error = draft === null ? null : numberDraftIssue(draft, bounds)
  const commit = () => {
    if (draft === null || error !== null) return
    const text = draft.trim()
    if (text !== committed) onCommit(text === "" ? undefined : Number(text))
    setDraft(null)
  }
  return (
    <>
      <input
        type="number"
        aria-label={label}
        aria-invalid={error !== null}
        className={className}
        style={{ ...MODELLING_INPUT_STYLE, ...style, borderColor: error ? "var(--danger)" : "var(--border)" }}
        value={draft ?? committed}
        placeholder={placeholder}
        min={bounds.min}
        max={bounds.max}
        step={step}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === "Enter") commit()
        }}
      />
      {error && <span role="alert" className="text-[10px]" style={{ color: "var(--danger)" }}>{error}</span>}
    </>
  )
}
