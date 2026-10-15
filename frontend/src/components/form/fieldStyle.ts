/**
 * The look the panels' fields share (specs/frontend-shared): the input's colours, and the
 * class of a small field in a side panel, with a denser one for the schema's rows.
 */
export const INPUT_STYLE = {
  background: "var(--bg-input)",
  border: "1px solid var(--border)",
  color: "var(--text-primary)",
} as const

/** A small field in a side panel: a select, a text or a number box. */
export const FIELD_CLASS = "focus-ring min-w-0 rounded-md px-1.5 py-1 text-xs"

/** The same, in the schema editor's dense rows, a column to a row. */
export const COMPACT_FIELD_CLASS = "focus-ring min-w-0 rounded-md px-1.5 py-0.5 text-xs"

/**
 * A check for a whole number typed into a field, a `ValidatedTextField`'s `validate`: no
 * fewer than `min`, and with `max` no more than it, the error said as a range; without
 * one, as a least number of `unit`. A number past what a whole number can safely hold is
 * refused too.
 */
export function wholeNumber({ min, max, unit }: { min: number; max?: number; unit?: string }): (text: string) => string | null {
  const said = max !== undefined ? `Enter a whole number from ${min} to ${max}.` : `Enter a whole number${unit === undefined ? "" : ` of ${unit}`}, ${min} or more.`
  return (text) => {
    const number = Number(text)
    const whole = /^\d+$/.test(text.trim()) && Number.isSafeInteger(number)
    return whole && number >= min && (max === undefined || number <= max) ? null : said
  }
}
