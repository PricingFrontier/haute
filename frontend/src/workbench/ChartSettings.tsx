/**
 * A Chart's settings in its panel (specs/workbench): its type, what labels its rows while
 * it plots many-row tables, where its legend sits, and whether it is stacked, horizontal
 * (neither for a pie) and shows its values as data labels. Each change is one edit.
 */
import type { ChartWidget, FormSpec } from "../api/types"
import { ConfigCheckbox, FIELD_CLASS, INPUT_STYLE, LabelledRow } from "../components/form"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import { chartGrain, fieldColumn, labelColumns, readableName, updateWidget, type ChartOptions } from "../utils/workbenchForm"

const TYPES: ReadonlyArray<{ value: ChartOptions["chart_type"]; label: string }> = [
  { value: "column", label: "Column" },
  { value: "line", label: "Line" },
  { value: "area", label: "Area" },
  { value: "pie", label: "Pie" },
]
const LEGENDS: ReadonlyArray<{ value: ChartOptions["legend"]; label: string }> = [
  { value: "bottom", label: "Bottom" },
  { value: "top", label: "Top" },
  { value: "left", label: "Left" },
  { value: "right", label: "Right" },
  { value: "none", label: "None" },
]
/** The Rows select's value while no column labels the rows: they are numbered. */
const ROW_NUMBER = ""

/** A column's value in the Rows select: its table and column ids. */
const columnValue = (table: string, column: string): string => JSON.stringify([table, column])

export default function ChartSettings({ form, widget }: { form: FormSpec; widget: ChartWidget }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const set = (patch: Partial<ChartOptions>) => change((spec) => updateWidget(spec, widget.id, patch))
  const grain = chartGrain(form.schema, widget)
  const labels = labelColumns(form, widget)
  const pie = widget.chart_type === "pie"
  const category = widget.category === null ? ROW_NUMBER : columnValue(widget.category.table, widget.category.column)
  // A column labelling the rows that is no longer one they can be labelled by stays chosen until
  // another is, as the problem on the Chart says.
  const offered = labels.some(({ table, columns }) => columns.some((column) => columnValue(table.id, column.id) === category))
  const kept = widget.category === null || offered ? null : fieldColumn(form, widget.category)

  return (
    <>
      <LabelledRow label="Type">
        <select
          aria-label="Chart type"
          value={widget.chart_type}
          onChange={(event) => set({ chart_type: event.target.value as ChartOptions["chart_type"] })}
          className={FIELD_CLASS}
          style={INPUT_STYLE}
        >
          {TYPES.map(({ value, label }) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </LabelledRow>
      {grain !== null && grain !== "one" && (
        <LabelledRow label="Rows">
          <select
            aria-label="Label rows by"
            value={category}
            onChange={(event) => {
              const value = event.target.value
              if (value === ROW_NUMBER) {
                set({ category: null })
                return
              }
              const [table, column] = JSON.parse(value) as [string, string]
              set({ category: { table, column } })
            }}
            className={`${FIELD_CLASS} flex-1`}
            style={INPUT_STYLE}
          >
            <option value={ROW_NUMBER}>Row number</option>
            {labels.map(({ table, columns }) => (
              <optgroup key={table.id} label={table.name}>
                {columns.map((column) => (
                  <option key={column.id} value={columnValue(table.id, column.id)}>
                    {column.label || readableName(column.name)}
                  </option>
                ))}
              </optgroup>
            ))}
            {widget.category !== null && !offered && (
              <option value={category}>{kept === null ? "Missing column" : kept.column.label || readableName(kept.column.name)}</option>
            )}
          </select>
        </LabelledRow>
      )}
      <LabelledRow label="Legend">
        <select
          aria-label="Legend"
          value={widget.legend}
          onChange={(event) => set({ legend: event.target.value as ChartOptions["legend"] })}
          className={FIELD_CLASS}
          style={INPUT_STYLE}
        >
          {LEGENDS.map(({ value, label }) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </LabelledRow>
      <LabelledRow>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <ConfigCheckbox label="Stacked" checked={widget.stacked} disabled={pie} onChange={(stacked) => set({ stacked })} />
          <ConfigCheckbox
            label="Horizontal"
            checked={widget.orientation === "horizontal"}
            disabled={pie}
            onChange={(horizontal) => set({ orientation: horizontal ? "horizontal" : "vertical" })}
          />
          <ConfigCheckbox label="Data labels" checked={widget.data_labels} onChange={(data_labels) => set({ data_labels })} />
        </div>
      </LabelledRow>
    </>
  )
}
