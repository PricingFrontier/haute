/** Test fixture: equal-width bins in the shape the banding statistics send, for histogram tests. */
import type { BandingHistogramBin } from "../../../../api/types"

/**
 * The lower edge of each bin, which is also what a count is measured against.
 *
 * The server derives the edges the same way and places values by comparing
 * against these numbers rather than by repeating the arithmetic: an index
 * computed as `floor((value - low) / width)` is a second calculation that can
 * round differently from the edge it is supposed to agree with — over 40 bins
 * of `[0, 1]` it puts `0.3` in the bin whose lower edge is `0.30000000000000004`,
 * above the value itself — and lets the browser and the server disagree about
 * the same number.
 */
export function binEdges(minimum: number, maximum: number, bins: number): number[] {
  const width = (maximum - minimum) / bins
  return Array.from({ length: bins }, (_unused, position) => minimum + width * position)
}

/**
 * Equal-width bins over the finite values given, in the shape the server sends.
 *
 * The editor renders one histogram whether its numbers came from the whole
 * dataset or from preview rows, so the fallback has to produce the server's
 * shape and its edges: non-finite values are not values a bin holds, a constant
 * column is one bin rather than an empty range, and the last bin is closed so
 * the largest value falls inside it.
 */
export function equalWidthBins(values: number[], binCount: number): BandingHistogramBin[] {
  const finite = values.filter((value) => Number.isFinite(value))
  if (finite.length === 0 || binCount < 1) return []

  let low = finite[0]
  let high = finite[0]
  for (const value of finite) {
    if (value < low) low = value
    if (value > high) high = value
  }
  if (low === high) return [{ lower: low, upper: high, count: finite.length }]

  // An extent narrow enough that equal-width edges are not distinct as floats
  // cannot be split that finely: the bins are the distinct edges, so every bin
  // shown is one a value can fall in — and the server does the same.
  const edges = binEdges(low, high, binCount).filter(
    (edge, index, all) => index === 0 || edge > all[index - 1],
  )
  const counts = new Array<number>(edges.length).fill(0)
  for (const value of finite) {
    // The last edge at or below the value, found by comparing against the
    // published edges — never by recomputing the index.
    let index = edges.length - 1
    for (let position = 1; position < edges.length; position += 1) {
      if (value < edges[position]) {
        index = position - 1
        break
      }
    }
    counts[index] += 1
  }
  return edges.map((lower, index) => ({
    lower,
    upper: index === edges.length - 1 ? high : edges[index + 1],
    count: counts[index],
  }))
}
