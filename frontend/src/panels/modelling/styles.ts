import { MODEL_COLORS } from "../../theme/colors"

/** Style helper for the purple selected/unselected toggle buttons used across modelling config. */
export function toggleButtonStyle(selected: boolean): React.CSSProperties {
  return {
    background: selected ? MODEL_COLORS.accentSoft : "var(--chrome-hover)",
    color: selected ? MODEL_COLORS.accent : "var(--text-muted)",
    border: `1px solid ${selected ? MODEL_COLORS.accentBorder : "transparent"}`,
  }
}

/** Shared compact fields for main effects and interaction terms. */
export const GLM_ROW_CLASS = "flex min-w-0 flex-wrap items-end gap-x-1.5 gap-y-1.5"
export const GLM_FIELD_CLASS = "flex min-w-0 max-w-full flex-col gap-0.5 text-[11px]"
