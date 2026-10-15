/** A Collection on a sheet: its fields as labelled boxes, its columns across (specs/workbench). */
import { Fragment } from "react"
import { cellKey } from "../../utils/sheetValues"
import { columnKey } from "../../utils/sheetVisibility"
import { type CollectionOptions, type ShownField } from "../../utils/workbenchForm"
import { useSheetValues } from "../useSheetValues"
import { Cell, FieldLabel } from "./Cell"
import { BOX_GAPS, ROW_HEIGHT, SPACER_HEIGHT, fieldLabel, fieldTitle, fillable, labelColor, shown, typeHere } from "./fieldCells"
import { type SheetColours } from "./sheetColours"

/**
 * One-row columns as boxes, `columns` across and as many rows as it takes, each labelled
 * above its box or beside it, the boxes compact or comfortable, as its options say, and a
 * gap across its whole width where each of its spacers sits, the fields after it starting a
 * new row. An output column shows what pricing gave its table's one row, dimmed while the
 * price is for other values, and a dash before the first answer or after pricing fails.
 */
export function FieldBoxes({
  fields,
  columns,
  options,
  colours,
}: {
  fields: ShownField[]
  columns: number
  options: CollectionOptions
  colours: SheetColours
}) {
  const { rows, setCell, price, basis, problems, readOnly, locked } = useSheetValues()
  const stale = price !== null && price.basis !== basis
  const height = ROW_HEIGHT[options.density]
  const beside = options.labels === "beside"
  // The spacers before the field at `at`, or after the last field for the end.
  const spacersAt = (at: number) =>
    options.spacers.flatMap((place, n) =>
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
        return (
          <Fragment key={key}>
            {spacersAt(index)}
            <div className={`flex min-w-0 ${beside ? "items-center gap-2" : "flex-col gap-1"}`} title={fieldTitle(found)}>
              <span className={`truncate text-xs ${beside ? "w-2/5 shrink-0" : ""}`} style={{ color: labelColor(found, colours.font) }}>
                <FieldLabel found={found} />
              </span>
              {fillable(found) ? (
                <div
                  className={`pointer-events-auto ${beside ? "min-w-0 flex-1" : ""}`}
                  onPointerDown={typeHere}
                >
                  <Cell
                    found={found}
                    value={rows[found.table.id]?.[0]?.[found.column.id]}
                    onChange={(value) => setCell(found.table.id, 0, found.column.id, value)}
                    label={fieldLabel(found)}
                    height={height}
                    boxed
                    colours={colours}
                    problem={problems[cellKey(found.table.id, 0, found.column.id)] ?? null}
                    disabled={readOnly || locked.has(columnKey(found.table.id, found.column.id))}
                  />
                </div>
              ) : (
                <div
                  data-testid={`priced-${key}`}
                  data-stale={stale && priced !== undefined ? "true" : undefined}
                  className={`flex ${height} items-center justify-end rounded-md px-2.5 text-xs tabular-nums transition-opacity ${beside ? "min-w-0 flex-1" : ""}`}
                  style={{
                    border: `1px solid ${colours.frame}`,
                    background: colours.output ?? "var(--bg-elevated)",
                    color: priced === undefined || priced === null ? "var(--text-secondary)" : (colours.font ?? "var(--text-primary)"),
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
