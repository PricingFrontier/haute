/** An Export's panel (specs/workbench). */
import { GripVertical, SlidersHorizontal, Trash2, TriangleAlert, X } from "lucide-react"
import { useId, useRef, useState, type KeyboardEvent } from "react"
import type { CsvExport, ExcelCellsExport, ExportBlock, ExportCell, ExportWidget, FieldRef, FormSpec, SchemaColumn, SchemaTable, TrueFalseText } from "../../api/types"
import { CommittedTextField, ConfigCheckbox, FIELD_CLASS, INPUT_STYLE, LabelledRow, SelectRow } from "../../components/form"
import useListReorder, { type RowDrag } from "../../hooks/useListReorder"
import useRefocusAfterMove, { altArrowMove } from "../../hooks/useRefocusAfterMove"
import { useRowKeys } from "../../panels/editors/shared/useRowKeys"
import { ColumnSelector, type ColumnOption } from "../../panels/modelling/ColumnSelector"
import { confirmed } from "../../stores/useQuestionStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { sheetColumns } from "../../utils/quoteIdentity"
import {
  addBlockColumn,
  addExportBlock,
  addExportCell,
  cellClashes,
  cellColumns,
  excelLayout,
  exportCellsOf,
  LABEL_POSITIONS,
  labelAddress,
  labelPosition,
  type LabelPosition,
  exportFormatShown,
  fieldColumn,
  moveBlockColumn,
  moveExportCell,
  readableName,
  removeBlockColumn,
  removeExportBlock,
  removeExportCell,
  setExcelLayout,
  setExportBlockTrueFalse,
  setExportCellColumn,
  setExportCellTrueFalse,
  setExportFormat,
  toggleExportTable,
  trueFalsePreset,
  updateExportBlock,
  updateExportCell,
  updateExportTarget,
  type ExcelLayout,
  type ExportHeader,
  EXPORT_FORMATS,
  TRUE_FALSE_PRESETS,
} from "../../utils/workbenchForm"
import { DragGrip } from "../schema/DragGrip"
import { IconButton } from "../schema/IconButton"
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

const CUSTOM = "custom"
const TRUE_FALSE_CHOICES: ReadonlyArray<{ value: string; label: string }> = [...TRUE_FALSE_PRESETS.map(({ value, label }) => ({ value, label })), { value: CUSTOM, label: "Custom" }]

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
          <CellClashes form={form} target={target} />
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

/**
 * What a workbook of cells writes to one place twice (`cellClashes`), at the foot of the
 * pane and held there as it scrolls, in the warning colour; nothing while there is none.
 */
function CellClashes({ form, target }: { form: FormSpec; target: ExcelCellsExport }) {
  const clashes = cellClashes(form, target)
  if (clashes.length === 0) return null
  // A band to the pane's edges, over the pane's padding, so nothing scrolls past under it.
  return (
    <div className="sticky -bottom-3 -mx-3 -mb-3 mt-auto shrink-0 px-3 pb-3 pt-2" style={{ background: "var(--bg-panel)" }}>
      <section aria-label="Clashes" className="max-h-40 overflow-auto rounded-md p-2" style={{ border: "1px solid var(--warning)", background: "var(--warning-soft)" }}>
        <div className="flex items-center gap-1.5 font-medium" style={{ color: "var(--warning)" }}>
          <TriangleAlert size={12} aria-hidden="true" className="shrink-0" />
          {clashes.length === 1 ? "1 clash" : `${clashes.length} clashes`}
        </div>
        <ul className="mt-1 flex list-disc flex-col gap-0.5 pl-5 text-[11px]" style={{ color: "var(--text-secondary)" }}>
          {clashes.map((clash) => (
            <li key={clash}>{clash}</li>
          ))}
        </ul>
      </section>
    </div>
  )
}

/** The columns a cell can be given (`cellColumns`), each as `table.column` with its type for `ColumnSelector`, and the field each names. */
function cellChoices(tables: readonly SchemaTable[]): { options: ColumnOption[]; fields: ReadonlyMap<string, FieldRef> } {
  const options: ColumnOption[] = []
  const fields = new Map<string, FieldRef>()
  for (const table of tables) {
    for (const column of cellColumns(table)) {
      const name = `${table.name}.${column.name}`
      options.push({ name, dtype: column.type })
      fields.set(name, { table: table.name, column: column.name })
    }
  }
  return { options, fields }
}

