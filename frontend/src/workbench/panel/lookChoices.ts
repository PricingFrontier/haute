/** The choices a Table's and a Collection's looks are set from in the panel (specs/workbench). */
import type { CollectionOptions, ColumnLookOptions, TableOptions } from "../../utils/workbenchForm"

/** Where a Collection's labels sit. */
export const POSITIONS: ReadonlyArray<{ value: CollectionOptions["labels"]; label: string }> = [
  { value: "above", label: "Above" },
  { value: "beside", label: "Beside" },
]

export const DENSITIES: ReadonlyArray<{ value: TableOptions["density"]; label: string }> = [
  { value: "compact", label: "Compact" },
  { value: "comfortable", label: "Comfortable" },
]

export const GRIDLINES: ReadonlyArray<{ value: TableOptions["gridlines"]; label: string }> = [
  { value: "rows", label: "Between rows" },
  { value: "all", label: "Full grid" },
  { value: "none", label: "None" },
]

export const CASES: ReadonlyArray<{ value: TableOptions["header"]; label: string }> = [
  { value: "plain", label: "As written" },
  { value: "caps", label: "Capitals" },
]

const SIZES: readonly TableOptions["font_size"][] = [10, 11, 12, 13, 14, 16, 18]
export const TEXT_SIZES = SIZES.map((size) => ({ value: String(size), label: `${size} px` }))
/** A header's size: its text's, or a pixel smaller in capitals, until one is chosen. */
export const HEADER_SIZES = [{ value: "auto", label: "Auto" }, ...TEXT_SIZES]

export const DIRECTIONS: ReadonlyArray<{ value: TableOptions["header_orientation"]; label: string }> = [
  { value: "horizontal", label: "Horizontal" },
  { value: "up", label: "Rotated up" },
  { value: "down", label: "Rotated down" },
  { value: "stacked", label: "Stacked" },
]

export const WEIGHTS: ReadonlyArray<{ value: TableOptions["grid_weight"]; label: string }> = [
  { value: "thin", label: "Thin" },
  { value: "medium", label: "Medium" },
  { value: "thick", label: "Thick" },
]

export const STYLES: ReadonlyArray<{ value: TableOptions["grid_style"]; label: string }> = [
  { value: "solid", label: "Solid" },
  { value: "dashed", label: "Dashed" },
  { value: "dotted", label: "Dotted" },
]

export const ALIGNS: ReadonlyArray<{ value: ColumnLookOptions["align"]; label: string }> = [
  { value: "auto", label: "Auto" },
  { value: "left", label: "Left" },
  { value: "center", label: "Centre" },
  { value: "right", label: "Right" },
]
