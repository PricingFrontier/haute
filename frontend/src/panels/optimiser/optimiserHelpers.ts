/**
 * Pure helpers shared across the optimiser sub-components.
 *
 * Kept out of OptimiserPreview.tsx so that file only exports components
 * (satisfies `react-refresh/only-export-components`).
 */

/**
 * Decide whether an absolute constraint is met given its threshold type and
 * observed value.  Used by both `SummaryTab` (to tint
 * the constraint row green/red) and `DetailCard` (to summarise the
 * selected frontier point).
 */
export function isConstraintMet(
  thresholdType: string,
  _ratio: number,
  absValue: number,
  thresholdVal: number,
): boolean {
  if (thresholdType === "min") return absValue >= thresholdVal
  if (thresholdType === "max") return absValue <= thresholdVal
  return false
}
