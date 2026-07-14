import { describe, it, expect } from "vitest"
import { formatNullPct, formatValue, formatValueCompact, formatFixed } from "../formatValue"

describe("formatValue", () => {
  it("formats null as 'null'", () => {
    expect(formatValue(null)).toBe("null")
  })

  it("formats undefined as 'null'", () => {
    expect(formatValue(undefined)).toBe("null")
  })

  it("formats integers with locale separators", () => {
    const result = formatValue(1000)
    // toLocaleString is locale-dependent; just verify it's a string representation of 1000
    expect(result).toContain("1")
    expect(result).toContain("000")
  })

  it("formats small integers without separators", () => {
    expect(formatValue(42)).toBe("42")
  })

  it("formats zero", () => {
    expect(formatValue(0)).toBe("0")
  })

  it("formats negative integers", () => {
    const result = formatValue(-5)
    expect(result).toContain("5")
    expect(result).toContain("-")
  })

  it("formats floats with up to 4 fraction digits by default", () => {
    const result = formatValue(3.14159)
    expect(result).toContain("3")
    // Should be truncated/rounded to at most 4 fraction digits
    expect(result.replace(/[^0-9]/g, "").length).toBeLessThanOrEqual(6)
  })

  it("respects custom maxFractionDigits", () => {
    const result = formatValue(1.23456789, 2)
    expect(result).toContain("1")
    // With maxFractionDigits=2, should have at most 2 decimal places
    const parts = result.split(/[.,]/)
    if (parts.length > 1) {
      const lastPart = parts[parts.length - 1]
      expect(lastPart.length).toBeLessThanOrEqual(2)
    }
  })

  it("formats strings via String()", () => {
    expect(formatValue("hello")).toBe("hello")
  })

  it("formats booleans via String()", () => {
    expect(formatValue(true)).toBe("true")
    expect(formatValue(false)).toBe("false")
  })

  it("formats struct (object) values as JSON, not '[object Object]'", () => {
    expect(formatValue({})).toBe("{}")
    expect(formatValue({ a: 1, b: "x" })).toBe('{"a":1,"b":"x"}')
    expect(formatValue({ nested: { list: [1, 2] } })).toBe('{"nested":{"list":[1,2]}}')
  })

  it("formats list/array values as JSON", () => {
    expect(formatValue([1, 2, 3])).toBe("[1,2,3]")
    expect(formatValue([{ a: 1 }, null])).toBe('[{"a":1},null]')
  })

  it("renders non-finite-float sentinels nested inside structs as display strings", () => {
    expect(
      formatValue({ x: { __haute_type__: "non_finite_float", value: "nan" }, y: 2 }),
    ).toBe('{"x":"NaN","y":2}')
  })

  it("formats Haute non-finite JSON sentinels distinctly from null", () => {
    expect(formatValue({ __haute_type__: "non_finite_float", value: "nan" })).toBe("NaN")
    expect(formatValue({ __haute_type__: "non_finite_float", value: "inf" })).toBe("Infinity")
    expect(formatValue({ __haute_type__: "non_finite_float", value: "-inf" })).toBe("-Infinity")
    expect(formatValue(null)).toBe("null")
  })
})

describe("formatValueCompact", () => {
  it("returns short values unchanged", () => {
    expect(formatValueCompact("short")).toBe("short")
    expect(formatValueCompact(42)).toBe("42")
    expect(formatValueCompact(null)).toBe("null")
  })

  it("truncates values longer than 20 characters", () => {
    const result = formatValueCompact("a]very long string that exceeds twenty chars")
    expect(result.length).toBe(19) // 18 chars + ellipsis
    expect(result).toMatch(/\u2026$/) // ends with ellipsis
  })

  it("does not truncate exactly 20 character values", () => {
    const result = formatValueCompact("12345678901234567890")
    expect(result).toBe("12345678901234567890")
    expect(result.length).toBe(20)
  })

  it("truncates 21+ character values", () => {
    const result = formatValueCompact("123456789012345678901")
    expect(result.length).toBe(19)
    expect(result.endsWith("\u2026")).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// formatValue – edge cases
// ---------------------------------------------------------------------------

describe("formatValue edge cases", () => {
  it("formats NaN as a string", () => {
    const result = formatValue(NaN)
    expect(result).toBe("NaN")
  })

  it("formats Infinity as a string", () => {
    const result = formatValue(Infinity)
    // toLocaleString may render as "∞" or "Infinity" depending on locale/runtime
    expect(result === "∞" || result === "Infinity").toBe(true)
  })

  it("formats -Infinity as a string", () => {
    const result = formatValue(-Infinity)
    expect(result === "-∞" || result === "-Infinity").toBe(true)
  })

  it("formats negative zero", () => {
    const result = formatValue(-0)
    // Number.isInteger(-0) is true; toLocaleString may return "0" or "-0"
    expect(result === "0" || result === "-0").toBe(true)
  })

  it("formats very large numbers (1e15+)", () => {
    const result = formatValue(1e15)
    expect(result.length).toBeGreaterThan(0)
    // Should contain digits representing 1 quadrillion
    expect(result.replace(/[^0-9]/g, "")).toContain("1")
  })

  it("formats negative numbers correctly", () => {
    const result = formatValue(-42)
    expect(result).toContain("-")
    expect(result).toContain("42")
  })

  it("formats negative float correctly", () => {
    const result = formatValue(-3.14, 2)
    expect(result).toContain("-")
    expect(result).toContain("3")
  })
})

// ---------------------------------------------------------------------------
// formatFixed – edge cases
// ---------------------------------------------------------------------------

describe("formatFixed", () => {
  it("formats a finite number with given digits", () => {
    expect(formatFixed(3.14159, 2)).toBe("3.14")
  })

  it("returns N/A for NaN", () => {
    expect(formatFixed(NaN, 2)).toBe("N/A")
  })

  it("returns N/A for Infinity", () => {
    expect(formatFixed(Infinity, 2)).toBe("N/A")
  })

  it("returns N/A for -Infinity", () => {
    expect(formatFixed(-Infinity, 2)).toBe("N/A")
  })

  it("returns N/A for string input", () => {
    expect(formatFixed("hello", 2)).toBe("N/A")
  })

  it("returns N/A for null", () => {
    expect(formatFixed(null, 2)).toBe("N/A")
  })
})

describe("formatNullPct", () => {
  it("returns null when the row count is 0", () => {
    expect(formatNullPct(0, 0)).toBeNull()
    expect(formatNullPct(5, 0)).toBeNull()
  })

  it("formats the null ratio as a 1-dp percentage", () => {
    expect(formatNullPct(0, 200)).toBe("0.0%")
    expect(formatNullPct(50, 200)).toBe("25.0%")
    expect(formatNullPct(200, 200)).toBe("100.0%")
  })
})
