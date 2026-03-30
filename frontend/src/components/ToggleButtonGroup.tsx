import { useRef } from "react"
import { withAlpha } from "../utils/color"

interface ToggleButtonGroupProps<T extends string> {
  value: T
  onChange: (value: T) => void
  options: { key: T; label: string; icon?: React.ReactNode }[]
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

  const handleKeyDown = (e: React.KeyboardEvent, currentIdx: number) => {
    let nextIdx: number | null = null

    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      e.preventDefault()
      nextIdx = (currentIdx + 1) % options.length
    } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      e.preventDefault()
      nextIdx = (currentIdx - 1 + options.length) % options.length
    } else if (e.key === "Home") {
      e.preventDefault()
      nextIdx = 0
    } else if (e.key === "End") {
      e.preventDefault()
      nextIdx = options.length - 1
    }

    if (nextIdx !== null) {
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
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(opt.key)}
            onKeyDown={(e) => handleKeyDown(e, idx)}
            className="flex-1 flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-lg text-xs font-medium transition-colors"
            style={{
              background: active ? withAlpha(accentColor, 0.1) : "var(--bg-input)",
              border: active ? `1px solid ${accentColor}` : "1px solid var(--border)",
              color: active ? accentColor : "var(--text-secondary)",
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
