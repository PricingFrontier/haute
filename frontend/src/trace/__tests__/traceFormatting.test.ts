import { describe, expect, it } from "vitest"

import { formatResultValueFull, formatSmartValue } from "../traceFormatting"

describe("traceFormatting", () => {
  it("renders Haute non-finite JSON sentinels distinctly from null", () => {
    const nan = { __haute_type__: "non_finite_float", value: "nan" }
    const posInf = { __haute_type__: "non_finite_float", value: "inf" }
    const negInf = { __haute_type__: "non_finite_float", value: "-inf" }

    expect(formatSmartValue(nan)).toBe("NaN")
    expect(formatSmartValue(posInf)).toBe("Infinity")
    expect(formatSmartValue(negInf)).toBe("-Infinity")
    expect(formatSmartValue(null)).toBe("null")
    expect(formatResultValueFull(nan)).toBe("NaN")
  })
})
