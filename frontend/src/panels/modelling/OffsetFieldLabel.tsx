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
 * MULTIPLIER, an exposure, and scoring reads a missing one as a full policy
 * term; under the identity link (Gaussian / linear) it's ADDITIVE and scoring
 * needs it. A constant column of 1 is the plain unit basis. The help names the
 * losses as the families' offset rules do, including the Logloss refusals.
 *
 * The info icon is the natural mount point for a future click-through to a
 * fuller explainer (Nick's "clickable later to add more detail"); today it
 * carries the concise hover tooltip.
 */
export const OFFSET_HELP =
  "An offset is a known per-row term the model folds in through its link function. " +
  "Under a log link (a log-link GLM, or a Poisson, Gamma or Tweedie loss) it is a " +
  "positive multiplier, such as an exposure column: 2× exposure gives 2× the expected " +
  "count. Training refuses null, zero or negative values; when scoring, a missing or " +
  "empty exposure is an exposure of one, a full policy term. Under any other link it is " +
  "added to the prediction and must be present when scoring. A constant column of 1 is " +
  "the unit basis under a log link. XGBoost, LightGBM, t-boost and EBM take no offset " +
  "with the Logloss loss."

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
