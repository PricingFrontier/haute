import { describe, it, expect } from "vitest"
import {
  chartAxisLabel,
  chartDomain,
  chartLabelIndices,
  chartTicks,
  formatChartNumber,
} from "../chartHelpers"

describe("formatChartNumber", () => {
  it("keeps three significant digits below ten thousand", () => {
    expect(formatChartNumber(0)).toBe("0")
    expect(formatChartNumber(999)).toBe("999")
    expect(formatChartNumber(1234.5)).toBe("1,230")
    expect(formatChartNumber(-0.12345)).toBe("-0.123")
  })

  it("uses compact notation from ten thousand", () => {
    expect(formatChartNumber(12_345)).toBe("12.3K")
    expect(formatChartNumber(1_234_567)).toBe("1.23M")
    expect(formatChartNumber(-2_500_000)).toBe("-2.5M")
  })

  it("uses exponential notation for tiny non-zero values", () => {
    expect(formatChartNumber(0.00005)).toBe("5.0e-5")
  })
})

describe("chartDomain", () => {
  it("pads a range by eight percent on each side", () => {
    const [low, high] = chartDomain([0, 100])
    expect(low).toBeCloseTo(-8)
    expect(high).toBeCloseTo(108)
  })

  it("gives a constant or single-point series a finite scale", () => {
    const [low, high] = chartDomain([5])
    expect(low).toBeLessThan(5)
    expect(high).toBeGreaterThan(5)
    const [zeroLow, zeroHigh] = chartDomain([0, 0])
    expect(zeroHigh - zeroLow).toBeGreaterThan(0)
  })

  it("can include zero", () => {
    expect(chartDomain([10, 20], true)[0]).toBeLessThan(0)
  })
})

describe("chartTicks", () => {
  it("spaces ticks evenly and includes both ends", () => {
    expect(chartTicks(0, 100, 5)).toEqual([0, 25, 50, 75, 100])
    expect(chartTicks(-1, 1, 3)).toEqual([-1, 0, 1])
  })

  it("yields one tick for a degenerate range", () => {
    expect(chartTicks(5, 5, 5)).toEqual([5])
  })
})

describe("chartLabelIndices", () => {
  it("keeps both ends and thins labels to the available width", () => {
    expect(chartLabelIndices(10, 168, 84)).toEqual(new Set([0, 9]))
    expect(chartLabelIndices(3, 1000, 84)).toEqual(new Set([0, 1, 2]))
    expect(chartLabelIndices(1, 1000)).toEqual(new Set([0]))
  })
})

describe("chartAxisLabel", () => {
  it("truncates a long label to the plot width", () => {
    expect(chartAxisLabel("short", 700)).toBe("short")
    expect(chartAxisLabel("a very long feature name", 70)).toBe("a very lo…")
  })
})
