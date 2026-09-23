import type { EvaluationPreview } from "../../api/types"

export type EvaluationStrategy = EvaluationPreview["strategy"]
export type ValidationMethod = EvaluationPreview["validation_method"]

export function compatibleEvaluationPreview(
  preview: EvaluationPreview | null,
  strategy: EvaluationStrategy,
  method: ValidationMethod,
) {
  return preview !== null &&
    preview.strategy === strategy &&
    preview.validation_method === method
    ? preview
    : null
}
