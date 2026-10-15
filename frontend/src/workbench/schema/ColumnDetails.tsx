/** A schema column's details: its label, help, rules, format, allowed values and default (specs/workbench). */
import { useId } from "react"
import type { SchemaColumn, SchemaTable } from "../../api/types"
import { COMPACT_FIELD_CLASS, CommittedTextField, ConfigCheckbox, INPUT_STYLE, ValidatedTextField } from "../../components/form"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { setRowPerValue } from "../../utils/workbenchForm"
import { PIVOT_DECIMAL_PLACES_MAX, PIVOT_NUMBER_FORMATS } from "../../panels/explore/pivotConfig"
import { NUMBER_FORMAT_LABELS } from "../../panels/explore/pivotNumberFormat"
import { isNumeric } from "../columnTypes"
import { AllowedField } from "./AllowedField"
import { DefaultField } from "./DefaultField"

const DECIMAL_PLACES = Array.from({ length: PIVOT_DECIMAL_PLACES_MAX + 1 }, (_, places) => places)

/**
 * A column's label, group and help; in an input table, what a new quote starts with in it
 * (in a one-row table), what an empty cell shows, the rules its value must meet and, in a
 * many-row table while it has allowed values, whether the table has a row for each; for a
 * number, how the sheets show it. The boxes hold no hint text: the docs say what each is.
 */
