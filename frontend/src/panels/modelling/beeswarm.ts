/** Value colours and dot stacking shared by the SHAP and ratebook beeswarms. */

/** A value's low and high colours, as a SHAP-style beeswarm colours its dots. */
export const VALUE_LOW_COLOR = "var(--chart-impact-value-low)"
export const VALUE_HIGH_COLOR = "var(--chart-impact-value-high)"
/** The colour of a dot whose value has no position, such as a categorical level. */
export const VALUE_NEUTRAL_COLOR = "var(--chart-impact-value-neutral)"

/** The colour for a value at `position`, 0 (low) to 1 (high); null is the neutral colour. */
export function valuePositionColor(position: number | null): string {
  if (position == null) return VALUE_NEUTRAL_COLOR

  const highPct = Math.round(position * 100)
  const lowPct = 100 - highPct
  return `color-mix(in srgb, ${VALUE_LOW_COLOR} ${lowPct}%, ${VALUE_HIGH_COLOR} ${highPct}%)`
}

/**
 * Each dot's offset from its row line. Dots whose x falls in the same
 * `bucketWidth` bucket take lanes 0, +1, -1, +2, -2, ... `laneStep` apart; the
 * lanes are scaled down when the densest bucket would pass `halfHeight`, so
 * density shows as height and no dot leaves its row.
 */
export function beeswarmOffsets(
  xs: readonly number[],
  { bucketWidth, laneStep, halfHeight }: { bucketWidth: number; laneStep: number; halfHeight: number },
): number[] {
  const counts = new Map<number, number>()
  const lanes = xs.map((x) => {
    const bucket = Math.round(x / bucketWidth)
    const count = counts.get(bucket) ?? 0
    counts.set(bucket, count + 1)
    return count === 0 ? 0 : (count % 2 === 1 ? 1 : -1) * Math.ceil(count / 2)
  })
  const widest = lanes.reduce((max, lane) => Math.max(max, Math.abs(lane)), 0)
  const step = widest * laneStep > halfHeight ? halfHeight / widest : laneStep
  return lanes.map((lane) => lane * step)
}
