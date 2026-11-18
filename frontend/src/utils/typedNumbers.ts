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

/** The spaces that group digits in threes, as a comma does. */
const GROUP_SPACES = " \u00a0\u2009\u202f"

const NUMBER = new RegExp(
  `^([+-]?)(?:[$£€][${GROUP_SPACES}]*)?([+-]?)` +
    `([0-9]+|[1-9][0-9]{0,2}(?:,[0-9]{3})+|[1-9][0-9]{0,2}(?:[${GROUP_SPACES}][0-9]{3})+)?` +
    `(\\.[0-9]*)?([eE][+-]?[0-9]+)?$`,
)
const GROUPING = new RegExp(`[,${GROUP_SPACES}]`, "g")



const isTypedSpace = (character: string): boolean => TYPED_SPACES.includes(character)

/** *text* without the typed spaces at either end. */
export function trimTyped(text: string): string {
  let start = 0
  let end = text.length
  while (start < end && isTypedSpace(text[start])) start += 1
  while (end > start && isTypedSpace(text[end - 1])) end -= 1
  return text.slice(start, end)
}

/** The number *text* is as plain digits for the language to read, and whether it holds only digits; or null. */
function parts(text: string): { plain: string; digitsOnly: boolean } | null {
  const match = NUMBER.exec(trimTyped(text))
  if (match === null) return null
  const [, sign1, sign2, whole, fraction, exponent] = match
  if (sign1 && sign2) return null
  if (whole === undefined && (fraction === undefined || fraction === ".")) return null
  const digits = (whole ?? "").replace(GROUPING, "")
  return { plain: `${sign1 || sign2}${digits}${fraction ?? ""}${exponent ?? ""}`, digitsOnly: fraction === undefined && exponent === undefined }
}

/** The number *text* is, read by the grammar, or null when it is not one (finite). */
export function readTypedNumber(text: string): number | null {
  const read = parts(text)
  if (read === null) return null
  const number = Number(read.plain)
  return Number.isFinite(number) ? number : null
}

/**
 * The whole number *text* is, exactly however large, or null when it is not a number or not
 * whole: digits alone read as digits, any other number when its value is whole.
 */
export function readTypedWhole(text: string): bigint | null {
  const read = parts(text)
  if (read === null) return null
  if (read.digitsOnly) return BigInt(read.plain)
  const number = readTypedNumber(text)
  return number !== null && Number.isInteger(number) ? BigInt(number) : null
}

/** A decimal comma, said as what to do: the grammar never guesses which a comma is. */
export const DECIMAL_COMMA = "Use a point for decimals."
