/** A Text Box's panel (specs/workbench). */
import type { FormSpec, TextBoxWidget } from "../../api/types"
import { FIELD_CLASS, INPUT_STYLE, LabelledRow } from "../../components/form"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { columnKey, takesNothingTyped } from "../../utils/sheetVisibility"
import { fieldColumn, readableName, setTextBoxField, textBoxColumns, updateWidget } from "../../utils/workbenchForm"
import { ShowWhenEditor } from "../ShowWhenEditor"
import { ComponentNameField } from "./ComponentNameField"
import { Section } from "./Section"

/**
 * A Text Box's panel: its name; its Field, a select of the one-row tables' Text inputs by
 * table, each by its label (`textBoxColumns`), a column it holds that is no longer one of
 * them shown first, disabled, by its label or "Missing column"; and when it shows.
 */
export function TextBoxSettings({ form, widget }: { form: FormSpec; widget: TextBoxWidget }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const offered = textBoxColumns(form.schema)
  const held = widget.fields[0] ?? null
  const heldKey = held === null ? "" : columnKey(held.table, held.column)
  const offers = (key: string) => offered.some(({ table, columns }) => columns.some((column) => columnKey(table.name, column.name) === key))
  const found = held === null ? null : fieldColumn(form, held)
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto px-3 py-3 text-xs">
      <ComponentNameField form={form} widget={widget} />
      <Section title="Field">
        <LabelledRow label="Column">
          <select
            aria-label="Text Box column"
            value={heldKey}
            onChange={(event) => {
              const key = event.target.value
              const chosen = offered.flatMap(({ table, columns }) => columns.map((column) => ({ table: table.name, column: column.name }))).find((field) => columnKey(field.table, field.column) === key)
              change((spec) => setTextBoxField(spec, widget.id, chosen ?? null))
            }}
            className={`${FIELD_CLASS} min-w-0 flex-1`}
            style={INPUT_STYLE}
          >
            <option value="">Choose a Text column</option>
            {held !== null && !offers(heldKey) && (
              <option value={heldKey} disabled>
                {found === null ? "Missing column" : found.column.label || readableName(found.column.name)}
              </option>
            )}
            {offered.map(({ table, columns }) => (
              <optgroup key={table.name} label={table.name}>
                {columns.map((column) => (
                  <option key={column.name} value={columnKey(table.name, column.name)}>
                    {column.label || readableName(column.name)}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </LabelledRow>
      </Section>
      <Section title="Show when">
        <ShowWhenEditor form={form} showWhen={widget.show_when} priced={takesNothingTyped(form, widget)} onChange={(next) => change((spec) => updateWidget(spec, widget.id, { show_when: next }))} />
      </Section>
    </div>
  )
}