export function ColumnDetails({
  table,
  column,
  managed,
  set,
}: {
  table: SchemaTable
  column: SchemaColumn
  managed: boolean
  set: (patch: Partial<SchemaColumn>) => void
}) {
  const change = useWorkbenchFormStore((s) => s.change)
  // The index takes no rules, nor does a column the app sets: nothing is typed into them.
  const input = table.role === "input" && !column.index && !managed
  // The groups the table's columns are in, offered as one is typed.
  const groupsId = useId()
  const groups = [...new Set(table.columns.map((other) => other.group).filter((group) => group !== ""))]
  // General with automatic decimal places shows a number as it is, separators and all.
  const grouped = column.number_format !== "general" || column.decimal_places !== null
  const numberOrNothing = (text: string): string | null =>
    text.trim() === "" || Number.isFinite(Number(text)) ? null : "Enter a number."
  const bound = (text: string): number | null => (text.trim() === "" ? null : Number(text))
  return (
    <div
      className="mr-12 ml-7 grid grid-cols-[4.75rem_minmax(0,1fr)] items-center gap-x-2 gap-y-1 rounded-md px-2 py-1.5 text-[11px]"
      style={{ border: "1px solid var(--border)", background: "var(--bg-base)" }}
    >
      <span style={{ color: "var(--text-muted)" }}>Label</span>
      <CommittedTextField
        aria-label={`Label of ${column.name}`}
        value={column.label}
        onCommit={(label) => set({ label })}
        className={`${COMPACT_FIELD_CLASS} w-full`}
        style={INPUT_STYLE}
      />
      <span style={{ color: "var(--text-muted)" }}>Group</span>
      <CommittedTextField
        aria-label={`Group of ${column.name}`}
        value={column.group}
        list={groupsId}
        onCommit={(group) => set({ group: group.trim() })}
        className={`${COMPACT_FIELD_CLASS} w-full`}
        style={INPUT_STYLE}
      />
      <datalist id={groupsId}>
        {groups.map((group) => (
          <option key={group} value={group} />
        ))}
      </datalist>
      <span style={{ color: "var(--text-muted)" }}>Help</span>
      <CommittedTextField
        aria-label={`Help of ${column.name}`}
        value={column.help}
        onCommit={(help) => set({ help })}
        className={`${COMPACT_FIELD_CLASS} w-full`}
        style={INPUT_STYLE}
      />
      {isNumeric(column.type) && !column.index && (
        <>
          <span style={{ color: "var(--text-muted)" }}>Format</span>
          <div className="flex items-center gap-1.5">
            <select
              aria-label={`Format of ${column.name}`}
              value={column.number_format}
              onChange={(e) => set({ number_format: e.target.value as SchemaColumn["number_format"] })}
              className={COMPACT_FIELD_CLASS}
              style={INPUT_STYLE}
            >
              {PIVOT_NUMBER_FORMATS.map((format) => (
                <option key={format} value={format}>
                  {NUMBER_FORMAT_LABELS[format]}
                </option>
              ))}
            </select>
            <select
              aria-label={`Decimal places of ${column.name}`}
              value={column.decimal_places ?? "automatic"}
              onChange={(e) => set({ decimal_places: e.target.value === "automatic" ? null : Number(e.target.value) })}
              title="Decimal places"
              className={COMPACT_FIELD_CLASS}
              style={INPUT_STYLE}
            >
              <option value="automatic">Auto decimals</option>
              {DECIMAL_PLACES.map((places) => (
                <option key={places} value={places}>
                  {places} {places === 1 ? "decimal" : "decimals"}
                </option>
              ))}
            </select>
            <ConfigCheckbox
              label="Separators (1,000)"
              checked={grouped && column.use_grouping}
              // Ticked under General, as an Explore pivot does, it becomes a Number.
              onChange={(useGrouping) => set(useGrouping && !grouped ? { number_format: "number", use_grouping: true } : { use_grouping: useGrouping })}
            />
          </div>
        </>
      )}
      {input && table.rows === "one" && (
        <>
          <span style={{ color: "var(--text-muted)" }}>Default</span>
          <DefaultField column={column} set={set} />
        </>
      )}
      {input && column.type !== "bool" && column.type !== "date" && (
        <>
          <span style={{ color: "var(--text-muted)" }}>Placeholder</span>
          <CommittedTextField
            aria-label={`Placeholder of ${column.name}`}
            value={column.placeholder}
            onCommit={(placeholder) => set({ placeholder })}
            className={`${COMPACT_FIELD_CLASS} w-full`}
            style={INPUT_STYLE}
          />
        </>
      )}
      {input && (
        <>
          <span style={{ color: "var(--text-muted)" }}>Required</span>
          <ConfigCheckbox label={`${column.name} is required`} checked={column.required} onChange={(required) => set({ required })} />
          {isNumeric(column.type) && (
            <>
              <span style={{ color: "var(--text-muted)" }}>Range</span>
              <div className="flex items-center gap-1.5">
                <ValidatedTextField
                  dataTestId={`schema-column-${column.id}-min`}
                  value={column.min === null ? "" : String(column.min)}
                  onCommit={(text) => set({ min: bound(text) })}
                  validate={numberOrNothing}
                  containerClassName="w-24"
                  className={`${COMPACT_FIELD_CLASS} w-full`}
                  style={INPUT_STYLE}
                />
                <span style={{ color: "var(--text-muted)" }}>to</span>
                <ValidatedTextField
                  dataTestId={`schema-column-${column.id}-max`}
                  value={column.max === null ? "" : String(column.max)}
                  onCommit={(text) => set({ max: bound(text) })}
                  validate={numberOrNothing}
                  containerClassName="w-24"
                  className={`${COMPACT_FIELD_CLASS} w-full`}
                  style={INPUT_STYLE}
                />
              </div>
            </>
          )}
          {column.type !== "bool" && column.type !== "date" && (
            <>
              <span style={{ color: "var(--text-muted)" }}>Allowed</span>
              <AllowedField tableId={table.id} column={column} set={set} />
            </>
          )}
          {table.rows === "many" && column.type !== "bool" && column.type !== "date" && column.options.length > 0 && (
            <>
              <span style={{ color: "var(--text-muted)" }}>Rows</span>
              <ConfigCheckbox
                label="A row for each"
                checked={column.row_per_value}
                onChange={(on) => change((f) => setRowPerValue(f, table.id, column.id, on))}
              />
            </>
          )}
        </>
      )}
    </div>
  )
}
