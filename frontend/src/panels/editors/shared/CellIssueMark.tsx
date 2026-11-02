import { AlertTriangle } from "lucide-react"

/**
 * The icon inside a grid cell that names its issue: the cell sets `aria-invalid`
 * and `aria-describedby={id}`, and leaves room for the icon at its right edge.
 */
export function CellIssueMark({
  id,
  message,
  severity,
}: {
  id: string
  message: string
  severity: "error" | "warning"
}) {
  return (
    <span
      id={id}
      role="img"
      aria-label={message}
      title={message}
      className="absolute right-1 top-1/2 -translate-y-1/2"
      style={{ color: severity === "error" ? "var(--danger)" : "var(--warning-strong)" }}
    >
      <AlertTriangle size={12} aria-hidden="true" />
    </span>
  )
}
