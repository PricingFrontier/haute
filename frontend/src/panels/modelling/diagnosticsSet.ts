import type { TrainResult } from "../../stores/useNodeResultsStore"

/** The name of the partition a result's diagnostics were evaluated on. */
export function diagnosticsSetLabel(set: TrainResult["diagnostics_set"]): string {
  return set === "final_test" ? "Test" : set === "validation" ? "Validation" : "Training"
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
