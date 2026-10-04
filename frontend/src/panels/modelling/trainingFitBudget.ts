/**
 * Top-level selection fits and the one fit beyond them (not GLM's internal penalty CV):
 * the final development fit, or for a family that publishes its validation fit
 * (t-boost) the winning trial's fit a study reproduces. *finalFit* says whether
 * that fit runs.
 */
export function trainingFitBudget(
  evaluation: Record<string, unknown>,
  tuning: Record<string, unknown> | null,
  finalFit = true,
): { selection: number; total: number; folds: number; trials: number } | null {
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
  return { selection, total: selection + Number(finalFit), folds, trials }
}
