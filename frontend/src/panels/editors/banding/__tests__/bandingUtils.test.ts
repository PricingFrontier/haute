/**
 * Tests for banding validation utilities:
 * parseRuleInterval, detectOverlaps, detectGaps, validateRule,
 * detectDuplicateCategorical, suggestOutputColumn, breakpointsToRules
 */
import { describe, it, expect } from "vitest"
import {
  parseRuleInterval,
  detectOverlaps,
  detectGaps,
  validateRule,
  detectDuplicateCategorical,
  suggestOutputColumn,
  breakpointsToRules,
  matchesContinuousRule,
} from "../bandingUtils"
import type { ContinuousRule, CategoricalRule, BreakpointRule } from "../../../../types/banding"

// Helper to create a ContinuousRule with defaults
function rule(
  overrides: Partial<ContinuousRule> = {},
): ContinuousRule {
  return { op1: "", val1: "", op2: "", val2: "", assignment: "", ...overrides }
}

// ─── parseRuleInterval ────────────────────────────────────────────

describe("parseRuleInterval", () => {
  it("parses a single upper bound", () => {
    const iv = parseRuleInterval(rule({ op1: "<=", val1: "10" }))
    expect(iv).toEqual({ lower: null, upper: 10, lowerInclusive: false, upperInclusive: true })
  })

  it("parses a single lower bound", () => {
    const iv = parseRuleInterval(rule({ op1: ">", val1: "5" }))
    expect(iv).toEqual({ lower: 5, upper: null, lowerInclusive: false, upperInclusive: false })
  })

  it("parses inclusive lower bound", () => {
    const iv = parseRuleInterval(rule({ op1: ">=", val1: "0" }))
    expect(iv).toEqual({ lower: 0, upper: null, lowerInclusive: true, upperInclusive: false })
  })

  it("parses double bound (range)", () => {
    const iv = parseRuleInterval(rule({ op1: ">", val1: "10", op2: "<=", val2: "20" }))
    expect(iv).toEqual({ lower: 10, upper: 20, lowerInclusive: false, upperInclusive: true })
  })

  it("lets the second condition supply an inclusive lower bound", () => {
    const iv = parseRuleInterval(rule({ op1: "<", val1: "20", op2: ">=", val2: "10" }))
    expect(iv).toEqual({ lower: 10, upper: 20, lowerInclusive: true, upperInclusive: false })
  })

  it("lets the second condition supply an equality bound", () => {
    const iv = parseRuleInterval(rule({ op2: "==", val2: "42" }))
    expect(iv).toEqual({ lower: 42, upper: 42, lowerInclusive: true, upperInclusive: true })
  })

  it("parses equality operator", () => {
    const iv = parseRuleInterval(rule({ op1: "=", val1: "42" }))
    expect(iv).toEqual({ lower: 42, upper: 42, lowerInclusive: true, upperInclusive: true })
  })

  it("parses == operator", () => {
    const iv = parseRuleInterval(rule({ op1: "==", val1: "7" }))
    expect(iv).toEqual({ lower: 7, upper: 7, lowerInclusive: true, upperInclusive: true })
  })

  it("returns null for empty rule", () => {
    expect(parseRuleInterval(rule())).toBeNull()
  })

  it("returns null when val is non-numeric", () => {
    expect(parseRuleInterval(rule({ op1: "<=", val1: "abc" }))).toBeNull()
  })

  it("returns null when op is empty but val is present", () => {
    expect(parseRuleInterval(rule({ val1: "10" }))).toBeNull()
  })

  it("handles negative numbers", () => {
    const iv = parseRuleInterval(rule({ op1: ">=", val1: "-5", op2: "<", val2: "0" }))
    expect(iv).toEqual({ lower: -5, upper: 0, lowerInclusive: true, upperInclusive: false })
  })

  it("handles decimal numbers", () => {
    const iv = parseRuleInterval(rule({ op1: ">", val1: "1.5", op2: "<=", val2: "3.7" }))
    expect(iv).toEqual({ lower: 1.5, upper: 3.7, lowerInclusive: false, upperInclusive: true })
  })
})

