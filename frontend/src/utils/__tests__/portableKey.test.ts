import { describe, expect, it } from "vitest"

import { portableKey } from "../portableKey"

describe("portableKey", () => {
  it("preserves case and separates words without executable-language rules", () => {
    expect(portableKey("My node-name!")).toBe("My_node_name")
    expect(portableKey("class")).toBe("class")
    expect(portableKey("return")).toBe("return")
  })

  it("uses JavaScript trimming and a domain-neutral digit prefix", () => {
    expect(portableKey("\ufeff padded \ufeff")).toBe("padded")
    expect(portableKey("2026")).toBe("item_2026")
    expect(portableKey(" @#$ ")).toBe("untitled")
  })

  it("visibly encodes Unicode code points and remains idempotent", () => {
    expect(portableKey("café")).toBe("caf_ue9_")
    expect(portableKey("a😀b")).toBe("a_u1f600_b")
    const once = portableKey("café")
    expect(portableKey(once)).toBe(once)
  })

  it("documents collision behavior that callers must handle", () => {
    expect(portableKey("data lake")).toBe(portableKey("data-lake"))
    expect(portableKey("data!lake")).toBe(portableKey("datalake"))
  })
})
