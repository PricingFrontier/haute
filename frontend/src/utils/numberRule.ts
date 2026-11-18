/**
 * A number field's rule (specs/frontend-shared high-level, "Typed numbers"): what a box takes,
 * the number a draft reads as by the one grammar for a typed number, or why it is refused in
 * words. Apart from the grammar (`typedNumbers.ts`) so the editor's startup code, which reads
 * typed text, does not load the fields' rule.
 */

import { DECIMAL_COMMA, readTypedNumber, readTypedWhole, trimTyped } from "./typedNumbers"

/** What a number field takes: a whole number or any, within a range, and whether it may be blank. */
export type NumberRule = {
  integer?: boolean
  min?: number
  max?: number
  exclusiveMin?: boolean
  exclusiveMax?: boolean
  /** Whether a blank field is refused; a blank optional field commits null. */
  required?: boolean
  /** What a whole number counts, said in the refusal: "rows". */
  unit?: string
}

/** What a draft reads as under its rule: its number, null for a blank optional field, or why it is refused. */
export type NumberRead = { ok: true; value: number | null } | { ok: false; problem: string }

function rangeWords({ min, max, exclusiveMin = false, exclusiveMax = false }: NumberRule): string {
  if (min !== undefined && max !== undefined) {
    if (!exclusiveMin && !exclusiveMax) return `from ${min} to ${max}`
    const lower = exclusiveMin ? `greater than ${min}` : `from ${min}`
    return `${lower} and ${exclusiveMax ? "less than" : "at most"} ${max}`
  }
  if (min !== undefined) return exclusiveMin ? `greater than ${min}` : `${min} or more`
  if (max !== undefined) return exclusiveMax ? `less than ${max}` : `${max} or less`
  return ""
}

/** The refusal of a draft under *rule*: what to enter, as a sentence. */
export function numberRuleWords(rule: NumberRule): string {
  const kind = rule.integer ? `a whole number${rule.unit === undefined ? "" : ` of ${rule.unit}`}` : "a number"
  const range = rangeWords(rule)
  if (range === "") return `Enter ${kind}.`
  // "Enter a whole number from 1 to 12."; "Enter a whole number of rows, 1 or more."
  return `Enter ${kind}${range.startsWith("from ") ? " " : ", "}${range}.`
}

/** The number *text* reads as under *rule*, by the one grammar for a typed number, or why it is refused. */
export function readNumberDraft(text: string, rule: NumberRule = {}): NumberRead {
  const refused = (problem: string): NumberRead => ({ ok: false, problem })
  if (trimTyped(text) === "") return rule.required ? refused(numberRuleWords(rule)) : { ok: true, value: null }
  const number = readTypedNumber(text)
  if (number === null) return refused(text.includes(",") ? DECIMAL_COMMA : numberRuleWords(rule))
  let value = number
  if (rule.integer) {
    const whole = readTypedWhole(text)
    if (whole === null || whole > BigInt(Number.MAX_SAFE_INTEGER) || whole < BigInt(Number.MIN_SAFE_INTEGER)) return refused(numberRuleWords(rule))
    value = Number(whole)
  }
  const { min, max, exclusiveMin = false, exclusiveMax = false } = rule
  const inRange = (min === undefined || (exclusiveMin ? value > min : value >= min)) && (max === undefined || (exclusiveMax ? value < max : value <= max))
  return inRange ? { ok: true, value } : refused(numberRuleWords(rule))
}
