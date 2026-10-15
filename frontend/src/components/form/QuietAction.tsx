/**
 * The quiet "+ Add …" text action under a list (specs/frontend-shared): accent text,
 * underlined on hover, a plus before it unless another icon is given.
 */
import { Plus, type LucideIcon } from "lucide-react"
import type { ButtonHTMLAttributes, ReactNode } from "react"

export default function QuietAction({
  icon: Icon = Plus,
  className = "",
  children,
  ...props
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, "type" | "children"> & { icon?: LucideIcon; children: ReactNode }) {
  return (
    <button
      type="button"
      {...props}
      className={`quiet-action focus-ring inline-flex items-center gap-1 rounded px-1 py-0.5 -ml-1 text-[11px] font-medium disabled:opacity-50 ${className}`.trim()}
    >
      <Icon size={11} aria-hidden="true" />
      {children}
    </button>
  )
}
