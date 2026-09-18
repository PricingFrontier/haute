/**
 * The preview fallback bins the same way the server does (RAT-B02).
 *
 * The editor draws one histogram whether its numbers came from the whole
 * dataset or from preview rows, so the fallback has to agree with the server
 * about where a value falls — otherwise the picture would change shape when
 * the data was cached, for no reason the user could see.
 */
import { describe, expect, it } from "vitest"

import { equalWidthBins } from "../bandingBins"

describe("equalWidthBins", () => {
  it("splits the extent into equal intervals with the last one closed", () => {
    // The server's own vector: [0,1,2,3,4] in two bins is [0,2) and [2,4].
    expect(equalWidthBins([0, 1, 2, 3, 4], 2)).toEqual([
      { lower: 0, upper: 2, count: 2 },
      { lower: 2, upper: 4, count: 3 },
    ])
  })

  it("gives a constant column one bin rather than an empty range", () => {
    expect(equalWidthBins([7, 7, 7], 8)).toEqual([{ lower: 7, upper: 7, count: 3 }])
  })

  it("holds no value a bin cannot hold", () => {
    // NaN and infinity are not values a bin holds, and they never widen the
    // extent — the same rule the server applies.
    expect(equalWidthBins([1, 2, NaN, Infinity, -Infinity], 2)).toEqual([
      { lower: 1, upper: 1.5, count: 1 },
      { lower: 1.5, upper: 2, count: 1 },
    ])
  })

  it("has nothing to draw without a finite value", () => {
    expect(equalWidthBins([], 4)).toEqual([])
    expect(equalWidthBins([NaN, Infinity], 4)).toEqual([])
  })

  it("bins an extent too narrow to split without repeating an edge", () => {
    // 110.0 and 100 * 1.1 differ by one representable step, so 40 equal-width
    // edges are not 40 distinct numbers; a bin whose edges are the same number
    // is not an interval, and asking Polars for those breaks is an error.
    const bins = equalWidthBins([110.0, 100.0 * 1.1], 40)
    expect(bins.length).toBeGreaterThan(0)
    expect(bins.length).toBeLessThan(40)
    expect(bins.reduce((total, bin) => total + bin.count, 0)).toBe(2)
    for (let index = 1; index < bins.length; index += 1) {
      expect(bins[index].lower).toBeGreaterThan(bins[index - 1].lower)
    }
  })

  it("puts the largest value in the last bin rather than past the end", () => {
    const bins = equalWidthBins([0, 10], 4)
    expect(bins).toHaveLength(4)
    expect(bins[bins.length - 1]).toMatchObject({ upper: 10, count: 1 })
    expect(bins.reduce((total, bin) => total + bin.count, 0)).toBe(2)
  })
})
