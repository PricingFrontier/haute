/** What a component's fields and cells share on a sheet: the column each names, the input cells, their labels and titles, the rows' heights by density, and the values a field shows (specs/workbench). */
import { type CSSProperties, type PointerEvent as ReactPointerEvent } from "react"
import { formatValue } from "../../utils/formatValue"
import { formattedNumber } from "../../utils/sheetValues"
import { readableName, type CollectionOptions, type ShownField, type TableOptions } from "../../utils/workbenchForm"

export type Found = ShownField["found"]

/** A column of an input table that is still in the schema, other than its index. */
export type Fillable = NonNullable<Found> & { table: { role: "input" } }

/** Whether an underwriter fills this field in. */
export const fillable = (found: Found): found is Fillable => found !== null && found.table.role === "input" && !found.column.index

/**
 * Muted for an input, green for an output the pricing engine fills, or the component's font
 * colour for either; amber when it is gone, whatever the font.
 */
export const labelColor = (found: Found, font: string | null): string =>
  found === null ? "var(--warning)" : (font ?? (found.table.role === "output" ? "var(--success)" : "var(--text-muted)"))

export const fieldTitle = (found: Found): string =>
  found === null ? "This column is no longer in the schema" : `${found.table.name}.${found.column.name}`

export const fieldLabel = (found: Found): string =>
  found === null ? "Missing column" : found.column.label || readableName(found.column.name)

export const BOX_CLASS = "focus-ring w-full rounded-md px-2.5 text-xs"

/** A grid cell reads as part of the sheet until focused, then fills and rings inside its edges; it is as tall as its row. */
export const GRID_CELL_CLASS =
  "w-full rounded-none border-0 bg-transparent px-2.5 text-xs outline-none hover:bg-[var(--accent-soft-whisper)] focus:bg-[var(--accent-soft-faint)] focus:shadow-[inset_0_0_0_1px_var(--accent-outline)]"

/** A grid row's or a Collection box's height at each density: Haute's compact ones, or roomier ones. */
export const ROW_HEIGHT: Record<TableOptions["density"], string> = { compact: "h-7", comfortable: "h-9" }

/** The space between a Collection's boxes, and round them, at each density. */
export const BOX_GAPS: Record<CollectionOptions["density"], string> = { compact: "gap-x-3 gap-y-2 p-2", comfortable: "gap-x-4 gap-y-3 p-3" }

/** How tall a Collection's spacer is at each density: a gap between its boxes, so with the gaps round it, three. */
export const SPACER_HEIGHT: Record<CollectionOptions["density"], string> = { compact: "h-2", comfortable: "h-3" }

/** A cell whose value breaks its column's rules: outlined as a field in error is, naming the problem. */
export const PROBLEM_STYLE: CSSProperties = { boxShadow: "inset 0 0 0 1px var(--danger-border-strong)" }

/**
 * What an output cell shows: its column's format of the value, a dash before the first
 * answer, for a column nothing filled, and after pricing fails.
 */
export const shown = (value: unknown, found: Found): string =>
  value === undefined || value === null ? "—" : ((found && formattedNumber(found.column, value)) ?? formatValue(value))

/** Pressing in a cell types there, and goes no further: the sheet behind it neither deselects nor commits. */
export const typeHere = (event: ReactPointerEvent) => event.stopPropagation()
