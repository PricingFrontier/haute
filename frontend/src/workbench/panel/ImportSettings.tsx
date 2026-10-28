/** An Import's panel (specs/workbench). */
import { X } from "lucide-react"
import type { FormSpec, ImportWidget, SchemaColumn, SchemaTable, SqlImport } from "../../api/types"
import { CommittedTextArea, CommittedTextField, FIELD_CLASS, INPUT_STYLE, LabelledRow, SelectRow, ValidatedTextField, wholeNumber } from "../../components/form"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { columnKey } from "../../utils/sheetVisibility"
import {
  bindParameter,
  fieldColumn,
  HEADER_ROW_MAX,
  importColumns,
  parameterColumns,
  queryParameters,
  readableName,
  setColumnName,
  setImportFormat,
  setImportMode,
  setImportQuery,
  setImportTable,
  updateImportSource,
} from "../../utils/workbenchForm"
import { COLUMN_TYPES } from "../columnTypes"
import { ButtonPanes, FormatToggle } from "./ButtonPanes"
import { Section } from "./Section"

const IMPORT_DELIMITERS: ReadonlyArray<{ value: Extract<ImportWidget["source"], { format: "csv" }>["delimiter"]; label: string }> = [
  { value: "auto", label: "Auto" },
  { value: "comma", label: "Comma" },
  { value: "semicolon", label: "Semicolon" },
  { value: "tab", label: "Tab" },
]

const IMPORT_MODES: ReadonlyArray<{ value: ImportWidget["mode"]; label: string }> = [
  { value: "replace", label: "Replace" },
  { value: "append", label: "Append" },
  { value: "update", label: "Update by key" },
]

const columnLabel = (column: SchemaColumn): string => column.label || readableName(column.name)

const REMOVE_CLASS = "icon-danger-btn focus-ring shrink-0 rounded p-0.5"

/**
 * An Import's panel in its panes (`ButtonPanes`), Config holding its Format, its format's
 * settings, the input table it fills, Into, how it fills a many-row one, Rows, and the
 * names its columns are read from, Columns.
 */
