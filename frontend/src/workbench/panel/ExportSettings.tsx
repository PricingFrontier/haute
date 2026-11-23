/** An Export's panel (specs/workbench). */
import { GripVertical, X } from "lucide-react"
import type { KeyboardEvent } from "react"
import type { CsvExport, ExcelCellsExport, ExportBlock, ExportCell, ExportWidget, FieldRef, FormSpec, SchemaColumn, SchemaTable } from "../../api/types"
import { CommittedTextField, ConfigCheckbox, FIELD_CLASS, INPUT_STYLE, LabelledRow, SelectRow } from "../../components/form"
import useListReorder from "../../hooks/useListReorder"
import useRefocusAfterMove, { altArrowMove } from "../../hooks/useRefocusAfterMove"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { sheetColumns } from "../../utils/quoteIdentity"
import { columnKey } from "../../utils/sheetVisibility"
import {
  addBlockColumn,
  addExportBlock,
  addExportCell,
  cellColumns,
  excelLayout,
  exportFormatShown,
  fieldColumn,
  moveBlockColumn,
  readableName,
  removeBlockColumn,
  removeExportBlock,
  removeExportCell,
  setExcelLayout,
  setExportCellColumn,
  setExportFormat,
  toggleExportTable,
  updateExportBlock,
  updateExportCell,
  updateExportTarget,
  type ExcelLayout,
  type ExportHeader,
  EXPORT_FORMATS,
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

const BLOCK_HEADERS: ReadonlyArray<{ value: ExportHeader; label: string }> = [
  { value: "labels", label: "Labels" },
  { value: "names", label: "Names" },
  { value: "none", label: "None" },
]

const EXCEL_LAYOUTS: ReadonlyArray<{ value: ExcelLayout; label: string }> = [
  { value: "tables", label: "Sheet per table" },
  { value: "cells", label: "Cells" },
]

const REMOVE_CLASS = "icon-danger-btn focus-ring shrink-0 rounded p-0.5"

const columnLabel = (column: SchemaColumn): string => column.label || readableName(column.name)

/**
 * An Export's panel in its panes (`ButtonPanes`), Config holding its Format, CSV or Excel;
 * to Excel, its Layout; a file's name, a CSV's delimiter and, but for a workbook of cells,
 * its headers and values; then a workbook of cells' Cells and Tables, its blocks, or the
 * Tables it exports.
 */
export function ExportSettings({ form, widget }: { form: FormSpec; widget: ExportWidget }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const { target } = widget
  const set = (patch: Parameters<typeof updateExportTarget>[2]) => change((spec) => updateExportTarget(spec, widget.id, patch))
  const strays = widget.tables.filter((id) => !form.schema.tables.some((table) => table.name === id))
  return (
    <ButtonPanes form={form} widget={widget}>
      <FormatToggle widget={widget} formats={EXPORT_FORMATS} format={exportFormatShown(target.format)} onChange={(format) => change((spec) => setExportFormat(spec, widget.id, format))} />
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
      {target.format === "excel_cells" ? (
        <>
          <ExportCells form={form} widget={widget} target={target} />
          <ExportBlocks form={form} widget={widget} target={target} />
        </>
      ) : (
        <Section title="Tables" aside={`${widget.tables.length} exported`}>
          {form.schema.tables.map((table) => {
            const exported = widget.tables.includes(table.name)
            return (
              <div key={table.name} className="flex items-center gap-2">
                <div className="min-w-0 flex-1 font-mono">
                  <ConfigCheckbox label={table.name} checked={exported} onChange={() => change((spec) => toggleExportTable(spec, widget.id, table.name))} />
                </div>
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
 * sheet, its address and its label's address, and a cross; then Add a cell, a select of
 * the columns a cell can be given, held at its first option.
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
          <span className="w-14 shrink-0">Label cell</span>
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
      <CommittedTextField
        aria-label={`Cell ${number} label address`}
        value={cell.label_cell}
        onCommit={(address) => change((spec) => updateExportCell(spec, widget.id, index, { label_cell: address.trim().toUpperCase() }))}
        className={`${FIELD_CLASS} w-14 shrink-0 font-mono`}
        style={INPUT_STYLE}
      />
      <button type="button" onClick={() => change((spec) => removeExportCell(spec, widget.id, index))} aria-label={`Remove cell ${number}`} className={REMOVE_CLASS}>
        <X size={12} aria-hidden="true" />
      </button>
    </div>
  )
}

/**
 * A workbook of cells' Tables: under a heading row, each block, its table's name or Missing
 * table, its sheet, its top-left cell's address, its header and a cross; then its columns
 * and Add a column (`BlockColumns`); and under them Add a table, a select of the tables a
 * sheet shows columns of, held at its first option.
 */
function ExportBlocks({ form, widget, target }: { form: FormSpec; widget: ExportWidget; target: ExcelCellsExport }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const tables = form.schema.tables.filter((table) => sheetColumns(table).length > 0)
  return (
    <Section title="Tables" aside={`${target.blocks.length} written`}>
      {target.blocks.length > 0 && (
        <div className="flex items-center gap-1.5" style={{ color: "var(--text-muted)" }} aria-hidden="true">
          <span className="min-w-0 flex-1">Table</span>
          <span className="w-20 shrink-0">Sheet</span>
          <span className="w-14 shrink-0">Cell</span>
          <span className="w-16 shrink-0">Header</span>
          <span className="w-4 shrink-0" />
        </div>
      )}
      {target.blocks.map((block, index) => (
        <BlockRow key={index} form={form} widget={widget} block={block} index={index} />
      ))}
      <select
        aria-label="Add a table"
        value=""
        onChange={(event) => {
          const table = event.target.value
          if (table !== "") change((spec) => addExportBlock(spec, widget.id, table))
        }}
        className={`${FIELD_CLASS} w-full`}
        style={INPUT_STYLE}
      >
        <option value="">Add a table</option>
        {tables.map((table) => (
          <option key={table.name} value={table.name}>
            {table.name}
          </option>
        ))}
      </select>
    </Section>
  )
}

function BlockRow({ form, widget, block, index }: { form: FormSpec; widget: ExportWidget; block: ExportBlock; index: number }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const missing = !form.schema.tables.some((table) => table.name === block.table)
  const number = index + 1
  const set = (patch: Parameters<typeof updateExportBlock>[3]) => change((spec) => updateExportBlock(spec, widget.id, index, patch))
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-1.5">
        <span className="min-w-0 flex-1 truncate font-mono" style={missing ? { color: "var(--warning)" } : undefined}>
          {missing ? "Missing table" : block.table}
        </span>
        <CommittedTextField aria-label={`Table ${number} sheet`} value={block.sheet} onCommit={(sheet) => set({ sheet })} className={`${FIELD_CLASS} w-20 shrink-0`} style={INPUT_STYLE} />
        <CommittedTextField
          aria-label={`Table ${number} address`}
          value={block.cell}
          onCommit={(address) => set({ cell: address.trim().toUpperCase() })}
          className={`${FIELD_CLASS} w-14 shrink-0 font-mono`}
          style={INPUT_STYLE}
        />
        <select
          aria-label={`Table ${number} header`}
          value={block.header}
          onChange={(event) => set({ header: event.target.value as ExportHeader })}
          className={`${FIELD_CLASS} w-16 shrink-0`}
          style={INPUT_STYLE}
        >
          {BLOCK_HEADERS.map((choice) => (
            <option key={choice.value} value={choice.value}>
              {choice.label}
            </option>
          ))}
        </select>
        <button type="button" onClick={() => change((spec) => removeExportBlock(spec, widget.id, index))} aria-label={`Remove table ${number}`} className={REMOVE_CLASS}>
          <X size={12} aria-hidden="true" />
        </button>
      </div>
      <BlockColumns form={form} widget={widget} block={block} index={index} />
    </div>
  )
}

/**
 * A block's columns in its order, each by its label or Missing column, with a cross, moved
 * by dragging or Alt+Up and Alt+Down; then Add a column, a select of its table's columns as
 * a sheet shows them that it does not hold, held at its first option.
 */
function BlockColumns({ form, widget, block, index }: { form: FormSpec; widget: ExportWidget; block: ExportBlock; index: number }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const dragFor = useListReorder((from, to) => change((spec) => moveBlockColumn(spec, widget.id, index, from, to)))
  // A column moved by Alt+arrows takes the focus again in its new place.
  const refocus = useRefocusAfterMove<HTMLDivElement>()
  const table = form.schema.tables.find((held) => held.name === block.table)
  const shown = table === undefined ? [] : sheetColumns(table)
  const number = index + 1
  return (
    <div className="flex flex-col gap-1 pl-2">
      {block.columns.length > 0 && (
        <div role="list" aria-label={`Table ${number} columns`} className="flex flex-col gap-0.5 rounded-md p-1" style={{ border: "1px solid var(--border)", background: "var(--bg-elevated)" }}>
          {block.columns.map((name, place) => {
            const column = shown.find((held) => held.name === name)
            const label = column === undefined ? "Missing column" : columnLabel(column)
            const drag = dragFor(place)
            const rowKey = `${place}-${name}`
            const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
              const to = altArrowMove(event, "vertical", place, block.columns.length)
              if (to === null) return
              refocus.moved(`${to}-${name}`)
              change((spec) => moveBlockColumn(spec, widget.id, index, place, to))
            }
            return (
              <div
                key={rowKey}
                ref={refocus.register(rowKey)}
                tabIndex={0}
                role="listitem"
                aria-label={label}
                aria-keyshortcuts="Alt+ArrowUp Alt+ArrowDown"
                draggable
                onDragStart={drag.onStart}
                onDragOver={drag.onOver}
                onDrop={drag.onDrop}
                onDragEnd={drag.onEnd}
                onKeyDown={onKeyDown}
                data-drop-target={drag.target ? "true" : undefined}
                className="group focus-ring flex cursor-grab items-center gap-1.5 rounded px-1 py-0.5 hover-bg active:cursor-grabbing"
                style={{ opacity: drag.dragging ? 0.5 : 1, boxShadow: drag.target ? "0 -2px 0 0 var(--accent)" : undefined }}
              >
                <GripVertical size={12} aria-hidden="true" className="shrink-0" style={{ color: "var(--text-muted)" }} />
                <span className="min-w-0 flex-1 truncate" style={column === undefined ? { color: "var(--warning)" } : undefined}>
                  {label}
                </span>
                <button
                  type="button"
                  onClick={() => change((spec) => removeBlockColumn(spec, widget.id, index, place))}
                  aria-label={`Remove ${label} from table ${number}`}
                  className={REMOVE_CLASS}
                >
                  <X size={12} aria-hidden="true" />
                </button>
              </div>
            )
          })}
        </div>
      )}
      <select
        aria-label={`Table ${number} add a column`}
        value=""
        onChange={(event) => {
          const name = event.target.value
          if (name !== "") change((spec) => addBlockColumn(spec, widget.id, index, name))
        }}
        className={`${FIELD_CLASS} w-full`}
        style={INPUT_STYLE}
      >
        <option value="">Add a column</option>
        {shown
          .filter((column) => !block.columns.includes(column.name))
          .map((column) => (
            <option key={column.name} value={column.name}>
              {columnLabel(column)}
            </option>
          ))}
      </select>
    </div>
  )
}
