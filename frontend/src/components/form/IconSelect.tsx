import type { CSSProperties } from "react"
import type { LucideIcon } from "lucide-react"

export interface IconSelectOption<T extends string> {
  value: T
  label: string
}

interface IconSelectProps<T extends string> {
  /** The icon for the chosen option. */
  icon: LucideIcon
  value: T
  options: ReadonlyArray<IconSelectOption<T>>
  onChange: (value: T) => void
  /** The select's accessible name. */
  ariaLabel: string
  /** The marker's tooltip: the chosen option, and that it can be changed. */
  title?: string
  /** Size, shape and colours are the caller's. */
  className?: string
  style?: CSSProperties
  iconSize?: number
}

/**
 * A choice shown as an icon: the chosen option's icon over an invisible native select
 * of the options, so a click (or Alt+Down) opens the choice and the select keeps its
 * keyboard and screen-reader behaviour. The step editor's kind marker and the
 * workbench's column types are made of it.
 */
export default function IconSelect<T extends string>({
  icon: Icon,
  value,
  options,
  onChange,
  ariaLabel,
  title,
  className = "",
  style,
  iconSize = 12,
}: IconSelectProps<T>) {
  return (
    <span
      className={`relative flex shrink-0 items-center justify-center focus-within:ring-2 focus-within:ring-[var(--accent-ring)] ${className}`.trim()}
      style={style}
      title={title}
    >
      <Icon size={iconSize} aria-hidden="true" />
      <select
        aria-label={ariaLabel}
        value={value}
        onChange={(event) => onChange(event.target.value as T)}
        className="absolute inset-0 w-full cursor-pointer opacity-0"
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </span>
  )
}