/** Where a cell writes, on one line: its sheet and address, its label's address and its True/False text. */
function cellSummary(cell: ExportCell): string {
  const parts = [`${cell.sheet}!${cell.cell}`]
  if (cell.label_cell.trim() !== "") parts.push(`label ${cell.label_cell}`)
  if (cell.true_false !== null) {
    const preset = TRUE_FALSE_PRESETS.find((held) => held.value === trueFalsePreset(cell.true_false))
    parts.push(preset?.label ?? `${cell.true_false.true || "blank"} / ${cell.true_false.false || "blank"}`)
  }
  return parts.join(" · ")
}

/**
 * A workbook of cells' Cells: a `CellRow` for each cell, in order, keyed by `useRowKeys` so a
 * cell keeps its key, its open settings and its Custom as it moves, is added or taken away,
 * each write said to it as the store makes it; one cell's settings open at a time; then Add a
 * cell, a `ColumnSelector` of the columns a cell can be given.
 */
function ExportCells({ form, widget, target }: { form: FormSpec; widget: ExportWidget; target: ExcelCellsExport }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const tables = form.schema.tables.filter((table) => cellColumns(table).length > 0)
  const { options, fields } = cellChoices(tables)
  const rowKeys = useRowKeys(target.cells)
  const [open, setOpen] = useState<number | null>(null)
  // A cell moved by Alt+arrows takes the focus again in its new place.
  const refocus = useRefocusAfterMove<HTMLDivElement>()
  /** The list's own write: `edit` made, and the cells it leaves said to `useRowKeys` as `say` takes them. */
  const write = (edit: (spec: FormSpec) => FormSpec, say: (next: ExportCell[]) => void) =>
    change((spec) => {
      const next = edit(spec)
      if (next !== spec) say(exportCellsOf(next, widget.id))
      return next
    })
  const move = (from: number, to: number) =>
    write(
      (spec) => moveExportCell(spec, widget.id, from, to),
      (next) => rowKeys.move(from, to, next),
    )
  const dragFor = useListReorder(move)
  return (
    <Section title="Cells" aside={`${target.cells.length} written`}>
      {target.cells.length > 0 && (
        <div role="list" aria-label="Cells" className="flex flex-col gap-0.5">
          {target.cells.map((cell, index) => {
            const key = rowKeys.keys[index]
            return (
              <CellRow
                key={key}
                form={form}
                widget={widget}
                cell={cell}
                index={index}
                count={target.cells.length}
                open={open === key}
                onToggle={() => setOpen(open === key ? null : key)}
                drag={dragFor(index)}
                register={refocus.register(String(key))}
                onMove={(to) => {
                  refocus.moved(String(key))
                  move(index, to)
                }}
                options={options}
                fields={fields}
                write={(edit) => write(edit, (next) => rowKeys.edit(next))}
                onRemove={() =>
                  write(
                    (spec) => removeExportCell(spec, widget.id, index),
                    (next) => rowKeys.remove(index, next),
                  )
                }
              />
            )
          })}
        </div>
      )}
      <ColumnSelector
        label="Add a cell"
        value=""
        columns={options}
        compact
        trigger="add"
        placeholder="Add a cell"
        onChange={(name) => {
          const field = fields.get(name)
          if (field !== undefined)
            write(
              (spec) => addExportCell(spec, widget.id, field),
              (next) => rowKeys.add(next),
            )
        }}
      />
    </Section>
  )
}

/**
 * A cell's row, as a schema column's is drawn: a grip it is dragged by and Alt+Up and
 * Alt+Down on the row moving it, its column's label or Missing column, where it writes
 * (`cellSummary`), and a settings button opening `CellSettings` under it, which holds its
 * Remove cell.
 */