// ─── detectOverlaps ──────────────────────────────────────────────

describe("detectOverlaps", () => {
  it("returns empty for non-overlapping ranges", () => {
    const rules = [
      rule({ op1: "<=", val1: "10", assignment: "A" }),
      rule({ op1: ">", val1: "10", op2: "<=", val2: "20", assignment: "B" }),
      rule({ op1: ">", val1: "20", assignment: "C" }),
    ]
    expect(detectOverlaps(rules)).toEqual([])
  })

  it("detects overlapping ranges", () => {
    const rules = [
      rule({ op1: "<=", val1: "15", assignment: "A" }),
      rule({ op1: ">=", val1: "10", op2: "<=", val2: "20", assignment: "B" }),
    ]
    const overlaps = detectOverlaps(rules)
    expect(overlaps).toHaveLength(1)
    expect(overlaps[0].ruleA).toBe(0)
    expect(overlaps[0].ruleB).toBe(1)
  })

  it("does not report adjacent exclusive ranges as overlapping", () => {
    const rules = [
      rule({ op1: "<", val1: "10", assignment: "A" }),
      rule({ op1: ">", val1: "10", assignment: "B" }),
    ]
    expect(detectOverlaps(rules)).toEqual([])
  })

  it("reports adjacent inclusive ranges as overlapping", () => {
    const rules = [
      rule({ op1: "<=", val1: "10", assignment: "A" }),
      rule({ op1: ">=", val1: "10", assignment: "B" }),
    ]
    const overlaps = detectOverlaps(rules)
    expect(overlaps).toHaveLength(1)
  })

  it("detects fully contained range", () => {
    const rules = [
      rule({ op1: ">=", val1: "0", op2: "<=", val2: "100", assignment: "A" }),
      rule({ op1: ">=", val1: "10", op2: "<=", val2: "20", assignment: "B" }),
    ]
    const overlaps = detectOverlaps(rules)
    expect(overlaps).toHaveLength(1)
  })

  it("returns empty for single rule", () => {
    expect(detectOverlaps([rule({ op1: "<=", val1: "10" })])).toEqual([])
  })

  it("returns empty for empty rules", () => {
    expect(detectOverlaps([])).toEqual([])
  })

  it("skips rules with no parseable interval", () => {
    const rules = [
      rule({ op1: "<=", val1: "10", assignment: "A" }),
      rule(), // empty rule
      rule({ op1: ">", val1: "20", assignment: "C" }),
    ]
    expect(detectOverlaps(rules)).toEqual([])
  })
})

// ─── detectGaps ──────────────────────────────────────────────────

describe("detectGaps", () => {
  it("returns empty when ranges are adjacent", () => {
    const rules = [
      rule({ op1: "<=", val1: "10", assignment: "A" }),
      rule({ op1: ">", val1: "10", op2: "<=", val2: "20", assignment: "B" }),
    ]
    // No gap: first rule upper=10 inclusive, second rule lower=10 exclusive
    // But actually there's no gap because 10 is covered by rule A (<=10)
    // and >10 starts from the next value
    expect(detectGaps(rules)).toEqual([])
  })

  it("detects a simple gap", () => {
    const rules = [
      rule({ op1: "<", val1: "10", assignment: "A" }),
      rule({ op1: ">", val1: "20", assignment: "B" }),
    ]
    const gaps = detectGaps(rules)
    expect(gaps).toHaveLength(1)
    expect(gaps[0]).toContain("10")
    expect(gaps[0]).toContain("20")
  })

  it("detects gap at a point (exclusive on both sides)", () => {
    const rules = [
      rule({ op1: "<", val1: "10", assignment: "A" }),
      rule({ op1: ">", val1: "10", assignment: "B" }),
    ]
    const gaps = detectGaps(rules)
    expect(gaps).toHaveLength(1)
    expect(gaps[0]).toContain("10")
  })

  it("returns empty for single rule", () => {
    expect(detectGaps([rule({ op1: "<=", val1: "10" })])).toEqual([])
  })

  it("returns empty for empty rules", () => {
    expect(detectGaps([])).toEqual([])
  })

  it("detects multiple gaps", () => {
    const rules = [
      rule({ op1: "<", val1: "10", assignment: "A" }),
      rule({ op1: ">", val1: "20", op2: "<", val2: "30", assignment: "B" }),
      rule({ op1: ">", val1: "40", assignment: "C" }),
    ]
    const gaps = detectGaps(rules)
    expect(gaps.length).toBeGreaterThanOrEqual(2)
  })

  it("no gap when open-ended upper bound present", () => {
    const rules = [
      rule({ op1: ">", val1: "0", assignment: "A" }), // open-ended upper
      rule({ op1: ">", val1: "10", assignment: "B" }),
    ]
    // First rule has no upper bound, so no gap is reported
    expect(detectGaps(rules)).toEqual([])
  })
})

