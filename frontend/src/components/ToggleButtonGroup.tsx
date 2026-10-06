import { useRef } from "react"
import { withAlpha } from "../utils/color"

interface ToggleButtonGroupProps<T extends string> {
  value: T
  onChange: (value: T) => void
  /** A disabled option is greyed and cannot be chosen; its reason is its tooltip. */
  options: { key: T; label: string; icon?: React.ReactNode; disabled?: boolean; disabledReason?: string }[]
  accentColor: string
  ariaLabel?: string
  ariaLabelledBy?: string
}

export default function ToggleButtonGroup<T extends string>({
  value,
  onChange,
  options,
  accentColor,
  ariaLabel,
  ariaLabelledBy,
}: ToggleButtonGroupProps<T>) {
  const buttonRefs = useRef<(HTMLButtonElement | null)[]>([])
  const enabled = options.flatMap((opt, idx) => (opt.disabled ? [] : [idx]))
  const activeIdx = options.findIndex((opt) => opt.key === value)
  // A disabled button cannot take focus, so the tab stop moves to the first
  // enabled option when the selected one is disabled.
  const tabStop = enabled.includes(activeIdx) ? activeIdx : enabled[0]

  const handleKeyDown = (e: React.KeyboardEvent, currentIdx: number) => {
    const position = enabled.indexOf(currentIdx)
    let nextIdx: number | undefined

    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      e.preventDefault()
      nextIdx = position === -1 ? enabled[0] : enabled[(position + 1) % enabled.length]
    } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      e.preventDefault()
      nextIdx =
        position === -1 ? enabled[enabled.length - 1] : enabled[(position - 1 + enabled.length) % enabled.length]
    } else if (e.key === "Home") {
      e.preventDefault()
      nextIdx = enabled[0]
    } else if (e.key === "End") {
      e.preventDefault()
      nextIdx = enabled[enabled.length - 1]
    }

    if (nextIdx !== undefined) {
      onChange(options[nextIdx].key)
      buttonRefs.current[nextIdx]?.focus()
    }
  }

  return (
    <div className="flex gap-1.5" role="radiogroup" aria-label={ariaLabel} aria-labelledby={ariaLabelledBy}>
      {options.map((opt, idx) => {
        const active = value === opt.key
        return (
          <button
            key={opt.key}
            ref={(el) => { buttonRefs.current[idx] = el }}
            role="radio"
            aria-checked={active}
            disabled={opt.disabled}
            title={opt.disabled ? opt.disabledReason : undefined}
            tabIndex={idx === tabStop ? 0 : -1}
            onClick={() => onChange(opt.key)}
            onKeyDown={(e) => handleKeyDown(e, idx)}
            className="flex-1 flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-lg text-xs font-medium transition-colors disabled:cursor-not-allowed"
            style={{
              background: active ? withAlpha(accentColor, 0.1) : "var(--bg-input)",
              border: active ? `1px solid ${accentColor}` : "1px solid var(--border)",
              color: active ? accentColor : "var(--text-secondary)",
              opacity: opt.disabled ? 0.5 : undefined,
            }}
          >
            {opt.icon}
            {opt.label}
          </button>
        )
      })}
    </div>
  )
}
