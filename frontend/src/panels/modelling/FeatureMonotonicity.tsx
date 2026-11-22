import type { OnUpdateConfig } from "../editors/_shared"
import { configField } from "../../utils/configField"
import { isNumericDtype } from "../../utils/polarsDtypes"
import { NODE_GROUP_COLORS } from "../../theme/colors"
import { withAlpha } from "../../utils/color"
import type { ModellingColumn } from "./featureSelection"

const MONOTONIC_DIRECTIONS = [
  {
    value: -1,
    label: "decreasing",
    glyph: "↓",
    color: "var(--danger)",
    activeBackground: "var(--danger-soft)",
  },
  {
    value: 0,
    label: "no constraint",
    glyph: "−",
    color: "var(--warning-strong)",
    activeBackground: "var(--warning-soft)",
  },
  {
    value: 1,
    label: "increasing",
    glyph: "↑",
    color: NODE_GROUP_COLORS.data,
    activeBackground: withAlpha(NODE_GROUP_COLORS.data, 0.1),
  },
] as const

/** The same fitting directions in the node editor and the condensed factor list. */
export function FeatureMonotonicity({ config, column, included, onUpdate, noun = "feature", disabled = false }: {
  config: Record<string, unknown>
  column: ModellingColumn
  included: boolean
  onUpdate: OnUpdateConfig
  noun?: "feature" | "factor"
  disabled?: boolean
}) {
  const monotone = configField<Record<string, number>>(config, "monotone_constraints", {})
  const monotonicity = monotone[column.name] ?? 0
  const canSetMonotonicity = included && isNumericDtype(column.dtype)
  const unavailableReason = !isNumericDtype(column.dtype)
    ? `Monotonicity is only available for numeric ${noun}s.`
    : `Include this ${noun} to set monotonicity.`
  const updateMonotonicity = (direction: -1 | 0 | 1) => {
    const next = { ...monotone }
    if (direction === 0) delete next[column.name]
    else next[column.name] = direction
    onUpdate("monotone_constraints", Object.keys(next).length > 0 ? next : null)
  }
  return (
    <fieldset
      className="relative m-0 min-w-0 border-0 p-0 disabled:opacity-40 disabled:grayscale"
      disabled={disabled || !canSetMonotonicity}
      title={canSetMonotonicity ? undefined : unavailableReason}
    >
      <legend className="sr-only">Monotonicity</legend>
      <div className="flex items-center gap-1">
        {MONOTONIC_DIRECTIONS.map((direction) => {
          const active = monotonicity === direction.value
          return (
            <button
              type="button"
              key={direction.value}
              aria-label={`${column.name}: ${direction.label}`}
              aria-pressed={active}
              className="flex h-6 w-6 items-center justify-center rounded-lg text-xs font-semibold transition-[filter] hover:brightness-125 disabled:cursor-not-allowed"
              style={{
                background: active
                  ? direction.activeBackground
                  : "var(--bg-input)",
                border: `1px solid ${
                  active ? direction.color : "var(--border)"
                }`,
                color: direction.color,
              }}
              title={
                canSetMonotonicity
                  ? `${column.name}: ${direction.label}`
                  : unavailableReason
              }
              onClick={() =>
                updateMonotonicity(direction.value)
              }
            >
              <span aria-hidden="true">{direction.glyph}</span>
            </button>
          )
        })}
      </div>
    </fieldset>
  )
}
