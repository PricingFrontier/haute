import { describe, it, expect } from "vitest"
import {
  formatTraceValue,
  formatExpression,
  formatCalculation,
  formatSchemaSummary,
} from "../formatTrace"

// ---------------------------------------------------------------------------
// formatTraceValue
// ---------------------------------------------------------------------------

describe("formatTraceValue", () => {
  // --- Floats ---
  it("formats a simple float", () => {
    expect(formatTraceValue(42.5)).toBe("42.5")
  })

  it("smart-rounds IEEE 754 artifacts", () => {
    // 0.1 + 0.2 = 0.30000000000000004 in JS
    expect(formatTraceValue(0.30000000000000004)).toBe("0.3")
  })

  it("preserves meaningful precision", () => {
    expect(formatTraceValue(3.14159)).toBe("3.14159")
  })

  it("formats float with trailing zeros trimmed", () => {
    expect(formatTraceValue(10.0)).toBe("10")
  })

  // --- Integers ---
  it("formats an integer without decimal point", () => {
    expect(formatTraceValue(42)).toBe("42")
  })

  it("formats zero", () => {
    expect(formatTraceValue(0)).toBe("0")
  })

  it("formats negative integer", () => {
    expect(formatTraceValue(-7)).toBe("-7")
  })

  // --- NULL / NaN / Infinity ---
  it("formats null as null", () => {
    expect(formatTraceValue(null)).toBe("null")
  })

  it("formats undefined as null", () => {
    expect(formatTraceValue(undefined)).toBe("null")
  })

  it("formats NaN", () => {
    expect(formatTraceValue(NaN)).toBe("NaN")
  })

  it("formats Infinity", () => {
    expect(formatTraceValue(Infinity)).toBe("\u221e")
  })

  it("formats -Infinity", () => {
    expect(formatTraceValue(-Infinity)).toBe("-\u221e")
  })

  // --- Strings ---
  it("formats a string with quotes", () => {
    expect(formatTraceValue("hello")).toBe('"hello"')
  })

  it("formats an empty string with quotes", () => {
    expect(formatTraceValue("")).toBe('""')
  })

  it("formats a date-like string without extra quotes", () => {
    expect(formatTraceValue("2026-01-15")).toBe("2026-01-15")
  })

  // --- Booleans ---
  it("formats true", () => {
    expect(formatTraceValue(true)).toBe("true")
  })

  it("formats false", () => {
    expect(formatTraceValue(false)).toBe("false")
  })

  // --- Large / small numbers ---
  it("formats a very large number with grouping or scientific notation", () => {
    const result = formatTraceValue(1e15)
    // Accept either grouped "1,000,000,000,000,000" or scientific "1e15"
    expect(
      result === "1,000,000,000,000,000" || result === "1e15" || result === "1e+15",
    ).toBe(true)
  })

  it("formats a very small number", () => {
    expect(formatTraceValue(0.000001)).toBe("0.000001")
  })

  it("formats negative float", () => {
    expect(formatTraceValue(-42.5)).toBe("-42.5")
  })

  // --- Arrays / objects fallback ---
  it("formats an array as JSON", () => {
    expect(formatTraceValue([1, 2, 3])).toBe("[1,2,3]")
  })

  it("formats an object as JSON", () => {
    expect(formatTraceValue({ a: 1 })).toBe('{"a":1}')
  })

  it("formats a nested object as JSON", () => {
    const val = { foo: [1, 2], bar: { x: true } }
    expect(formatTraceValue(val)).toBe(JSON.stringify(val))
  })
})

// ---------------------------------------------------------------------------
// formatExpression
// ---------------------------------------------------------------------------

