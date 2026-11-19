/**
 * Text typed as a number, read by one grammar in two ways (specs/frontend-shared high-level,
 * "Typed numbers"). The server's reader, `src/haute/_typed_numbers.py`, is the same rule in the
 * other language; both are held to `__tests__/fixtures/typedText.json`. No other code reads
 * typed text with `Number`, `parseFloat`, `parseInt` or unary `+`:
 * `components/form/__tests__/typedNumbersByHand.test.ts` fails for such a call outside this
 * module that is not a named exception.
 *
 * - A field declared numeric reads text as a number or refuses it (`readTypedNumber`,
 *   `readTypedWhole`): a sign and a currency sign, digits grouped in threes by commas or by
 *   spaces, a point as the only decimal mark, an exponent, the value finite. Text that could be
 *   two numbers (`1,5`) is refused, never guessed.
 * - A Constant's value, which has no declared type, is a number only when it is canonical
 *   finite decimal text (`constantValues.ts`).
 */

/**
 * The characters trimmed from either end of typed text, and the only ones a value holding
 * nothing else counts as empty for: one list in both languages, in place of each language's
 * own notion of whitespace (`trim` also takes the byte-order mark and leaves the separator
 * controls U+001C to U+001F and U+0085; Python's `strip` the reverse).
 */
export const TYPED_SPACES =
  "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"

/**
 * A number by the grammar: a sign either side of a currency sign, spaces after it; digits
 * ungrouped, or grouped in threes by commas or by the spaces that group digits (a space, a
 * no-break space, a thin space, a narrow no-break space); a point and a fraction; an exponent.
 */
const NUMBER =
  /^([+-]?)(?:[$£€][ \u00a0\u2009\u202f]*)?([+-]?)([0-9]+|[1-9][0-9]{0,2}(?:,[0-9]{3})+|[1-9][0-9]{0,2}(?:[ \u00a0\u2009\u202f][0-9]{3})+)?(\.[0-9]*)?([eE][+-]?[0-9]+)?$/
const GROUPING = /[, \u00a0\u2009\u202f]/g
/** `TYPED_SPACES` at either end, as character ranges (held to the list by the tests). */
const TRIMMED = /^[\t-\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+|[\t-\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+$/g

/** *text* without the typed spaces at either end. */
export const trimTyped = (text: string): string => text.replace(TRIMMED, "")

/** A number's parts as typed: its sign, its digits (ungrouped), its fraction's and its exponent. */
type Parts = { sign: string; whole: string; fraction: string; exponent: string }

/** The parts of the number *text* is, or null when it is not one by the grammar. */
function numberParts(text: string): Parts | null {
  const match = NUMBER.exec(trimTyped(text))
  if (match === null) return null
  const [, sign1, sign2, whole = "", point = "", exponent = ""] = match
  if ((sign1 && sign2) || (whole === "" && point.length < 2)) return null
  return { sign: sign1 || sign2, whole: whole.replace(GROUPING, ""), fraction: point.slice(1), exponent: exponent.slice(1) }
}

/** The number *text* is, read by the grammar, or null when it is not one (finite). */
export function readTypedNumber(text: string): number | null {
  const parts = numberParts(text)
  if (parts === null) return null
  const number = Number(`${parts.sign}${parts.whole || "0"}.${parts.fraction}e${parts.exponent || "0"}`)
  return Number.isFinite(number) ? number : null
}

/**
 * The whole number *text* is, exactly however large, or null when it is not a number or not
 * whole: its value read from its decimal digits, never through a Number, so
 * `1234567890123456789.0` is that whole number and `1234567890123456789.5` is not; a number
 * written with an exponent is one only when it is finite.
 */
export function readTypedWhole(text: string): bigint | null {
  const parts = numberParts(text)
  if (parts === null || (parts.exponent !== "" && readTypedNumber(text) === null)) return null
  // The value is the digits times ten to the power `shift`; those it drops must be zeros.
  const digits = parts.whole + parts.fraction
  const shift = Number(parts.exponent || "0") - parts.fraction.length
  const kept = shift >= 0 ? digits + "0".repeat(shift) : digits.slice(0, Math.max(0, digits.length + shift))
  return /[1-9]/.test(digits.slice(kept.length)) && shift < 0 ? null : BigInt(`${parts.sign}${kept || "0"}`)
}

/** A decimal comma, said as what to do: the grammar never guesses which a comma is. */
export const DECIMAL_COMMA = "Use a point for decimals."
