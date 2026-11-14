/**
 * The look of the fields not yet drawn by the kit's field (specs/frontend-shared): the
 * input's colours, and the class of a dense field in the schema's rows.
 */
export const INPUT_STYLE = {
  background: "var(--bg-input)",
  border: "1px solid var(--border)",
  color: "var(--text-primary)",
} as const

/** A dense field in the schema editor's rows, a column to a row: the kit's compact field,
 *  for the one row that still colours its own. */
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
