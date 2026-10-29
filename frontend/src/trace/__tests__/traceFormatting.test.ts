import { describe, expect, it } from "vitest"

import {
  displayOperators,
  formatDisplayExpression,
  formatResultValueFull,
  formatSmartValue,
} from "../traceFormatting"

describe("traceFormatting", () => {
  it("renders Haute non-finite JSON sentinels distinctly from null", () => {
    const nan = { __haute_type__: "non_finite_float", value: "nan" }
    const posInf = { __haute_type__: "non_finite_float", value: "inf" }
    const negInf = { __haute_type__: "non_finite_float", value: "-inf" }

    expect(formatSmartValue(nan)).toBe("NaN")
    expect(formatSmartValue(posInf)).toBe("Infinity")
    expect(formatSmartValue(negInf)).toBe("-Infinity")
    expect(formatSmartValue(null)).toBe("-")
    expect(formatResultValueFull(nan)).toBe("NaN")
  })
})

describe("displayOperators", () => {
  it("shows arithmetic operators as glyphs", () => {
    expect(displayOperators("base * factor / 12")).toBe("base × factor ÷ 12")
    expect(displayOperators("(a + b) * -2")).toBe("(a + b) × -2")
  })

  it("leaves quoted text as the code wrote it", () => {
    expect(displayOperators("replace('a/b', 'a*b')")).toBe("replace('a/b', 'a*b')")
    expect(displayOperators('concat_str("A", "x*y") * 2')).toBe('concat_str("A", "x*y") × 2')
    expect(displayOperators('"say \\"a/b\\"" / 2')).toBe('"say \\"a/b\\"" ÷ 2')
  })

  it("keeps a power, a floor division and an unpacking star as written", () => {
    expect(displayOperators("a ** 2")).toBe("a ** 2")
    expect(displayOperators("a // 2")).toBe("a // 2")
    expect(displayOperators("sum_horizontal(*cols)")).toBe("sum_horizontal(*cols)")
  })

  it("is what the shared formula formatter shows", () => {
    expect(formatDisplayExpression("replace('a/b', 'a*b') * rate").text).toBe(
      "replace('a/b', 'a*b') × rate",
    )
  })
})
