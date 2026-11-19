/**
 * A Constant's value, read the Constant way of the one grammar for a typed number
 * (specs/frontend-shared high-level, "Typed numbers"): a number only when it is canonical
 * finite decimal text. The server's `constant_value` in `src/haute/_typed_numbers.py` is the
 * same rule, both held to `__tests__/fixtures/typedText.json`. Apart from the grammar
 * (`typedNumbers.ts`) since only the Constant editor reads it.
 */

import { trimTyped } from "./typedNumbers"

const INT64_MIN = -(2n ** 63n)
const INT64_MAX = 2n ** 63n - 1n

/** Text that reads as a number that is not finite, in any letter case and with any sign. */
const NOT_FINITE = /^[+-]?(?:nan|inf|infinity)$/i

/** A Constant's float as it is written, before its digits are held to the float's own. */
const CANONICAL_DECIMAL = /^-?(?:0|[1-9][0-9]*)\.[0-9]+$/

/** A Constant's whole number as it is written: no leading zero, no `+`, and not `-0`. */
const CANONICAL_WHOLE = /^(?:0|-?[1-9][0-9]*)$/

/** A float's canonical text, as the server's `number_text`: its shortest digits, without an exponent, with a point. */
export function numberText(number: number): string {
  if (Object.is(number, -0)) return "-0.0"
  const [mantissa, exponentText] = number.toExponential().split("e")
  const exponent = Number(exponentText)
  const sign = mantissa.startsWith("-") ? "-" : ""
  const digits = mantissa.replace("-", "").replace(".", "")
  if (exponent < 0) return `${sign}0.${"0".repeat(-exponent - 1)}${digits}`
  const whole = digits.slice(0, exponent + 1).padEnd(exponent + 1, "0")
  return `${sign}${whole}.${digits.slice(exponent + 1) || "0"}`
}

/** A Constant's value as it runs, by the type its column takes. */
export type ConstantHeld =
  | { type: "Int64"; value: bigint }
  | { type: "Float64"; value: number }
  | { type: "Boolean"; value: boolean }
  | { type: "String"; value: string }
  | { type: "Null"; value: null }

const heldWhole = (value: bigint): ConstantHeld | null => (value < INT64_MIN || value > INT64_MAX ? null : { type: "Int64", value })

/**
 * A Constant's value as it runs and as its settings file holds it, as the server's
 * `constant_value`, or null when it is refused: a value that is not finite (`nan`, `inf`,
 * `infinity` as text, a number that is not finite) or a whole number outside a 64-bit
 * integer's range. A boolean stays a boolean and a null a null; a whole number, or canonical whole-number text,
 * an integer; another finite number, or canonical decimal text, a float; any other text stays
 * text.
 */
export function constantValue(value: unknown): ConstantHeld | null {
  if (value === null) return { type: "Null", value }
  if (typeof value === "boolean") return { type: "Boolean", value }
  if (typeof value === "bigint") return heldWhole(value)
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return null
    return Number.isInteger(value) ? heldWhole(BigInt(value)) : { type: "Float64", value }
  }
  if (typeof value !== "string") return null
  if (NOT_FINITE.test(trimTyped(value))) return null
  if (CANONICAL_WHOLE.test(value)) return heldWhole(BigInt(value))
  if (CANONICAL_DECIMAL.test(value)) {
    const number = Number(value)
    if (Number.isFinite(number) && numberText(number) === value) return { type: "Float64", value: number }
  }
  return { type: "String", value }
}
