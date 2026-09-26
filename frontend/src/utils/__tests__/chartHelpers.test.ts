import { describe, it, expect } from "vitest"
import {
  chartAxisLabel,
  chartDomain,
  chartLabelIndices,
  chartTicks,
  formatChartNumber,
  formatChartTicks,
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

describe("formatChartTicks", () => {
  it("keeps near-constant padded axes distinct without rejecting their float spacing", () => {
    // A series that barely moves: the axis pads it and spaces ticks below the
    // values' last significant digit, where floating point is uneven.
    for (const values of [[100, 100.0000001], [107, 107.00000001]]) {
      const labels = formatChartTicks(chartTicks(...chartDomain(values), 5))
      expect(new Set(labels).size).toBe(labels.length)
    }
  })

  it("labels a narrow range with distinct numbers at one precision", () => {
    // formatChartNumber labels each of these ticks "107".
    expect(formatChartTicks(chartTicks(107, 107.3, 5))).toEqual(["107.00", "107.08", "107.15", "107.23", "107.30"])
  })

  it("labels a range that three significant figures already tell apart as formatChartNumber does", () => {
    expect(formatChartTicks(chartTicks(0, 1_250, 5))).toEqual(["0", "313", "625", "938", "1,250"])
    expect(formatChartTicks(chartTicks(0.0001, 0.0005, 5))).toEqual(["0.0001", "0.0002", "0.0003", "0.0004", "0.0005"])
    expect(formatChartTicks(chartTicks(1_092_000, 1_208_000, 5))).toEqual(["1.09M", "1.12M", "1.15M", "1.18M", "1.21M"])
    expect(formatChartTicks(chartTicks(0, 1_000_000, 5))).toEqual(["0", "250K", "500K", "750K", "1M"])
    expect(formatChartTicks(chartTicks(0.00001, 0.00005, 5))).toEqual(["1.0e-5", "2.0e-5", "3.0e-5", "4.0e-5", "5.0e-5"])
  })

  it("gives every label the same decimals, dropping only trailing zeros every tick shares", () => {
    expect(formatChartTicks(chartTicks(8.4, 31.6, 5))).toEqual(["8.4", "14.2", "20.0", "25.8", "31.6"])
    expect(formatChartTicks(chartTicks(0, 1, 5))).toEqual(["0.00", "0.25", "0.50", "0.75", "1.00"])
    expect(formatChartTicks(chartTicks(0, 40, 5))).toEqual(["0", "10", "20", "30", "40"])
  })

  it("keeps compact notation and tells large neighbouring ticks apart", () => {
    expect(formatChartTicks(chartTicks(12_300_000, 12_500_000, 5))).toEqual(["12.3M", "12.35M", "12.4M", "12.45M", "12.5M"])
  })

  it("never labels a tick that rounds to zero as negative", () => {
    expect(formatChartTicks(chartTicks(-0.3, 0.3, 5))).toEqual(["-0.30", "-0.15", "0.00", "0.15", "0.30"])
  })

  it("formats a lone tick as a single value", () => {
    expect(formatChartTicks([0.5])).toEqual(["0.5"])
  })

  it("refuses ticks that are not evenly spaced, such as a log axis's decades", () => {
    expect(() => formatChartTicks([0.001, 0.01, 0.1, 1])).toThrow(/evenly spaced/)
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
