/**
 * The cast table's name for a column type, as `str(dtype)` renders it, or null
 * for a type the table does not cover (a list, a struct) or an unknown one:
 * the backend's `cast_family`.
 */
export type CastFamily =
  | "integer" | "float" | "decimal" | "boolean" | "text" | "category"
  | "date" | "datetime" | "duration" | "time" | "binary" | "null"

export function castFamily(dtype: string | null): CastFamily | null {
  if (dtype === null) return null
  const d = dtype.trim()
  if (/^U?Int(?:8|16|32|64|128)$/.test(d)) return "integer"
  if (/^Float(?:32|64)$/.test(d)) return "float"
  if (/^Decimal(?:\(.*\))?$/.test(d)) return "decimal"
  if (/^(?:Categorical|Enum)(?:\(.*\))?$/.test(d)) return "category"
  if (/^Datetime(?:\(.*\))?$/.test(d)) return "datetime"
  if (/^Duration(?:\(.*\))?$/.test(d)) return "duration"
  const plain: Record<string, CastFamily> = {
    Boolean: "boolean", String: "text", Utf8: "text", Date: "date", Time: "time", Binary: "binary", Null: "null",
  }
  return plain[d] ?? null
}

/** The scale of a Decimal column type (`Decimal(precision=10, scale=2)`), or null for another type. */
export function decimalScale(dtype: string | null): number | null {
  const match = dtype === null ? null : /^Decimal\(precision=\d+, scale=(\d+)\)$/.exec(dtype.trim())
  return match ? Number(match[1]) : null
}

/** Whether a Polars dtype is one of the scalar numeric types. */
export function isNumericDtype(dtype: string): boolean {
  const normalised = dtype.trim().toLowerCase()
  return /^(?:u?int(?:8|16|32|64|128)|float(?:32|64)|[iu](?:8|16|32|64|128)|f(?:32|64)|decimal(?:\([^)]*\))?)$/.test(normalised)
}

/**
 * Whether a Polars dtype is String or Categorical, as `str(dtype)` renders them
 * (`String`, `Categorical`, `Categorical(ordering='physical')`): the columns
 * CatBoost trains as categorical features, as the backend detects them. Enum
 * is not one of them.
 */
export function isStringOrCategoricalDtype(dtype: string): boolean {
  return /^(?:string|str|utf8|categorical(?:\(.*\))?)$/i.test(dtype.trim())
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
