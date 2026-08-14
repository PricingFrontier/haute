import { type ReactNode, useId } from "react"
import { NODE_GROUP_COLORS } from "../../theme/colors"
import { withAlpha } from "../../utils/color"

type ExploreToggleCardProps = {
  enabled: boolean
  label: string
  description?: ReactNode
  onToggle: () => void
  actions?: ReactNode
}

const cardStyle = (enabled: boolean) => ({
  background: enabled ? "var(--accent-soft)" : "var(--bg-input)",
  border: `1px solid ${enabled ? NODE_GROUP_COLORS.explore : "var(--border)"}`,
  ["--focus-ring-border" as string]: withAlpha(NODE_GROUP_COLORS.explore, 0.3),
  ["--focus-ring-shadow" as string]: withAlpha(NODE_GROUP_COLORS.explore, 0.1),
})

/** A full-card Explore checkbox, with an optional adjacent action region. */
export default function ExploreToggleCard({
  enabled,
  label,
  description,
  onToggle,
  actions,
}: ExploreToggleCardProps) {
  const id = useId()
  const labelId = `${id}-label`
  const descriptionId = `${id}-description`
  const body = (
    <span className="flex-1 min-w-0 flex flex-col gap-0.5">
      <span
        id={labelId}
        className="text-xs font-semibold"
        style={{ color: enabled ? NODE_GROUP_COLORS.explore : "var(--text-primary)" }}
      >
        {label}
      </span>
      {description !== undefined && (
        <span
          id={descriptionId}
          className="text-[11px] leading-relaxed"
          style={{ color: "var(--text-muted)" }}
        >
          {description}
        </span>
      )}
    </span>
  )
  const toggleProps = {
    type: "button" as const,
    role: "checkbox",
    "aria-checked": enabled,
    "aria-labelledby": labelId,
    ...(description !== undefined ? { "aria-describedby": descriptionId } : {}),
    onClick: onToggle,
  }

  if (actions === undefined) {
    return (
      <button
        {...toggleProps}
        data-testid="explore-toggle-card"
        data-state={enabled ? "enabled" : "disabled"}
        className="focus-ring text-left flex items-center px-3 py-2 rounded-lg cursor-pointer select-none transition-colors hover:brightness-105"
        style={cardStyle(enabled)}
      >
        {body}
      </button>
    )
  }

  return (
    <div
      data-testid="explore-toggle-card"
      data-state={enabled ? "enabled" : "disabled"}
      role="group"
      aria-label={label}
      className="flex items-center overflow-hidden rounded-lg transition-colors hover:brightness-105"
      style={cardStyle(enabled)}
    >
      <button
        {...toggleProps}
        className="focus-ring text-left flex min-w-0 flex-1 items-center px-3 py-2 rounded-l-lg cursor-pointer select-none"
      >
        {body}
      </button>
      <div className="shrink-0 flex items-center px-2 py-1">{actions}</div>
    </div>
  )
}
