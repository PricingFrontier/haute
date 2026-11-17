import { describe, expect, it } from "vitest"
import { addressKey, childAddress, nodeFrame, rootAddress, sameAddress } from "../nodeAddress"

describe("node addresses", () => {
  it("keys a root node by its id, so stores keyed by node id keep their keys", () => {
    expect(addressKey(rootAddress("policy_rating"))).toBe("policy_rating")
  })

  it("keys a child apart from every root node and from the same child of another occurrence", () => {
    const owner = addressKey(childAddress("pricing", "score"))
    const copy = addressKey(childAddress("pricing_2", "score"))

    expect(owner).not.toBe(copy)
    expect(owner).not.toBe("score")
    // Even a root node named after the child's key text stays distinct.
    expect(owner).not.toBe(addressKey(rootAddress(JSON.stringify(["pricing", "score"]))))
    expect(addressKey(childAddress("a/b", "c"))).not.toBe(addressKey(childAddress("a", "b/c")))
  })

  it("refuses to key a root node id that opens with the child prefix", () => {
    expect(() => addressKey(rootAddress("\u0000score"))).toThrow(/control character/)
  })

  it("compares addresses by occurrence and node", () => {
    expect(sameAddress(childAddress("pricing", "score"), childAddress("pricing", "score"))).toBe(true)
    expect(sameAddress(childAddress("pricing", "score"), rootAddress("score"))).toBe(false)
    expect(nodeFrame(rootAddress("api"))).toEqual({ kind: "node", address: { occurrence: null, node: "api" }, port: null })
  })
})
