/** Whether a Polars dtype is one of the scalar numeric types. */
export function isNumericDtype(dtype: string): boolean {
  const normalised = dtype.trim().toLowerCase()
  return /^(?:u?int(?:8|16|32|64|128)|float(?:32|64)|[iu](?:8|16|32|64|128)|f(?:32|64)|decimal(?:\([^)]*\))?)$/.test(normalised)
}

/**
 * Whether a Polars dtype is Date or Datetime, as `str(dtype)` renders them
 * (`Date`, `Datetime(time_unit='us', time_zone=None)`); Duration and Time are
 * not dates.
 */
export function isTemporalDtype(dtype: string): boolean {
  const normalised = dtype.trim().toLowerCase()
  return /^(?:date|datetime(?:\(.*\))?)$/.test(normalised)
}
