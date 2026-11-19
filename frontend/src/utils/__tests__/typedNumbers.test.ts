import { describe, expect, it } from "vitest"

import { constantValue, numberText } from "../constantValues"
import { readTypedNumber, readTypedWhole, trimTyped, TYPED_SPACES } from "../typedNumbers"
import cases from "./fixtures/typedText.json"

// The one grammar for a typed number, read in both ways, held to the case file the server's
// reader is held to too (specs/frontend-shared high-level, "Typed numbers").

type Expected = string | { type: string; value?: string | number | boolean | null }

function heldAs(expected: Expected, value: unknown): void {
  const held = constantValue(value)
  if (typeof expected === "string") {
    expect(expected).toBe("refused")
    expect(held).toBeNull()
    return
  }
  expect(held?.type).toBe(expected.type)
  if (expected.type === "String") expect(held?.value).toBe(value)
  else if (expected.type === "Int64") expect(held?.value).toBe(BigInt(expected.value as string))
  else expect(held?.value).toBe(expected.value)
}

describe("typed numbers", () => {
  it("trims the case file's typed spaces", () => {
    expect([...TYPED_SPACES].map((character) => character.codePointAt(0))).toEqual(cases.spaces)
    expect(trimTyped(`${TYPED_SPACES}5${TYPED_SPACES}`)).toBe("5")
    expect(trimTyped("\u001c5\u0085")).toBe("\u001c5\u0085")
  })

  it("trims every typed space and no other character", () => {
    for (let code = 0; code <= 0xffff; code += 1) {
      const character = String.fromCharCode(code)
      expect(trimTyped(`${character}5${character}`) === "5", `U+${code.toString(16)}`).toBe(TYPED_SPACES.includes(character))
    }
  })

  it.each(cases.numbers)("a numeric field reads $name by the grammar", ({ text, field }) => {
    if (field === null) {
      expect([readTypedNumber(text), readTypedWhole(text)]).toEqual([null, null])
      return
    }
    expect(readTypedNumber(text) === field.float).toBe(true)
    expect(readTypedWhole(text)).toBe(field.int === null ? null : BigInt(field.int))
  })

  it.each(cases.numbers)("a Constant holds $name as its canonical value", ({ text, constant }) => {
    heldAs(constant, text)
  })

  it.each(cases.constants)("a Constant holds $name from a settings file as its type", ({ value, constant }) => {
    heldAs(constant, value)
  })

  it("refuses a Constant's number that is not finite", () => {
    for (const value of [Number.NaN, Infinity, -Infinity]) expect(constantValue(value)).toBeNull()
  })

  it.each([2.5, 5, 1e-5, 1e20, -0, 0.1 + 0.2, 123456.789])("writes %s as canonical text that reads back as it", (number) => {
    const text = numberText(number)
    expect(text).not.toContain("e")
    expect(text).toContain(".")
    expect(constantValue(text)).toEqual({ type: "Float64", value: number })
  })
})