// ─── validateRule ────────────────────────────────────────────────

describe("validateRule", () => {
  it("returns null for valid single-bound rule", () => {
    expect(validateRule(rule({ op1: "<=", val1: "10" }))).toBeNull()
  })

  it("returns null for valid range rule", () => {
    expect(validateRule(rule({ op1: ">", val1: "10", op2: "<=", val2: "20" }))).toBeNull()
  })

  it("returns null for empty rule", () => {
    expect(validateRule(rule())).toBeNull()
  })

  it("detects contradictory conditions (lower > upper)", () => {
    const err = validateRule(rule({ op1: ">", val1: "50", op2: "<", val2: "30" }))
    expect(err).not.toBeNull()
    expect(err).toContain("Contradictory")
  })

  it("detects equal bounds with exclusive operators", () => {
    const err = validateRule(rule({ op1: ">", val1: "10", op2: "<", val2: "10" }))
    expect(err).not.toBeNull()
    expect(err).toContain("Contradictory")
  })

  it("returns null for equal bounds with both inclusive", () => {
    expect(validateRule(rule({ op1: ">=", val1: "10", op2: "<=", val2: "10" }))).toBeNull()
  })

  it("detects contradictory with inclusive lower, exclusive upper at same value", () => {
    const err = validateRule(rule({ op1: ">=", val1: "10", op2: "<", val2: "10" }))
    expect(err).not.toBeNull()
  })
})

// ─── detectDuplicateCategorical ──────────────────────────────────

describe("detectDuplicateCategorical", () => {
  it("returns empty when no duplicates", () => {
    const rules: CategoricalRule[] = [
      { value: "A", assignment: "Group1" },
      { value: "B", assignment: "Group2" },
    ]
    expect(detectDuplicateCategorical(rules)).toEqual([])
  })

  it("detects single duplicate", () => {
    const rules: CategoricalRule[] = [
      { value: "A", assignment: "Group1" },
      { value: "B", assignment: "Group2" },
      { value: "A", assignment: "Group3" },
    ]
    const dupes = detectDuplicateCategorical(rules)
    expect(dupes).toHaveLength(1)
    expect(dupes[0].value).toBe("A")
    expect(dupes[0].indices).toEqual([0, 2])
  })

  it("detects multiple duplicates", () => {
    const rules: CategoricalRule[] = [
      { value: "A", assignment: "G1" },
      { value: "B", assignment: "G2" },
      { value: "A", assignment: "G3" },
      { value: "B", assignment: "G4" },
    ]
    const dupes = detectDuplicateCategorical(rules)
    expect(dupes).toHaveLength(2)
  })

  it("ignores empty values", () => {
    const rules: CategoricalRule[] = [
      { value: "", assignment: "G1" },
      { value: "", assignment: "G2" },
    ]
    expect(detectDuplicateCategorical(rules)).toEqual([])
  })

  it("returns empty for empty rules", () => {
    expect(detectDuplicateCategorical([])).toEqual([])
  })
})

