import type { CSSProperties } from "react"

export const EDITABLE_RELATIVITY_INPUT_STYLE: CSSProperties = {
  background: "transparent",
  border: 0,
  color: "var(--text-primary)",
  fontWeight: 600,
}

export const NON_EDITABLE_LABEL_CELL_STYLE: CSSProperties = {
  background: "var(--bg-elevated)",
  color: "var(--text-secondary)",
  borderRight: "1px solid var(--border)",
}