describe("formatExpression", () => {
  // --- Arithmetic operators ---
  it("replaces * with multiplication sign", () => {
    expect(formatExpression("a * b")).toBe("a \u00d7 b")
  })

  it("replaces / with division sign", () => {
    expect(formatExpression("a / b")).toBe("a \u00f7 b")
  })

  it("preserves + operator", () => {
    expect(formatExpression("a + b")).toBe("a + b")
  })

  it("preserves - operator", () => {
    expect(formatExpression("a - b")).toBe("a - b")
  })

  it("handles chained arithmetic", () => {
    expect(formatExpression("a * b + c / d")).toBe("a \u00d7 b + c \u00f7 d")
  })

  // --- Parentheses ---
  it("preserves parentheses", () => {
    expect(formatExpression("(a + b) * c")).toBe("(a + b) \u00d7 c")
  })

  it("preserves nested parentheses", () => {
    expect(formatExpression("((a + b) * c) / d")).toBe("((a + b) \u00d7 c) \u00f7 d")
  })

  // --- Column names & constants ---
  it("preserves column names", () => {
    expect(formatExpression("premium_base * rate")).toBe("premium_base \u00d7 rate")
  })

  it("preserves numeric constants", () => {
    expect(formatExpression("premium * 0.7")).toBe("premium \u00d7 0.7")
  })

  // --- when/then/otherwise ---
  it("formats when/then/otherwise as decision tree text", () => {
    const expr = "when age > 25 then premium * 1.2 otherwise premium"
    const result = formatExpression(expr)
    expect(result).toContain("when")
    expect(result).toContain("then")
    expect(result).toContain("otherwise")
  })

  // --- Horizontal functions ---
  it("preserves horizontal functions like MAX(a, b)", () => {
    const result = formatExpression("MAX(a, b)")
    expect(result).toContain("MAX")
    expect(result).toContain("a")
    expect(result).toContain("b")
  })

  it("preserves MIN function call", () => {
    const result = formatExpression("MIN(x, y, z)")
    expect(result).toContain("MIN")
  })

  // --- Truncation ---
  it("truncates long expressions with ellipsis at default length", () => {
    const longExpr = "a * b + c * d + e * f + g * h + i * j + k * l + m * n + o * p + q * r + s * t"
    const result = formatExpression(longExpr)
    expect(result.endsWith("\u2026")).toBe(true)
    expect(result.length).toBeLessThanOrEqual(81) // 80 + ellipsis
  })

  it("truncates at a configurable length", () => {
    const expr = "a * b + c * d + e * f"
    const result = formatExpression(expr, 10)
    expect(result.endsWith("\u2026")).toBe(true)
    expect(result.length).toBeLessThanOrEqual(11) // 10 + ellipsis
  })

  it("does not truncate short expressions", () => {
    const result = formatExpression("a + b")
    expect(result).toBe("a + b")
    expect(result.endsWith("\u2026")).toBe(false)
  })

  // --- Edge cases ---
  it("returns empty string for null input", () => {
    expect(formatExpression(null as unknown as string)).toBe("")
  })

  it("returns empty string for undefined input", () => {
    expect(formatExpression(undefined as unknown as string)).toBe("")
  })

  it("returns empty string for empty string input", () => {
    expect(formatExpression("")).toBe("")
  })
})

// ---------------------------------------------------------------------------
// formatCalculation
// ---------------------------------------------------------------------------

describe("formatCalculation", () => {
  it("formats a simple multiplication", () => {
    const result = formatCalculation({
      expression: "premium * 0.7",
      values: { premium: 208 },
      result: 145.6,
    })
    expect(result).toBe("208 \u00d7 0.7 = 145.6")
  })

  it("formats multiple inputs", () => {
    const result = formatCalculation({
      expression: "a * b * c",
      values: { a: 1, b: 2, c: 3 },
      result: 6,
    })
    expect(result).toBe("1 \u00d7 2 \u00d7 3 = 6")
  })

  it("formats addition", () => {
    const result = formatCalculation({
      expression: "x + y",
      values: { x: 10, y: 20 },
      result: 30,
    })
    expect(result).toBe("10 + 20 = 30")
  })

  it("formats division", () => {
    const result = formatCalculation({
      expression: "total / count",
      values: { total: 100, count: 4 },
      result: 25,
    })
    expect(result).toBe("100 \u00f7 4 = 25")
  })

  it("shows null when an input value is null", () => {
    const result = formatCalculation({
      expression: "premium * 0.7",
      values: { premium: null },
      result: null,
    })
    expect(result).toContain("null")
    expect(result).toContain("0.7")
    expect(result).toMatch(/= null$/)
  })

  it("shows column name when value is missing from values map", () => {
    const result = formatCalculation({
      expression: "premium * rate",
      values: { premium: 208 },
      result: 145.6,
    })
    // "rate" not in values, so it should appear as the column name
    expect(result).toContain("208")
    expect(result).toContain("rate")
    expect(result).toContain("145.6")
  })

  it("shows only result when no expression (opaque calculation)", () => {
    const result = formatCalculation({
      expression: null,
      values: {},
      result: 42.5,
    })
    expect(result).toBe("= 42.5")
  })

  it("shows only result when expression is empty string", () => {
    const result = formatCalculation({
      expression: "",
      values: {},
      result: 99,
    })
    expect(result).toBe("= 99")
  })

  it("formats result that is zero", () => {
    const result = formatCalculation({
      expression: "a * b",
      values: { a: 0, b: 5 },
      result: 0,
    })
    expect(result).toBe("0 \u00d7 5 = 0")
  })

  it("formats with boolean result", () => {
    const result = formatCalculation({
      expression: null,
      values: {},
      result: true,
    })
    expect(result).toBe("= true")
  })

  it("formats parenthesized sub-expressions", () => {
    const result = formatCalculation({
      expression: "(a + b) * c",
      values: { a: 2, b: 3, c: 4 },
      result: 20,
    })
    expect(result).toContain("(2 + 3)")
    expect(result).toContain("4")
    expect(result).toContain("= 20")
  })
})

