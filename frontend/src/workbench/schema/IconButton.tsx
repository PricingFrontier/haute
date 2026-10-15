/** A small icon button of the schema editor's (specs/workbench). */
import { type LucideIcon } from "lucide-react"
import { type ButtonHTMLAttributes } from "react"

/** A small icon button; its colour is the caller's. */
export function IconButton({
  icon: Icon,
  size = 12,
  className = "",
  ...props
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, "type" | "children"> & { icon: LucideIcon; size?: number }) {
  return (
    <button type="button" {...props} className={`focus-ring hover-bg grid size-5 shrink-0 place-items-center rounded ${className}`.trim()}>
      <Icon size={size} aria-hidden="true" />
    </button>
  )
}
