/** A Table on a sheet: its fields as a grid of rows, or on its side, with its groups, drawn as its look says (specs/workbench). */
import { useState, type CSSProperties, type MouseEvent } from "react"
import { cellKey, formattedNumber, presetColumn } from "../../utils/sheetValues"
import { columnKey } from "../../utils/sheetVisibility"
import {
  answeredOutputs,
  gridInputTables,
  linedUpOutputs,
  padRows,
  withCell,
  type ColumnLookOptions,
  type SampleValue,
  type ShownField,
  type TableOptions,
} from "../../utils/workbenchForm"
import { isNumeric } from "../columnTypes"
import { ShowWhenMark } from "../ShowWhenEditor"
import { useSheetValues } from "../useSheetValues"
import { Cell, FieldLabel } from "./Cell"
import {
  type FieldMark,
  type Fillable,
  type Found,
  HIDDEN_OPACITY,
  LINE_WIDTH,
  ROW_HEIGHT,
  fieldLabel,
  fieldTitle,
  fillable,
  headerText,
  labelFont,
  looksByKey,
  shown,
  typeHere,
} from "./fieldCells"
import { RowMenu } from "./RowMenu"
import { type SheetColours } from "./sheetColours"

/** How wide a transposed grid's column of groups is, so its labels stay in view beside it. */
const GROUP_COLUMN_WIDTH = 96

/** How a turned label's letters run: up the column, down it, or upright one under another. */
const TURNS: Record<Exclude<TableOptions["header_orientation"], "horizontal">, CSSProperties> = {
  up: { writingMode: "vertical-rl", transform: "rotate(180deg)" },
  down: { writingMode: "vertical-rl" },
  stacked: { writingMode: "vertical-rl", textOrientation: "upright" },
}

/** How long a turned label that wraps runs before it starts another line beside it. */
const TURNED_WRAP_LENGTH = 128

/** A grid's fields under their schema groups, in order: each run of fields side by side in one group, and each field in none alone. */
function groupRuns(fields: ShownField[]): { group: string; start: number; size: number }[] {
  const runs: { group: string; start: number; size: number }[] = []
  fields.forEach(({ found }, start) => {
    const group = found?.column.group ?? ""
    const last = runs.at(-1)
    if (group !== "" && last?.group === group) last.size += 1
    else runs.push({ group, start, size: 1 })
  })
  return runs
}

/**
 * Many-row columns as a grid that starts with `rows` rows. Its input columns take typing,
 * row by row, and rows are inserted, through the menu a right-click on a row opens
 * (`RowMenu`), and deleted in every input table the grid shows at once, so their cells
 * stay side by side; a one-row table's column, which the checks flag here,
 * shows that table's one value on every row. While its input tables hold no rows it shows
 * `rows` empty ones, which the first value typed into it keeps; once they hold rows it
 * shows every one, so rows can be deleted below `rows`, deleting the last starting it
 * again. An output column shows what pricing gave the row keyed like each grid row,
 * through the first input table the grid shows that is keyed alike, dimmed while the price
 * is for other values. Showing output columns alone, it shows `rows` rows of dashes until
 * there is a price, then a row for each of the first output table's answer, the others'
 * matched by its keys (`answeredOutputs`), as a Chart of them plots them, none added or
 * deleted. Its options say whether the rows are numbered, how tall they are,
 * which lines it draws, whether every other row is shaded, whether its header's labels are
 * in capitals and whether rows can be inserted and deleted; fixed, it never shows fewer than
 * `rows`, and fixed or not, it shows every row the values hold. Showing a table a column
 * gives a row for each value, it shows those rows, fixed, that column's cells holding each
 * row's value as text, and a value typed writes them whole; beside a table whose rows
 * differ, it shows every row of each, a table's cells past its own rows taking no typing.
 * Transposed, its fields run down its side, each labelled at the start of its line, and its
 * rows across, numbered above, a row's menu opening on its column;
 * the shading and the lines are the grid's as drawn, an output field ruled off above its line. Showing
 * groups, while a field it shows is in one, each run of fields side by side in a schema
 * group is headed by one cell naming it, over their labels, or beside them when
 * transposed (`groupRuns`).
 *
 * Its look, as a spreadsheet's is set: its text's size; its labels' size, weight, fill,
 * colour, place, wrapping and turn, the turned ones read upwards, downwards or stacked
 * and sitting at the foot of the header; its lines' colour and weight, which its
 * gridlines, the rule under its header, the line before an output and a group's rule all
 * take, and its gridlines' style, the rules staying solid; and each column's own look, its
 * label's fill and colour, its cells' fill, colour and weight, where its values sit across
 * it and its label, as the Table's labels do until it is given a place of its own and as
 * its values do while the Table gives none, and, unless transposed, its width. A colour it
 * sets is drawn over the theme's, and a
 * column's over the Table's; a column's fill hides the stripes and the output shade in its
 * cells. Transposed, a column's look is its field's line, its label the line's label.
 *
 * It draws the fields it is given, in Preview those that show, its rows those of the input
 * tables of every field it shows (`rowFields`), so a row inserted or deleted goes from each
 * of them whatever hides; while building, a column with conditions carries an eye after its
 * label, and one a new quote hides is faded, its label and its cells (`marks`).
 */
