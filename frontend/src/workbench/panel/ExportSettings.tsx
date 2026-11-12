/** An Export's panel (specs/workbench). */
import { X } from "lucide-react"
import type { CsvExport, ExcelCellsExport, ExportCell, ExportWidget, FieldRef, FormSpec, SchemaColumn, SchemaTable } from "../../api/types"
import { CommittedTextField, ConfigCheckbox, FIELD_CLASS, INPUT_STYLE, LabelledRow, SelectRow } from "../../components/form"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { columnKey } from "../../utils/sheetVisibility"
import {
  addExportCell,
  cellColumns,
  excelLayout,
  exportFormatShown,
  fieldColumn,
  readableName,
  removeExportCell,
  setExcelLayout,
  setExportCellColumn,
  setExportFormat,
  setTableName,
  toggleExportTable,
  updateExportCell,
  updateExportTarget,
  type ExcelLayout,
} from "../../utils/workbenchForm"
import { ButtonPanes, FormatToggle } from "./ButtonPanes"
import { Section } from "./Section"

const EXPORT_DELIMITERS: ReadonlyArray<{ value: CsvExport["delimiter"]; label: string }> = [
  { value: "comma", label: "Comma" },
  { value: "semicolon", label: "Semicolon" },
  { value: "tab", label: "Tab" },
]

const HEADERS: ReadonlyArray<{ value: CsvExport["headers"]; label: string }> = [
  { value: "names", label: "Names" },
  { value: "labels", label: "Labels" },
]

const VALUES: ReadonlyArray<{ value: CsvExport["values"]; label: string }> = [
  { value: "raw", label: "Raw" },
  { value: "formatted", label: "Formatted" },
]

const EXCEL_LAYOUTS: ReadonlyArray<{ value: ExcelLayout; label: string }> = [
  { value: "tables", label: "Sheet per table" },
  { value: "cells", label: "Cells" },
]

const REMOVE_CLASS = "icon-danger-btn focus-ring shrink-0 rounded p-0.5"

const columnLabel = (column: SchemaColumn): string => column.label || readableName(column.name)

/**
 * An Export's panel in its panes (`ButtonPanes`), Config holding its Format; to Excel, its
 * Layout; a file's name, a CSV's delimiter and, but for a workbook of cells, its headers and
 * values, or a database's connection; then a workbook of cells' Cells, or the Tables it
 * exports, each, to a database, beside the name of the table it is written to.
 */
export function ExportSettings({ form, widget }: { form: FormSpec; widget: ExportWidget }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const { target } = widget
  const set = (patch: Parameters<typeof updateExportTarget>[2]) => change((spec) => updateExportTarget(spec, widget.id, patch))
  const strays = widget.tables.filter((id) => !form.schema.tables.some((table) => table.name === id))
  return (
    <ButtonPanes form={form} widget={widget}>
      <FormatToggle widget={widget} format={exportFormatShown(target.format)} onChange={(format) => change((spec) => setExportFormat(spec, widget.id, format))} />
      {target.format === "sql" ? (
        <Section title="Connection">
          <CommittedTextField
            aria-label="Connection"
            value={target.connection}
            placeholder="None"
            onCommit={(connection) => set({ connection })}
            className={`${FIELD_CLASS} w-full font-mono`}
            style={INPUT_STYLE}
          />
        </Section>
      ) : (
        <Section title="File">
          {(target.format === "excel" || target.format === "excel_cells") && (
            <SelectRow label="Layout" value={excelLayout(target)} choices={EXCEL_LAYOUTS} onChange={(layout) => change((spec) => setExcelLayout(spec, widget.id, layout))} />
          )}
          <LabelledRow label="Name">
            <CommittedTextField
              aria-label="File name"
              value={target.file_name}
              onCommit={(file_name) => set({ file_name })}
              className={`${FIELD_CLASS} min-w-0 flex-1 font-mono`}
              style={INPUT_STYLE}
            />
          </LabelledRow>
          {target.format === "csv" && <SelectRow label="Delimiter" value={target.delimiter} choices={EXPORT_DELIMITERS} onChange={(delimiter) => set({ delimiter })} />}
          {target.format !== "excel_cells" && (
            <>
              <SelectRow label="Headers" value={target.headers} choices={HEADERS} onChange={(headers) => set({ headers })} />
              <SelectRow label="Values" value={target.values} choices={VALUES} onChange={(values) => set({ values })} />
            </>
          )}
        </Section>
      )}
      {target.format === "excel_cells" ? (
        <ExportCells form={form} widget={widget} target={target} />
      ) : (
        <Section title="Tables" aside={`${widget.tables.length} exported`}>
          {form.schema.tables.map((table) => {
            const exported = widget.tables.includes(table.name)
            return (
              <div key={table.name} className="flex items-center gap-2">
                <div className="min-w-0 flex-1 font-mono">
                  <ConfigCheckbox label={table.name} checked={exported} onChange={() => change((spec) => toggleExportTable(spec, widget.id, table.name))} />
                </div>
                {target.format === "sql" && exported && (
                  <CommittedTextField
                    aria-label={`${table.name} is written to`}
                    value={target.table_names.find((named) => named.table === table.name)?.name ?? ""}
                    placeholder={table.name}
                    onCommit={(name) => change((spec) => setTableName(spec, widget.id, table.name, name))}
                    className={`${FIELD_CLASS} w-32 font-mono`}
                    style={INPUT_STYLE}
                  />
                )}
              </div>
            )
          })}
          {strays.map((id) => (
            <div key={id} className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate" style={{ color: "var(--warning)" }}>
                Missing table
              </span>
              <button type="button" onClick={() => change((spec) => toggleExportTable(spec, widget.id, id))} aria-label="Stop exporting the missing table" className="icon-danger-btn focus-ring shrink-0 rounded p-0.5">
                <X size={12} aria-hidden="true" />
              </button>
            </div>
          ))}
        </Section>
      )}
    </ButtonPanes>
  )
}

