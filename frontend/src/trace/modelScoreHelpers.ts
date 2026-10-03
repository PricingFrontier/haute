import type {
  ModelScoreContributionDetail,
  ModelScoreExplanationDetail,
  ModelScoreNodeDetail,
  TraceNodeDetail,
} from "../types/trace"

export function asModelScoreDetail(detail: TraceNodeDetail): ModelScoreNodeDetail {
  return detail as ModelScoreNodeDetail
}

export function modelScoreTitle(detail: ModelScoreNodeDetail): string {
  const identity = detail.model_identity
  if (!identity) return "Model Score"
  if (identity.registered_model) {
    return identity.version
      ? `Model: ${identity.registered_model} v${identity.version}`
      : `Model: ${identity.registered_model}`
  }
  if (identity.model_path) return `Model file: ${identity.model_path}`
  if (identity.run_id) return `Model run: ${identity.run_id}`
  return identity.source_type ? `Model source: ${identity.source_type}` : "Model Score"
}

export function modelScorePrediction(detail: ModelScoreNodeDetail): { hasPrediction: boolean; value: unknown } {
  if ("prediction_value" in detail) return { hasPrediction: true, value: detail.prediction_value }
  return { hasPrediction: false, value: undefined }
}

const INVERSE_LINK_LABELS: Record<string, string> = { log: "exp", logit: "inverse logit" }
/** What a link-function ladder's contributions sum to, by the explanation's output space. */
const LINKED_SUM_LABELS: Record<string, string> = {
  raw_formula_val: "Raw score",
  log_odds: "Log-odds",
  log: "Log scale",
}

/**
 * The response-scale prediction of a link-function model, whose contribution
 * ladder sums to the linear predictor. Null when the ladder already ends on the
 * response scale (identity link, or no link reported). A classifier's response
 * is its probability, not the class label its prediction column holds.
 */
export function modelScoreLinkedPrediction(
  explanation: ModelScoreExplanationDetail | undefined,
): { inverseLink: string; isProbability: boolean; sumLabel: string; value: number } | null {
  const link = explanation?.link ?? explanation?.link_function
  if (!link || link === "identity") return null
  const value = explanation?.model_prediction_value ?? explanation?.prediction_value
  if (typeof value !== "number" || !Number.isFinite(value)) return null
  return {
    inverseLink: INVERSE_LINK_LABELS[link] ?? `inverse ${link}`,
    isProbability: explanation?.prediction_space === "probability",
    sumLabel: LINKED_SUM_LABELS[explanation?.output_space ?? ""] ?? "Linear predictor",
    value,
  }
}

export function modelScoreFeatureColumns(detail: ModelScoreNodeDetail): string[] {
  if (Array.isArray(detail.feature_columns) && detail.feature_columns.length > 0) return detail.feature_columns
  return []
}

export function resolveContributionFeatureValue(
  detail: ModelScoreNodeDetail,
  explanation: ModelScoreExplanationDetail | undefined,
  contribution: ModelScoreContributionDetail,
): { hasValue: boolean; value: unknown } {
  if (Object.prototype.hasOwnProperty.call(contribution, "feature_value")) {
    return { hasValue: true, value: contribution.feature_value }
  }
  if (
    explanation?.feature_values &&
    Object.prototype.hasOwnProperty.call(explanation.feature_values, contribution.feature)
  ) {
    return { hasValue: true, value: explanation.feature_values[contribution.feature] }
  }
  if (
    detail.feature_values &&
    Object.prototype.hasOwnProperty.call(detail.feature_values, contribution.feature)
  ) {
    return { hasValue: true, value: detail.feature_values[contribution.feature] }
  }
  return { hasValue: false, value: undefined }
}
