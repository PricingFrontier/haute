/** A Collection on a sheet: its fields as labelled boxes, its columns across, drawn as its look says (specs/workbench). */
import { type CSSProperties, Fragment } from "react"
import { cellKey } from "../../utils/sheetValues"
import { columnKey } from "../../utils/sheetVisibility"
import { COLUMN_WIDTH_MIN, type CollectionOptions, type ColumnLookOptions, type ShownField } from "../../utils/workbenchForm"
import { ShowWhenMark } from "../ShowWhenEditor"
import { useSheetValues } from "../useSheetValues"
import { Cell, FieldLabel } from "./Cell"
import {
  BOX_GAPS,
  HIDDEN_OPACITY,
  LINE_WIDTH,
  ROW_HEIGHT,
  SPACER_HEIGHT,
  fieldLabel,
  fieldTitle,
  fillable,
  headerText,
  labelFont,
  looksByKey,
  shown,
  typeHere,
  type FieldMark,
} from "./fieldCells"
import { type SheetColours } from "./sheetColours"

/** How much of its field a label beside its box takes, while neither sets a width. */
const LABEL_SHARE = "40%"

/** The most of its field a label beside its box takes: all but the gap between them (`gap-2`) and the narrowest box a width can set. */
const LABEL_MOST = `calc(100% - ${8 + COLUMN_WIDTH_MIN}px)`

/** A label or a box beside the other taking what the other leaves of its field. */
const TAKES_THE_REST: CSSProperties = { flexGrow: 1, flexBasis: 0, minWidth: 0 }

/** Where a box narrower than its field sits under its label, by its box's alignment: at the field's start while none is set. */
const BOX_PLACE: Record<ColumnLookOptions["align"], CSSProperties> = {
  auto: {},
  left: {},
  center: { marginLeft: "auto", marginRight: "auto" },
  right: { marginLeft: "auto" },
}

/** Where an output's value sits across its box, by its box's alignment: on the right, as a number's does, while none is set. */
const VALUE_PLACE: Record<ColumnLookOptions["align"], CSSProperties["justifyContent"]> = {
  auto: "flex-end",
  left: "flex-start",
  center: "center",
  right: "flex-end",
}

/**
 * One-row columns as boxes, `columns` across and as many rows as it takes, each labelled
 * above its box or beside it, the boxes compact or comfortable, as its options say, and a
 * gap across its whole width where each of its spacers sits, the fields after it starting a
 * new row. An output column shows what pricing gave its table's one row, dimmed while the
 * price is for other values, and a dash before the first answer or after pricing fails.
 * It draws the fields it is given, in Preview those that show, with `spacers` placed among
 * them; while building, a field with conditions carries an eye after its label, and one a
 * new quote hides is faded (`marks`).
 *
 * Its look, as a Table's is set: its values' size; its labels' size, case, weight, wrapping,
 * place, fill and colour, a filled label padded as a cell and, beside its box, as tall as
 * it, and beside their boxes the share of a field they take, else two fifths of it, or what
 * a box of a set width leaves; its boxes' borders' style, weight and colour, input and
 * output boxes alike, where their values sit, and their width, never wider than the field,
 * a narrower box under its label placed as its value sits; and each field's own look over
 * the Collection's: its label's fill, colour and place, its box's fill, over the input and
 * output fills, its colour, weight and place, and its box's width.
 */
