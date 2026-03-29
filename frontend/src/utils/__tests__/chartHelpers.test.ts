import { describe, it, expect } from "vitest"
import { formatAxisLabel, yTicks } from "../chartHelpers"

// ---------------------------------------------------------------------------
// formatAxisLabel
// ---------------------------------------------------------------------------

describe("formatAxisLabel", () => {
  it("formats 1000000 with M suffix", () => {
    expect(formatAxisLabel(1_000_000)).toBe("1.0M")
  })

  it("formats -1000000 with M suffix", () => {
    expect(formatAxisLabel(-1_000_000)).toBe("-1.0M")
  })

  it("formats 1500000 with M suffix", () => {
    expect(formatAxisLabel(1_500_000)).toBe("1.5M")
  })

  it("formats 1000 with K suffix", () => {
    expect(formatAxisLabel(1_000)).toBe("1.0K")
  })

  it("formats 1500 with K suffix", () => {
    expect(formatAxisLabel(1_500)).toBe("1.5K")
  })

  it("formats 999 as plain integer", () => {
    expect(formatAxisLabel(999)).toBe("999")
  })

  it("formats very small numbers in exponential notation", () => {
    expect(formatAxisLabel(0.001)).toBe("1.0e-3")
  })

  it("formats 0 as '0'", () => {
    expect(formatAxisLabel(0)).toBe("0")
  })

  it("formats 42 as plain integer", () => {
    expect(formatAxisLabel(42)).toBe("42")
  })

  it("formats 3.14 with two decimal places", () => {
    expect(formatAxisLabel(3.14)).toBe("3.14")
  })

  it("handles NaN gracefully", () => {
    expect(formatAxisLabel(NaN)).toBe("NaN")
  })

  it("handles Infinity", () => {
    expect(formatAxisLabel(Infinity)).toBe("InfinityM")
  })

  it("handles -Infinity", () => {
    expect(formatAxisLabel(-Infinity)).toBe("-InfinityM")
  })

  it("formats negative thousands with K suffix", () => {
    expect(formatAxisLabel(-2_500)).toBe("-2.5K")
  })

  it("formats a float below 0.01 with exponential notation", () => {
    expect(formatAxisLabel(0.005)).toBe("5.0e-3")
  })

  it("formats a negative small number with exponential notation", () => {
    expect(formatAxisLabel(-0.005)).toBe("-5.0e-3")
  })
})

// ---------------------------------------------------------------------------
// yTicks
// ---------------------------------------------------------------------------

describe("yTicks", () => {
  it("generates 5 evenly-spaced ticks for count=4", () => {
    const ticks = yTicks(0, 10, 4)
    expect(ticks).toEqual([0, 2.5, 5, 7.5, 10])
  })

  it("returns [min] when min equals max", () => {
    expect(yTicks(5, 5)).toEqual([5])
  })

  it("defaults count to 4", () => {
    const ticks = yTicks(0, 100)
    expect(ticks).toHaveLength(5)
    expect(ticks[0]).toBe(0)
    expect(ticks[4]).toBe(100)
  })

  it("handles a negative range", () => {
    const ticks = yTicks(-10, -2, 4)
    expect(ticks).toHaveLength(5)
    expect(ticks[0]).toBe(-10)
    expect(ticks[4]).toBe(-2)
  })

  it("first element is min and last is max", () => {
    const ticks = yTicks(3, 27, 3)
    expect(ticks[0]).toBe(3)
    expect(ticks[ticks.length - 1]).toBe(27)
  })

  it("generates correct count+1 ticks", () => {
    const ticks = yTicks(0, 20, 2)
    expect(ticks).toEqual([0, 10, 20])
  })

  it("handles fractional boundaries", () => {
    const ticks = yTicks(0.5, 1.5, 2)
    expect(ticks).toHaveLength(3)
    expect(ticks[0]).toBe(0.5)
    expect(ticks[2]).toBe(1.5)
  })
})
