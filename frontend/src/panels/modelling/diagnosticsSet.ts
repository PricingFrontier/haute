import type { TrainResult } from "../../stores/useNodeResultsStore"

/** The name of the partition a result's diagnostics were evaluated on. */
export function diagnosticsSetLabel(set: TrainResult["diagnostics_set"]): string {
  return set === "final_test" ? "Test" : set === "validation" ? "Validation" : "Training"
}

/**
 * Whether the validation fit's out-of-sample metrics lead the result: a
 * validation fit ran, the model was refit, and no test set was reserved, so
 * the diagnostics are in-sample.
 */
export function validationMetricsLead(result: TrainResult): boolean {
  return (
    result.diagnostics_set === "development"
    && result.evaluation !== undefined
    && result.evaluation.validation_method !== "none"
  )
}

/** The metrics a result leads with: test, else validation (see above), else its diagnostics. */
export function headlineMetrics(result: TrainResult): Record<string, number> {
  if (Object.keys(result.final_test_metrics).length > 0) return result.final_test_metrics
  if (validationMetricsLead(result)) {
    return Object.fromEntries(
      Object.entries(result.evaluation!.selection_metrics).map(([name, summary]) => [name, summary.mean]),
    )
  }
  return result.diagnostic_metrics
}

/** The rows the diagnostics were evaluated on. A kept validation model's
 *  diagnostics come from its one selection fit, which the evaluation must name. */
export function diagnosticsRowCount(result: TrainResult): number {
  if (result.diagnostics_set === "final_test") return result.final_test_rows
  if (result.diagnostics_set === "development") return result.development_rows
  const fit = result.evaluation?.selection_fits[0]
  if (!fit) {
    throw new Error(
      "Validation diagnostics need the evaluation's selection fit to count their rows.",
    )
  }
  return fit.validation_rows
}
