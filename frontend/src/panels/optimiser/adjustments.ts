/** The Adjustments view's shared copy and formatting, which Summary also uses. */

/** A point report's identity: point indices mean something only within one job and generation. */
export function pointAdjustmentKey(jobId: string, frontierGeneration: number, pointIndex: number): string {
  return `${jobId}:${frontierGeneration}:${pointIndex}`
}

export const NO_UNADJUSTED_NOTE = "The scenario grid has no 1.0 step, so no quote is unadjusted."

/** A ratebook report's count of quotes whose deployed factor differs from the evaluated step. */
export const DEPLOYED_FACTOR_DIFFERS_LABEL = "Deployed factor differs from evaluated step"

export const DEPLOYED_FACTOR_DIFFERS_NOTE =
  "Inside the scenario range the Optimiser Apply node deploys the unsnapped product of the "
  + "factor rates; the solve evaluated each quote at the nearest grid step. A product past a "
  + "grid edge deploys at that edge, the step the solve evaluated, so it is not counted."

/** A grid value as the solver scored it (Float32 widened), without the widening noise. */
export function formatScenarioValue(value: number): string {
  return String(Number(value.toPrecision(6)))
}

export function formatShare(share: number): string {
  return `${(share * 100).toFixed(1)}%`
}
