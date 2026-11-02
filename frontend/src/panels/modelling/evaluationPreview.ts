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

/**
 * The `error_code` of an estimate refused because its evaluation preview
 * failed, as opposed to its memory estimate: the Split pane shows the first,
 * and the Train pane labels the two apart.
 */
export const EVALUATION_PREVIEW_FAILED = "evaluation_preview_failed"
