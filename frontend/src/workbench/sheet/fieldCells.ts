/** What a component's fields and cells share on a sheet: the column each names, the input cells, their labels and titles, how labels are set, the rows' heights by density, and the values a field shows (specs/workbench). */
import { type CSSProperties, type PointerEvent as ReactPointerEvent } from "react"
import type { FormSchema } from "../../api/types"
import { formatValue } from "../../utils/formatValue"
import { formattedNumber } from "../../utils/sheetValues"
import { fieldKey, fieldShowWhen } from "../../utils/sheetVisibility"
import {
  PLAIN_COLUMN_LOOK,
  readableName,
  showWhenText,
  shownFields,
  type CollectionOptions,
  type ColumnLookOptions,
  type SharedLook,
  type ShownField,
  type TableOptions,
  type Widget,
} from "../../utils/workbenchForm"

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

/** A field's label colour: its column's own, else the component's, else the theme's or its role's (`labelColor`); amber while it is gone. */
export const labelFont = (found: Found, look: ColumnLookOptions, options: Pick<SharedLook, "header_font_color">, font: string | null): string =>
  found === null ? labelColor(found, null) : (look.header_font_color ?? options.header_font_color ?? labelColor(found, font))

/**
 * How a Table's header's, or a Collection's, labels are set: in capitals or as written, as
 * large as its text, or a pixel smaller in capitals, unless it sets their size, and bold or not.
 */
export function headerText(options: Pick<SharedLook, "header" | "header_font_size" | "header_bold" | "font_size">): { className: string; style: CSSProperties } {
  const caps = options.header === "caps"
  return {
    className: caps ? "tracking-wider uppercase" : "",
    style: {
      fontSize: options.header_font_size ?? (caps ? options.font_size - 1 : options.font_size),
      fontWeight: options.header_bold ? 600 : 400,
    },
  }
}

/** Each field's own look, by its field's key (`ShownField`'s), or the plain one. */
export function looksByKey(options: Pick<SharedLook, "column_looks">): (key: string) => ColumnLookOptions {
  const looks = new Map(options.column_looks.map((look) => [`${look.table}:${look.column}`, look]))
  return (key) => looks.get(key) ?? PLAIN_COLUMN_LOOK
}

export const fieldTitle = (found: Found): string =>
  found === null ? "This column is no longer in the schema" : `${found.table.name}.${found.column.name}`

export const fieldLabel = (found: Found): string =>
  found === null ? "Missing column" : found.column.label || readableName(found.column.name)

export const BOX_CLASS = "focus-ring w-full rounded-md px-2.5 text-xs"

/** A grid cell reads as part of the sheet until focused, then fills and rings inside its edges; it is as tall as its row. */
export const GRID_CELL_CLASS =
  "w-full rounded-none border-0 bg-transparent px-2.5 text-xs outline-none hover:bg-[var(--accent-soft-whisper)] focus:bg-[var(--accent-soft-faint)] focus:shadow-[inset_0_0_0_1px_var(--accent-outline)]"

/** How wide a Table's line or frame is at each weight, in pixels. */
export const LINE_WIDTH: Record<TableOptions["grid_weight"], number> = { thin: 1, medium: 2, thick: 3 }

/** A grid row's or a Collection box's height at each density: Haute's compact ones, or roomier ones. */
export const ROW_HEIGHT: Record<TableOptions["density"], string> = { compact: "h-7", comfortable: "h-9" }

/** The space between a Collection's boxes, and round them, at each density. */
export const BOX_GAPS: Record<CollectionOptions["density"], string> = { compact: "gap-x-3 gap-y-2 p-2", comfortable: "gap-x-4 gap-y-3 p-3" }

/** How tall a Collection's spacer is at each density: a gap between its boxes, so with the gaps round it, three. */
export const SPACER_HEIGHT: Record<CollectionOptions["density"], string> = { compact: "h-2", comfortable: "h-3" }

/** A cell whose value breaks its column's rules: outlined as a field in error is, naming the problem. */
export const PROBLEM_STYLE: CSSProperties = { boxShadow: "inset 0 0 0 1px var(--danger-border-strong)" }

/** How a component or a field a new quote hides is drawn while building: faded. */
export const HIDDEN_OPACITY = 0.45

/**
 * A field with conditions while building, by its field's key: whether a new quote hides it,
 * whether it is drawn faded for it (while its component shows: a hidden component is faded
 * whole), and its conditions as a sentence, for its eye.
 */
export interface FieldMark {
  hidden: boolean
  faded: boolean
  conditions: string
}

/** No field marked, as in Preview. */
export const NO_MARKS: ReadonlyMap<string, FieldMark> = new Map()

/**
 * A Collection's spacers among its fields that show, `shows` saying which of its fields do:
 * each placed by the fields that show before it, and gone with the fields after it, up to
 * the next spacer, while it has some and none of them shows.
 */
export function spacersShown(spacers: readonly number[], shows: readonly boolean[]): number[] {
  return spacers.flatMap((place, n) => {
    const after = shows.slice(place, spacers[n + 1] ?? shows.length)
    return after.length > 0 && !after.includes(true) ? [] : [shows.slice(0, place).filter(Boolean).length]
  })
}

/**
 * What a component draws of its fields: the fields drawn; every field it shows, whose input
 * tables a Table takes its rows from; a Collection's spacers among those drawn; and, while
 * building, each field with conditions marked.
 */
export interface DrawnFields {
  fields: ShownField[]
  rowFields: ShownField[]
  spacers: readonly number[]
  marks: ReadonlyMap<string, FieldMark>
}

/** Every field a component shows drawn and none marked, a Collection's spacers where they sit. */
export function everyField(schema: FormSchema, widget: Widget): DrawnFields {
  const fields = shownFields(schema, widget)
  return { fields, rowFields: fields, spacers: widget.type === "collection" ? widget.spacers : [], marks: NO_MARKS }
}

/**
 * What a component draws of its fields for the fields that show (`showing`, by `fieldKey`):
 * in Preview those alone, a Collection's spacers placed among them (`spacersShown`); while
 * `building` every field, each with conditions marked, hidden while `showing` lacks it and
 * faded for it while the component itself shows (`hidden` false).
 */
export function drawnFields(schema: FormSchema, widget: Widget, showing: ReadonlySet<string>, building: boolean, hidden: boolean): DrawnFields {
  const every = everyField(schema, widget)
  if (widget.type !== "tableInput" && widget.type !== "collection") return every
  const shows = widget.fields.map((field) => showing.has(fieldKey(widget.id, field.table, field.column)))
  if (!building) {
    return { ...every, fields: every.fields.filter((_, at) => shows[at]), spacers: widget.type === "collection" ? spacersShown(widget.spacers, shows) : [] }
  }
  const marks = new Map<string, FieldMark>()
  widget.fields.forEach((field, at) => {
    const showWhen = fieldShowWhen(widget, field)
    if (showWhen.conditions.length === 0) return
    marks.set(every.fields[at].key, { hidden: !shows[at], faded: !shows[at] && !hidden, conditions: showWhenText({ schema }, showWhen) })
  })
  return { ...every, marks }
}

/**
 * What an output cell shows: its column's format of the value, a dash before the first
 * answer, for a column nothing filled, and after pricing fails.
 */
export const shown = (value: unknown, found: Found): string =>
  value === undefined || value === null ? "—" : ((found && formattedNumber(found.column, value)) ?? formatValue(value))

/** Pressing in a cell types there, and goes no further: the sheet behind it neither deselects nor commits. */
export const typeHere = (event: ReactPointerEvent) => event.stopPropagation()
