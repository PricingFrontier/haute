/**
 * Pure logic tests for banding utility functions.
 *
 * Tests: normaliseBandingFactors, isNumericDtype, inferBandingType
 */
import { describe, it, expect } from "vitest"
import {
  normaliseBandingFactors,
  isNumericDtype,
  inferBandingType,
} from "../../panels/editors/banding/bandingUtils"

// ─── normaliseBandingFactors ─────────────────────────────────────

describe("normaliseBandingFactors", () => {
  it("returns existing factors when present", () => {
    const factors = [{ banding: "continuous", column: "age", outputColumn: "age_band", rules: [] }]
    const result = normaliseBandingFactors({ factors })
    expect(result).toBe(factors)
  })

  it("returns default factor when factors is undefined", () => {
    const result = normaliseBandingFactors({})
    expect(result).toHaveLength(1)
    expect(result[0].banding).toBe("continuous")
    expect(result[0].column).toBe("")
    expect(result[0].outputColumn).toBe("")
    expect(result[0].rules).toEqual([])
    expect(result[0].default).toBeNull()
  })

  it("returns default factor when factors is empty array", () => {
    const result = normaliseBandingFactors({ factors: [] })
    expect(result).toHaveLength(1)
  })

  it("returns default factor when factors is non-array", () => {
    const result = normaliseBandingFactors({ factors: "invalid" })
    expect(result).toHaveLength(1)
  })
})

// ─── isNumericDtype ──────────────────────────────────────────────

describe("isNumericDtype", () => {
  it("recognizes int types", () => {
    expect(isNumericDtype("int32")).toBe(true)
    expect(isNumericDtype("int64")).toBe(true)
    expect(isNumericDtype("Int8")).toBe(true)
  })

  it("recognizes uint types", () => {
    expect(isNumericDtype("uint32")).toBe(true)
    expect(isNumericDtype("UInt16")).toBe(true)
  })

  it("recognizes float types", () => {
    expect(isNumericDtype("float32")).toBe(true)
    expect(isNumericDtype("float64")).toBe(true)
    expect(isNumericDtype("Float64")).toBe(true)
  })

  it("recognizes short Polars type aliases", () => {
    expect(isNumericDtype("f32")).toBe(true)
    expect(isNumericDtype("f64")).toBe(true)
    expect(isNumericDtype("i8")).toBe(true)
    expect(isNumericDtype("i16")).toBe(true)
    expect(isNumericDtype("i32")).toBe(true)
    expect(isNumericDtype("i64")).toBe(true)
    expect(isNumericDtype("u8")).toBe(true)
    expect(isNumericDtype("u16")).toBe(true)
    expect(isNumericDtype("u32")).toBe(true)
    expect(isNumericDtype("u64")).toBe(true)
  })

  it("rejects string types", () => {
    expect(isNumericDtype("str")).toBe(false)
    expect(isNumericDtype("Utf8")).toBe(false)
    expect(isNumericDtype("String")).toBe(false)
  })

  it("rejects boolean types", () => {
    expect(isNumericDtype("bool")).toBe(false)
    expect(isNumericDtype("Boolean")).toBe(false)
  })

  it("rejects date types", () => {
    expect(isNumericDtype("date")).toBe(false)
    expect(isNumericDtype("datetime")).toBe(false)
  })
})

// ─── inferBandingType ────────────────────────────────────────────

describe("inferBandingType", () => {
  const colMap = {
    age: "int64",
    premium: "float64",
    region: "Utf8",
    active: "bool",
    score: "f32",
  }

  it("returns breakpoints for integer column", () => {
    expect(inferBandingType("age", colMap)).toBe("breakpoints")
  })

  it("returns breakpoints for float column", () => {
    expect(inferBandingType("premium", colMap)).toBe("breakpoints")
  })

  it("returns breakpoints for Polars short alias", () => {
    expect(inferBandingType("score", colMap)).toBe("breakpoints")
  })

  it("returns categorical for string column", () => {
    expect(inferBandingType("region", colMap)).toBe("categorical")
  })

  it("returns categorical for boolean column", () => {
    expect(inferBandingType("active", colMap)).toBe("categorical")
  })

  it("returns null for unknown column", () => {
    expect(inferBandingType("nonexistent", colMap)).toBeNull()
  })

  it("returns null for empty colMap", () => {
    expect(inferBandingType("age", {})).toBeNull()
  })
})
