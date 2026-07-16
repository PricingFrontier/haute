import { Info } from "lucide-react"
import Tooltip from "../../components/Tooltip"

/**
 * Label + hover help for the modelling offset column, shared by the CatBoost
 * (TargetAndTaskConfig) and GLM (GLMTargetConfig) panels so the wording stays
 * in one place.
 *
 * The unifying idea is the model's link function: an offset is a known per-row
 * term folded into the linear predictor, so how it lands on the prediction
 * depends on the link. Under a log link (Poisson / Tweedie / Gamma — the
 * exponential-family frequency/severity workflows) it comes through as a
 * MULTIPLIER; under the identity link (Gaussian / linear) it's ADDITIVE. The
 * offset must be present at scoring time, and a constant column of 1 is the
 * plain unit basis.
 *
 * The info icon is the natural mount point for a future click-through to a
 * fuller explainer (Nick's "clickable later to add more detail"); today it
 * carries the concise hover tooltip.
 */
export const OFFSET_HELP =
  "An offset is a known per-row term the model folds in through its link function. " +
  "Log link (Poisson/Tweedie/Gamma): it acts as a multiplier — e.g. an exposure " +
  "column, so 2× exposure ⇒ 2× expected count. Identity link (Gaussian/linear): " +
  "it's added to the prediction. A constant column of 1 is the unit basis. The " +
  "offset column must be present when scoring."

export function OffsetFieldLabel() {
  return (
    <span className="inline-flex items-center gap-1 text-xs" style={{ color: "var(--text-secondary)" }}>
      Offset column (optional)
      <Tooltip label={OFFSET_HELP}>
        <span
          data-testid="offset-help"
          className="inline-flex cursor-help"
          aria-label="About the offset column"
        >
          <Info size={11} style={{ color: "var(--text-muted)" }} />
        </span>
      </Tooltip>
    </span>
  )
}
