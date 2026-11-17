import {
  COLUMN_KIND_GROUPS,
  COLUMN_KINDS,
  type COLUMN_KIND_VALUES,
} from "../generated/api-contracts.constants.generated"

/**
 * What kind of column a type is: the server's `column_kind` (specs/io-layer, "Column
 * kinds"), whose table for every type Polars defines the generated contract carries.
 */
export type ColumnKind = (typeof COLUMN_KIND_VALUES)[number]

const KINDS_BY_TYPE: Readonly<Record<string, ColumnKind>> = COLUMN_KINDS
const NUMBER_KINDS: ReadonlySet<ColumnKind> = new Set(COLUMN_KIND_GROUPS.number)
const DATE_KINDS: ReadonlySet<ColumnKind> = new Set(COLUMN_KIND_GROUPS.date)
const UNCASTABLE_KINDS: ReadonlySet<ColumnKind> = new Set(["nested", "object", "extension"])

/** A type's base name as `str(dtype)` renders it: the text before its parameters' bracket. */
function baseTypeName(dtype: string): string {
  const text = dtype.trim()
  const bracket = text.indexOf("(")
  return bracket === -1 ? text : text.slice(0, bracket)
}

/**
 * The kind of a column type as `str(dtype)` renders it (`Float16`,
 * `Datetime(time_unit='us', time_zone=None)`), or null for a name Polars does not define.
 */
export function columnKind(dtype: string | null): ColumnKind | null {
  if (dtype === null) return null
  const name = baseTypeName(dtype)
  return Object.hasOwn(KINDS_BY_TYPE, name) ? KINDS_BY_TYPE[name] : null
}

/**
 * The cast table's name for a column type, its kind, or null for a type the table does
 * not cover (a list, a struct, an object) or an unknown one: the backend's `cast_family`.
 */
export type CastFamily = Exclude<ColumnKind, "nested" | "object" | "extension">

export function castFamily(dtype: string | null): CastFamily | null {
  const kind = columnKind(dtype)
  return kind === null || UNCASTABLE_KINDS.has(kind) ? null : (kind as CastFamily)
}

/** The scale of a Decimal column type (`Decimal(precision=10, scale=2)`), or null for another type. */
export function decimalScale(dtype: string | null): number | null {
  const match = dtype === null ? null : /^Decimal\(precision=\d+, scale=(\d+)\)$/.exec(dtype.trim())
  return match ? Number(match[1]) : null
}

/** Whether a column type is a number of any kind: an integer, a float of any width or a decimal. */
export function isNumericDtype(dtype: string): boolean {
  const kind = columnKind(dtype)
  return kind !== null && NUMBER_KINDS.has(kind)
}

/**
 * Whether a column type is String or Categorical, as `str(dtype)` renders them
 * (`String`, `Categorical`, `Categorical(Categories(...))`): the columns CatBoost trains
 * as categorical features, as the backend's `is_string_or_categorical` detects them. An
 * Enum is a category too, but not one of them.
 */
export function isStringOrCategoricalDtype(dtype: string): boolean {
  return columnKind(dtype) === "text" || baseTypeName(dtype) === "Categorical"
}

/** Whether a column type is a Date or a Datetime: what date breakpoints band. */
export function isTemporalDtype(dtype: string): boolean {
  const kind = columnKind(dtype)
  return kind !== null && DATE_KINDS.has(kind)
}
