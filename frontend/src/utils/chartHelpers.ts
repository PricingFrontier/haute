/**
 * The shared axis and scale helpers every SVG chart uses: compact axis numbers,
 * axis tick labels formatted together, padded finite domains, evenly spaced
 * ticks, exact log-axis decades, label thinning and axis-label truncation.
 */

const COMPACT_FROM = 10_000
const EXPONENTIAL_BELOW = 0.0001

/** Keep exact data in details; axes use a readable, consistent compact format. */
export function formatChartNumber(value: number): string {
  if (value !== 0 && Math.abs(value) < EXPONENTIAL_BELOW) return value.toExponential(1)
  return new Intl.NumberFormat("en", {
    maximumSignificantDigits: 3,
    notation: Math.abs(value) >= COMPACT_FROM ? "compact" : "standard",
  }).format(value)
}

type TickNotation = "standard" | "compact" | "exponential"

/** The power of ten of a non-zero value's leading digit. */
function exponent(value: number): number {
  return Math.floor(Math.log10(Math.abs(value)))
}

/** Rounded half away from zero to `decimals` places; negative rounds to tens, hundreds and so on. */
function roundTo(value: number, decimals: number): number {
  const magnitude = decimals >= 0
    ? Math.round(Math.abs(value) * decade(decimals)) / decade(decimals)
    : Math.round(Math.abs(value) / decade(-decimals)) * decade(-decimals)
  return magnitude === 0 ? 0 : Math.sign(value) * magnitude
}

function tickLabel(value: number, notation: TickNotation, decimals: number): string {
  // A tick that rounds to zero is labelled 0, never "-0.00".
  const shown = Math.abs(value) < decade(-decimals) / 2 ? 0 : value
  if (notation === "standard") {
    return new Intl.NumberFormat("en", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    }).format(shown)
  }
  if (shown === 0) return "0"
  if (notation === "exponential") return shown.toExponential(Math.max(1, decimals + exponent(shown)))
  // Compact units differ from label to label (250K beside 1M), so they share
  // the rounding rather than a count of decimals.
  const rounded = roundTo(shown, decimals)
  return new Intl.NumberFormat("en", {
    maximumSignificantDigits: Math.max(1, exponent(rounded) + decimals + 1),
    notation: "compact",
  }).format(rounded)
}

/**
 * The labels of one linear axis's evenly spaced ticks, formatted together at
 * one precision: the precision `formatChartNumber` gives the largest tick,
 * raised until neighbouring labels differ. A standard-notation axis shows the
 * same decimals on every label, less the trailing zeros every tick shares;
 * from ten thousand the labels stay compact and below 0.0001 exponential, as
 * `formatChartNumber` does. A log axis's decades are not evenly spaced and
 * are labelled one by one with `formatChartNumber`.
 */
export function formatChartTicks(ticks: readonly number[]): string[] {
  if (ticks.length < 2) return ticks.map(formatChartNumber)
  const step = (ticks[ticks.length - 1] - ticks[0]) / (ticks.length - 1)
  const largest = Math.max(...ticks.map(Math.abs))
  // Ticks spaced near the values' last significant digit carry float error of
  // that size, so evenness is judged against it as well as the step.
  const tolerance = Math.abs(step) * 1e-6 + largest * Number.EPSILON * 8
  const uneven = ticks.some((tick, i) => i > 0 && Math.abs(tick - ticks[i - 1] - step) > tolerance)
  if (!(Math.abs(step) > 0) || uneven) {
    throw new Error(`Axis ticks must be distinct and evenly spaced to share one precision: ${ticks.join(", ")}`)
  }
  const notation: TickNotation =
    largest >= COMPACT_FROM ? "compact" : largest < EXPONENTIAL_BELOW ? "exponential" : "standard"
  // Three significant figures, or two for the exponential mantissa, as formatChartNumber.
  const initial = (notation === "exponential" ? 1 : 2) - exponent(largest)
  let decimals = notation === "standard" ? Math.max(0, initial) : initial
  const labelsAt = (places: number) => ticks.map((tick) => tickLabel(tick, notation, places))
  let labels = labelsAt(decimals)
  while (labels.some((label, i) => i > 0 && label === labels[i - 1])) {
    decimals += 1
    labels = labelsAt(decimals)
  }
  if (notation !== "standard") return labels
  // Drop a decimal only while every label ends in a zero there, which removes
  // nothing a label shows, so the labels stay distinct.
  while (decimals > 0 && labels.every((label) => label.endsWith("0"))) {
    decimals -= 1
    labels = labelsAt(decimals)
  }
  return labels
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
