/** A schema column's details: its label, help, rules, format, allowed values and default (specs/workbench). */
import { useId } from "react"
import type { SchemaColumn, SchemaTable } from "../../api/types"
import { COMPACT_FIELD_CLASS, CommittedTextField, ConfigCheckbox, INPUT_STYLE, ValidatedTextField } from "../../components/form"
import { isOutputColumn, tableColumns } from "../../utils/quoteIdentity"
import { PIVOT_DECIMAL_PLACES_MAX, PIVOT_NUMBER_FORMATS } from "../../panels/explore/pivotConfig"
import { NUMBER_FORMAT_LABELS } from "../../panels/explore/pivotNumberFormat"
import { isNumeric } from "../columnTypes"
import { ColumnDatabaseDetails } from "../ColumnDatabaseDetails"
import { AllowedField } from "./AllowedField"
import { DefaultField } from "./DefaultField"
import { IndexKindSelect } from "./IndexControl"

const DECIMAL_PLACES = Array.from({ length: PIVOT_DECIMAL_PLACES_MAX + 1 }, (_, places) => places)

/**
 * A column's label, group and help; whether it needs a value before a quote is marked final,
 * in an input or an output table; in an input table, what a new quote starts with in it (in a
 * one-row table), what an empty cell shows and the other rules its value must meet; for a
 * number, how the sheets show it. The index's kind is chosen here too. Always filled, it takes no Required, range or
 * default: a typed one among the inputs takes what an empty cell shows, an index of values
 * the allowed values that give its rows, and an Integer one other than numbered its format.
 * The boxes hold no hint text: the docs say what each is.
 */
export function ColumnDetails({
  table,
  column,
  managed,
  readOnly,
  set,
}: {
  table: SchemaTable
  column: SchemaColumn
  managed: boolean
  /** In Preview: every field shows its value and none takes a change. */
  readOnly: boolean
  set: (patch: Partial<SchemaColumn>) => void
}) {
  // The index takes no rules, nor does a column the app sets.
  const input = !isOutputColumn(table, column) && column.index === null && !managed
  // Required, which marking a quote final checks, an output takes too.
  const requirable = column.index === null && !managed
  const takesText = column.type !== "bool" && column.type !== "date"
  // What an empty cell shows: an input's, a typed index's among them.
  const placeholder = takesText && (input || (column.index === "typed" && !isOutputColumn(table, column)))
  // Allowed values: an input's, and an index of values', which give its rows.
  const allowed = takesText && (input || column.index === "values")
  // The groups the table's columns are in, offered as one is typed.
  const groupsId = useId()
  const groups = [...new Set(tableColumns(table).map((other) => other.group).filter((group) => group !== ""))]
  // General with automatic decimal places shows a number as it is, separators and all.
  const grouped = column.number_format !== "general" || column.decimal_places !== null
  const numberOrNothing = (text: string): string | null =>
    text.trim() === "" || Number.isFinite(Number(text)) ? null : "Enter a number."
  const bound = (text: string): number | null => (text.trim() === "" ? null : Number(text))
  return (
    <fieldset
      disabled={readOnly}
      className="mr-12 ml-7 grid max-w-[32rem] min-w-0 grid-cols-[4.75rem_minmax(0,1fr)] items-center gap-x-2 gap-y-1 rounded-md px-2 py-1.5 text-[11px]"
      style={{ border: "1px solid var(--border)", background: "var(--bg-base)" }}
    >
      <ColumnDatabaseDetails table={table} column={column} readOnly={readOnly} />
      {column.index !== null && (
        <>
          <span style={{ color: "var(--text-muted)" }}>Index values</span>
          <IndexKindSelect tableId={table.name} column={column} />
        </>
      )}
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
      {isNumeric(column.type) && column.index !== "numbered" && (
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
      {placeholder && (
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
      {requirable && (
        <>
          <span style={{ color: "var(--text-muted)" }}>Required</span>
          <ConfigCheckbox label={`${column.name} is required`} checked={column.required} onChange={(required) => set({ required })} />
        </>
      )}
      {input && isNumeric(column.type) && (
        <>
          <span style={{ color: "var(--text-muted)" }}>Range</span>
          <div className="flex items-center gap-1.5">
            <ValidatedTextField
              dataTestId={`schema-column-${column.name}-min`}
              value={column.min === null ? "" : String(column.min)}
              onCommit={(text) => set({ min: bound(text) })}
              validate={numberOrNothing}
              containerClassName="w-24"
              className={`${COMPACT_FIELD_CLASS} w-full`}
              style={INPUT_STYLE}
            />
            <span style={{ color: "var(--text-muted)" }}>to</span>
            <ValidatedTextField
              dataTestId={`schema-column-${column.name}-max`}
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
      {allowed && (
        <>
          <span style={{ color: "var(--text-muted)" }}>Allowed</span>
          <AllowedField tableId={table.name} column={column} set={set} />
        </>
      )}
    </fieldset>
  )
}
