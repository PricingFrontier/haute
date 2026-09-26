/** The Adjustments view's shared copy and formatting, which Summary also uses. */

/** A point report's identity: point indices mean something only within one job and generation. */
export function pointAdjustmentKey(jobId: string, frontierGeneration: number, pointIndex: number): string {
  return `${jobId}:${frontierGeneration}:${pointIndex}`
}

export const NO_UNADJUSTED_NOTE = "The scenario grid has no 1.0 step, so no quote is unadjusted."

/** A grid value as the solver scored it (Float32 widened), without the widening noise. */
export function formatScenarioValue(value: number): string {
  return String(Number(value.toPrecision(6)))
}

export function formatShare(share: number): string {
  return `${(share * 100).toFixed(1)}%`
}
