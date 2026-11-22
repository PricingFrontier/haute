/**
 * A step's number as it was written (CGEN-09; specs/codegen/structured-syntax-boundary.md,
 * "A number's spelling").
 *
 * A browser has one kind of number, so a step's `1.0` arrives as `1`, `1e20` as a long whole
 * number and an integer past 2^53 as its nearest double; the server would write each back
 * otherwise. A number literal therefore carries its `text`, its spelling as Python writes it,
 * exactly when the server, given the number as this browser sends it, would write it
 * otherwise: the server's lift gives one by that rule (`spelled_number` in
 * `haute._polars_steps`), and the editor gives a typed number one by the same rule, so a step
 * made here renders, and reads back, as typed.
 */
import type { LiteralOperand, NumberType } from "./types"

/** A number literal as Python source writes one (`_NUMBER_TEXT` on the server). */
const NUMBER_TEXT = /^-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/

/**
 * `text` as the server writes a number literal spelled so (`number_spelling`): a lowercase
 * exponent with no plus sign, a digit on each side of a decimal point, a whole number without
 * leading zeros; null for text that is not a number literal.
 */
export function numberSpelling(text: string): string | null {
  if (!NUMBER_TEXT.test(text)) return null
  const negative = text.startsWith("-")
  const body = (negative ? text.slice(1) : text).toLowerCase()
  const at = body.indexOf("e")
  let mantissa = at < 0 ? body : body.slice(0, at)
  const exponent = at < 0 ? "" : body.slice(at + 1).replace(/^\+/, "")
  let spelled: string
  if (!mantissa.includes(".") && exponent === "") {
    spelled = mantissa.replace(/^0+(?=\d)/, "")
  } else {
    if (mantissa.startsWith(".")) mantissa = `0${mantissa}`
    if (mantissa.endsWith(".")) mantissa = `${mantissa}0`
    spelled = exponent === "" ? mantissa : `${mantissa}e${exponent}`
  }
  return (negative ? "-" : "") + spelled
}

/**
 * The spelling of a number typed into a field and read by the typed reader: the typed text
 * without its currency sign, group separators and plus sign, as a number literal; null when
 * that is not one.
 */
export function typedSpelling(typed: string): string | null {
  return numberSpelling(typed.trim().replace(/[$£€]/g, "").replace(/[, \u00a0\u2009\u202f]/g, "").replace(/^\+/, ""))
}

/** The shortest digits of `value` (no sign) and the power of ten of their first, as
 * `toExponential` writes them. */
function shortestDigits(value: number): { digits: string; exponent: number } {
  const [mantissa, power] = Math.abs(value).toExponential().split("e")
  return { digits: mantissa.replace(".", ""), exponent: Number(power) }
}

/** `value` as Python's `repr` writes a float, with no plus sign in its exponent
 * (`float_literal` on the server). */
function pythonFloat(value: number): string {
  if (value === 0) return Object.is(value, -0) ? "-0.0" : "0.0"
  const sign = value < 0 ? "-" : ""
  const { digits, exponent } = shortestDigits(value)
  if (exponent >= -4 && exponent < 16) {
    if (exponent < 0) return `${sign}0.${"0".repeat(-exponent - 1)}${digits}`
    const whole = digits.slice(0, exponent + 1).padEnd(exponent + 1, "0")
    return `${sign}${whole}.${digits.slice(exponent + 1) || "0"}`
  }
  const mantissa = digits.length > 1 ? `${digits[0]}.${digits.slice(1)}` : digits
  const power = `${exponent < 0 ? "-" : ""}${String(Math.abs(exponent)).padStart(2, "0")}`
  return `${sign}${mantissa}e${power}`
}

/** `value` as the server writes an exact decimal (`_decimal_text`): fixed point, no
 * trailing zeros. */
function decimalText(value: number): string {
  if (value === 0) return Object.is(value, -0) ? "-0" : "0"
  const sign = value < 0 ? "-" : ""
  const { digits, exponent } = shortestDigits(value)
  const fixed = exponent < 0
    ? `0.${"0".repeat(-exponent - 1)}${digits}`
    : `${digits.slice(0, exponent + 1).padEnd(exponent + 1, "0")}.${digits.slice(exponent + 1)}`
  return sign + fixed.replace(/\.?0*$/, "")
}

/**
 * How the server writes `value`, as this browser sends it, given no spelling
 * (`number_rendering` of what arrives): a bare literal (`numberType` null) as Python writes
 * the integer or the float the JSON holds, a member of a list of numbers as that list writes
 * its members.
 */
export function arrivedRendering(value: number, numberType: NumberType | null = null): string {
  const sent = JSON.stringify(value)
  const whole = /^-?\d+$/.test(sent)
  switch (numberType) {
    case null:
      return whole ? sent : pythonFloat(value)
    case "integer":
      return whole || !Number.isInteger(value) ? sent : BigInt(value).toString()
    case "float":
      return pythonFloat(whole ? value + 0 : value)
    case "decimal":
      return whole ? sent : decimalText(value)
  }
}

/**
 * The spelling of a number typed into a slot (`typedSpelling`): none for a slot of whole
 * numbers given one that is not whole, such as `2.0` or `1e3`, which writes the whole number
 * it is, as the server takes only a whole spelling there (`_spelling`).
 */
export function slotSpelling(typed: string, integer: boolean): string | null {
  const spelling = typedSpelling(typed)
  return spelling !== null && integer && !isWhole(spelling) ? null : spelling
}

/** The number literal of `value` written as `spelling`, carrying it as its `text` exactly when
 * the server would write the number otherwise. */
export function spelledNumber(
  value: number,
  spelling: string | null,
  numberType: NumberType | null = null,
): LiteralOperand {
  return spelling !== null && arrivedRendering(value, numberType) !== spelling
    ? { kind: "literal", type: "number", value, text: spelling }
    : { kind: "literal", type: "number", value }
}

/** Whether `spelling` writes a whole number literal. */
function isWhole(spelling: string): boolean {
  return !/[.e]/.test(spelling)
}

/**
 * `literal` as a member of a list of `numberType` numbers: its spelling kept where that
 * list can write it (a whole number in a list of whole numbers, a decimal one in a list of
 * floats, one without an exponent in a list of decimals), and its `text` decided again.
 */
export function asMember(literal: LiteralOperand, numberType: NumberType | null): LiteralOperand {
  if (literal.type !== "number" || typeof literal.value !== "number") return literal
  const spelling = literal.text ?? arrivedRendering(literal.value)
  const fits = numberType === "integer"
    ? isWhole(spelling)
    : numberType === "float"
      ? !isWhole(spelling)
      : numberType === "decimal" ? !spelling.includes("e") : true
  return spelledNumber(literal.value, fits ? spelling : null, numberType)
}

/** How `literal`, a number, reads: its spelling, else the number. */
export function numberShown(literal: LiteralOperand): string {
  return literal.text ?? String(literal.value)
}