export function ImportSettings({ form, widget }: { form: FormSpec; widget: ImportWidget }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const { source } = widget
  const inputs = form.schema.tables.filter((table) => table.role === "input")
  const table = form.schema.tables.find((candidate) => candidate.id === widget.table)
  const offered = inputs.some((candidate) => candidate.id === widget.table)
  return (
    <ButtonPanes form={form} widget={widget}>
      <FormatToggle widget={widget} format={source.format} onChange={(format) => change((spec) => setImportFormat(spec, widget.id, format))} />
      {source.format === "csv" && (
        <Section title="File">
          <SelectRow
            label="Delimiter"
            value={source.delimiter}
            choices={IMPORT_DELIMITERS}
            onChange={(delimiter) => change((spec) => updateImportSource(spec, widget.id, { delimiter }))}
          />
        </Section>
      )}
      {source.format === "excel" && (
        <Section title="Workbook">
          <LabelledRow label="Sheet">
            <CommittedTextField
              aria-label="Sheet"
              value={source.sheet}
              placeholder="First"
              onCommit={(sheet) => change((spec) => updateImportSource(spec, widget.id, { sheet }))}
              className={`${FIELD_CLASS} min-w-0 flex-1`}
              style={INPUT_STYLE}
            />
          </LabelledRow>
          <LabelledRow label="Header row">
            <ValidatedTextField
              dataTestId="import-header-row"
              value={String(source.header_row)}
              onCommit={(text) => change((spec) => updateImportSource(spec, widget.id, { header_row: Number(text) }))}
              validate={wholeNumber({ min: 1, max: HEADER_ROW_MAX })}
              containerClassName="w-16"
              className={`${FIELD_CLASS} w-full tabular-nums`}
              style={INPUT_STYLE}
            />
          </LabelledRow>
        </Section>
      )}
      {source.format === "sql" && <DatabaseSettings form={form} widget={widget} source={source} />}
      <Section title="Into">
        <select
          aria-label="Into"
          value={widget.table ?? ""}
          onChange={(event) => change((spec) => setImportTable(spec, widget.id, event.target.value === "" ? null : event.target.value))}
          className={`${FIELD_CLASS} w-full`}
          style={INPUT_STYLE}
        >
          {widget.table !== null && !offered && (
            <option value={widget.table} disabled>
              {table === undefined ? "Missing table" : table.name}
            </option>
          )}
          <option value="">None</option>
          {inputs.map((candidate) => (
            <option key={candidate.id} value={candidate.id}>
              {candidate.name}
            </option>
          ))}
        </select>
      </Section>
      {table?.role === "input" && table.rows === "many" && (
        <Section title="Rows">
          <select
            aria-label="Rows"
            value={widget.mode}
            onChange={(event) => change((spec) => setImportMode(spec, widget.id, event.target.value as ImportWidget["mode"]))}
            className={`${FIELD_CLASS} w-full`}
            style={INPUT_STYLE}
          >
            {IMPORT_MODES.map(({ value, label }) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Section>
      )}
      {table?.role === "input" && <ColumnNames widget={widget} table={table} />}
    </ButtonPanes>
  )
}

/**
 * A database's Connection and Query, and Parameters: a row for each `:name` the query
 * holds, each given a one-row input table's column, and one for each column given a
 * parameter the query does not hold, with a cross that takes it away.
 */
function DatabaseSettings({ form, widget, source }: { form: FormSpec; widget: ImportWidget; source: SqlImport }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const held = queryParameters(source.query)
  const strays = source.parameters.filter((given) => !held.includes(given.name)).map((given) => given.name)
  const tables = form.schema.tables.filter((table) => parameterColumns(table).length > 0)
  return (
    <>
      <Section title="Connection">
        <CommittedTextField
          aria-label="Connection"
          value={source.connection}
          placeholder="None"
          onCommit={(connection) => change((spec) => updateImportSource(spec, widget.id, { connection }))}
          className={`${FIELD_CLASS} w-full font-mono`}
          style={INPUT_STYLE}
        />
      </Section>
      <Section title="Query">
        <CommittedTextArea
          aria-label="Query"
          value={source.query}
          rows={5}
          spellCheck={false}
          onCommit={(query) => change((spec) => setImportQuery(spec, widget.id, query))}
          className={`${FIELD_CLASS} w-full resize-y font-mono`}
          style={INPUT_STYLE}
        />
      </Section>
      {held.length + strays.length > 0 && (
        <Section title="Parameters">
          {[...held, ...strays].map((name) => (
            <ParameterRow key={name} form={form} widget={widget} source={source} tables={tables} name={name} held={held.includes(name)} />
          ))}
        </Section>
      )}
    </>
  )
}

function ParameterRow({
  form,
  widget,
  source,
  tables,
  name,
  held,
}: {
  form: FormSpec
  widget: ImportWidget
  source: SqlImport
  tables: readonly SchemaTable[]
  name: string
  /** Whether the query holds it. */
  held: boolean
}) {
  const change = useWorkbenchFormStore((s) => s.change)
  const given = source.parameters.find((parameter) => parameter.name === name)
  const key = given === undefined ? "" : columnKey(given.table, given.column)
  const choices = new Map(tables.flatMap((table) => parameterColumns(table).map((column) => [columnKey(table.id, column.id), { table: table.id, column: column.id }] as const)))
  const found = given === undefined ? null : fieldColumn(form, given)
  return (
    <div className="flex items-center gap-1.5">
      <span className="w-20 shrink-0 truncate font-mono" title={`:${name}`}>
        :{name}
      </span>
      <select
        aria-label={`:${name} column`}
        value={key}
        onChange={(event) => change((spec) => bindParameter(spec, widget.id, name, choices.get(event.target.value) ?? null))}
        className={`${FIELD_CLASS} min-w-0 flex-1`}
        style={INPUT_STYLE}
      >
        {key !== "" && !choices.has(key) && (
          <option value={key} disabled>
            {found === null ? "Missing column" : columnLabel(found.column)}
          </option>
        )}
        <option value="">None</option>
        {tables.map((table) => (
          <optgroup key={table.id} label={table.name}>
            {parameterColumns(table).map((column) => (
              <option key={column.id} value={columnKey(table.id, column.id)}>
                {columnLabel(column)}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      {!held && (
        <button type="button" onClick={() => change((spec) => bindParameter(spec, widget.id, name, null))} aria-label={`Remove :${name}`} className={REMOVE_CLASS}>
          <X size={12} aria-hidden="true" />
        </button>
      )}
    </div>
  )
}

/**
 * Columns: a row for each of the table's columns an underwriter types, its type's icon,
 * its label and the name it is read from, showing its own while it has none; and one for
 * each name given a column it cannot fill, with a cross that takes it away.
 */
function ColumnNames({ widget, table }: { widget: ImportWidget; table: SchemaTable }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const columns = importColumns(table)
  const strays = widget.column_names.filter((named) => !columns.some((column) => column.id === named.column))
  const commit = (column: string, name: string) => change((spec) => setColumnName(spec, widget.id, column, name))
  return (
    <Section title="Columns" aside={`${widget.column_names.length} named`}>
      {columns.map((column) => {
        const type = COLUMN_TYPES[column.type]
        const label = columnLabel(column)
        return (
          <div key={column.id} className="flex items-center gap-1.5">
            <span title={type.label} className={`flex size-4 shrink-0 items-center justify-center ${type.color}`}>
              <type.icon size={12} aria-hidden="true" />
            </span>
            <span className="w-24 shrink-0 truncate" title={label}>
              {label}
            </span>
            <CommittedTextField
              aria-label={`${label} is read from`}
              value={widget.column_names.find((named) => named.column === column.id)?.name ?? ""}
              placeholder={column.name}
              onCommit={(name) => commit(column.id, name)}
              className={`${FIELD_CLASS} min-w-0 flex-1`}
              style={INPUT_STYLE}
            />
          </div>
        )
      })}
      {strays.map((named) => {
        const column = table.columns.find((candidate) => candidate.id === named.column)
        const label = column === undefined ? "Missing column" : columnLabel(column)
        return (
          <div key={named.column} className="flex items-center gap-1.5">
            <span className="size-4 shrink-0" aria-hidden="true" />
            <span className="w-24 shrink-0 truncate" style={{ color: "var(--warning)" }} title={label}>
              {label}
            </span>
            <span className="min-w-0 flex-1 truncate">{named.name}</span>
            <button type="button" onClick={() => commit(named.column, "")} aria-label={`Remove the name given ${label}`} className={REMOVE_CLASS}>
              <X size={12} aria-hidden="true" />
            </button>
          </div>
        )
      })}
    </Section>
  )
}