// ---------------------------------------------------------------------------
// formatSchemaSummary
// ---------------------------------------------------------------------------

describe("formatSchemaSummary", () => {
  it("formats a mix of added, modified, and passed", () => {
    const result = formatSchemaSummary({
      added: 2,
      modified: 1,
      removed: 0,
      passed: 5,
    })
    expect(result).toContain("2 added")
    expect(result).toContain("1 modified")
    expect(result).toContain("5 passed through")
    expect(result).not.toContain("removed")
  })

  it("returns 'no changes' when all counts are zero", () => {
    const result = formatSchemaSummary({
      added: 0,
      modified: 0,
      removed: 0,
      passed: 0,
    })
    expect(result).toBe("no changes")
  })

  it("formats only added", () => {
    const result = formatSchemaSummary({
      added: 3,
      modified: 0,
      removed: 0,
      passed: 0,
    })
    expect(result).toBe("3 added")
  })

  it("formats only passed", () => {
    const result = formatSchemaSummary({
      added: 0,
      modified: 0,
      removed: 0,
      passed: 8,
    })
    expect(result).toBe("8 passed through")
  })

  it("includes removed count when present", () => {
    const result = formatSchemaSummary({
      added: 1,
      modified: 0,
      removed: 1,
      passed: 3,
    })
    expect(result).toContain("1 removed")
    expect(result).toContain("1 added")
    expect(result).toContain("3 passed through")
  })

  it("formats only modified", () => {
    const result = formatSchemaSummary({
      added: 0,
      modified: 4,
      removed: 0,
      passed: 0,
    })
    expect(result).toBe("4 modified")
  })

  it("formats all categories present", () => {
    const result = formatSchemaSummary({
      added: 1,
      modified: 2,
      removed: 3,
      passed: 4,
    })
    expect(result).toContain("1 added")
    expect(result).toContain("2 modified")
    expect(result).toContain("3 removed")
    expect(result).toContain("4 passed through")
  })

  it("formats only removed", () => {
    const result = formatSchemaSummary({
      added: 0,
      modified: 0,
      removed: 2,
      passed: 0,
    })
    expect(result).toBe("2 removed")
  })
})

// ---------------------------------------------------------------------------
// formatTraceValue – additional edge cases
// ---------------------------------------------------------------------------

describe("formatTraceValue edge cases", () => {
  it("formats negative zero", () => {
    // -0 is an integer; toLocaleString may return "0" or "-0"
    const result = formatTraceValue(-0)
    expect(result === "0" || result === "-0").toBe(true)
  })
})

// ---------------------------------------------------------------------------
// formatExpression – boundary truncation
// ---------------------------------------------------------------------------

describe("formatExpression boundary truncation", () => {
  it("does not truncate expression of exactly 60 chars", () => {
    const expr = "a".repeat(60)
    const result = formatExpression(expr)
    expect(result).toBe(expr)
    expect(result.length).toBe(60)
  })

  it("truncates expression of 61 chars to 60 + ellipsis", () => {
    const expr = "a".repeat(61)
    const result = formatExpression(expr)
    expect(result.length).toBe(61)
    expect(result).toBe("a".repeat(60) + "\u2026")
  })

  it("does not truncate expression shorter than 60 chars", () => {
    const expr = "a".repeat(59)
    const result = formatExpression(expr)
    expect(result).toBe(expr)
  })
})

// ---------------------------------------------------------------------------
// formatCalculation – additional edge cases
// ---------------------------------------------------------------------------

describe("formatCalculation edge cases", () => {
  it("formats negative numbers in values", () => {
    const result = formatCalculation({
      expression: "a + b",
      values: { a: -10, b: 5 },
      result: -5,
    })
    expect(result).toBe("-10 + 5 = -5")
  })

  it("formats NaN result", () => {
    const result = formatCalculation({
      expression: "a / b",
      values: { a: 0, b: 0 },
      result: NaN,
    })
    expect(result).toContain("= NaN")
  })

  it("formats Infinity result", () => {
    const result = formatCalculation({
      expression: "a / b",
      values: { a: 1, b: 0 },
      result: Infinity,
    })
    expect(result).toContain("= \u221e")
  })

  it("formats negative values with multiplication", () => {
    const result = formatCalculation({
      expression: "price * quantity",
      values: { price: -5.5, quantity: 3 },
      result: -16.5,
    })
    expect(result).toContain("-5.5")
    expect(result).toContain("3")
    expect(result).toContain("-16.5")
  })
})
