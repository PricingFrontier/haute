import { describe, it, expect } from "vitest"
import {
  deriveInputRows,
  resolveBindingAlias,
  type InputBindingSource,
} from "../inputBindingSelection"

const src = (over: Partial<InputBindingSource> = {}): InputBindingSource => ({
  edgeId: "e1",
  sourceNodeId: "policies",
  sourceLabel: "policies",
  varName: "policies",
  ...over,
})

describe("deriveInputRows", () => {
  it("derives the default binding name from the upstream var when unaliased", () => {
    const [row] = deriveInputRows([src()])
    expect(row.incomingName).toBe("policies")
    expect(row.bindingName).toBe("policies")
    expect(row.aliased).toBe(false)
    expect(row.incomingOrder).toBe(1)
    expect(row.edgeId).toBe("e1")
  })

  it("prefers the alias as the binding name and flags it aliased", () => {
    const [row] = deriveInputRows([src({ inputAlias: "claims_2024", varName: "claims" })])
    expect(row.incomingName).toBe("claims")
    expect(row.bindingName).toBe("claims_2024")
    expect(row.aliased).toBe(true)
  })

  it("treats a blank/whitespace alias as no override", () => {
    const [row] = deriveInputRows([src({ inputAlias: "   " })])
    expect(row.bindingName).toBe("policies")
    expect(row.aliased).toBe(false)
  })

  it("numbers incoming order positionally and preserves edge identity", () => {
    const rows = deriveInputRows([
      src({ edgeId: "e1", varName: "policies" }),
      src({ edgeId: "e2", sourceNodeId: "claims", sourceLabel: "claims", varName: "claims" }),
    ])
    expect(rows.map((r) => r.incomingOrder)).toEqual([1, 2])
    expect(rows.map((r) => r.edgeId)).toEqual(["e1", "e2"])
  })
})

describe("resolveBindingAlias", () => {
  it("returns null for a blank value (cleared override)", () => {
    expect(resolveBindingAlias("", "policies")).toBeNull()
    expect(resolveBindingAlias("   ", "policies")).toBeNull()
  })

  it("returns null when the value equals the default incoming name", () => {
    expect(resolveBindingAlias("policies", "policies")).toBeNull()
  })

  it("returns the sanitised alias when it differs from the default", () => {
    expect(resolveBindingAlias("claims_2024", "claims")).toBe("claims_2024")
  })

  it("sanitises a non-identifier value to a valid parameter name", () => {
    expect(resolveBindingAlias("my alias", "policies")).toBe("my_alias")
    expect(resolveBindingAlias("rate-table", "policies")).toBe("rate_table")
  })

  it("returns null when a value sanitises back to the incoming name", () => {
    expect(resolveBindingAlias("poli cies", "poli_cies")).toBeNull()
  })
})