export function FieldGrid({
  fields,
  rowFields,
  marks,
  rows,
  options,
  colours,
}: {
  fields: ShownField[]
  rowFields: ShownField[]
  marks: ReadonlyMap<string, FieldMark>
  rows: number
  options: TableOptions
  colours: SheetColours
}) {
  const { rows: held, setCell, setRows, price, basis, problems, quoted, readOnly, locked } = useSheetValues()
  const oneRow = (found: Fillable) => found.table.rows === "one"
  const inputs = gridInputTables(rowFields)
  const inputTables = inputs.map((table) => table.id)
  // The rows a column gives each table that has them, a row for each value: a grid showing one
  // shows every row its tables hold, none added or deleted.
  const givenRows = new Map(
    inputs.flatMap((table) => {
      const column = presetColumn(table)
      return column === null ? [] : [[table.id, column.options.length] as const]
    }),
  )
  const given = givenRows.size > 0
  const holding = Math.max(0, ...inputTables.map((id) => held[id]?.length ?? 0))
  // Each many-row output table of every field, whatever hides; showing no input table, their answer gives its rows once priced.
  const outputs = rowFields.flatMap(({ found }) => (found !== null && found.table.role === "output" && found.table.rows === "many" ? [found.table] : []))
  const answered = inputs.length === 0 ? answeredOutputs(outputs, price) : null
  const answer = outputs.length === 0 ? undefined : answered?.get(outputs[0].id)
  const count =
    answer !== undefined ? answer.length : given ? holding : holding === 0 ? rows : options.fixed_rows ? Math.max(rows, holding) : holding
  // A table a column gives its rows has no cells past them, beside a table whose rows differ.
  const past = (found: NonNullable<Found>, row: number): boolean => row >= (givenRows.get(found.table.id) ?? Infinity)
  // Each input table's rows as the grid shows them.
  const shownRows = (id: string) => padRows(held[id] ?? [], count)
  // Its rows as shown are written whole until its tables hold rows, and always while a column gives them.
  const setValue = (tableId: string, row: number, columnId: string, value: SampleValue) => {
    if (!inputTables.includes(tableId) || (holding > 0 && !given)) setCell(tableId, row, columnId, value)
    else setRows(Object.fromEntries(inputTables.map((id) => [id, id === tableId ? withCell(shownRows(id), row, columnId, value) : shownRows(id)])))
  }
  // Empty rows put in at `at` in every input table shown, as one edit.
  const insertRows = (at: number, added: number) =>
    setRows(Object.fromEntries(inputTables.map((id) => [id, [...shownRows(id).slice(0, at), ...padRows([], added), ...shownRows(id).slice(at)]])))
  const deleteRow = (row: number) => setRows(Object.fromEntries(inputTables.map((id) => [id, shownRows(id).filter((_, i) => i !== row)])))
  const stale = price !== null && price.basis !== basis
  // Each many-row output table's rows lined up with the grid's.
  const matched = answered ?? linedUpOutputs(inputs, outputs, held, count, price, quoted)
  const pricedAt = (found: NonNullable<Found>, row: number): unknown =>
    found.table.rows === "one" ? price?.tables[found.table.name]?.[0]?.[found.column.name] : matched.get(found.table.id)?.[row]?.[found.column.name]

  const { row_numbers: numbered, gridlines, striped, transposed } = options
  // Rows are not inserted or deleted in a grid of fixed rows, of rows a column gives or of no input table, or while the values are read only.
  const fixed = options.fixed_rows || given || readOnly || inputTables.length === 0
  // The row whose menu is open, and where the right-click was.
  const [menu, setMenu] = useState<{ row: number; x: number; y: number } | null>(null)
  // A right-click on a row opens its menu, unless its rows are fixed: then the browser's own opens.
  const menuOn = (row: number) =>
    fixed
      ? undefined
      : (event: MouseEvent) => {
          event.preventDefault()
          setMenu({ row, x: event.clientX, y: event.clientY })
        }
  const height = ROW_HEIGHT[options.density]
  // Its lines, in its own colour while it sets one, else the theme's, else each line's own:
  // the gridlines in their style, the rules solid.
  const weight = LINE_WIDTH[options.grid_weight]
  const gridColour = options.grid_color ?? colours.gridline
  const ruleColour = options.grid_color ?? colours.headRule
  const gridLine = `${weight}px ${options.grid_style} ${gridColour}`
  const outputLine = `${weight}px solid ${options.grid_color ?? colours.frame}`
  // The line before a field's column: an output's, and every column's in a full grid; none
  // without gridlines, or at the grid's left edge.
  const ruleBefore = (found: Found, position: number): string | undefined => {
    if (gridlines === "none" || (position === 0 && !numbered)) return undefined
    if (found?.table.role === "output") return outputLine
    return gridlines === "all" ? gridLine : undefined
  }
  // Transposed, the line above a field's line: an output's, but for the first, under the
  // header or at the grid's top edge; the lines between fields are the rows' as drawn.
  const ruleAbove = (found: Found, position: number): string | undefined =>
    gridlines === "none" || position === 0 || found?.table.role !== "output" ? undefined : outputLine
  // Transposed, the line before a row's column in a full grid, but for the first, after the labels.
  const columnLine = (row: number): string | undefined => (gridlines === "all" && row > 0 ? gridLine : undefined)
  const rowLine = gridlines === "none" ? undefined : gridLine
  const rowClass = `transition-colors ${striped ? "even:bg-[var(--chip-rest)] " : ""}hover:bg-[var(--bg-hover)]`
  const headRule = `${weight}px solid ${ruleColour}`
  const headFill = options.header_color ?? (options.frame ? colours.componentFill : colours.sheetFill) ?? "var(--bg-elevated)"
  const { className: headText, style: headStyle } = headerText(options)
  const wrap = options.header_wrap
  const turn = options.header_orientation === "horizontal" ? null : TURNS[options.header_orientation]
  // A rule along the inner edge of a header that stays in view is its cells' shadow: a table's
  // own border there stays behind as the grid scrolls under it.
  const ruleUnder = `inset 0 -${weight}px 0 ${ruleColour}`
  // The fields under their groups, while it shows them and a field it shows is in one.
  const runs = options.groups && fields.some(({ found }) => (found?.column.group ?? "") !== "") ? groupRuns(fields) : null
  const groupColour = options.header_font_color ?? colours.font ?? "var(--text-secondary)"
  const lookOf = looksByKey(options)
  // Every column set to a width: the grid is as wide as they are, not its frame.
  const sized = !transposed && fields.length > 0 && fields.every(({ key }) => lookOf(key).width !== null)
  // A column of a set width, unless transposed: exactly that wide.
  const widthOf = (look: ColumnLookOptions): CSSProperties =>
    transposed || look.width === null ? {} : { width: look.width, minWidth: look.width, maxWidth: look.width }
  const labelColour = (found: Found, look: ColumnLookOptions): string => labelFont(found, look, options, colours.font)
  // Where a column's label sits across it, while anything sets it: its own place, else the
  // Table's, else its values'.
  const labelAcross = (look: ColumnLookOptions): CSSProperties["textAlign"] | null =>
    look.header_align !== "auto" ? look.header_align : options.header_align !== "auto" ? options.header_align : look.align !== "auto" ? look.align : null
  // A field's label, turned as the header says: a turned label wraps only past a set length.
  const labelText = (found: Found) =>
    turn === null ? (
      <FieldLabel found={found} />
    ) : (
      <span
        className="inline-block text-left"
        style={{ ...turn, whiteSpace: wrap ? "normal" : "nowrap", maxHeight: wrap ? TURNED_WRAP_LENGTH : undefined }}
      >
        <FieldLabel found={found} />
      </span>
    )
  // A column with conditions carries its eye after its label while building, and one a new quote hides is faded.
  const eyeOf = (key: string) => {
    const mark = marks.get(key)
    return mark === undefined ? null : (
      <span className="ml-1 inline-block align-middle">
        <ShowWhenMark hidden={mark.hidden} title={mark.conditions} />
      </span>
    )
  }
  const fadedOf = (key: string): boolean => marks.get(key)?.faded === true
  // How a value sits in its cell: the column's weight and place across it, and the Table's size.
  const valueText = (look: ColumnLookOptions): CSSProperties => ({
    fontSize: options.font_size,
    ...(look.bold ? { fontWeight: 600 } : {}),
    ...(look.align === "auto" ? {} : { textAlign: look.align }),
  })

  /** A field's cell in one of the grid's rows, ruled as `rules` say: one in its line, opening its row's menu, when transposed. */
  const valueCell = ({ key, found }: ShownField, row: number, rules: CSSProperties) => {
    const look = lookOf(key)
    const text = valueText(look)
    const menuHere = transposed ? menuOn(row) : undefined
    const fade = fadedOf(key) ? HIDDEN_OPACITY : 1
    if (found !== null && presetColumn(found.table)?.id === found.column.id) {
      // The column giving its table's rows shows each row's value, and takes no typing.
      const value = held[found.table.id]?.[row]?.[found.column.id]
      return (
        <td
          key={transposed ? row : key}
          className={`${height} px-2.5 ${isNumeric(found.column.type) ? "text-right tabular-nums" : ""}`}
          style={{
            ...rules,
            ...widthOf(look),
            ...text,
            background: look.cell_color ?? undefined,
            color: look.font_color ?? colours.font ?? "var(--text-primary)",
            opacity: fade,
          }}
        >
          {typeof value === "string" ? (formattedNumber(found.column, value) ?? value) : null}
        </td>
      )
    }
    if (fillable(found) && !past(found, row)) {
      const at = oneRow(found) ? 0 : row
      return (
        <td
          key={transposed ? row : key}
          className={`pointer-events-auto p-0 ${found.column.type === "bool" ? `${height} text-center` : ""}`}
          style={{
            ...rules,
            ...widthOf(look),
            ...(look.align === "auto" ? {} : { textAlign: look.align }),
            background: look.cell_color ?? colours.input ?? undefined,
            opacity: fade,
          }}
          onPointerDown={typeHere}
          onContextMenu={menuHere}
        >
          <Cell
            found={found}
            value={held[found.table.id]?.[at]?.[found.column.id]}
            onChange={(value) => setValue(found.table.id, at, found.column.id, value)}
            label={`${fieldLabel(found)} row ${row + 1}`}
            height={height}
            box={null}
            font={look.font_color ?? colours.font}
            textStyle={text}
            problem={problems[cellKey(found.table.id, at, found.column.id)] ?? null}
            disabled={readOnly || locked.has(columnKey(found.table.id, found.column.id))}
          />
        </td>
      )
    }
    const output = found !== null && found.table.role === "output"
    const priced = output ? pricedAt(found, row) : undefined
    return (
      <td
        key={transposed ? row : key}
        data-testid={output ? `priced-${key}-${row + 1}` : undefined}
        data-stale={output && stale && priced !== undefined ? "true" : undefined}
        className={`${height} px-2.5 text-right tabular-nums transition-opacity`}
        style={{
          ...rules,
          ...widthOf(look),
          ...text,
          background: look.cell_color ?? (output ? (colours.output ?? "var(--workbench-output-cell-bg)") : undefined),
          // The column's own font on anything it shows, an index's numbers and a dash too.
          color: look.font_color ?? (priced === undefined || priced === null ? "var(--text-secondary)" : (colours.font ?? "var(--text-primary)")),
          opacity: (output && stale ? 0.5 : 1) * fade,
        }}
        onContextMenu={menuHere}
      >
        {found?.column.index ? (past(found, row) ? null : row + 1) : output ? shown(priced, found) : null}
      </td>
    )
  }

  const rowMenu = menu !== null && (
    <RowMenu
      key={`${menu.row} ${menu.x} ${menu.y}`}
      row={menu.row}
      x={menu.x}
      y={menu.y}
      transposed={transposed}
      onInsert={(side, added) => insertRows(side === "above" ? menu.row : menu.row + 1, added)}
      onDelete={() => deleteRow(menu.row)}
      onClose={() => setMenu(null)}
    />
  )

  if (transposed) {
    // The labels stay in view as the rows scroll across, and the numbers as the fields scroll
    // down, the corner over both and all of them under the component's marks.
    const ruleRight = `inset -${weight}px 0 0 ${ruleColour}`
    const ruleLeft = `inset ${weight}px 0 0 ${ruleColour}`
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 overflow-auto">
          <table className="w-full border-collapse" style={{ fontSize: options.font_size }}>
            {numbered && (
              <thead>
                <tr>
                  <th
                    scope="col"
                    colSpan={runs === null ? undefined : 2}
                    className="sticky top-0 left-0 z-10 h-7 w-px px-2.5 text-left font-semibold"
                    style={{ background: headFill, boxShadow: `${ruleRight}, ${ruleUnder}`, color: "var(--text-disabled)" }}
                  >
                    #
                  </th>
                  {Array.from({ length: count }, (_, row) => (
                    <th
                      key={row}
                      scope="col"
                      className="sticky top-0 z-[1] h-7 min-w-32 px-2 text-center text-[11px] font-normal tabular-nums"
                      style={{ background: headFill, boxShadow: ruleUnder, borderLeft: columnLine(row), color: "var(--text-disabled)" }}
                      onContextMenu={menuOn(row)}
                    >
                      {row + 1}
                    </th>
                  ))}
                </tr>
              </thead>
            )}
            <tbody>
              {fields.map((field, position) => {
                const above = ruleAbove(field.found, position)
                const run = runs?.find(({ start }) => start === position)
                // A label in a group is ruled off from it on its own edge, so the label can
                // overlap the group's column by a pixel and leave no seam as the rows scroll under.
                const grouped = runs !== null && (field.found?.column.group ?? "") !== ""
                const look = lookOf(field.key)
                return (
                  <tr key={field.key} className={rowClass} style={{ borderBottom: rowLine }}>
                    {run !== undefined && (
                      <th
                        scope="row"
                        rowSpan={run.size}
                        className={`sticky left-0 z-[1] px-2.5 text-center [overflow-wrap:anywhere] ${headText}`}
                        style={{
                          ...headStyle,
                          // Its least width too, or a grid wider than its frame squeezes it.
                          width: GROUP_COLUMN_WIDTH,
                          minWidth: GROUP_COLUMN_WIDTH,
                          background: headFill,
                          borderTop: above,
                          color: groupColour,
                        }}
                      >
                        {run.group}
                      </th>
                    )}
                    <th
                      scope="row"
                      title={fieldTitle(field.found)}
                      className={`sticky z-[1] w-px px-2.5 ${turn === null ? (wrap ? "" : "whitespace-nowrap") : "py-1.5"} ${headText}`}
                      style={{
                        ...headStyle,
                        left: runs === null ? 0 : GROUP_COLUMN_WIDTH - 1,
                        textAlign: labelAcross(look) ?? (turn === null ? "left" : "center"),
                        background: look.header_color ?? headFill,
                        boxShadow: grouped ? `${ruleLeft}, ${ruleRight}` : ruleRight,
                        borderTop: above,
                        color: labelColour(field.found, look),
                        opacity: fadedOf(field.key) ? HIDDEN_OPACITY : undefined,
                      }}
                      data-hidden={fadedOf(field.key) ? "true" : undefined}
                    >
                      {turn === null && wrap ? (
                        // A level label that wraps does so past a set width, as the line's labels are as wide as the widest.
                        <span className="block max-w-40 min-w-24 break-words">
                          <FieldLabel found={field.found} />
                        </span>
                      ) : (
                        labelText(field.found)
                      )}
                      {eyeOf(field.key)}
                    </th>
                    {Array.from({ length: count }, (_, row) => valueCell(field, row, { borderTop: above, borderLeft: columnLine(row) }))}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        {rowMenu}
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-auto">
        <table className={`border-collapse ${sized ? "w-max" : "w-full"}`} style={{ fontSize: options.font_size }}>
          <thead className="sticky top-0 z-10" style={{ background: headFill }}>
            {runs !== null && (
              // A group's name over its fields, ruled under it inside the cell's edges so
              // groups side by side stay apart whatever the gridlines.
              <tr className={headText} style={headStyle}>
                {numbered && <th />}
                {runs.map(({ group, start, size }) => (
                  <th
                    key={start}
                    scope="col"
                    colSpan={size}
                    className="h-7 px-1.5 pt-1 text-center align-bottom"
                    style={{ fontWeight: headStyle.fontWeight, borderLeft: ruleBefore(fields[start].found, start), color: groupColour }}
                  >
                    {group !== "" && (
                      <div className="truncate pb-1" style={{ borderBottom: headRule }}>
                        {group}
                      </div>
                    )}
                  </th>
                ))}
              </tr>
            )}
            <tr className={`text-left ${headText}`} style={headStyle}>
              {numbered && (
                <th scope="col" className="h-7 w-8 px-2 text-center font-semibold" style={{ boxShadow: ruleUnder, color: "var(--text-disabled)" }}>
                  #
                </th>
              )}
              {fields.map(({ key, found }, position) => {
                const look = lookOf(key)
                // Level, it cuts off or wraps across its column; turned, it sits at the header's foot.
                const shape = turn !== null ? "px-1.5 py-1.5 align-bottom" : wrap ? "px-2.5 py-1 break-words" : "truncate px-2.5"
                // As set, else centred turned, a number's on the right.
                const across = labelAcross(look) ?? (turn !== null ? "center" : found !== null && isNumeric(found.column.type) ? "right" : undefined)
                return (
                  <th
                    key={key}
                    scope="col"
                    title={fieldTitle(found)}
                    className={`h-7 ${look.width === null && turn === null ? "min-w-32" : ""} ${shape}`}
                    style={{
                      // A header cell's own weight: it does not take its row's.
                      fontWeight: headStyle.fontWeight,
                      ...widthOf(look),
                      textAlign: across,
                      background: look.header_color ?? undefined,
                      boxShadow: ruleUnder,
                      color: labelColour(found, look),
                      borderLeft: ruleBefore(found, position),
                      opacity: fadedOf(key) ? HIDDEN_OPACITY : undefined,
                    }}
                    data-hidden={fadedOf(key) ? "true" : undefined}
                  >
                    {labelText(found)}
                    {eyeOf(key)}
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: count }, (_, row) => (
              <tr key={row} className={rowClass} style={{ borderBottom: rowLine }} onContextMenu={menuOn(row)}>
                {numbered && (
                  <td className="text-center text-[11px] tabular-nums" style={{ color: "var(--text-disabled)" }}>
                    {row + 1}
                  </td>
                )}
                {fields.map((field, position) => valueCell(field, row, { borderLeft: ruleBefore(field.found, position) }))}
              </tr>
            ))}
          </tbody>
        </table>
        {rowMenu}
      </div>
    </div>
  )
}