function CellRow({
  form,
  widget,
  cell,
  index,
  count,
  open,
  onToggle,
  drag,
  register,
  onMove,
  options,
  fields,
  write,
  onRemove,
}: {
  form: FormSpec
  widget: ExportWidget
  cell: ExportCell
  index: number
  count: number
  open: boolean
  onToggle: () => void
  drag: RowDrag
  register: (element: HTMLDivElement | null) => void
  onMove: (to: number) => void
  options: readonly ColumnOption[]
  fields: ReadonlyMap<string, FieldRef>
  write: (edit: (spec: FormSpec) => FormSpec) => void
  onRemove: () => void
}) {
  const row = useRef<HTMLDivElement | null>(null)
  const settingsId = useId()
  const found = fieldColumn(form, cell)
  const label = found === null ? "Missing column" : columnLabel(found.column)
  const number = index + 1
  const summary = cellSummary(cell)
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    // The row's own keys: Alt+arrows in its settings' fields stay theirs.
    if (event.target !== event.currentTarget) return
    const to = altArrowMove(event, "vertical", index, count)
    if (to !== null) onMove(to)
  }
  return (
    <div
      ref={(element) => {
        row.current = element
        register(element)
      }}
      tabIndex={0}
      role="listitem"
      aria-label={`Cell ${number} ${label}`}
      aria-keyshortcuts="Alt+ArrowUp Alt+ArrowDown"
      onKeyDown={onKeyDown}
      onDragOver={drag.onOver}
      onDrop={drag.onDrop}
      data-drop-target={drag.target ? "true" : undefined}
      className="group focus-ring flex flex-col rounded"
      style={{ opacity: drag.dragging ? 0.5 : 1, boxShadow: drag.target ? "0 -2px 0 0 var(--accent)" : undefined }}
    >
      <div className="flex items-center gap-1.5 rounded px-1 py-0.5 hover-bg">
        <DragGrip drag={drag} image={row} />
        <span className="min-w-0 shrink truncate" title={`${cell.table}.${cell.column}`} style={found === null ? { color: "var(--warning)" } : undefined}>
          {label}
        </span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px]" style={{ color: "var(--text-muted)" }} title={summary} data-testid={`export-cell-${number}-summary`}>
          {summary}
        </span>
        <IconButton
          icon={SlidersHorizontal}
          onClick={onToggle}
          aria-expanded={open}
          aria-controls={open ? settingsId : undefined}
          aria-label={`Cell ${number} settings`}
          title="Field, sheet, cell, label cell and True/False"
          style={{ color: open ? "var(--text-primary)" : "var(--text-muted)" }}
        />
      </div>
      {open && <CellSettings id={settingsId} form={form} widget={widget} cell={cell} index={index} options={options} fields={fields} write={write} onRemove={onRemove} />}
    </div>
  )
}

/** The Label select's Custom: the label at an address typed in Label cell. */
const LABEL_CUSTOM = "custom"
type LabelChoice = LabelPosition | "none" | typeof LABEL_CUSTOM

/**
 * A cell's settings, mounted only while open: its Field, Sheet and Cell, its Label (None, a
 * position next to the cell, or Custom showing Label cell), a tick's True/False, and Remove
 * cell, which asks first. A new address for the cell takes a label in a position with it.
 */
function CellSettings({
  id,
  form,
  widget,
  cell,
  index,
  options,
  fields,
  write,
  onRemove,
}: {
  id: string
  form: FormSpec
  widget: ExportWidget
  cell: ExportCell
  index: number
  options: readonly ColumnOption[]
  fields: ReadonlyMap<string, FieldRef>
  write: (edit: (spec: FormSpec) => FormSpec) => void
  onRemove: () => void
}) {
  const found = fieldColumn(form, cell)
  const number = index + 1
  // Custom stays chosen, its box shown, even while the address typed is a position's.
  const [custom, setCustom] = useState(false)
  const position = labelPosition(cell)
  const placed: LabelChoice = custom || position === null ? LABEL_CUSTOM : position
  const labelChoices: ReadonlyArray<{ value: LabelChoice; label: string; disabled?: boolean }> = [
    { value: "none", label: "None" },
    ...LABEL_POSITIONS.map((held) => ({ ...held, disabled: labelAddress(cell.cell, held.value) === null })),
    { value: LABEL_CUSTOM, label: "Custom" },
  ]
  const placeLabel = (choice: LabelChoice) => {
    setCustom(choice === LABEL_CUSTOM)
    if (choice === LABEL_CUSTOM) return
    const label_cell = choice === "none" ? "" : labelAddress(cell.cell, choice)
    if (label_cell !== null) write((spec) => updateExportCell(spec, widget.id, index, { label_cell }))
  }
  const moveCell = (typed: string) => {
    const address = typed.trim().toUpperCase()
    // A label in a position goes with the cell, where the new address has such a cell.
    const along = placed === LABEL_CUSTOM || placed === "none" ? null : labelAddress(address, placed)
    write((spec) => updateExportCell(spec, widget.id, index, along === null ? { cell: address } : { cell: address, label_cell: along }))
  }
  const remove = async () => {
    const label = found === null ? "Missing column" : columnLabel(found.column)
    const at = `${cell.sheet}!${cell.cell}`
    const text = cell.label_cell.trim() === "" ? `It stops writing to ${at}.` : `It stops writing to ${at}, and its label to ${cell.label_cell}.`
    if (await confirmed({ title: `Remove cell ${number}, ${label}?`, text, confirm: "Remove", danger: true })) onRemove()
  }
  return (
    <div id={id} role="group" aria-label={`Cell ${number} settings`} className="ml-4 flex flex-col gap-1 border-l py-1 pl-2" style={{ borderColor: "var(--border)" }}>
      <LabelledRow label="Field">
        <ColumnSelector
          label={`Cell ${number} field`}
          value={`${cell.table}.${cell.column}`}
          columns={options}
          compact
          trigger="field"
          onChange={(name) => {
            const field = fields.get(name)
            if (field !== undefined) write((spec) => setExportCellColumn(spec, widget.id, index, field))
          }}
        />
      </LabelledRow>
      <LabelledRow label="Sheet">
        <CommittedTextField
          aria-label={`Cell ${number} sheet`}
          value={cell.sheet}
          onCommit={(sheet) => write((spec) => updateExportCell(spec, widget.id, index, { sheet }))}
          className={`${FIELD_CLASS} min-w-0 flex-1`}
          style={INPUT_STYLE}
        />
      </LabelledRow>
      <LabelledRow label="Cell">
        <CommittedTextField
          aria-label={`Cell ${number} address`}
          value={cell.cell}
          onCommit={moveCell}
          className={`${FIELD_CLASS} min-w-0 flex-1 font-mono`}
          style={INPUT_STYLE}
        />
      </LabelledRow>
      <SelectRow label="Label" name={`Cell ${number} label`} value={placed} choices={labelChoices} onChange={placeLabel} />
      {placed === LABEL_CUSTOM && (
        <LabelledRow label="Label cell">
          <CommittedTextField
            aria-label={`Cell ${number} label address`}
            value={cell.label_cell}
            onCommit={(address) => write((spec) => updateExportCell(spec, widget.id, index, { label_cell: address.trim().toUpperCase() }))}
            className={`${FIELD_CLASS} min-w-0 flex-1 font-mono`}
            style={INPUT_STYLE}
          />
        </LabelledRow>
      )}
      {(found?.column.type === "bool" || cell.true_false !== null) && (
        <TrueFalseSetting name={`Cell ${number}`} text={cell.true_false} onChange={(text) => write((spec) => setExportCellTrueFalse(spec, widget.id, index, text))} />
      )}
      <button
        type="button"
        onClick={() => void remove()}
        aria-label={`Remove cell ${number}`}
        className="focus-ring inline-flex items-center gap-1 justify-self-start self-start rounded px-1 py-0.5 -ml-1 text-[11px] font-medium hover-bg"
        style={{ color: "var(--danger)" }}
      >
        <Trash2 size={11} aria-hidden="true" />
        Remove cell
      </button>
    </div>
  )
}

