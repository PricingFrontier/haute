import { HelpCircle } from "lucide-react"
import Tooltip from "../../components/Tooltip"

export type FailoverHelpProps = {
  /** Plain-English summary of the failover this parameter would trigger if
   *  left unset, and what the explicit choices mean. */
  label: string
  side?: "top" | "bottom"
}

/**
 * A "?" affordance placed next to any control whose unset state would trigger
 * a silent library/literal failover (Tweedie variance power, elastic-net mix,
 * empty GLM factor set). It explains, on hover, what the failover would be and
 * why an explicit choice is required. A docs link will replace the inline
 * summary once the modelling docs exist.
 */
export function FailoverHelp({ label, side = "top" }: FailoverHelpProps) {
  return (
    <Tooltip label={label} side={side} className="align-middle">
      <button
        type="button"
        aria-label="What is this?"
        className="inline-flex items-center justify-center rounded-full"
        style={{ color: "var(--text-muted)", cursor: "help" }}
      >
        <HelpCircle size={12} />
      </button>
    </Tooltip>
  )
}
