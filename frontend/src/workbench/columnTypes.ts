import { Calendar, Hash, Quote, ToggleRight, type LucideIcon } from "lucide-react"
import { getKindColor } from "../utils/dtypeColors"
import type { ColumnType } from "../utils/workbenchForm"

/**
 * Each column type as the editor draws a value of that type: the step editor's icon for
 * the kind (a number, text, true/false, a date) in the data preview's colour for that kind.
 */
export const COLUMN_TYPES: Record<ColumnType, { label: string; icon: LucideIcon; color: string }> = {
  str: { label: "Text", icon: Quote, color: getKindColor("text") },
  int: { label: "Integer", icon: Hash, color: getKindColor("integer") },
  float: { label: "Decimal", icon: Hash, color: getKindColor("float") },
  bool: { label: "True/false", icon: ToggleRight, color: getKindColor("boolean") },
  date: { label: "Date", icon: Calendar, color: getKindColor("date") },
}

/** The types a column can be given, in the order the picker offers them. */
export const COLUMN_TYPE_OPTIONS = (Object.keys(COLUMN_TYPES) as ColumnType[]).map((value) => ({
  value,
  label: COLUMN_TYPES[value].label,
}))

export const isNumeric = (type: ColumnType): boolean => type === "int" || type === "float"
