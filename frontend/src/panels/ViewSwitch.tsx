/**
 * The results workspaces' switch between a few views of one result: a group of
 * pressed buttons, the pressed one in the workspace's accent
 * (`.view-switch-option` in `modelling/validation.css`).
 */
type ViewSwitchName =
  | { label: string; labelledBy?: never }
  | { labelledBy: string; label?: never }

export function ViewSwitch<T extends string>({
  label,
  labelledBy,
  options,
  value,
  onChange,
}: ViewSwitchName & {
  options: readonly { key: T; label: string }[]
  value: T
  onChange: (value: T) => void
}) {
  return (
    <div className="flex flex-wrap gap-1" role="group" aria-label={label} aria-labelledby={labelledBy}>
      {options.map((option) => (
        <button
          key={option.key}
          type="button"
          aria-pressed={value === option.key}
          onClick={() => onChange(option.key)}
          className="view-switch-option focus-ring rounded px-2 py-0.5 text-[12px] font-medium"
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}
