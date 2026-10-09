/**
 * How a component looks on a sheet while building (specs/workbench): a Collection's
 * fields as labelled boxes and a Table's as the columns of a grid. An input column takes
 * the sample quote typed into it, through the control its type calls for; an output
 * column is shaded, and in a Collection shows what pricing the sample gave, dimmed until
 * the next answer. Pressing a cell types there and selects the component rather than
 * moving it.
 */
import { Plus, Trash2 } from "lucide-react"
import type { PointerEvent as ReactPointerEvent } from "react"
import { CommittedTextField } from "../components/form"
import { INPUT_STYLE } from "../panels/editors/_shared"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchPricingStore from "../stores/useWorkbenchPricingStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import { formatValue } from "../utils/formatValue"
import {
  pricingBasis,
  readableName,
  withSampleCell,
  withSampleRows,
  type SampleRow,
  type SampleValue,
  type ShownField,
  type Widget,
} from "../utils/workbenchForm"
import { isNumeric } from "./columnTypes"

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

/** A field's label, with a star when an input must be filled in. */
function FieldLabel({ found }: { found: Found }) {
  return (
    <>
      {fieldLabel(found)}
      {found?.table.role === "input" && found.column.required && <span style={{ color: "var(--danger)" }}> *</span>}
    </>
  )
}

const BOX_CLASS = "focus-ring h-9 w-full rounded-md px-2.5 text-xs"
const GRID_CELL_CLASS = "focus-ring h-8 w-full rounded-none border-0 bg-transparent px-2.5 text-xs"

/** The control a field's type calls for, holding what the sample has for it. */
function Cell({
  found,
  value,
  onChange,
  label,
  compact,
}: {
  found: Fillable
  value: SampleValue | undefined
  onChange: (value: SampleValue) => void
  label: string
  compact: boolean
}) {
  const { column } = found
  const text = typeof value === "string" ? value : ""
  const className = compact ? GRID_CELL_CLASS : BOX_CLASS
  const style = compact ? undefined : INPUT_STYLE
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
      <select aria-label={label} value={text} onChange={(event) => onChange(event.target.value)} className={className} style={style}>
        <option value="">{compact ? "" : "Select…"}</option>
        {column.options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    )
  }
  if (column.type === "date") {
    return <input type="date" aria-label={label} value={text} onChange={(event) => onChange(event.target.value)} className={className} style={style} />
  }
  return (
    <CommittedTextField
      aria-label={label}
      value={text}
      onCommit={onChange}
      inputMode={isNumeric(column.type) ? "decimal" : undefined}
      className={`${className} ${isNumeric(column.type) ? "text-right tabular-nums" : ""}`}
      style={style}
    />
  )
}

/** Pressing in a cell types there: it selects the component instead of moving it. */
function useTypeHere(widgetId: string) {
  const select = useWorkbenchViewStore((s) => s.select)
  return (event: ReactPointerEvent) => {
    event.stopPropagation()
    select(widgetId)
  }
}

/** What a sample's cell shows: nothing typed reads as an empty string. */
const EMPTY_SAMPLE: Record<string, SampleRow[]> = {}

function useSample(): Record<string, SampleRow[]> {
  return useWorkbenchFormStore((s) => s.form?.sample ?? EMPTY_SAMPLE)
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
      ) : widget.type === "collection" ? (
        <FieldBoxes widgetId={widget.id} fields={fields} columns={widget.columns} />
      ) : (
        <FieldGrid widgetId={widget.id} fields={fields} rows={widget.rows} />
      )}
    </div>
  )
}

/**
 * One-row columns as boxes, labels above, `columns` across and as many rows as it takes.
 * An output column shows what pricing the sample gave its table's one row, dimmed while a
 * newer answer is on its way, and a dash before the first answer or after pricing fails.
 */
