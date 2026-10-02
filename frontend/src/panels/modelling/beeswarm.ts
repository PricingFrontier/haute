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

/** The CSS custom properties behind the value colours, for painting them on a canvas. */
export const VALUE_COLOR_TOKENS = {
  low: "--chart-impact-value-low",
  high: "--chart-impact-value-high",
  neutral: "--chart-impact-value-neutral",
} as const

export type Rgb = readonly [number, number, number]

/** A `#rgb` or `#rrggbb` colour token's channels; anything else throws. */
export function parseHexColor(token: string): Rgb {
  const hex = token.trim()
  const short = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i.exec(hex)
  const long = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex)
  const channels = long?.slice(1) ?? short?.slice(1).map((digit) => digit + digit)
  if (!channels) throw new Error(`Expected a hex colour for a beeswarm value colour, got "${token}"`)
  return [parseInt(channels[0], 16), parseInt(channels[1], 16), parseInt(channels[2], 16)]
}

/**
 * The canvas colour for a value at `position`, 0 (low) to 1 (high): the same
 * sRGB mix, at the same whole-percent steps, as `valuePositionColor`.
 */
export function valuePositionRgb(position: number, low: Rgb, high: Rgb): string {
  const highShare = Math.round(position * 100) / 100
  const channel = (index: number) => Math.round(low[index] + (high[index] - low[index]) * highShare)
  return `rgb(${channel(0)}, ${channel(1)}, ${channel(2)})`
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
