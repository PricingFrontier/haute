/** The Adjustments view's shared copy and formatting, which Summary also uses. */

import type { OptimiserAdjustmentReport, OptimiserAdjustmentWeighting } from "../../api/types"

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

/** Whether the report's grid is one step, which is then both the range minimum and its maximum. */
export function isOneStepGrid(report: OptimiserAdjustmentReport): boolean {
  return report.bars.length === 1
}

/**
 * A weighting's share at the edge of the scenario range: at its minimum step or
 * its maximum. On a one-step grid they are the same step, so its share is
 * counted once (the union), never summed to 200%.
 */
export function rangeEdgeShare(report: OptimiserAdjustmentReport, weighting: OptimiserAdjustmentWeighting): number {
  if (!isOneStepGrid(report)) return weighting.share_at_min + weighting.share_at_max
  if (weighting.share_at_min !== weighting.share_at_max) {
    throw new Error(
      `A one-step grid's share at the minimum (${weighting.share_at_min}) and at the maximum `
      + `(${weighting.share_at_max}) must be the same step's share (${weighting.label}).`,
    )
  }
  return weighting.share_at_min
}