// ─── suggestOutputColumn ─────────────────────────────────────────

describe("suggestOutputColumn", () => {
  it("appends _band to column name", () => {
    expect(suggestOutputColumn("age")).toBe("age_band")
  })

  it("handles empty string", () => {
    expect(suggestOutputColumn("")).toBe("_band")
  })

  it("returns unchanged if already ends with _band", () => {
    expect(suggestOutputColumn("age_band")).toBe("age_band")
  })

  it("handles column with spaces", () => {
    expect(suggestOutputColumn("age group")).toBe("age group_band")
  })

  it("handles column with underscores", () => {
    expect(suggestOutputColumn("my_column")).toBe("my_column_band")
  })
})

// ─── matchesContinuousRule ───────────────────────────────────────

describe("matchesContinuousRule", () => {
  it("matches value within exclusive range", () => {
    expect(matchesContinuousRule(15, rule({ op1: ">", val1: "10", op2: "<", val2: "20" }))).toBe(true)
  })

  it("rejects value outside exclusive range", () => {
    expect(matchesContinuousRule(10, rule({ op1: ">", val1: "10", op2: "<", val2: "20" }))).toBe(false)
    expect(matchesContinuousRule(20, rule({ op1: ">", val1: "10", op2: "<", val2: "20" }))).toBe(false)
  })

  it("matches value at inclusive bounds", () => {
    expect(matchesContinuousRule(10, rule({ op1: ">=", val1: "10", op2: "<=", val2: "20" }))).toBe(true)
    expect(matchesContinuousRule(20, rule({ op1: ">=", val1: "10", op2: "<=", val2: "20" }))).toBe(true)
  })

  it("matches value with only lower bound", () => {
    expect(matchesContinuousRule(100, rule({ op1: ">", val1: "10" }))).toBe(true)
    expect(matchesContinuousRule(5, rule({ op1: ">", val1: "10" }))).toBe(false)
  })

  it("matches value with only upper bound", () => {
    expect(matchesContinuousRule(5, rule({ op1: "<=", val1: "10" }))).toBe(true)
    expect(matchesContinuousRule(15, rule({ op1: "<=", val1: "10" }))).toBe(false)
  })

  it("matches equality rule", () => {
    expect(matchesContinuousRule(42, rule({ op1: "=", val1: "42" }))).toBe(true)
    expect(matchesContinuousRule(43, rule({ op1: "=", val1: "42" }))).toBe(false)
  })

  it("returns false for empty rule", () => {
    expect(matchesContinuousRule(10, rule())).toBe(false)
  })

  it("handles negative values and bounds", () => {
    expect(matchesContinuousRule(-3, rule({ op1: ">=", val1: "-5", op2: "<", val2: "0" }))).toBe(true)
    expect(matchesContinuousRule(-6, rule({ op1: ">=", val1: "-5", op2: "<", val2: "0" }))).toBe(false)
  })

  it("handles decimal values", () => {
    expect(matchesContinuousRule(2.5, rule({ op1: ">", val1: "1.5", op2: "<=", val2: "3.7" }))).toBe(true)
    expect(matchesContinuousRule(1.5, rule({ op1: ">", val1: "1.5", op2: "<=", val2: "3.7" }))).toBe(false)
  })

  it("returns false for NaN value", () => {
    expect(matchesContinuousRule(NaN, rule({ op1: ">=", val1: "0", op2: "<=", val2: "100" }))).toBe(false)
  })

  it("returns false for Infinity value", () => {
    expect(matchesContinuousRule(Infinity, rule({ op1: ">=", val1: "0", op2: "<=", val2: "100" }))).toBe(false)
    expect(matchesContinuousRule(-Infinity, rule({ op1: ">=", val1: "0", op2: "<=", val2: "100" }))).toBe(false)
  })

  it("returns false for NaN with open-ended rule", () => {
    expect(matchesContinuousRule(NaN, rule({ op1: ">", val1: "0" }))).toBe(false)
  })
})

