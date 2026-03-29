import { describe, it, expect } from "vitest"
import { configField, safeParseFloat, safeParseInt } from "../../utils/configField"

describe("configField", () => {
  // ── Present values are returned ────────────────────────────────

  it("returns value when key exists", () => {
    const config: Record<string, unknown> = { name: "Alice" }
    expect(configField(config, "name", "fallback")).toBe("Alice")
  })

  // ── Nullish values fall back ───────────────────────────────────

  it("returns fallback when key is missing", () => {
    const config: Record<string, unknown> = {}
    expect(configField(config, "missing", "default")).toBe("default")
  })

  it("returns fallback when value is null", () => {
    const config: Record<string, unknown> = { key: null }
    expect(configField(config, "key", "default")).toBe("default")
  })

  it("returns fallback when value is undefined", () => {
    const config: Record<string, unknown> = { key: undefined }
    expect(configField(config, "key", "default")).toBe("default")
  })

  // ── Falsy-but-not-nullish values are preserved ─────────────────

  it("returns empty string (not fallback) — nullish coalescing doesn't trigger on ''", () => {
    const config: Record<string, unknown> = { name: "" }
    expect(configField(config, "name", "fallback")).toBe("")
  })

  it("returns 0 (not fallback) — nullish coalescing doesn't trigger on 0", () => {
    const config: Record<string, unknown> = { count: 0 }
    expect(configField(config, "count", 42)).toBe(0)
  })

  it("returns false (not fallback) — nullish coalescing doesn't trigger on false", () => {
    const config: Record<string, unknown> = { enabled: false }
    expect(configField(config, "enabled", true)).toBe(false)
  })
})

describe("safeParseFloat", () => {
  it("parses a valid float string", () => {
    expect(safeParseFloat("3.14", 0)).toBe(3.14)
  })

  it("parses a valid integer string as float", () => {
    expect(safeParseFloat("42", 0)).toBe(42)
  })

  it("returns fallback when result is NaN", () => {
    expect(safeParseFloat("not-a-number", 99.9)).toBe(99.9)
  })

  it("returns fallback for empty string", () => {
    expect(safeParseFloat("", 5.5)).toBe(5.5)
  })

  it("parses negative floats", () => {
    expect(safeParseFloat("-2.5", 0)).toBe(-2.5)
  })
})

describe("safeParseInt", () => {
  it("parses a valid integer string", () => {
    expect(safeParseInt("42", 0)).toBe(42)
  })

  it("truncates a float string to integer", () => {
    expect(safeParseInt("3.99", 0)).toBe(3)
  })

  it("returns fallback when result is NaN", () => {
    expect(safeParseInt("not-a-number", 10)).toBe(10)
  })

  it("returns fallback for empty string", () => {
    expect(safeParseInt("", 7)).toBe(7)
  })

  it("parses negative integers", () => {
    expect(safeParseInt("-15", 0)).toBe(-15)
  })
})