/** The columns a cell can be given, by table, each by its label (`cellColumns`). */
function ColumnOptions({ tables }: { tables: readonly SchemaTable[] }) {
  return (
    <>
      {tables.map((table) => (
        <optgroup key={table.name} label={table.name}>
          {cellColumns(table).map((column) => (
            <option key={column.name} value={columnKey(table.name, column.name)}>
              {columnLabel(column)}
            </option>
          ))}
        </optgroup>
      ))}
    </>
  )
}

/**
 * A workbook of cells' Cells: under a heading row, a row for each cell, its column, its
 * sheet and its address, and a cross; then Add a cell, a select of the columns a cell can
 * be given, held at its first option.
 */
function ExportCells({ form, widget, target }: { form: FormSpec; widget: ExportWidget; target: ExcelCellsExport }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const tables = form.schema.tables.filter((table) => cellColumns(table).length > 0)
  const choices = new Map(tables.flatMap((table) => cellColumns(table).map((column) => [columnKey(table.name, column.name), { table: table.name, column: column.name }] as const)))
  return (
    <Section title="Cells" aside={`${target.cells.length} written`}>
      {target.cells.length > 0 && (
        <div className="flex items-center gap-1.5" style={{ color: "var(--text-muted)" }} aria-hidden="true">
          <span className="min-w-0 flex-1">Field</span>
          <span className="w-20 shrink-0">Sheet</span>
          <span className="w-14 shrink-0">Cell</span>
          <span className="w-4 shrink-0" />
        </div>
      )}
      {target.cells.map((cell, index) => (
        <CellRow key={index} form={form} widget={widget} cell={cell} index={index} tables={tables} choices={choices} />
      ))}
      <select
        aria-label="Add a cell"
        value=""
        onChange={(event) => {
          const field = choices.get(event.target.value)
          if (field !== undefined) change((spec) => addExportCell(spec, widget.id, field))
        }}
        className={`${FIELD_CLASS} w-full`}
        style={INPUT_STYLE}
      >
        <option value="">Add a cell</option>
        <ColumnOptions tables={tables} />
      </select>
    </Section>
  )
}

function CellRow({
  form,
  widget,
  cell,
  index,
  tables,
  choices,
}: {
  form: FormSpec
  widget: ExportWidget
  cell: ExportCell
  index: number
  tables: readonly SchemaTable[]
  choices: ReadonlyMap<string, FieldRef>
}) {
  const change = useWorkbenchFormStore((s) => s.change)
  const key = columnKey(cell.table, cell.column)
  const found = fieldColumn(form, cell)
  const number = index + 1
  return (
    <div className="flex items-center gap-1.5">
      <select
        aria-label={`Cell ${number} field`}
        value={key}
        onChange={(event) => {
          const field = choices.get(event.target.value)
          if (field !== undefined) change((spec) => setExportCellColumn(spec, widget.id, index, field))
        }}
        className={`${FIELD_CLASS} min-w-0 flex-1`}
        style={INPUT_STYLE}
      >
        {!choices.has(key) && (
          <option value={key} disabled>
            {found === null ? "Missing column" : columnLabel(found.column)}
          </option>
        )}
        <ColumnOptions tables={tables} />
      </select>
      <CommittedTextField
        aria-label={`Cell ${number} sheet`}
        value={cell.sheet}
        onCommit={(sheet) => change((spec) => updateExportCell(spec, widget.id, index, { sheet }))}
        className={`${FIELD_CLASS} w-20 shrink-0`}
        style={INPUT_STYLE}
      />
      <CommittedTextField
        aria-label={`Cell ${number} address`}
        value={cell.cell}
        onCommit={(address) => change((spec) => updateExportCell(spec, widget.id, index, { cell: address.trim().toUpperCase() }))}
        className={`${FIELD_CLASS} w-14 shrink-0 font-mono`}
        style={INPUT_STYLE}
      />
      <button type="button" onClick={() => change((spec) => removeExportCell(spec, widget.id, index))} aria-label={`Remove cell ${number}`} className={REMOVE_CLASS}>
        <X size={12} aria-hidden="true" />
      </button>
    </div>
  )
}
