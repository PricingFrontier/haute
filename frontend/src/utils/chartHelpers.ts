/**
 * The shared axis and scale helpers every SVG chart uses: compact axis numbers,
 * padded finite domains, evenly spaced ticks, exact log-axis decades, label
 * thinning and axis-label truncation.
 */

/** Keep exact data in details; axes use a readable, consistent compact format. */
export function formatChartNumber(value: number): string {
  if (value !== 0 && Math.abs(value) < 0.0001) return value.toExponential(1)
  return new Intl.NumberFormat("en", {
    maximumSignificantDigits: 3,
    notation: Math.abs(value) >= 10000 ? "compact" : "standard",
  }).format(value)
}

/** A padded domain also gives constant and single-point series a finite scale. */
export function chartDomain(values: number[], includeZero = false): [number, number] {
  const min = Math.min(...values, ...(includeZero ? [0] : []))
  const max = Math.max(...values, ...(includeZero ? [0] : []))
  const pad = (max - min || Math.max(Math.abs(max), 0.001)) * 0.08
  return [min - pad, max + pad]
}

/** `count` evenly spaced ticks from `low` to `high` inclusive; a degenerate range yields one. */
export function chartTicks(low: number, high: number, count = 4): number[] {
  if (low === high) return [low]
  return Array.from({ length: count }, (_, i) => low + ((high - low) * i) / (count - 1))
}

/** 10 to the power `exponent`, exactly as its decimal literal (`10 ** -4` is not 0.0001). */
export function decade(exponent: number): number {
  return Number(`1e${exponent}`)
}

/** Evenly spaced label indices, including endpoints without adjacent end labels. */
export function chartLabelIndices(length: number, width: number, labelWidth = 84): Set<number> {
  const count = Math.min(length, Math.max(2, Math.floor(width / labelWidth)))
  if (length <= 1) return new Set([0])
  return new Set(
    Array.from({ length: count }, (_, i) => Math.round((i * (length - 1)) / (count - 1))),
  )
}

/** The chart heading and SVG title retain the full name when the axis is narrow. */
export function chartAxisLabel(label: string, plotWidth: number): string {
  const limit = Math.max(8, Math.floor(plotWidth / 7))
  return label.length > limit ? `${label.slice(0, limit - 1)}…` : label
}
