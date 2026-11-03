import type { CSSProperties } from "react"

/** A grid cell's issue style: an error the save refuses, or a warning it allows. */
export function cellIssueStyle(
  base: CSSProperties,
  severity: "error" | "warning",
): CSSProperties {
  return severity === "error"
    ? { ...base, background: "var(--danger-soft)", border: "1px solid var(--danger-border-strong)" }
    : { ...base, background: "var(--warning-soft)", border: "1px solid var(--warning-border-emphasis)" }
}
