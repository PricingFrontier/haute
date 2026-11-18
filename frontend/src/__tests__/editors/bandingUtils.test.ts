/**
 * Pure logic tests for banding utility functions.
 *
 * Tests: normaliseBandingFactors, isNumericDtype, isTemporalDtype, inferBandingType
 */
import { describe, it, expect } from "vitest"
import {
  normaliseBandingFactors,
  isNumericDtype,
  inferBandingType,
} from "../../panels/editors/banding/bandingUtils"
import { isTemporalDtype } from "../../utils/polarsDtypes"

// ─── normaliseBandingFactors ─────────────────────────────────────

describe("normaliseBandingFactors", () => {
  it("returns existing factors when present", () => {
    const factors = [{ banding: "breakpoints", column: "age", output_column: "age_band", rules: [] }]
    const result = normaliseBandingFactors({ factors })
    expect(result).toBe(factors)
  })

  it("returns one empty Numeric (breakpoints) factor when factors is undefined", () => {
    const result = normaliseBandingFactors({})
    expect(result).toHaveLength(1)
    expect(result[0].banding).toBe("breakpoints")
    expect(result[0].column).toBe("")
    expect(result[0].output_column).toBe("")
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
  // The types a preview sends, as Polars renders them; every kind is in columnKinds.test.ts.
  it.each(["Int8", "Int64", "Int128", "UInt16", "UInt128", "Float16", "Float32", "Float64", "Decimal(precision=10, scale=2)"])(
    "recognizes %s",
    (dtype) => {
      expect(isNumericDtype(dtype)).toBe(true)
    },
  )

  it.each(["String", "Utf8", "Boolean", "Date", "Datetime(time_unit='us', time_zone=None)", "Null", "List(Int64)"])(
    "rejects %s",
    (dtype) => {
      expect(isNumericDtype(dtype)).toBe(false)
    },
  )

  it("rejects text that is not a Polars type's name", () => {
    expect(isNumericDtype("f64")).toBe(false)
    expect(isNumericDtype("int64")).toBe(false)
  })
})

// ─── isTemporalDtype ─────────────────────────────────────────────

describe("isTemporalDtype", () => {
  it.each(["Date", "Datetime(time_unit='us', time_zone=None)", "Datetime(time_unit='ns', time_zone='Europe/London')"])(
    "recognizes %s",
    (dtype) => {
      expect(isTemporalDtype(dtype)).toBe(true)
    },
  )

  it.each(["Duration(time_unit='us')", "Time", "String", "Int64", "Float64", "Boolean"])("rejects %s", (dtype) => {
    expect(isTemporalDtype(dtype)).toBe(false)
  })
})

// ─── inferBandingType ────────────────────────────────────────────

describe("inferBandingType", () => {
  const colMap = {
    age: "Int64",
    premium: "Float64",
    region: "String",
    active: "Boolean",
    score: "Float16",
  }

  it("returns breakpoints for integer column", () => {
    expect(inferBandingType("age", colMap)).toBe("breakpoints")
  })

  it("returns breakpoints for float column", () => {
    expect(inferBandingType("premium", colMap)).toBe("breakpoints")
  })

  it("returns breakpoints for a half-precision float column", () => {
    expect(inferBandingType("score", colMap)).toBe("breakpoints")
  })

  it("returns categorical for string column", () => {
    expect(inferBandingType("region", colMap)).toBe("categorical")
  })

  it("returns breakpoints for Date and Datetime columns, and categorical for a Duration", () => {
    const dates = {
      start_date: "Date",
      quoted_at: "Datetime(time_unit='ns', time_zone='Europe/London')",
      term: "Duration(time_unit='us')",
    }
    expect(inferBandingType("start_date", dates)).toBe("breakpoints")
    expect(inferBandingType("quoted_at", dates)).toBe("breakpoints")
    expect(inferBandingType("term", dates)).toBe("categorical")
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
