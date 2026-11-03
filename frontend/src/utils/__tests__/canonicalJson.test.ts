import { describe, expect, it } from "vitest"

import { canonicalJson, canonicalValue } from "../canonicalJson"

describe("canonicalJson", () => {
  it("writes every object's keys in sorted order, through nested arrays", () => {
    const a = { b: [{ d: 1, c: [{ f: 2, e: 3 }] }], a: { z: null, y: [1, 2] } }
    const b = { a: { y: [1, 2], z: null }, b: [{ c: [{ e: 3, f: 2 }], d: 1 }] }
    expect(canonicalJson(a)).toBe('{"a":{"y":[1,2],"z":null},"b":[{"c":[{"e":3,"f":2}],"d":1}]}')
    expect(canonicalJson(b)).toBe(canonicalJson(a))
    // Array order is data: it is never sorted.
    expect(canonicalJson([2, 1])).not.toBe(canonicalJson([1, 2]))
  })

  it("keeps JSON's rules for undefined, non-finite numbers and toJSON", () => {
    expect(canonicalJson({ kept: 1, dropped: undefined })).toBe('{"kept":1}')
    expect(canonicalJson([undefined, NaN, Infinity])).toBe("[null,null,null]")
    expect(canonicalJson(undefined)).toBe("null")
    expect(canonicalJson({ at: new Date(0) })).toBe('{"at":"1970-01-01T00:00:00.000Z"}')
  })

  it("refuses a bigint, which JSON cannot hold", () => {
    expect(() => canonicalJson({ big: 1n })).toThrow(TypeError)
  })

  it("gives the value it writes for embedding in a larger structure", () => {
    expect(JSON.stringify(canonicalValue({ b: 1, a: undefined, c: [{ e: 1, d: 2 }] }))).toBe('{"b":1,"c":[{"d":2,"e":1}]}')
  })
})
