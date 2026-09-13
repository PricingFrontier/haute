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