/**
 * What a tick writes in place of TRUE and FALSE: True/False, a select of `TRUE_FALSE_PRESETS`
 * and Custom, Custom, or a text that is none of them, showing a True and a False text field.
 */
function TrueFalseSetting({ name, text, onChange, className = "" }: { name: string; text: TrueFalseText | null; onChange: (text: TrueFalseText | null) => void; className?: string }) {
  const [custom, setCustom] = useState(false)
  const preset = trueFalsePreset(text)
  const shown = custom || preset === null ? CUSTOM : preset
  return (
    <div className={`flex flex-col gap-1 ${className}`.trim()}>
      <SelectRow
        label="True/False"
        name={`${name} True/False`}
        value={shown}
        choices={TRUE_FALSE_CHOICES}
        onChange={(value) => {
          setCustom(value === CUSTOM)
          const chosen = TRUE_FALSE_PRESETS.find((held) => held.value === value)
          if (chosen !== undefined) onChange(chosen.text)
        }}
      />
      {shown === CUSTOM && (
        <>
          <LabelledRow label="True">
            <CommittedTextField aria-label={`${name} True text`} value={text?.true ?? ""} onCommit={(written) => onChange({ true: written, false: text?.false ?? "" })} className={`${FIELD_CLASS} min-w-0 flex-1`} style={INPUT_STYLE} />
          </LabelledRow>
          <LabelledRow label="False">
            <CommittedTextField aria-label={`${name} False text`} value={text?.false ?? ""} onCommit={(written) => onChange({ true: text?.true ?? "", false: written })} className={`${FIELD_CLASS} min-w-0 flex-1`} style={INPUT_STYLE} />
          </LabelledRow>
        </>
      )}
    </div>
  )
}

/**
 * A workbook of cells' Tables: under a heading row, each block, its table's name or Missing
 * table, its sheet, its top-left cell's address, its header and a cross, its True/False
 * while it writes a tick or holds text; then its columns
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
  const table = form.schema.tables.find((held) => held.name === block.table)
  const missing = table === undefined
  // It writes a tick: one of its columns is True/false.
  const ticks = table !== undefined && sheetColumns(table).some((column) => column.type === "bool" && block.columns.includes(column.name))
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
      {(ticks || block.true_false !== null) && (
        <TrueFalseSetting key={`${block.table}@${block.sheet}!${block.cell}`} name={`Table ${number}`} className="pl-2" text={block.true_false} onChange={(text) => change((spec) => setExportBlockTrueFalse(spec, widget.id, index, text))} />
      )}
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
