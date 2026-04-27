import { describe, expect, it } from "vitest"
import {
  columnFingerprint,
  columnsEqualByFingerprint,
} from "../columnFingerprint"
import type { ColumnFingerprintInput } from "../columnFingerprint"

const baseColumns: ColumnFingerprintInput = [
  { name: "age", dtype: "i64" },
  { name: "premium", dtype: "f64" },
  { name: "postcode", dtype: "str" },
]

describe("columnFingerprint", () => {
  it("returns the same fingerprint for equivalent column lists", () => {
    const equivalent: ColumnFingerprintInput = [
      { name: "age", dtype: "i64" },
      { name: "premium", dtype: "f64" },
      { name: "postcode", dtype: "str" },
    ]

    expect(columnFingerprint(baseColumns)).toBe(columnFingerprint(equivalent))
    expect(columnsEqualByFingerprint(baseColumns, equivalent)).toBe(true)
  })

  it("changes when order, name, or dtype changes", () => {
    const reordered: ColumnFingerprintInput = [
      { name: "premium", dtype: "f64" },
      { name: "age", dtype: "i64" },
      { name: "postcode", dtype: "str" },
    ]
    const renamed: ColumnFingerprintInput = [
      { name: "age_years", dtype: "i64" },
      { name: "premium", dtype: "f64" },
      { name: "postcode", dtype: "str" },
    ]
    const retyped: ColumnFingerprintInput = [
      { name: "age", dtype: "f64" },
      { name: "premium", dtype: "f64" },
      { name: "postcode", dtype: "str" },
    ]

    const baseFingerprint = columnFingerprint(baseColumns)
    expect(columnFingerprint(reordered)).not.toBe(baseFingerprint)
    expect(columnFingerprint(renamed)).not.toBe(baseFingerprint)
    expect(columnFingerprint(retyped)).not.toBe(baseFingerprint)
  })

  it("keeps undefined, empty, and non-empty column lists distinct", () => {
    expect(columnFingerprint(undefined)).not.toBe(columnFingerprint([]))
    expect(columnFingerprint(undefined)).not.toBe(columnFingerprint(baseColumns))
    expect(columnFingerprint([])).not.toBe(columnFingerprint(baseColumns))
  })

  it("is collision-safe for separator-like characters in names and dtypes", () => {
    expect(
      columnFingerprint([{ name: "a|b", dtype: "x" }]),
    ).not.toBe(columnFingerprint([{ name: "a", dtype: "b|x" }]))
    expect(
      columnFingerprint([{ name: "a,b", dtype: "x" }]),
    ).not.toBe(
      columnFingerprint([
        { name: "a", dtype: "x" },
        { name: "b", dtype: "x" },
      ]),
    )
  })
})
