/** A Table on a sheet: its fields as a grid of rows, or on its side, with its groups (specs/workbench). */
import { Plus, Trash2 } from "lucide-react"
import { type CSSProperties } from "react"
import { cellKey, formattedNumber, presetColumn } from "../../utils/sheetValues"
import { columnKey } from "../../utils/sheetVisibility"
import { answeredOutputs, gridInputTables, linedUpOutputs, padRows, withCell, type SampleValue, type ShownField, type TableOptions } from "../../utils/workbenchForm"
import { isNumeric } from "../columnTypes"
import { useSheetValues } from "../useSheetValues"
import { Cell, FieldLabel } from "./Cell"
import { type Fillable, type Found, ROW_HEIGHT, fieldLabel, fieldTitle, fillable, labelColor, shown, typeHere } from "./fieldCells"
import { type SheetColours } from "./sheetColours"

/** How wide a transposed grid's column of groups is, so its labels stay in view beside it. */
const GROUP_COLUMN_WIDTH = 96

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
 * row by row, and rows are added and deleted in every input table the grid shows at once,
 * so their cells stay side by side; a one-row table's column, which the checks flag here,
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
 * in capitals and whether rows can be added and deleted; fixed, it never shows fewer than
 * `rows`, and fixed or not, it shows every row the values hold. Showing a table a column
 * gives a row for each value, it shows those rows, fixed, that column's cells holding each
 * row's value as text, and a value typed writes them whole; beside a table whose rows
 * differ, it shows every row of each, a table's cells past its own rows taking no typing.
 * Transposed, its fields run down its side, each labelled at the start of its line, and its
 * rows across, numbered above with a delete over each and an add at the end; the shading
 * and the lines are the grid's as drawn, an output field ruled off above its line. Showing
 * groups, while a field it shows is in one, each run of fields side by side in a schema
 * group is headed by one cell naming it, over their labels, or beside them when
 * transposed (`groupRuns`).
 */
