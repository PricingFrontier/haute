/**
 * How a component looks on a sheet (specs/workbench): a Collection's fields as labelled
 * boxes and a Table's as the columns of a grid, drawn as its options say, and a Chart's
 * plotted (`ChartBody`). An input column takes the values the
 * sheet holds, the sample while building or an underwriter's quote in Preview, through
 * the control its type calls for, marked when the value breaks the column's rules; an
 * output column is shaded and shows what pricing those values gave its row, dimmed until
 * the next answer. Pressing a cell types there and selects the component rather than
 * moving it.
 */
import { Info, Plus, Trash2 } from "lucide-react"
import { useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react"
import { CommittedTextField } from "../components/form"
import { INPUT_STYLE } from "../panels/editors/_shared"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import { formatValue } from "../utils/formatValue"
import { cellKey, formattedNumber } from "../utils/sheetValues"
import { linedUpOutputs, readableName, type SampleRow, type SampleValue, type ShownField, type TableOptions, type Widget } from "../utils/workbenchForm"
import ChartBody from "./ChartBody"
import { isNumeric } from "./columnTypes"
import { useSheetValues } from "./useSheetValues"

type Found = ShownField["found"]
/** A column of an input table that is still in the schema, other than its index. */
type Fillable = NonNullable<Found> & { table: { role: "input" } }

/** Whether an underwriter fills this field in. */
const fillable = (found: Found): found is Fillable => found !== null && found.table.role === "input" && !found.column.index

/** Muted for an input, green for an output the pricing engine fills, amber when it is gone. */
const labelColor = (found: Found): string =>
  found === null ? "var(--warning)" : found.table.role === "output" ? "var(--success)" : "var(--text-muted)"

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

const BOX_CLASS = "focus-ring h-9 w-full rounded-md px-2.5 text-xs"
/** A grid cell reads as part of the sheet until focused, then fills and rings inside its edges; it is as tall as its row. */
const GRID_CELL_CLASS =
  "w-full rounded-none border-0 bg-transparent px-2.5 text-xs outline-none hover:bg-[var(--accent-soft-whisper)] focus:bg-[var(--accent-soft-faint)] focus:shadow-[inset_0_0_0_1px_var(--accent-outline)]"
/** A grid row's height at each density: Haute's compact rows, or roomier ones. */
const ROW_HEIGHT: Record<TableOptions["density"], string> = { compact: "h-7", comfortable: "h-9" }
/** A cell whose value breaks its column's rules: outlined as a field in error is, naming the problem. */
const PROBLEM_STYLE: CSSProperties = { boxShadow: "inset 0 0 0 1px var(--danger-border-strong)" }

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
  problem,
}: {
  found: Fillable
  value: SampleValue | undefined
  onChange: (value: SampleValue) => void
  label: string
  /** In a Table's grid, its rows' height class; in a Collection's box, null. */
  height: string | null
  /** What breaks the column's rules in the value, or null. */
  problem: string | null
}) {
  const [typing, setTyping] = useState(false)
  const { column } = found
  const text = typeof value === "string" ? value : ""
  const className = height === null ? BOX_CLASS : `${GRID_CELL_CLASS} ${height}`
  const style = { ...(height === null ? INPUT_STYLE : {}), ...(problem === null ? {} : PROBLEM_STYLE) }
  const marked = problem === null ? {} : { "aria-invalid": true as const, title: problem }
  if (column.type === "bool") {
    return (
      <input
        type="checkbox"
        aria-label={label}
        checked={value === true}
        onChange={(event) => onChange(event.target.checked)}
        className="size-4"
        style={{ accentColor: "var(--accent)" }}
      />
    )
  }
  if (column.options.length > 0) {
    return (
      <select {...marked} aria-label={label} value={text} onChange={(event) => onChange(event.target.value)} className={className} style={style}>
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
    return <input {...marked} type="date" aria-label={label} value={text} onChange={(event) => onChange(event.target.value)} className={className} style={style} />
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
      className={`${className} ${isNumeric(column.type) ? "text-right tabular-nums" : ""}`}
      style={style}
    />
  )
}

/** Pressing in a cell types there: while building, it selects the component instead of moving it. */
function useTypeHere(widgetId: string) {
  const select = useWorkbenchViewStore((s) => s.select)
  const editable = useWorkbenchViewStore((s) => s.section !== "preview")
  return (event: ReactPointerEvent) => {
    event.stopPropagation()
    if (editable) select(widgetId)
  }
}

export default function WidgetBody({ widget, fields }: { widget: Widget; fields: ShownField[] }) {
  return (
    <div className="flex h-full flex-col overflow-hidden rounded-lg" style={{ border: "1px solid var(--border)", background: "var(--bg-panel)" }}>
      {widget.title && (
        <div className="px-3 py-2 text-xs font-semibold" style={{ borderBottom: "1px solid var(--border)", color: "var(--text-primary)" }}>
          {widget.title}
        </div>
      )}
      {fields.length === 0 ? (
        <div
          className="m-3 grid flex-1 place-items-center rounded-md text-center text-[11px]"
          style={{ border: "1px dashed var(--border)", color: "var(--text-secondary)" }}
        >
          Choose its fields from the schema in the panel on the right
        </div>
      ) : widget.type === "chart" ? (
        <ChartBody widget={widget} />
      ) : widget.type === "collection" ? (
        <FieldBoxes widgetId={widget.id} fields={fields} columns={widget.columns} />
      ) : (
        <FieldGrid widgetId={widget.id} fields={fields} rows={widget.rows} options={widget} />
      )}
    </div>
  )
}

/**
 * One-row columns as boxes, labels above, `columns` across and as many rows as it takes.
 * An output column shows what pricing gave its table's one row, dimmed while the price
 * is for other values, and a dash before the first answer or after pricing fails.
 */
function FieldBoxes({ widgetId, fields, columns }: { widgetId: string; fields: ShownField[]; columns: number }) {
  const { rows, setCell, price, basis, problems } = useSheetValues()
  const typeHere = useTypeHere(widgetId)
  const stale = price !== null && price.basis !== basis
  return (
    <div
      className="grid min-h-0 flex-1 content-start gap-x-4 gap-y-3 overflow-auto p-3"
      style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
    >
      {fields.map(({ key, found }) => {
        const priced = found !== null && found.table.role === "output" ? price?.tables[found.table.name]?.[0]?.[found.column.name] : undefined
        return (
          <div key={key} className="flex min-w-0 flex-col gap-1" title={fieldTitle(found)}>
            <span className="truncate text-xs" style={{ color: labelColor(found) }}>
              <FieldLabel found={found} />
            </span>
            {fillable(found) ? (
              <div className={`pointer-events-auto ${found.column.type === "bool" ? "flex h-9 items-center" : ""}`} onPointerDown={typeHere}>
                <Cell
                  found={found}
                  value={rows[found.table.id]?.[0]?.[found.column.id]}
                  onChange={(value) => setCell(found.table.id, 0, found.column.id, value)}
                  label={fieldLabel(found)}
                  height={null}
                  problem={problems[cellKey(found.table.id, 0, found.column.id)] ?? null}
                />
              </div>
            ) : (
              <div
                data-testid={`priced-${key}`}
                data-stale={stale && priced !== undefined ? "true" : undefined}
                className="flex h-9 items-center justify-end rounded-md px-2.5 text-xs tabular-nums transition-opacity"
                style={{
                  border: "1px solid var(--border)",
                  background: "var(--bg-elevated)",
                  color: priced === undefined || priced === null ? "var(--text-secondary)" : "var(--text-primary)",
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

/** `rows` with empty rows added to make `length`. */
const padRows = (rows: readonly SampleRow[], length: number): SampleRow[] => [
  ...rows,
  ...Array.from({ length: Math.max(0, length - rows.length) }, (): SampleRow => ({})),
]

/**
 * Many-row columns as a grid of at least `rows` rows. Its input columns take typing, row
 * by row, and rows are added and deleted in every input table the grid shows at once, so
 * their cells stay side by side; a one-row table's column, which the checks flag here,
 * shows that table's one value on every row. An output column shows what pricing gave
 * the row keyed like each grid row, through the first input table the grid shows that
 * is keyed alike, dimmed while the price is for other values. Its options say whether
 * the rows are numbered, how tall they are, which lines it draws, whether every other row
 * is shaded and whether rows can be added and deleted; fixed or not, it shows every row
 * the values hold.
 */
function FieldGrid({ widgetId, fields, rows, options }: { widgetId: string; fields: ShownField[]; rows: number; options: TableOptions }) {
  const { rows: held, setCell, setRows, price, basis, problems, quoted } = useSheetValues()
  const typeHere = useTypeHere(widgetId)
  const oneRow = (found: Fillable) => found.table.rows === "one"
  const inputs = [...new Map(fields.flatMap(({ found }) => (fillable(found) && !oneRow(found) ? [[found.table.id, found.table] as const] : []))).values()]
  const inputTables = inputs.map((table) => table.id)
  const count = Math.max(rows, ...inputTables.map((id) => held[id]?.length ?? 0))
  const addRow = () => setRows(Object.fromEntries(inputTables.map((id) => [id, padRows(held[id] ?? [], count + 1)])))
  const deleteRow = (row: number) => setRows(Object.fromEntries(inputTables.map((id) => [id, (held[id] ?? []).filter((_, i) => i !== row)])))
  const stale = price !== null && price.basis !== basis
  // Each many-row output table's rows lined up with the grid's.
  const outputs = fields.flatMap(({ found }) => (found !== null && found.table.role === "output" && found.table.rows === "many" ? [found.table] : []))
  const matched = linedUpOutputs(inputs, outputs, held, count, price, quoted)
  const pricedAt = (found: NonNullable<Found>, row: number): unknown =>
    found.table.rows === "one" ? price?.tables[found.table.name]?.[0]?.[found.column.name] : matched.get(found.table.id)?.[row]?.[found.column.name]

  const { row_numbers: numbered, gridlines, striped, fixed_rows: fixed } = options
  const height = ROW_HEIGHT[options.density]
  // The line before a field's column: an output's, and every column's in a full grid; none
  // without gridlines, or at the grid's left edge.
  const ruleBefore = (found: Found, position: number): string | undefined => {
    if (gridlines === "none" || (position === 0 && !numbered)) return undefined
    if (found?.table.role === "output") return "1px solid var(--border)"
    return gridlines === "all" ? "1px solid var(--border-subtle)" : undefined
  }

  const headRule = { borderBottom: "1px solid var(--border-bright)" }
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-auto">
        <table className="w-full border-collapse text-xs">
          <thead className="sticky top-0 z-10" style={{ background: "var(--bg-elevated)" }}>
            <tr className="text-left text-[11px] font-semibold tracking-wider uppercase">
              {numbered && (
                <th className="h-7 w-8 px-2 text-center font-semibold" style={{ ...headRule, color: "var(--text-disabled)" }}>
                  #
                </th>
              )}
              {fields.map(({ key, found }, position) => (
                <th
                  key={key}
                  title={fieldTitle(found)}
                  className={`h-7 min-w-32 truncate px-2.5 ${found !== null && isNumeric(found.column.type) ? "text-right" : ""}`}
                  style={{ ...headRule, color: labelColor(found), borderLeft: ruleBefore(found, position) }}
                >
                  <FieldLabel found={found} />
                </th>
              ))}
              {!fixed && <th className="w-8" style={headRule} />}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: count }, (_, row) => (
              <tr
                key={row}
                className={`group/row transition-colors ${striped ? "even:bg-[var(--chip-rest)] " : ""}hover:bg-[var(--bg-hover)]`}
                style={{ borderBottom: gridlines === "none" ? undefined : "1px solid var(--border-subtle)" }}
              >
                {numbered && (
                  <td className="text-center text-[11px] tabular-nums" style={{ color: "var(--text-disabled)" }}>
                    {row + 1}
                  </td>
                )}
                {fields.map(({ key, found }, position) => {
                  if (fillable(found)) {
                    const at = oneRow(found) ? 0 : row
                    return (
                      <td
                        key={key}
                        className={`pointer-events-auto p-0 ${found.column.type === "bool" ? `${height} text-center` : ""}`}
                        style={{ borderLeft: ruleBefore(found, position) }}
                        onPointerDown={typeHere}
                      >
                        <Cell
                          found={found}
                          value={held[found.table.id]?.[at]?.[found.column.id]}
                          onChange={(value) => setCell(found.table.id, at, found.column.id, value)}
                          label={`${fieldLabel(found)} row ${row + 1}`}
                          height={height}
                          problem={problems[cellKey(found.table.id, at, found.column.id)] ?? null}
                        />
                      </td>
                    )
                  }
                  const output = found !== null && found.table.role === "output"
                  const priced = output ? pricedAt(found, row) : undefined
                  return (
                    <td
                      key={key}
                      data-testid={output ? `priced-${key}-${row + 1}` : undefined}
                      data-stale={output && stale && priced !== undefined ? "true" : undefined}
                      className={`${height} px-2.5 text-right tabular-nums transition-opacity`}
                      style={{
                        borderLeft: ruleBefore(found, position),
                        background: output ? "var(--workbench-output-cell-bg)" : undefined,
                        color: priced === undefined || priced === null ? "var(--text-secondary)" : "var(--text-primary)",
                        opacity: output && stale ? 0.5 : 1,
                      }}
                    >
                      {found?.column.index ? row + 1 : output ? shown(priced, found) : null}
                    </td>
                  )
                })}
                {!fixed && (
                  <td className="text-center">
                    {inputTables.length > 0 && (
                      <button
                        type="button"
                        aria-label={`Delete row ${row + 1}`}
                        title="Delete row"
                        onClick={() => deleteRow(row)}
                        onPointerDown={typeHere}
                        className="icon-danger-btn focus-ring pointer-events-auto rounded p-1 opacity-0 group-hover/row:opacity-100 focus-visible:opacity-100"
                      >
                        <Trash2 size={13} aria-hidden="true" />
                      </button>
                    )}
                  </td>
                )}
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
