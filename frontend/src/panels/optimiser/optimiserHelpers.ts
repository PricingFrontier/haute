/**
 * Pure helpers shared across the optimiser sub-components.
 *
 * Kept out of OptimiserPreview.tsx so that file only exports components
 * (satisfies `react-refresh/only-export-components`).
 */

/**
 * Decide whether a constraint is met given its threshold type and the
 * observed ratio / absolute value.  Used by both `SummaryTab` (to tint
 * the constraint row green/red) and `DetailCard` (to summarise the
 * selected frontier point).
 */
export function isConstraintMet(
  thresholdType: string,
  ratio: number,
  absValue: number,
  thresholdVal: number,
): boolean {
  if (thresholdType === "min") return ratio >= thresholdVal
  if (thresholdType === "max") return ratio <= thresholdVal
  if (thresholdType === "min_abs") return absValue >= thresholdVal
  if (thresholdType === "max_abs") return absValue <= thresholdVal
  return true
}
