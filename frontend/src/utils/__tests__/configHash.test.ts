import { describe, expect, it } from "vitest"

import { hashConfig } from "../configHash"

describe("hashConfig", () => {
  it("returns the same hash for the same config", () => {
    const config = { solver: "glpk", tolerance: 0.01 }
    expect(hashConfig(config)).toBe(hashConfig({ ...config }))
  })

  it("returns different hashes for different configs", () => {
    const a = { solver: "glpk", tolerance: 0.01 }
    const b = { solver: "glpk", tolerance: 0.02 }
    expect(hashConfig(a)).not.toBe(hashConfig(b))
  })

  it("hashes every key of the config, editor state having none in it", () => {
    const base = { solver: "glpk", tolerance: 0.01 }
    expect(hashConfig({ ...base, _columns: ["x"] })).not.toBe(hashConfig(base))
  })

  it("keeps reserved key spellings semantic below the root config", () => {
    expect(hashConfig({
      nested: { _columns: ["premium"] },
    })).not.toBe(hashConfig({
      nested: { _columns: ["discount"] },
    }))
  })

  it("uses ordinary JSON semantics for undefined and non-finite values", () => {
    expect(hashConfig({ optional: undefined })).toBe(hashConfig({}))
    expect(hashConfig({ value: Number.NaN })).toBe(hashConfig({ value: null }))
    expect(hashConfig({
      values: [undefined, Number.POSITIVE_INFINITY],
    })).toBe(hashConfig({
      values: [null, null],
    }))
  })

  it("accepts nested values with a JSON toJSON contract", () => {
    expect(hashConfig({
      value: { toJSON: () => ({ b: 2, a: 1 }) },
    })).toBe(hashConfig({
      value: { a: 1, b: 2 },
    }))
  })

  it("still rejects genuinely cyclic configuration", () => {
    const cyclic: Record<string, unknown> = {}
    cyclic.self = cyclic

    expect(() => hashConfig(cyclic)).toThrow()
  })

  it("normalizes nested objects inside arrays when hashing", () => {
    const first = { constraints: [{ name: "premium", bounds: { min: 0, max: 100 } }] }
    const second = { constraints: [{ bounds: { max: 100, min: 0 }, name: "premium" }] }

    expect(hashConfig(first)).toBe(hashConfig(second))
  })

  it("returns a non-empty string", () => {
    const hash = hashConfig({ a: 1 })
    expect(hash.length).toBeGreaterThan(0)
  })

  it("uses the same identity for different object key orders", () => {
    const a = { x: 1, y: 2 }
    const b = { y: 2, x: 1 }

    expect(hashConfig(a)).toBe(hashConfig(b))
  })

  it("preserves array order in the identity", () => {
    expect(hashConfig({ columns: ["premium", "discount"] })).not.toBe(
      hashConfig({ columns: ["discount", "premium"] }),
    )
  })

  it("does not collide for a known DJB2 collision", () => {
    expect(hashConfig({ value: "10ry7jgv0xesb" })).not.toBe(
      hashConfig({ value: "n2mwwma6ztkt" }),
    )
  })
})
