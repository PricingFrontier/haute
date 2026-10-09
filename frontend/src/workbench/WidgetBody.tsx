/**
 * How a component looks on a sheet while building (specs/workbench): a picture of the
 * grid or the boxes it will be, each field it shows labelled, typed and shaded as an
 * output when the pricing engine fills it. Typing into it comes with the sample.
 */
import { Calendar, ChevronDown } from "lucide-react"
import { readableName, type ShownField, type Widget } from "../utils/workbenchForm"
import { isNumeric } from "./columnTypes"

type Found = ShownField["found"]

/** Muted for an input, green for an output the pricing engine fills, amber when it is gone. */
const labelColor = (found: Found): string =>
  found === null ? "var(--warning)" : found.table.role === "output" ? "var(--success)" : "var(--text-muted)"

const fieldTitle = (found: Found): string =>
  found === null ? "This column is no longer in the schema" : `${found.table.name}.${found.column.name}`

/** A field's label, with a star when an input must be filled in. */
function FieldLabel({ found }: { found: Found }) {
  return (
    <>
      {found === null ? "Missing column" : found.column.label || readableName(found.column.name)}
      {found?.table.role === "input" && found.column.required && <span style={{ color: "var(--danger)" }}> *</span>}
    </>
  )
}

/** What a cell holds while building: a hint at its type. */
function CellHint({ found }: { found: Found }) {
  if (found === null || found.table.role === "output") return null
  if (found.column.index) return <span className="tabular-nums">1</span>
  if (found.column.options.length > 0) return <ChevronDown size={13} aria-hidden="true" />
  if (found.column.type === "date") return <Calendar size={13} aria-hidden="true" />
  if (isNumeric(found.column.type)) return <span className="tabular-nums">0</span>
  return null
}

const cellStyle = (found: Found) => ({
  border: "1px solid var(--border)",
  background: found?.table.role === "output" ? "var(--bg-elevated)" : "var(--bg-input)",
  color: "var(--text-secondary)",
})

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
        <FieldBoxes fields={fields} columns={widget.columns} />
      ) : (
        <FieldGrid fields={fields} rows={widget.rows} />
      )}
    </div>
  )
}

/** One-row columns as boxes, labels above, `columns` across and as many rows as it takes. */
function FieldBoxes({ fields, columns }: { fields: ShownField[]; columns: number }) {
  return (
    <div
      className="grid min-h-0 flex-1 content-start gap-x-4 gap-y-3 overflow-hidden p-3"
      style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
    >
      {fields.map(({ key, found }) => (
        <div key={key} className="flex min-w-0 flex-col gap-1" title={fieldTitle(found)}>
          <span className="truncate text-xs" style={{ color: labelColor(found) }}>
            <FieldLabel found={found} />
          </span>
          {found?.column.type === "bool" && !found.column.index ? (
            <div className="flex h-9 items-center">
              <span className="size-4 rounded" style={{ border: "1px solid var(--border-strong)", background: "var(--bg-input)" }} />
            </div>
          ) : (
            <div className="flex h-9 items-center justify-end rounded-md px-2.5" style={cellStyle(found)}>
              <CellHint found={found} />
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

/** Many-row columns as a grid of `rows` rows, numbered, its index column counting them. */
function FieldGrid({ fields, rows }: { fields: ShownField[]; rows: number }) {
  return (
    <div className="min-h-0 flex-1 overflow-hidden">
      <table className="w-full border-collapse text-xs">
        <thead>
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
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }, (_, row) => (
            <tr key={row} style={{ borderTop: "1px solid var(--border)" }}>
              <td className="text-center" style={{ color: "var(--text-secondary)" }}>
                {row + 1}
              </td>
              {fields.map(({ key, found }) => (
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
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
