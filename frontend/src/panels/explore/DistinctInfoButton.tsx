import { HelpCircle } from "lucide-react"
import Tooltip from "../../components/Tooltip"

/**
 * Question-mark affordance clarifying that the "Distinct" count is of valid
 * values only: the null and NaN buckets are counted and reported separately
 * (Null % / NaN %), not folded into distinct. Placed next to every "Distinct"
 * column header in the Explore overview.
 */
const DISTINCT_INFO_LABEL =
  "Distinct counts unique valid values only. Null and NaN are not values — they are reported separately as Null % and NaN %."

export default function DistinctInfoButton() {
  return (
    <Tooltip label={DISTINCT_INFO_LABEL}>
      <button
        type="button"
        data-testid="explore-distinct-info"
        aria-label={DISTINCT_INFO_LABEL}
        className="focus-ring inline-flex items-center justify-center rounded opacity-60 hover:opacity-100"
        style={{ color: "var(--text-secondary)" }}
      >
        <HelpCircle size={11} aria-hidden />
      </button>
    </Tooltip>
  )
}
