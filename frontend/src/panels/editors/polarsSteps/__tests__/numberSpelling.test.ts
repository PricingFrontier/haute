import { describe, expect, it } from "vitest"

import { arrivedRendering, asMember, numberSpelling, slotSpelling, spelledNumber, typedSpelling } from "../numberSpelling"
import renderings from "./fixtures/numberRenderings.json"

describe("a number's spelling (CGEN-09)", () => {
  it("is written as the server writes a number literal", () => {
    expect(numberSpelling("1.0")).toBe("1.0")
    expect(numberSpelling("1E+20")).toBe("1e20")
    expect(numberSpelling(".5")).toBe("0.5")
    expect(numberSpelling("5.")).toBe("5.0")
    expect(numberSpelling("007")).toBe("7")
    expect(numberSpelling("-0.0")).toBe("-0.0")
    expect(numberSpelling("1_0")).toBeNull()
    expect(numberSpelling("one")).toBeNull()
    expect(typedSpelling(" 1,000.50 ")).toBe("1000.50")
    expect(typedSpelling("$2.0")).toBe("2.0")
  })

  it("is what the server writes the number as, as this browser sends it, given no spelling", () => {
    // Python's repr of the float or the int the JSON holds.
    expect(arrivedRendering(1)).toBe("1")
    expect(arrivedRendering(2.5)).toBe("2.5")
    expect(arrivedRendering(1e20)).toBe("100000000000000000000")
    expect(arrivedRendering(1e21)).toBe("1e21")
    expect(arrivedRendering(1.5e300)).toBe("1.5e300")
    expect(arrivedRendering(1.5e-7)).toBe("1.5e-07")
    expect(arrivedRendering(0.0001)).toBe("0.0001")
    expect(arrivedRendering(0.00001)).toBe("1e-05")
    expect(arrivedRendering(123.456)).toBe("123.456")
    expect(arrivedRendering(-0)).toBe("0")
    expect(arrivedRendering(Number("9007199254740993"))).toBe("9007199254740992")
    expect(arrivedRendering(1, "float")).toBe("1.0")
    expect(arrivedRendering(-0, "float")).toBe("0.0")
    expect(arrivedRendering(1.5, "decimal")).toBe("1.5")
    expect(arrivedRendering(1e-7, "decimal")).toBe("0.0000001")
    expect(arrivedRendering(2, "integer")).toBe("2")
  })

  it("is carried exactly when the server would write the number otherwise", () => {
    expect(spelledNumber(1, "1.0")).toEqual({ kind: "literal", type: "number", value: 1, text: "1.0" })
    expect(spelledNumber(2.5, "2.5")).toEqual({ kind: "literal", type: "number", value: 2.5 })
    expect(spelledNumber(9007199254740992, "9007199254740993").text).toBe("9007199254740993")
    expect(spelledNumber(0, "-0.0").text).toBe("-0.0")
    expect(spelledNumber(1, "1.0", "float")).toEqual({ kind: "literal", type: "number", value: 1 })
    expect(spelledNumber(1.5, "1.50", "decimal").text).toBe("1.50")
  })

  it("is kept in a slot of whole numbers only when it is whole", () => {
    // Round's decimal places: `2.0` would write `.round(2.0)`, which the server refuses.
    expect(slotSpelling("2.0", true)).toBeNull()
    expect(slotSpelling("1e3", true)).toBeNull()
    expect(slotSpelling("9007199254740993", true)).toBe("9007199254740993")
    expect(slotSpelling("2.0", false)).toBe("2.0")
  })

  it("is kept for a list's member only where that list can write it", () => {
    const decimal = spelledNumber(1, "1.0")
    expect(asMember(decimal, "integer")).toEqual({ kind: "literal", type: "number", value: 1 })
    expect(asMember(decimal, "float")).toEqual({ kind: "literal", type: "number", value: 1 })
    expect(asMember(spelledNumber(2.5, "2.50"), "decimal").text).toBe("2.50")
  })
})

describe("the server's writing of a number a browser sent", () => {
  it.each(renderings.cases)("is the editor's for $value", (row) => {
    // The case file is the server's own (scripts/regen_number_renderings_fixture.py).
    const value = Number(row.value)
    expect(arrivedRendering(value)).toBe(row.bare)
    expect(arrivedRendering(value, "float")).toBe(row.float)
    expect(arrivedRendering(value, "decimal")).toBe(row.decimal)
    if ("integer" in row) expect(arrivedRendering(value, "integer")).toBe(row.integer)
  })
})
