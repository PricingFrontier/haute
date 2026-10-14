/**
 * How a component looks on a sheet (specs/workbench): a Collection's fields as labelled
 * boxes and a Table's as the columns of a grid, drawn as its options say, a Chart's
 * plotted (`ChartBody`), and a Heading as its text. An input column takes the values the
 * sheet holds, none while building, as a new quote holds none, and the sample or the
 * underwriter app's quote in Preview, through the control its type calls for, marked when
 * the value breaks the column's rules; an output column is shaded and shows what pricing
 * those values gave its row, dimmed until the next answer. Pressing a cell types there.
 */
import { Info, Plus, Trash2 } from "lucide-react"
import { useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react"
import type { HeadingWidget, Theme } from "../api/types"
import { CommittedTextField } from "../components/form"
import { INPUT_STYLE } from "../panels/editors/_shared"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import { formatValue } from "../utils/formatValue"
import { cellKey, formattedNumber } from "../utils/sheetValues"
import { columnKey } from "../utils/sheetVisibility"
import {
  BLANK_THEME,
  gridInputTables,
  linedUpOutputs,
  padRows,
  readableName,
  withCell,
  type CollectionOptions,
  type SampleValue,
  type ShownField,
  type TableOptions,
  type Widget,
} from "../utils/workbenchForm"
import ChartBody from "./ChartBody"
import { isNumeric } from "./columnTypes"
import { useSheetValues } from "./useSheetValues"

type Found = ShownField["found"]
/** A column of an input table that is still in the schema, other than its index. */
type Fillable = NonNullable<Found> & { table: { role: "input" } }

/** Whether an underwriter fills this field in. */
const fillable = (found: Found): found is Fillable => found !== null && found.table.role === "input" && !found.column.index

/**
 * Muted for an input, green for an output the pricing engine fills, or the component's font
 * colour for either; amber when it is gone, whatever the font.
 */
const labelColor = (found: Found, font: string | null): string =>
  found === null ? "var(--warning)" : (font ?? (found.table.role === "output" ? "var(--success)" : "var(--text-muted)"))

const fieldTitle = (found: Found): string =>
  found === null ? "This column is no longer in the schema" : `${found.table.name}.${found.column.name}`

const fieldLabel = (found: Found): string =>
  found === null ? "Missing column" : found.column.label || readableName(found.column.name)

/** A field's label, with a star when an input must be filled in and a mark naming its help. */
function FieldLabel({ found }: { found: Found }) {
  return (
    <>
      {fieldLabel(found)}
      {found?.table.role === "input" && found.column.required && <span style={{ color: "var(--danger)" }}> *</span>}
      {found?.column.help && (
        <span title={found.column.help} className="ml-1 inline-flex align-[-1px] normal-case" style={{ color: "var(--text-secondary)" }}>
          <Info size={11} aria-hidden="true" />
          <span className="sr-only">{found.column.help}</span>
        </span>
      )}
    </>
  )
}

const BOX_CLASS = "focus-ring w-full rounded-md px-2.5 text-xs"
/** A grid cell reads as part of the sheet until focused, then fills and rings inside its edges; it is as tall as its row. */
const GRID_CELL_CLASS =
  "w-full rounded-none border-0 bg-transparent px-2.5 text-xs outline-none hover:bg-[var(--accent-soft-whisper)] focus:bg-[var(--accent-soft-faint)] focus:shadow-[inset_0_0_0_1px_var(--accent-outline)]"
/** A grid row's or a Collection box's height at each density: Haute's compact ones, or roomier ones. */
const ROW_HEIGHT: Record<TableOptions["density"], string> = { compact: "h-7", comfortable: "h-9" }
/** The space between a Collection's boxes, and round them, at each density. */
const BOX_GAPS: Record<CollectionOptions["density"], string> = { compact: "gap-x-3 gap-y-2 p-2", comfortable: "gap-x-4 gap-y-3 p-3" }
/** A cell whose value breaks its column's rules: outlined as a field in error is, naming the problem. */
const PROBLEM_STYLE: CSSProperties = { boxShadow: "inset 0 0 0 1px var(--danger-border-strong)" }

/**
 * The sheets' colours as drawn: the form's theme, each the editor's own while it sets none, a
 * border colour drawing every line its components draw.
 */
function sheetColours(theme: Theme) {
  const border = theme.border_color
  return {
    /** A framed component's background. */
    background: theme.background_color ?? "var(--bg-panel)",
    /** A frame, the rule under a title, the line before an output column, and a box's border. */
    frame: border ?? "var(--border)",
    /** The rule under a Table's header. */
    headRule: border ?? "var(--border-bright)",
    /** A Table's gridlines. */
    gridline: border ?? "var(--border-subtle)",
    /** The text, or null for each text's own colour. */
    font: theme.font_color,
    /** The fill of input cells and boxes, or null for their own. */
    input: theme.input_cell_color,
    /** The fill of output cells and boxes, or null for their shade. */
    output: theme.output_cell_color,
    /** The components' background and the sheet's as the theme sets them, or null: a Table's header strip takes them. */
    componentFill: theme.background_color,
    sheetFill: theme.sheet_color,
  }
}

/** The sheets' colours as drawn (`sheetColours`). */
type SheetColours = ReturnType<typeof sheetColours>

/**
 * What an output cell shows: its column's format of the value, a dash before the first
 * answer, for a column nothing filled, and after pricing fails.
 */
const shown = (value: unknown, found: Found): string =>
  value === undefined || value === null ? "—" : ((found && formattedNumber(found.column, value)) ?? formatValue(value))

/**
 * The control a field's type calls for, holding what the sheet has for it, or its
 * column's placeholder while empty. A number column's format shows a number while the
 * cell is not being typed in, the text as typed while it is.
 */
function Cell({
  found,
  value,
  onChange,
  label,
  height,
  boxed,
  colours,
  problem,
  disabled,
}: {
  found: Fillable
  value: SampleValue | undefined
  onChange: (value: SampleValue) => void
  label: string
  /** Its height class: a Table's rows' or a Collection's boxes'. */
  height: string
  /** In a Collection, a box of its own; in a Table, part of the grid. */
  boxed: boolean
  /** The sheets' colours: its text's and, in a box, the box's fill and border; a grid's cell takes the fill of the cell it is in. */
  colours: SheetColours
  /** What breaks the column's rules in the value, or null. */
  problem: string | null
  /** The value shows but takes no typing: the quote is read only. */
  disabled: boolean
}) {
  const [typing, setTyping] = useState(false)
  const { column } = found
  const text = typeof value === "string" ? value : ""
  const className = `${boxed ? BOX_CLASS : GRID_CELL_CLASS} ${height}`
  const style = {
    ...(boxed ? { ...INPUT_STYLE, border: `1px solid ${colours.frame}` } : {}),
    ...(boxed && colours.input !== null ? { background: colours.input } : {}),
    ...(colours.font === null ? {} : { color: colours.font }),
    ...(problem === null ? {} : PROBLEM_STYLE),
  }
  const marked = problem === null ? {} : { "aria-invalid": true as const, title: problem }
  if (column.type === "bool") {
    const tick = (
      <input
        type="checkbox"
        aria-label={label}
        checked={value === true}
        onChange={(event) => onChange(event.target.checked)}
        disabled={disabled}
        className="size-4"
        style={{ accentColor: "var(--accent)" }}
      />
    )
    // In a Collection, in a box as the other fields are; in a grid, in its cell.
    return boxed ? (
      <div className={`flex w-full items-center rounded-md px-2.5 ${height}`} style={style}>
        {tick}
      </div>
    ) : (
      tick
    )
  }
  if (column.options.length > 0) {
    return (
      <select {...marked} aria-label={label} value={text} onChange={(event) => onChange(event.target.value)} disabled={disabled} className={className} style={style}>
        <option value="">{column.placeholder}</option>
        {column.options.map((option) => (
          <option key={option} value={option}>
            {formattedNumber(column, option) ?? option}
          </option>
        ))}
      </select>
    )
  }
  if (column.type === "date") {
    return <input {...marked} type="date" aria-label={label} value={text} onChange={(event) => onChange(event.target.value)} disabled={disabled} className={className} style={style} />
  }
  return (
    <CommittedTextField
      {...marked}
      aria-label={label}
      value={typing ? text : (formattedNumber(column, text) ?? text)}
      onCommit={onChange}
      onFocus={() => setTyping(true)}
      onBlur={() => setTyping(false)}
      placeholder={column.placeholder || undefined}
      inputMode={isNumeric(column.type) ? "decimal" : undefined}
      disabled={disabled}
      className={`${className} ${isNumeric(column.type) ? "text-right tabular-nums" : ""}`}
      style={style}
    />
  )
}

/** Pressing in a cell types there, and goes no further: the sheet behind it neither deselects nor commits. */
const typeHere = (event: ReactPointerEvent) => event.stopPropagation()

/** What each size of Heading is: its element, a heading's level to a screen reader, its type and its colour. */
const HEADING_STYLES: Record<HeadingWidget["size"], { as: "h2" | "h3" | "h4"; className: string; color: string }> = {
  large: { as: "h2", className: "text-xl font-semibold", color: "var(--text-primary)" },
  medium: { as: "h3", className: "text-base font-semibold", color: "var(--text-primary)" },
  small: { as: "h4", className: "text-xs font-semibold", color: "var(--text-secondary)" },
}

/** Where each alignment puts a Heading's text, and its description, across its width. */
const HEADING_ALIGN: Record<HeadingWidget["align"], string> = { left: "text-left", center: "text-center", right: "text-right" }

/**
 * A Heading: its text alone on the sheet, at its size, with its description under it while
 * it has one, both aligned as it says, wrapping within its width and centred in its height.
 * With no text it asks for some while building, its description unshown, and shows nothing
 * in Preview.
 */
function HeadingBody({ widget, colours }: { widget: HeadingWidget; colours: SheetColours }) {
  const previewing = useWorkbenchViewStore((s) => s.section === "preview")
  if (widget.text.trim() === "") {
    if (previewing) return null
    // One line, cut short in a small Heading rather than running out over what is below it.
    return (
      <div
        className="flex h-full items-center justify-center overflow-hidden rounded-md px-2 text-[11px]"
        style={{ border: "1px dashed var(--border)", color: "var(--text-secondary)" }}
      >
        <span className="min-w-0 truncate">Type its text in the panel on the right</span>
      </div>
    )
  }
  const { as: Level, className, color } = HEADING_STYLES[widget.size]
  return (
    <div className={`flex h-full flex-col justify-center-safe gap-0.5 overflow-hidden ${HEADING_ALIGN[widget.align]}`}>
      <Level className={`min-w-0 break-words ${className}`} style={{ color: colours.font ?? color }}>
        {widget.text}
      </Level>
      {widget.description.trim() !== "" && (
        <p className="min-w-0 text-xs break-words" style={{ color: colours.font ?? "var(--text-secondary)" }}>
          {widget.description}
        </p>
      )}
    </div>
  )
}

export default function WidgetBody({ widget, fields }: { widget: Widget; fields: ShownField[] }) {
  const previewing = useWorkbenchViewStore((s) => s.section === "preview")
  const colours = sheetColours(useWorkbenchFormStore((s) => s.form?.theme ?? BLANK_THEME))
  if (widget.type === "heading") return <HeadingBody widget={widget} colours={colours} />
  // A Table or a Collection without its frame sits on the sheet as its title and fields alone.
  const framed = widget.type === "chart" || widget.frame
  return (
    <div
      className={`flex h-full flex-col overflow-hidden ${framed ? "rounded-lg" : ""}`}
      style={framed ? { border: `1px solid ${colours.frame}`, background: colours.background } : undefined}
    >
      {widget.title && (
        <div className="px-3 py-2 text-xs font-semibold" style={{ borderBottom: `1px solid ${colours.frame}`, color: colours.font ?? "var(--text-primary)" }}>
          {widget.title}
        </div>
      )}
      {fields.length === 0 ? (
        // Fields are chosen while building: in Preview, which has no panel, it is its frame alone.
        previewing ? null : (
          <div
            className="m-3 grid flex-1 place-items-center rounded-md text-center text-[11px]"
            style={{ border: "1px dashed var(--border)", color: "var(--text-secondary)" }}
          >
            Choose its fields from the schema in the panel on the right
          </div>
        )
      ) : widget.type === "chart" ? (
        <ChartBody widget={widget} />
      ) : widget.type === "collection" ? (
        <FieldBoxes fields={fields} columns={widget.columns} options={widget} colours={colours} />
      ) : (
        <FieldGrid fields={fields} rows={widget.rows} options={widget} colours={colours} />
      )}
    </div>
  )
}

/**
 * One-row columns as boxes, `columns` across and as many rows as it takes, each labelled
 * above its box or beside it, the boxes compact or comfortable, as its options say. An
 * output column shows what pricing gave its table's one row, dimmed while the price is for
 * other values, and a dash before the first answer or after pricing fails.
 */
function FieldBoxes({
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
  return (
    <div
      className={`grid min-h-0 flex-1 content-start overflow-auto ${BOX_GAPS[options.density]}`}
      style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
    >
      {fields.map(({ key, found }) => {
        const priced = found !== null && found.table.role === "output" ? price?.tables[found.table.name]?.[0]?.[found.column.name] : undefined
        return (
          <div key={key} className={`flex min-w-0 ${beside ? "items-center gap-2" : "flex-col gap-1"}`} title={fieldTitle(found)}>
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
        )
      })}
    </div>
  )
}

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
 * is for other values. Its options say whether the rows are numbered, how tall they are,
 * which lines it draws, whether every other row is shaded, whether its header's labels are
 * in capitals and whether rows can be added and deleted; fixed, it never shows fewer than
 * `rows`, and fixed or not, it shows every row the values hold. Transposed, its fields run
 * down its side, each labelled at the start of its line, and its rows across, numbered
 * above with a delete over each and an add at the end; the shading and the lines are the
 * grid's as drawn, an output field ruled off above its line. Showing groups, while a field
 * it shows is in one, each run of fields side by side in a schema group is headed by one
 * cell naming it, over their labels, or beside them when transposed (`groupRuns`).
 */
function FieldGrid({
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
  const holding = Math.max(0, ...inputTables.map((id) => held[id]?.length ?? 0))
  const count = holding === 0 ? rows : options.fixed_rows ? Math.max(rows, holding) : holding
  // Each input table's rows as the grid shows them.
  const shownRows = (id: string) => padRows(held[id] ?? [], count)
  const setValue = (tableId: string, row: number, columnId: string, value: SampleValue) => {
    if (holding > 0 || !inputTables.includes(tableId)) setCell(tableId, row, columnId, value)
    else setRows(Object.fromEntries(inputTables.map((id) => [id, id === tableId ? withCell(shownRows(id), row, columnId, value) : shownRows(id)])))
  }
  const addRow = () => setRows(Object.fromEntries(inputTables.map((id) => [id, padRows(held[id] ?? [], count + 1)])))
  const deleteRow = (row: number) => setRows(Object.fromEntries(inputTables.map((id) => [id, shownRows(id).filter((_, i) => i !== row)])))
  const stale = price !== null && price.basis !== basis
  // Each many-row output table's rows lined up with the grid's.
  const outputs = fields.flatMap(({ found }) => (found !== null && found.table.role === "output" && found.table.rows === "many" ? [found.table] : []))
  const matched = linedUpOutputs(inputs, outputs, held, count, price, quoted)
  const pricedAt = (found: NonNullable<Found>, row: number): unknown =>
    found.table.rows === "one" ? price?.tables[found.table.name]?.[0]?.[found.column.name] : matched.get(found.table.id)?.[row]?.[found.column.name]

  const { row_numbers: numbered, gridlines, striped, transposed } = options
  // Rows are not added or deleted in a grid of fixed rows, or while the values are read only.
  const fixed = options.fixed_rows || readOnly
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
    if (fillable(found)) {
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
        {found?.column.index ? row + 1 : output ? shown(priced, found) : null}
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
                    colSpan={runs === null ? undefined : 2}
                    className="sticky top-0 left-0 z-10 h-7 w-px px-2.5 text-left font-semibold"
                    style={{ background: headFill, boxShadow: `${ruleRight}, ${ruleUnder}`, color: "var(--text-disabled)" }}
                  >
                    {numbered && "#"}
                  </th>
                  {Array.from({ length: count }, (_, row) => (
                    <th
                      key={row}
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
                <th className="h-7 w-8 px-2 text-center font-semibold" style={{ boxShadow: ruleUnder, color: "var(--text-disabled)" }}>
                  #
                </th>
              )}
              {fields.map(({ key, found }, position) => (
                <th
                  key={key}
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