export function FieldBoxes({
  fields,
  spacers,
  marks,
  columns,
  options,
  colours,
}: {
  fields: ShownField[]
  /** Each the number of `fields` before it, in order. */
  spacers: readonly number[]
  marks: ReadonlyMap<string, FieldMark>
  columns: number
  options: CollectionOptions
  colours: SheetColours
}) {
  const { rows, setCell, price, basis, problems, readOnly, locked } = useSheetValues()
  const stale = price !== null && price.basis !== basis
  const height = ROW_HEIGHT[options.density]
  const beside = options.labels === "beside"
  const label = headerText(options)
  const lookOf = looksByKey(options)
  // Its boxes' borders, in its own colour while it sets one, else the theme's.
  const border = `${LINE_WIDTH[options.box_weight]}px ${options.box_style} ${options.box_color ?? colours.frame}`
  const boxStyle = (fill: string | null): CSSProperties => ({ border, ...(fill === null ? {} : { background: fill }) })
  // The spacers before the field at `at`, or after the last field for the end.
  const spacersAt = (at: number) =>
    spacers.flatMap((place, n) =>
      place === at || (at === fields.length && place > at)
        ? [<div key={`spacer-${n}`} aria-hidden="true" data-testid="collection-spacer" className={SPACER_HEIGHT[options.density]} style={{ gridColumn: "1 / -1" }} />]
        : [],
    )
  return (
    <div
      className={`grid min-h-0 flex-1 content-start overflow-auto ${BOX_GAPS[options.density]}`}
      style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
    >
      {fields.map(({ key, found }, index) => {
        const priced = found !== null && found.table.role === "output" ? price?.tables[found.table.name]?.[0]?.[found.column.name] : undefined
        const look = lookOf(key)
        const fill = look.header_color ?? options.header_color
        // Where its label and its value sit across them: as its own look says, else as the Collection's does.
        const labelAlign = look.header_align === "auto" ? options.header_align : look.header_align
        const boxAlign = look.align === "auto" ? options.box_align : look.align
        const labelAcross: CSSProperties = labelAlign === "auto" ? {} : { textAlign: labelAlign }
        const valueAcross: CSSProperties = boxAlign === "auto" ? {} : { textAlign: boxAlign }
        // Its box's width: its field's, else the Collection's, else what its label leaves.
        const width = look.width ?? options.box_width
        // Beside its box, a label takes the share of its field the Collection sets, else two
        // fifths of it while its box has no width, either leaving the box the narrowest a
        // width can set; else what the box leaves.
        const labelPlace: CSSProperties = !beside
          ? {}
          : options.label_width !== null
            ? { width: `${options.label_width}%`, maxWidth: LABEL_MOST, flexShrink: 0 }
            : width === null
              ? { width: LABEL_SHARE, maxWidth: LABEL_MOST, flexShrink: 0 }
              : TAKES_THE_REST
        // A box of a set width is exactly that wide, but never wider than its field.
        const boxPlace: CSSProperties =
          width === null ? (beside ? TAKES_THE_REST : {}) : { width, minWidth: 0, maxWidth: "100%", ...(beside ? {} : BOX_PLACE[boxAlign]) }
        const valueText: CSSProperties = { fontSize: options.font_size, ...(look.bold ? { fontWeight: 600 } : {}), ...valueAcross }
        const mark = marks.get(key)
        return (
          <Fragment key={key}>
            {spacersAt(index)}
            <div
              className={`flex min-w-0 ${beside ? "items-center gap-2" : "flex-col gap-1"}`}
              title={fieldTitle(found)}
              data-hidden={mark?.faded ? "true" : undefined}
              style={mark?.faded ? { opacity: HIDDEN_OPACITY } : undefined}
            >
              <span
                data-testid={`label-${key}`}
                className={`flex min-w-0 items-center ${fill === null ? "" : `rounded-md px-2.5 ${beside ? "self-stretch" : "py-1"}`}`}
                style={{ ...labelPlace, ...(fill === null ? {} : { background: fill }), color: labelFont(found, look, options, colours.font) }}
              >
                <span className={`min-w-0 flex-1 ${label.className} ${options.header_wrap ? "break-words" : "truncate"}`} style={{ ...label.style, ...labelAcross }}>
                  <FieldLabel found={found} />
                </span>
                {mark !== undefined && <ShowWhenMark hidden={mark.hidden} title={mark.conditions} className="ml-1" />}
              </span>
              {fillable(found) ? (
                <div className="pointer-events-auto" style={boxPlace} onPointerDown={typeHere}>
                  <Cell
                    found={found}
                    value={rows[found.table.id]?.[0]?.[found.column.id]}
                    onChange={(value) => setCell(found.table.id, 0, found.column.id, value)}
                    label={fieldLabel(found)}
                    height={height}
                    box={boxStyle(look.cell_color ?? colours.input)}
                    font={look.font_color ?? colours.font}
                    textStyle={valueText}
                    problem={problems[cellKey(found.table.id, 0, found.column.id)] ?? null}
                    disabled={readOnly || locked.has(columnKey(found.table.id, found.column.id))}
                  />
                </div>
              ) : (
                <div
                  data-testid={`priced-${key}`}
                  data-stale={stale && priced !== undefined ? "true" : undefined}
                  className={`flex ${height} items-center rounded-md px-2.5 tabular-nums transition-opacity`}
                  style={{
                    ...boxPlace,
                    ...boxStyle(look.cell_color ?? colours.output ?? "var(--bg-elevated)"),
                    ...valueText,
                    justifyContent: VALUE_PLACE[boxAlign],
                    // The field's own font on anything it shows, a dash too.
                    color: look.font_color ?? (priced === undefined || priced === null ? "var(--text-secondary)" : (colours.font ?? "var(--text-primary)")),
                    opacity: stale ? 0.5 : 1,
                  }}
                >
                  <span className="truncate">{found?.column.index ? "1" : shown(priced, found)}</span>
                </div>
              )}
            </div>
          </Fragment>
        )
      })}
      {spacersAt(fields.length)}
    </div>
  )
}
