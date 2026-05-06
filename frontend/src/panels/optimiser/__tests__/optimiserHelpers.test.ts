import { describe, expect, it } from "vitest"
import { isConstraintMet } from "../optimiserHelpers"

describe("isConstraintMet", () => {
  it("evaluates absolute sum constraints with min and max", () => {
    expect(isConstraintMet("min", 0.8, 1200, 1000)).toBe(true)
    expect(isConstraintMet("min", 1.2, 800, 1000)).toBe(false)
    expect(isConstraintMet("max", 1.2, 800, 1000)).toBe(true)
    expect(isConstraintMet("max", 0.8, 1200, 1000)).toBe(false)
  })

  it("does not silently pass unknown threshold keys", () => {
    expect(isConstraintMet("min_pct", 1.1, 1100, 0.9)).toBe(false)
    expect(isConstraintMet("max_pct", 0.9, 900, 1.1)).toBe(false)
    expect(isConstraintMet("min_abs", 1.0, 1000, 900)).toBe(false)
    expect(isConstraintMet("", 1.0, 1000, 900)).toBe(false)
  })
})