function FieldBoxes({ widgetId, fields, columns }: { widgetId: string; fields: ShownField[]; columns: number }) {
  const sample = useSample()
  const change = useWorkbenchFormStore((s) => s.change)
  const basis = useWorkbenchFormStore((s) => (s.form === null ? "" : pricingBasis(s.form)))
  const price = useWorkbenchPricingStore((s) => s.price)
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
                  value={sample[found.table.id]?.[0]?.[found.column.id]}
                  onChange={(value) => change((spec) => withSampleCell(spec, found.table.id, 0, found.column.id, value))}
                  label={fieldLabel(found)}
                  compact={false}
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
                <span className="truncate">{found?.column.index ? "1" : priced === undefined || priced === null ? "—" : formatValue(priced)}</span>
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
 * shows that table's one value on every row. Output columns stay shaded.
 */
function FieldGrid({ widgetId, fields, rows }: { widgetId: string; fields: ShownField[]; rows: number }) {
  const sample = useSample()
  const change = useWorkbenchFormStore((s) => s.change)
  const typeHere = useTypeHere(widgetId)
  const oneRow = (found: Fillable) => found.table.rows === "one"
  const inputTables = [...new Set(fields.flatMap(({ found }) => (fillable(found) && !oneRow(found) ? [found.table.id] : [])))]
  const count = Math.max(rows, ...inputTables.map((id) => sample[id]?.length ?? 0))
  const addRow = () =>
    change((spec) => withSampleRows(spec, Object.fromEntries(inputTables.map((id) => [id, padRows(spec.sample[id] ?? [], count + 1)]))))
  const deleteRow = (row: number) =>
    change((spec) => withSampleRows(spec, Object.fromEntries(inputTables.map((id) => [id, (spec.sample[id] ?? []).filter((_, i) => i !== row)]))))

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-auto">
        <table className="w-full border-collapse text-xs">
          <thead className="sticky top-0 z-10">
            <tr style={{ background: "var(--bg-elevated)" }} className="text-left">
              <th className="w-9 px-2 py-2 text-center font-normal" style={{ color: "var(--text-muted)" }}>
                #
              </th>
              {fields.map(({ key, found }) => (
                <th
                  key={key}
                  title={fieldTitle(found)}
                  className={`min-w-32 px-2.5 py-2 font-medium ${found !== null && isNumeric(found.column.type) ? "text-right" : ""}`}
                  style={{ color: labelColor(found) }}
                >
                  <FieldLabel found={found} />
                </th>
              ))}
              <th className="w-9" />
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: count }, (_, row) => (
              <tr key={row} className="group/row" style={{ borderTop: "1px solid var(--border)" }}>
                <td className="text-center" style={{ color: "var(--text-secondary)" }}>
                  {row + 1}
                </td>
                {fields.map(({ key, found }) =>
                  fillable(found) ? (
                    <td
                      key={key}
                      className={`pointer-events-auto p-0 ${found.column.type === "bool" ? "text-center" : ""}`}
                      style={{ borderLeft: "1px solid var(--border)" }}
                      onPointerDown={typeHere}
                    >
                      <Cell
                        found={found}
                        value={sample[found.table.id]?.[oneRow(found) ? 0 : row]?.[found.column.id]}
                        onChange={(value) => change((spec) => withSampleCell(spec, found.table.id, oneRow(found) ? 0 : row, found.column.id, value))}
                        label={`${fieldLabel(found)} row ${row + 1}`}
                        compact
                      />
                    </td>
                  ) : (
                    <td
                      key={key}
                      className="h-8 px-2.5 text-right tabular-nums"
                      style={{
                        borderLeft: "1px solid var(--border)",
                        background: found?.table.role === "output" ? "var(--bg-elevated)" : undefined,
                        color: "var(--text-secondary)",
                      }}
                    >
                      {found?.column.index ? row + 1 : null}
                    </td>
                  ),
                )}
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
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {inputTables.length > 0 && (
        <div className="px-2 py-1.5" style={{ borderTop: "1px solid var(--border)" }}>
          <button
            type="button"
            onClick={addRow}
            onPointerDown={typeHere}
            className="quiet-action focus-ring pointer-events-auto inline-flex items-center gap-1 rounded px-1 py-0.5 text-[11px] font-medium"
          >
            <Plus size={11} aria-hidden="true" />
            Add row
          </button>
        </div>
      )}
    </div>
  )
}