// ─── breakpointsToRules ──────────────────────────────────────────

describe("breakpointsToRules", () => {
  it("converts basic breakpoints with right-closed", () => {
    const bps: BreakpointRule[] = [
      { boundary: "10", label: "A" },
      { boundary: "20", label: "B" },
      { boundary: "", label: "C" },
    ]
    const rules = breakpointsToRules(bps, true)
    expect(rules).toHaveLength(3)
    // First: <=10
    expect(rules[0]).toEqual({ op1: "<=", val1: "10", op2: "", val2: "", assignment: "A" })
    // Second: >10 and <=20
    expect(rules[1]).toEqual({ op1: ">", val1: "10", op2: "<=", val2: "20", assignment: "B" })
    // Third: >20
    expect(rules[2]).toEqual({ op1: ">", val1: "20", op2: "", val2: "", assignment: "C" })
  })

  it("converts basic breakpoints with left-closed", () => {
    const bps: BreakpointRule[] = [
      { boundary: "10", label: "A" },
      { boundary: "20", label: "B" },
      { boundary: "", label: "C" },
    ]
    const rules = breakpointsToRules(bps, false)
    expect(rules).toHaveLength(3)
    // First: <10
    expect(rules[0]).toEqual({ op1: "<", val1: "10", op2: "", val2: "", assignment: "A" })
    // Second: >=10 and <20
    expect(rules[1]).toEqual({ op1: ">=", val1: "10", op2: "<", val2: "20", assignment: "B" })
    // Third: >=20
    expect(rules[2]).toEqual({ op1: ">=", val1: "20", op2: "", val2: "", assignment: "C" })
  })

  it("returns empty for empty breakpoints", () => {
    expect(breakpointsToRules([], true)).toEqual([])
  })

  it("handles single breakpoint with open-ended", () => {
    const bps: BreakpointRule[] = [
      { boundary: "50", label: "Low" },
      { boundary: "", label: "High" },
    ]
    const rules = breakpointsToRules(bps, true)
    expect(rules).toHaveLength(2)
    expect(rules[0]).toEqual({ op1: "<=", val1: "50", op2: "", val2: "", assignment: "Low" })
    expect(rules[1]).toEqual({ op1: ">", val1: "50", op2: "", val2: "", assignment: "High" })
  })

  it("handles single breakpoint without open-ended", () => {
    const bps: BreakpointRule[] = [{ boundary: "50", label: "Low" }]
    const rules = breakpointsToRules(bps, true)
    expect(rules).toHaveLength(1)
    expect(rules[0]).toEqual({ op1: "<=", val1: "50", op2: "", val2: "", assignment: "Low" })
  })

  it("sorts breakpoints by boundary value", () => {
    const bps: BreakpointRule[] = [
      { boundary: "30", label: "B" },
      { boundary: "10", label: "A" },
      { boundary: "", label: "C" },
    ]
    const rules = breakpointsToRules(bps, true)
    expect(rules[0].assignment).toBe("A")  // 10 first
    expect(rules[1].assignment).toBe("B")  // 30 second
    expect(rules[2].assignment).toBe("C")  // open-ended last
  })

  it("ignores breakpoints with non-numeric boundary", () => {
    const bps: BreakpointRule[] = [
      { boundary: "abc", label: "Invalid" },
      { boundary: "10", label: "Valid" },
    ]
    const rules = breakpointsToRules(bps, true)
    expect(rules).toHaveLength(1)
    expect(rules[0].assignment).toBe("Valid")
  })

  it("ignores breakpoints with Infinity boundary", () => {
    const bps: BreakpointRule[] = [
      { boundary: "Infinity", label: "Inf" },
      { boundary: "-Infinity", label: "NegInf" },
      { boundary: "10", label: "Valid" },
    ]
    const rules = breakpointsToRules(bps, true)
    expect(rules).toHaveLength(1)
    expect(rules[0].assignment).toBe("Valid")
  })
})
