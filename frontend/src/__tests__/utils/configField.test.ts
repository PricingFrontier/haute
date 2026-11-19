import { describe, it, expect } from "vitest"
import { configField } from "../../utils/configField"

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

  it("returns empty string (not fallback) - nullish coalescing doesn't trigger on ''", () => {
    const config: Record<string, unknown> = { name: "" }
    expect(configField(config, "name", "fallback")).toBe("")
  })

  it("returns 0 (not fallback) - nullish coalescing doesn't trigger on 0", () => {
    const config: Record<string, unknown> = { count: 0 }
    expect(configField(config, "count", 42)).toBe(0)
  })

  it("returns false (not fallback) - nullish coalescing doesn't trigger on false", () => {
    const config: Record<string, unknown> = { enabled: false }
    expect(configField(config, "enabled", true)).toBe(false)
  })
})
