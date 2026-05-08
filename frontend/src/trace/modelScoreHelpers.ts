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
  if (identity.run_id) return `Model run: ${identity.run_id}`
  return identity.source_type ? `Model source: ${identity.source_type}` : "Model Score"
}

export function modelScorePrediction(detail: ModelScoreNodeDetail): { hasPrediction: boolean; value: unknown } {
  if ("prediction_value" in detail) return { hasPrediction: true, value: detail.prediction_value }
  return { hasPrediction: false, value: undefined }
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
