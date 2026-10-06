import { MODEL_COLORS } from "../../theme/colors"

/** Style helper for the purple selected/unselected toggle buttons used across modelling config. */
export function toggleButtonStyle(selected: boolean): React.CSSProperties {
  return {
    background: selected ? MODEL_COLORS.accentSoft : "var(--chrome-hover)",
    color: selected ? MODEL_COLORS.accent : "var(--text-muted)",
    border: `1px solid ${selected ? MODEL_COLORS.accentBorder : "transparent"}`,
  }
}

/** Themed surface for the modelling editor's text and number inputs. */
export const MODELLING_INPUT_STYLE = {
  background: "var(--bg-input)",
  border: "1px solid var(--border)",
  color: "var(--text-primary)",
} as const

/** Shared compact fields for main effects and interaction terms. */
export const GLM_SELECT_CLASS = "h-7 w-full min-w-0 rounded px-1.5 py-1 text-xs"
export const GLM_ROW_CLASS = "flex min-w-0 flex-wrap items-end gap-x-1.5 gap-y-1.5"
export const GLM_FIELD_CLASS = "flex min-w-0 max-w-full flex-col gap-0.5 text-[11px]"
