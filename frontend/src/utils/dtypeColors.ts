import { columnKind, type ColumnKind } from "./polarsDtypes"

/** The text colour of a column-type badge, by the type's kind. */
const KIND_COLORS: Partial<Record<ColumnKind, string>> = {
  integer: "text-blue-400",
  float: "text-emerald-400",
  decimal: "text-emerald-400",
  text: "text-amber-400",
  boolean: "text-purple-400",
  date: "text-rose-400",
  datetime: "text-rose-400",
}
const OTHER_COLOR = "text-slate-400"

/** The badge colour of a column of a kind. */
export function getKindColor(kind: ColumnKind | null): string {
  return (kind !== null && KIND_COLORS[kind]) || OTHER_COLOR
}

/** The badge colour of a column type as `str(dtype)` renders it. */
export function getDtypeColor(dtype: string): string {
  return getKindColor(columnKind(dtype))
}
