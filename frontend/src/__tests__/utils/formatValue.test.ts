/**
 * Tests for formatValue utility functions.
 *
 * Tests: formatValue (null/undefined/number/string/boolean/maxFractionDigits),
 * formatValueCompact (truncation), formatNumber (M/K suffixes, negatives),
 * formatElapsed (seconds, minutes+seconds boundary).
 */
import { describe, it, expect } from "vitest"
import { formatValue, formatValueCompact, formatNumber, formatElapsed } from "../../utils/formatValue"

// ── formatValue ─────────────────────────────────────────────────

describe("formatValue", () => {
  it('returns "null" for null', () => {
    expect(formatValue(null)).toBe("null")
  })

  it('returns "null" for undefined', () => {
    expect(formatValue(undefined)).toBe("null")
  })

  it("formats an integer with locale separators", () => {
    // toLocaleString in Node (ICU) will format 1000 → "1,000" for en-US
    const result = formatValue(1000)
    // Accept any locale formatting — the key point is no decimals
    expect(result).not.toContain(".")
    expect(result).toMatch(/1.?000/)
  })

  it("formats a float with up to 4 fraction digits by default", () => {
    const result = formatValue(3.141592653)
    // Should have at most 4 decimal digits
    const parts = result.split(/[.,]/)
    // The last segment holds fractional digits (locale may use comma)
    const fractionPart = parts[parts.length - 1]
    expect(fractionPart.length).toBeLessThanOrEqual(4)
  })

  it("respects maxFractionDigits parameter", () => {
    const result = formatValue(1.23456789, 2)
    // With maxFractionDigits=2, should round to 2 decimal places
    // In en-US: "1.23"
    expect(result).toMatch(/1[.,]23/)
  })

  it("formats zero as an integer (no decimals)", () => {
    expect(formatValue(0)).toBe("0")
  })

  it("converts a string to itself", () => {
    expect(formatValue("hello")).toBe("hello")
  })

  it("converts an empty string to itself", () => {
    expect(formatValue("")).toBe("")
  })

  it("converts a boolean true to string", () => {
    expect(formatValue(true)).toBe("true")
  })

  it("converts a boolean false to string", () => {
    expect(formatValue(false)).toBe("false")
  })

  it("converts a struct (object) value to JSON, not '[object Object]'", () => {
    expect(formatValue({ a: 1 })).toBe('{"a":1}')
  })

  it("formats Haute non-finite JSON sentinels distinctly from null", () => {
    expect(formatValue({ __haute_type__: "non_finite_float", value: "nan" })).toBe("NaN")
    expect(formatValue({ __haute_type__: "non_finite_float", value: "inf" })).toBe("Infinity")
    expect(formatValue({ __haute_type__: "non_finite_float", value: "-inf" })).toBe("-Infinity")
    expect(formatValue(null)).toBe("null")
  })
})

// ── formatValueCompact ──────────────────────────────────────────

describe("formatValueCompact", () => {
  it("returns short strings unchanged", () => {
    expect(formatValueCompact("hello")).toBe("hello")
  })

  it("returns exactly 20-char strings unchanged", () => {
    const s = "a".repeat(20)
    expect(formatValueCompact(s)).toBe(s)
  })

  it("truncates strings longer than 20 chars with ellipsis", () => {
    const s = "a".repeat(25)
    const result = formatValueCompact(s)
    expect(result.length).toBe(19) // 18 chars + 1 ellipsis char
    expect(result).toBe("a".repeat(18) + "\u2026")
  })

  it("delegates to formatValue for non-string values", () => {
    expect(formatValueCompact(null)).toBe("null")
    expect(formatValueCompact(42)).toBe("42")
  })
})

// ── formatNumber ────────────────────────────────────────────────

describe("formatNumber", () => {
  it('formats millions with "M" suffix', () => {
    expect(formatNumber(1_234_567)).toBe("1.23M")
  })

  it('formats negative millions with "M" suffix', () => {
    expect(formatNumber(-5_000_000)).toBe("-5.00M")
  })

  it('formats exactly 1 million with "M" suffix', () => {
    expect(formatNumber(1_000_000)).toBe("1.00M")
  })

  it('formats thousands with "K" suffix', () => {
    expect(formatNumber(12_345)).toBe("12.3K")
  })

  it('formats negative thousands with "K" suffix', () => {
    expect(formatNumber(-1_500)).toBe("-1.5K")
  })

  it('formats exactly 1000 with "K" suffix', () => {
    expect(formatNumber(1_000)).toBe("1.0K")
  })

  it("formats small numbers with 4 decimal places", () => {
    expect(formatNumber(3.14159)).toBe("3.1416")
  })

  it("formats zero as integer", () => {
    expect(formatNumber(0)).toBe("0")
  })

  it("formats small negative integers without decimals", () => {
    expect(formatNumber(-42)).toBe("-42")
  })

  it("formats 999 as integer (below K threshold)", () => {
    expect(formatNumber(999)).toBe("999")
  })

  it("formats -999 as integer (above -K threshold)", () => {
    expect(formatNumber(-999)).toBe("-999")
  })
})

// ── formatElapsed ───────────────────────────────────────────────

describe("formatElapsed", () => {
  it("formats zero seconds", () => {
    expect(formatElapsed(0)).toBe("0s")
  })

  it("formats fractional seconds (rounds to nearest integer)", () => {
    expect(formatElapsed(3.7)).toBe("4s")
  })

  it("formats seconds under 60 without minutes", () => {
    expect(formatElapsed(45)).toBe("45s")
  })

  it("formats 59.4 seconds (rounds down, stays under 60)", () => {
    expect(formatElapsed(59.4)).toBe("59s")
  })

  it("formats exactly 60 seconds as 1m 0s", () => {
    expect(formatElapsed(60)).toBe("1m 0s")
  })

  it("formats 90 seconds as 1m 30s", () => {
    expect(formatElapsed(90)).toBe("1m 30s")
  })

  it("formats large values correctly", () => {
    expect(formatElapsed(3661)).toBe("61m 1s")
  })

  it("formats fractional minutes correctly (floors minutes and seconds)", () => {
    expect(formatElapsed(125.9)).toBe("2m 5s")
  })
})
