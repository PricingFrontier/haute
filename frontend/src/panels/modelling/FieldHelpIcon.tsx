import { Info } from "lucide-react"
import Tooltip from "../../components/Tooltip"

/** The offset-field help pattern: a hover-only Info icon beside a label. */
export function FieldHelpIcon({ label, ariaLabel }: { label: string; ariaLabel: string }) {
  return (
    <Tooltip label={label}>
      <span className="inline-flex cursor-help" aria-label={ariaLabel}>
        <Info size={11} style={{ color: "var(--text-muted)" }} />
      </span>
    </Tooltip>
  )
}