export function FieldGrid({
  fields,
  rows,
  options,
  colours,
}: {
  fields: ShownField[]
  rows: number
  options: TableOptions
  colours: SheetColours
}) {
  const { rows: held, setCell, setRows, price, basis, problems, quoted, readOnly, locked } = useSheetValues()
  const oneRow = (found: Fillable) => found.table.rows === "one"
  const inputs = gridInputTables(fields)
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
  // Each many-row output table it shows; showing no input table, their answer gives its rows once priced.
  const outputs = fields.flatMap(({ found }) => (found !== null && found.table.role === "output" && found.table.rows === "many" ? [found.table] : []))
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
  const addRow = () => setRows(Object.fromEntries(inputTables.map((id) => [id, padRows(held[id] ?? [], count + 1)])))
  const deleteRow = (row: number) => setRows(Object.fromEntries(inputTables.map((id) => [id, shownRows(id).filter((_, i) => i !== row)])))
  const stale = price !== null && price.basis !== basis
  // Each many-row output table's rows lined up with the grid's.
  const matched = answered ?? linedUpOutputs(inputs, outputs, held, count, price, quoted)
  const pricedAt = (found: NonNullable<Found>, row: number): unknown =>
    found.table.rows === "one" ? price?.tables[found.table.name]?.[0]?.[found.column.name] : matched.get(found.table.id)?.[row]?.[found.column.name]

  const { row_numbers: numbered, gridlines, striped, transposed } = options
  // Rows are not added or deleted in a grid of fixed rows, of rows a column gives or of no input table, or while the values are read only.
  const fixed = options.fixed_rows || given || readOnly || inputTables.length === 0
  const height = ROW_HEIGHT[options.density]
  // The line before a field's column: an output's, and every column's in a full grid; none
  // without gridlines, or at the grid's left edge.
  const ruleBefore = (found: Found, position: number): string | undefined => {
    if (gridlines === "none" || (position === 0 && !numbered)) return undefined
    if (found?.table.role === "output") return `1px solid ${colours.frame}`
    return gridlines === "all" ? `1px solid ${colours.gridline}` : undefined
  }
  // Transposed, the line above a field's line: an output's, but for the first, under the
  // header or at the grid's top edge; the lines between fields are the rows' as drawn.
  const ruleAbove = (found: Found, position: number): string | undefined =>
    gridlines === "none" || position === 0 || found?.table.role !== "output" ? undefined : `1px solid ${colours.frame}`
  // Transposed, the line before a row's column in a full grid, but for the first, after the labels.
  const columnLine = (row: number): string | undefined => (gridlines === "all" && row > 0 ? `1px solid ${colours.gridline}` : undefined)
  const rowLine = gridlines === "none" ? undefined : `1px solid ${colours.gridline}`
  const rowClass = `transition-colors ${striped ? "even:bg-[var(--chip-rest)] " : ""}hover:bg-[var(--bg-hover)]`
  const headRule = `1px solid ${colours.headRule}`
  const headFill = (options.frame ? colours.componentFill : colours.sheetFill) ?? "var(--bg-elevated)"
  const headText = options.header === "caps" ? "text-[11px] tracking-wider uppercase" : "text-xs"
  // A rule along the inner edge of a header that stays in view is its cells' shadow: a table's
  // own border there stays behind as the grid scrolls under it.
  const ruleUnder = `inset 0 -1px 0 ${colours.headRule}`
  // The fields under their groups, while it shows them and a field it shows is in one.
  const runs = options.groups && fields.some(({ found }) => (found?.column.group ?? "") !== "") ? groupRuns(fields) : null
  const groupColour = colours.font ?? "var(--text-secondary)"

  /** A field's cell in one of the grid's rows, ruled as `rules` say: one in its line when transposed. */
  const valueCell = ({ key, found }: ShownField, row: number, rules: CSSProperties) => {
    if (found !== null && presetColumn(found.table)?.id === found.column.id) {
      // The column giving its table's rows shows each row's value, and takes no typing.
      const value = held[found.table.id]?.[row]?.[found.column.id]
      return (
        <td
          key={transposed ? row : key}
          className={`${height} px-2.5 ${isNumeric(found.column.type) ? "text-right tabular-nums" : ""}`}
          style={{ ...rules, color: colours.font ?? "var(--text-primary)" }}
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
          style={{ ...rules, background: colours.input ?? undefined }}
          onPointerDown={typeHere}
        >
          <Cell
            found={found}
            value={held[found.table.id]?.[at]?.[found.column.id]}
            onChange={(value) => setValue(found.table.id, at, found.column.id, value)}
            label={`${fieldLabel(found)} row ${row + 1}`}
            height={height}
            boxed={false}
            colours={colours}
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
          background: output ? (colours.output ?? "var(--workbench-output-cell-bg)") : undefined,
          color: priced === undefined || priced === null ? "var(--text-secondary)" : (colours.font ?? "var(--text-primary)"),
          opacity: output && stale ? 0.5 : 1,
        }}
      >
        {found?.column.index ? (past(found, row) ? null : row + 1) : output ? shown(priced, found) : null}
      </td>
    )
  }

  // A row's delete, shown while the pointer is on its row, or on its column's number when
  // transposed, or while it has focus. Its name and Add row's are the rows' whatever the
  // grid's orientation; their tooltips say what they do to the grid as drawn.
  const deleteButton = (row: number) => (
    <button
      type="button"
      aria-label={`Delete row ${row + 1}`}
      title={transposed ? "Delete column" : "Delete row"}
      onClick={() => deleteRow(row)}
      onPointerDown={typeHere}
      className={`icon-danger-btn focus-ring pointer-events-auto rounded p-1 opacity-0 ${transposed ? "group-hover/col:opacity-100" : "group-hover/row:opacity-100"} focus-visible:opacity-100`}
    >
      <Trash2 size={13} aria-hidden="true" />
    </button>
  )

  if (transposed) {
    // The labels stay in view as the rows scroll across, and the numbers as the fields scroll
    // down, the corner over both and all of them under the component's marks.
    const ruleRight = `inset -1px 0 0 ${colours.headRule}`
    const ruleLeft = `inset 1px 0 0 ${colours.headRule}`
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 overflow-auto">
          <table className="w-full border-collapse text-xs">
            {(numbered || !fixed) && (
              <thead>
                <tr>
                  <th
                    scope="col"
                    colSpan={runs === null ? undefined : 2}
                    className="sticky top-0 left-0 z-10 h-7 w-px px-2.5 text-left font-semibold"
                    style={{ background: headFill, boxShadow: `${ruleRight}, ${ruleUnder}`, color: "var(--text-disabled)" }}
                  >
                    {numbered && "#"}
                  </th>
                  {Array.from({ length: count }, (_, row) => (
                    <th
                      key={row}
                      scope="col"
                      className="group/col sticky top-0 z-[1] h-7 min-w-32 px-2 text-center text-[11px] font-normal tabular-nums"
                      style={{ background: headFill, boxShadow: ruleUnder, borderLeft: columnLine(row), color: "var(--text-disabled)" }}
                    >
                      {numbered && row + 1}
                      {!fixed && inputTables.length > 0 && <span className="absolute inset-y-0 right-1 flex items-center">{deleteButton(row)}</span>}
                    </th>
                  ))}
                  {!fixed && (
                    <th className="sticky top-0 z-[1] w-8 text-center" style={{ background: headFill, boxShadow: ruleUnder }}>
                      {inputTables.length > 0 && (
                        <button
                          type="button"
                          aria-label="Add row"
                          title="Add column"
                          onClick={addRow}
                          onPointerDown={typeHere}
                          className="focus-ring pointer-events-auto rounded p-1 text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-accent)]"
                        >
                          <Plus size={13} aria-hidden="true" />
                        </button>
                      )}
                    </th>
                  )}
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
                return (
                  <tr key={field.key} className={rowClass} style={{ borderBottom: rowLine }}>
                    {run !== undefined && (
                      <th
                        scope="row"
                        rowSpan={run.size}
                        className={`sticky left-0 z-[1] px-2.5 text-left font-semibold [overflow-wrap:anywhere] ${headText}`}
                        style={{
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
                      className={`sticky z-[1] w-px px-2.5 text-left font-semibold whitespace-nowrap ${headText}`}
                      style={{
                        left: runs === null ? 0 : GROUP_COLUMN_WIDTH - 1,
                        background: headFill,
                        boxShadow: grouped ? `${ruleLeft}, ${ruleRight}` : ruleRight,
                        borderTop: above,
                        color: labelColor(field.found, colours.font),
                      }}
                    >
                      <FieldLabel found={field.found} />
                    </th>
                    {Array.from({ length: count }, (_, row) => valueCell(field, row, { borderTop: above, borderLeft: columnLine(row) }))}
                    {!fixed && <td style={{ borderTop: above }} />}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-auto">
        <table className="w-full border-collapse text-xs">
          <thead className="sticky top-0 z-10" style={{ background: headFill }}>
            {runs !== null && (
              // A group's name over its fields, ruled under it inside the cell's edges so
              // groups side by side stay apart whatever the gridlines.
              <tr className={`font-semibold ${headText}`}>
                {numbered && <th />}
                {runs.map(({ group, start, size }) => (
                  <th
                    key={start}
                    scope="col"
                    colSpan={size}
                    className="h-7 px-1.5 pt-1 text-center align-bottom"
                    style={{ borderLeft: ruleBefore(fields[start].found, start), color: groupColour }}
                  >
                    {group !== "" && (
                      <div className="truncate pb-1" style={{ borderBottom: headRule }}>
                        {group}
                      </div>
                    )}
                  </th>
                ))}
                {!fixed && <th />}
              </tr>
            )}
            <tr className={`text-left font-semibold ${headText}`}>
              {numbered && (
                <th scope="col" className="h-7 w-8 px-2 text-center font-semibold" style={{ boxShadow: ruleUnder, color: "var(--text-disabled)" }}>
                  #
                </th>
              )}
              {fields.map(({ key, found }, position) => (
                <th
                  key={key}
                  scope="col"
                  title={fieldTitle(found)}
                  className={`h-7 min-w-32 truncate px-2.5 ${found !== null && isNumeric(found.column.type) ? "text-right" : ""}`}
                  style={{ boxShadow: ruleUnder, color: labelColor(found, colours.font), borderLeft: ruleBefore(found, position) }}
                >
                  <FieldLabel found={found} />
                </th>
              ))}
              {!fixed && <th className="w-8" style={{ boxShadow: ruleUnder }} />}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: count }, (_, row) => (
              <tr key={row} className={`group/row ${rowClass}`} style={{ borderBottom: rowLine }}>
                {numbered && (
                  <td className="text-center text-[11px] tabular-nums" style={{ color: "var(--text-disabled)" }}>
                    {row + 1}
                  </td>
                )}
                {fields.map((field, position) => valueCell(field, row, { borderLeft: ruleBefore(field.found, position) }))}
                {!fixed && <td className="text-center">{inputTables.length > 0 && deleteButton(row)}</td>}
              </tr>
            ))}
          </tbody>
        </table>
        {inputTables.length > 0 && !fixed && (
          <button
            type="button"
            onClick={addRow}
            onPointerDown={typeHere}
            className={`focus-ring pointer-events-auto flex ${height} w-full items-center gap-1.5 px-2.5 text-[11px] font-medium text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-accent)]`}
          >
            <Plus size={12} aria-hidden="true" />
            Add row
          </button>
        )}
      </div>
    </div>
  )
}
