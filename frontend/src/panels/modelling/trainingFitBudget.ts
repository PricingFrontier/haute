/**
 * Top-level selection fits and the final development fit (not GLM's internal penalty
 * CV). *finalFit* says whether that fit runs.
 */
function trainingFitBudget(
  evaluation: Record<string, unknown>,
  tuning: Record<string, unknown> | null,
  finalFit: boolean,
): { selection: number; total: number } | null {
  const validation = evaluation.validation as
    Record<string, unknown> | undefined
  const folds =
    validation?.method === "none"
      ? 0
      : validation?.method === "single"
        ? 1
        : validation?.method === "cross_validation"
          ? Number(validation.fold_count)
          : NaN
  const trials = tuning ? Number(tuning.trial_count) : 1
  if (
    !Number.isInteger(folds) ||
    folds < 0 ||
    folds > 10 ||
    !Number.isInteger(trials) ||
    trials < 1 ||
    (tuning && folds === 0)
  )
    return null
  const selection = folds * trials
  return { selection, total: selection + Number(finalFit) }
}

/**
 * The fit budget in words, as the Train pane's run summary and the Parameters
 * pane's tuning note both say it, or null while the evaluation or tuning
 * settings are incomplete.
 */
export function fitBudgetLabel(
  evaluation: Record<string, unknown>,
  tuning: Record<string, unknown> | null,
  refitOnDevelopment: boolean,
): string | null {
  const budget = trainingFitBudget(evaluation, tuning, refitOnDevelopment)
  if (!budget) return null
  if (budget.selection === 1 && budget.total === 1) return "1 validation fit (saved model)"
  if (budget.selection === 0) return "1 final fit"
  const selectionFits = `${budget.selection} ${tuning ? "tuning" : "validation"} ${budget.selection === 1 ? "fit" : "fits"}`
  return `${budget.total} total fits: ${selectionFits} + 1 final fit`
}
