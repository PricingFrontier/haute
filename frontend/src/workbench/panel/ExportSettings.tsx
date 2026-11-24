/** An Export's panel (specs/workbench). */
import { ChevronDown, ChevronRight, GripVertical, Plus, SlidersHorizontal, Trash2, TriangleAlert, X } from "lucide-react"
import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react"
import type { CsvExport, ExcelCellsExport, ExportBlock, ExportCell, ExportWidget, FieldRef, FormSpec, SchemaColumn, SchemaTable, TrueFalseText } from "../../api/types"
import { COMPACT_FIELD_CLASS, CommittedTextField, ConfigCheckbox, FIELD_CLASS, INPUT_STYLE, LabelledRow, SelectRow } from "../../components/form"
import useListReorder, { type RowDrag } from "../../hooks/useListReorder"
import useRefocusAfterMove, { altArrowMove } from "../../hooks/useRefocusAfterMove"
import { useRowKeys } from "../../panels/editors/shared/useRowKeys"
import { ADD_BUTTON_CLASS, ADD_BUTTON_STYLE, ColumnSelector, type ColumnOption } from "../../panels/modelling/ColumnSelector"
import { confirmed } from "../../stores/useQuestionStore"
import useToastStore from "../../stores/useToastStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { sheetColumns } from "../../utils/quoteIdentity"
import {
  addBlockColumn,
  addExportBlock,
  addExportCellTo,
  cellClashes,
  cellColumns,
  cellsBySheet,
  cellSheets,
  excelLayout,
  exportCellsOf,
  LABEL_POSITIONS,
  labelAddress,
  labelPosition,
  type LabelPosition,
  exportFormatShown,
  fieldColumn,
  groupExportCells,
  moveBlockColumn,
  placeExportCell,
  readableName,
  removeBlockColumn,
  removeExportBlock,
  removeExportCell,
  renameExportSheet,
  setExcelLayout,
  setExportBlockTrueFalse,
  setExportCellColumn,
  setExportCellTrueFalse,
  setExportFormat,
  sheetNameProblem,
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
          {target.format !== "excel_cells" && <SelectRow label="Headers" value={target.headers} choices={HEADERS} onChange={(headers) => set({ headers })} />}
          <SelectRow label="Values" value={target.values} choices={VALUES} onChange={(values) => set({ values })} />
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
 * What a workbook of cells writes to one place twice (`cellClashes`, its cells numbered as
 * Cells shows them), at the foot of the pane and held there as it scrolls, in the warning
 * colour; nothing while there is none.
 */
function CellClashes({ form, target }: { form: FormSpec; target: ExcelCellsExport }) {
  const clashes = cellClashes(form, { ...target, cells: cellsBySheet(target.cells) })
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
            <li key={clash.text}>{clash.text}</li>
          ))}
        </ul>
      </section>
    </div>
  )
}

/** The columns a cell can be given (`cellColumns`), each as `table.column` with its label and its type for `ColumnSelector`, and the field each names. */
function cellChoices(tables: readonly SchemaTable[]): { options: ColumnOption[]; fields: ReadonlyMap<string, FieldRef> } {
  const options: ColumnOption[] = []
  const fields = new Map<string, FieldRef>()
  for (const table of tables) {
    for (const column of cellColumns(table)) {
      const name = `${table.name}.${column.name}`
      options.push({ name, dtype: column.type, label: columnLabel(column) })
      fields.set(name, { table: table.name, column: column.name })
    }
  }
  return { options, fields }
}

/** Where a cell writes on its sheet, on one line: its address, its label's address and its True/False text. */
function cellSummary(cell: ExportCell): string {
  const parts = [cell.cell]
  if (cell.label_cell.trim() !== "") parts.push(`label ${cell.label_cell}`)
  if (cell.true_false !== null) {
    const preset = TRUE_FALSE_PRESETS.find((held) => held.value === trueFalsePreset(cell.true_false))
    parts.push(preset?.label ?? `${cell.true_false.true || "blank"} / ${cell.true_false.false || "blank"}`)
  }
  return parts.join(" · ")
}

/** No sheets added to a component. */
const NO_SHEETS: readonly string[] = []
/** Two sheet names Excel holds as one. */
const sameSheet = (one: string, other: string) => one.toLowerCase() === other.toLowerCase()

/**
 * A workbook of cells' Cells: a `SheetGroup` for each sheet its cells name (`cellSheets`),
 * then each added in the view store's `exportSheets` that holds none yet, each closed while
 * the view store's `exportCollapsed` holds it; the cells in
 * `cellsBySheet` order, numbered in it and keyed by `useRowKeys`, so a cell keeps its key,
 * its open settings and its Custom as it moves, is added or taken away. Every write groups
 * the cells first (`groupExportCells`), so a row's place is its cell's, and is said to
 * `useRowKeys` as the store makes it. One cell's settings open at a time. A sheet a move or a
 * removal leaves with no cells stays, added. Then Add a sheet.
 */
function ExportCells({ form, widget, target }: { form: FormSpec; widget: ExportWidget; target: ExcelCellsExport }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const addToast = useToastStore((s) => s.addToast)
  const added = useWorkbenchViewStore((s) => s.exportSheets[widget.id] ?? NO_SHEETS)
  const setExportSheets = useWorkbenchViewStore((s) => s.setExportSheets)
  const closed = useWorkbenchViewStore((s) => s.exportCollapsed[widget.id] ?? NO_SHEETS)
  const setExportCollapsed = useWorkbenchViewStore((s) => s.setExportCollapsed)
  const isClosed = (sheet: string) => closed.includes(sheet.toLowerCase())
  const toggle = (sheet: string) =>
    setExportCollapsed(widget.id, isClosed(sheet) ? closed.filter((held) => held !== sheet.toLowerCase()) : [...closed, sheet.toLowerCase()])
  const tables = form.schema.tables.filter((table) => cellColumns(table).length > 0)
  const { options, fields } = cellChoices(tables)
  const cells = cellsBySheet(target.cells)
  const named = cellSheets(target.cells)
  // The workbook's sheets: its cells', then those only its tables are written to, as the
  // server makes them, then those added and holding nothing yet, each once whatever its case.
  const sheets = [...named, ...target.blocks.map((block) => block.sheet), ...added].filter(
    (sheet, at, all) => all.findIndex((other) => sameSheet(other, sheet)) === at,
  )
  const clashed = new Set(cellClashes(form, { ...target, cells }).map((clash) => clash.sheet))
  const rowKeys = useRowKeys(cells)
  const [open, setOpen] = useState<number | null>(null)
  const [fresh, setFresh] = useState<string | null>(null)
  // A cell moved by Alt+arrows takes the focus again in its new place, as a sheet's name
  // does after a rename, which draws its group anew under its new name.
  const refocus = useRefocusAfterMove<HTMLDivElement>()
  const nameFocus = useRefocusAfterMove<HTMLInputElement>()
  /** The list's own write: `edit` made on the cells grouped, and the cells it leaves said to `useRowKeys` as `say` takes them. */
  const write = (edit: (spec: FormSpec) => FormSpec, say: (next: ExportCell[]) => void) =>
    change((spec) => {
      const grouped = groupExportCells(spec, widget.id)
      const next = edit(grouped)
      if (next === grouped) return spec
      say(exportCellsOf(next, widget.id))
      return next
    })
  /** The sheets added, `sheet` among them while it will hold no cells once `index` leaves it. */
  const keepIfEmptied = (index: number) => {
    const sheet = cells[index].sheet
    const left = cells.filter((cell, at) => at !== index && sameSheet(cell.sheet, sheet)).length
    if (left === 0 && !added.some((name) => sameSheet(name, sheet))) setExportSheets(widget.id, [...added, sheet])
  }
  /** Put the cell at `from` at `to` on `sheet`, its key with it, the sheet it lands on opened. */
  const place = (from: number, to: number, sheet: string) => {
    if (!sameSheet(cells[from].sheet, sheet)) keepIfEmptied(from)
    if (isClosed(sheet)) toggle(sheet)
    const order = cells.map((cell, index) => ({ index, sheet: cell.sheet }))
    const [moved] = order.splice(from, 1)
    order.splice(to, 0, { ...moved, sheet })
    const was = cellsBySheet(order).map((held) => held.index)
    write(
      (spec) => placeExportCell(spec, widget.id, from, to, sheet),
      (next) => rowKeys.expect(next, { kind: "rows", from: was }),
    )
  }
  const dragFor = useListReorder((from, to) => place(from, to, cells[to].sheet))
  // The row being dragged, so a sheet's header takes it; a row dropped in another sheet is drawn anew there, never ending its drag.
  const [dragged, setDragged] = useState<number | null>(null)
  const dragOf = (index: number): RowDrag => {
    const drag = dragFor(index)
    return {
      ...drag,
      onStart: (event) => {
        setDragged(index)
        drag.onStart(event)
      },
      onDrop: (event) => {
        setDragged(null)
        drag.onDrop(event)
      },
      onEnd: () => {
        setDragged(null)
        drag.onEnd()
      },
    }
  }
  const lastOf = (sheet: string) => cells.reduce((last, cell, index) => (sameSheet(cell.sheet, sheet) ? index : last), -1)
  /** A cell dropped on a sheet's header goes last in it; on an empty sheet, after every cell. */
  const dropOn = (sheet: string) => {
    const from = dragged
    setDragged(null)
    if (from === null || cells[from] === undefined) return
    dragFor(from).onEnd()
    const last = lastOf(sheet)
    place(from, last === -1 ? cells.length - 1 : from <= last ? last : last + 1, sheet)
  }
  /** Alt+Up and Alt+Down on a row: within its sheet, or past its first or last into the sheet before or after. */
  const moveKeys = (index: number, key: number) => (event: KeyboardEvent<HTMLDivElement>) => {
    if (!event.altKey || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) return
    event.preventDefault()
    const sheet = cells[index].sheet
    const at = sheets.findIndex((held) => sameSheet(held, sheet))
    const up = event.key === "ArrowUp"
    const neighbour = cells[up ? index - 1 : index + 1]
    if (neighbour !== undefined && sameSheet(neighbour.sheet, sheet)) {
      refocus.moved(String(key))
      place(index, up ? index - 1 : index + 1, sheet)
      return
    }
    const next = sheets[up ? at - 1 : at + 1]
    if (next === undefined) return
    refocus.moved(String(key))
    place(index, index, next)
  }
  const addTo = (sheet: string, field: FieldRef) => {
    const last = lastOf(sheet)
    const was: (number | null)[] = cells.map((_, index) => index)
    was.splice(last === -1 ? cells.length : last + 1, 0, null)
    write(
      (spec) => addExportCellTo(spec, widget.id, sheet, field),
      (next) => rowKeys.expect(next, { kind: "rows", from: was }),
    )
  }
  /** A sheet given a new name, on its cells and its tables, its added and closed state following; the focus kept in its name while it was there. */
  const rename = (sheet: string, typed: string, focused: boolean) => {
    const name = typed.trim()
    if (name === sheet) return
    const problem = name === "" ? "is blank" : sheetNameProblem(name)
    if (problem !== null) return addToast("error", `The sheet name "${name}" ${problem}`)
    if (sheets.some((other) => !sameSheet(other, sheet) && sameSheet(other, name))) return addToast("error", `Another sheet is named ${name}`)
    if (added.some((held) => sameSheet(held, sheet))) setExportSheets(widget.id, added.map((held) => (sameSheet(held, sheet) ? name : held)))
    if (isClosed(sheet)) setExportCollapsed(widget.id, closed.map((held) => (held === sheet.toLowerCase() ? name.toLowerCase() : held)))
    if (focused) nameFocus.moved(name.toLowerCase())
    write((spec) => renameExportSheet(spec, widget.id, sheet, name), (next) => rowKeys.edit(next))
  }
  /** An empty sheet taken away, with what the view held of it. */
  const removeSheet = (sheet: string) => {
    setExportSheets(widget.id, added.filter((name) => !sameSheet(name, sheet)))
    if (isClosed(sheet)) toggle(sheet)
  }
  const addSheet = () => {
    let number = 1
    while (sheets.some((sheet) => sameSheet(sheet, `Sheet${number}`))) number += 1
    setExportSheets(widget.id, [...added, `Sheet${number}`])
    setFresh(`Sheet${number}`)
  }
  return (
    <Section title="Cells" aside={`${target.cells.length} written`}>
      {sheets.map((sheet) => {
        const shut = isClosed(sheet)
        const held = cells.map((cell, index) => ({ cell, index })).filter(({ cell }) => sameSheet(cell.sheet, sheet))
        return (
          // Keyed by its name, so a sheet and what its rows hold stay together as the sheets around it change.
          <SheetGroup
            key={sheet.toLowerCase()}
            sheet={sheet}
            count={held.length}
            tables={target.blocks.filter((block) => sameSheet(block.sheet, sheet)).length}
            nameRef={nameFocus.register(sheet.toLowerCase())}
            shut={shut}
            clashes={clashed.has(sheet.toLowerCase())}
            autoFocus={fresh === sheet}
            onToggle={() => toggle(sheet)}
            onRename={(name, focused) => rename(sheet, name, focused)}
            onRemove={() => removeSheet(sheet)}
            taking={dragged !== null}
            onDrop={() => dropOn(sheet)}
            options={options}
            onAdd={(name) => {
              const field = fields.get(name)
              if (field !== undefined) addTo(sheet, field)
            }}
          >
            {held.map(({ cell, index }) => {
              const key = rowKeys.keys[index]
              return (
                <CellRow
                  key={key}
                  form={form}
                  widget={widget}
                  cell={cell}
                  index={index}
                  open={open === key}
                  onToggle={() => setOpen(open === key ? null : key)}
                  drag={dragOf(index)}
                  register={refocus.register(String(key))}
                  onMoveKeys={moveKeys(index, key)}
                  options={options}
                  fields={fields}
                  write={(edit) => write(edit, (next) => rowKeys.edit(next))}
                  onRemove={() => {
                    keepIfEmptied(index)
                    write(
                      (spec) => removeExportCell(spec, widget.id, index),
                      (next) => rowKeys.remove(index, next),
                    )
                  }}
                />
              )
            })}
          </SheetGroup>
        )
      })}
      <button type="button" onClick={addSheet} className={ADD_BUTTON_CLASS} style={ADD_BUTTON_STYLE}>
        <Plus size={12} className="shrink-0" aria-hidden="true" />
        Add a sheet
      </button>
    </Section>
  )
}

/**
 * A sheet of a workbook of cells: its header, a toggle, its name (committed through the
 * caller, which refuses one Excel would not take or another sheet holds), how many cells it
 * holds and, while it has any, tables, a mark while one of them clashes and, while it holds
 * neither, a cross; a cell's row
 * dragged onto the header (`taking`) going last in it. While open, its cells' rows and Add a cell to it.
 */
function SheetGroup({
  sheet,
  count,
  tables,
  nameRef,
  shut,
  clashes,
  autoFocus,
  onToggle,
  onRename,
  onRemove,
  taking,
  onDrop,
  options,
  onAdd,
  children,
}: {
  sheet: string
  count: number
  /** How many tables are written to it. */
  tables: number
  nameRef: (element: HTMLInputElement | null) => void
  shut: boolean
  clashes: boolean
  autoFocus: boolean
  onToggle: () => void
  /** A name committed, and whether the focus was in the name as it was. */
  onRename: (name: string, focused: boolean) => void
  onRemove: () => void
  /** A cell's row is being dragged, which the header takes. */
  taking: boolean
  onDrop: () => void
  options: readonly ColumnOption[]
  onAdd: (name: string) => void
  children: ReactNode
}) {
  const [over, setOver] = useState(false)
  const name = useRef<HTMLInputElement | null>(null)
  return (
    <div role="group" aria-label={`Sheet ${sheet}`} className="flex flex-col gap-1">
      <div
        onDragOver={(event) => {
          if (!taking) return
          event.preventDefault()
          if (event.dataTransfer) event.dataTransfer.dropEffect = "move"
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(event) => {
          if (!taking) return
          event.preventDefault()
          setOver(false)
          onDrop()
        }}
        data-drop-target={over ? "true" : undefined}
        className="flex items-center gap-1.5 rounded px-1 py-0.5"
        style={{ background: "var(--bg-elevated)", boxShadow: over ? "inset 0 0 0 1px var(--accent)" : undefined }}
      >
        <IconButton
          icon={shut ? ChevronRight : ChevronDown}
          onClick={onToggle}
          aria-expanded={!shut}
          aria-label={`${shut ? "Expand" : "Collapse"} sheet ${sheet}`}
          style={{ color: "var(--text-muted)" }}
        />
        <CommittedTextField
          ref={(element) => {
            name.current = element
            nameRef(element)
          }}
          aria-label={`Sheet ${sheet} name`}
          value={sheet}
          onCommit={(typed) => onRename(typed, document.activeElement === name.current)}
          autoFocus={autoFocus}
          className={`${COMPACT_FIELD_CLASS} min-w-0 flex-1 font-medium`}
          style={INPUT_STYLE}
        />
        <span className="shrink-0 text-[11px]" style={{ color: "var(--text-muted)" }}>
          {count === 1 ? "1 cell" : `${count} cells`}
          {tables > 0 && (tables === 1 ? " · 1 table" : ` · ${tables} tables`)}
        </span>
        {clashes && (
          <span role="img" aria-label={`Clashes on ${sheet}`} title="Clashes on this sheet" className="flex shrink-0" style={{ color: "var(--warning)" }}>
            <TriangleAlert size={12} aria-hidden="true" />
          </span>
        )}
        {count === 0 && tables === 0 && <IconButton icon={X} onClick={onRemove} aria-label={`Remove sheet ${sheet}`} title="Remove sheet" className="icon-danger-btn" />}
      </div>
      {!shut && (
        <div className="flex flex-col gap-1 pl-3">
          {count > 0 && (
            <div role="list" aria-label={`${sheet} cells`} className="flex flex-col gap-0.5">
              {children}
            </div>
          )}
          <ColumnSelector label={`Add a cell to ${sheet}`} value="" columns={options} compact trigger="quiet" placeholder="Add a cell" onChange={onAdd} />
        </div>
      )}
    </div>
  )
}

/**
 * A cell's row, as a schema column's is drawn: a grip it is dragged by and Alt+Up and
 * Alt+Down on the row moving it (`onMoveKeys`), its number, its column's label or Missing
 * column, where it writes on its sheet (`cellSummary`), and a settings button opening
 * `CellSettings` under it, which holds its Remove cell.
 */
function CellRow({
  form,
  widget,
  cell,
  index,
  open,
  onToggle,
  drag,
  register,
  onMoveKeys,
  options,
  fields,
  write,
  onRemove,
}: {
  form: FormSpec
  widget: ExportWidget
  cell: ExportCell
  index: number
  open: boolean
  onToggle: () => void
  drag: RowDrag
  register: (element: HTMLDivElement | null) => void
  onMoveKeys: (event: KeyboardEvent<HTMLDivElement>) => void
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
      onKeyDown={(event) => {
        // The row's own keys: Alt+arrows in its settings' fields stay theirs.
        if (event.target === event.currentTarget) onMoveKeys(event)
      }}
      onDragOver={drag.onOver}
      onDrop={drag.onDrop}
      data-drop-target={drag.target ? "true" : undefined}
      className="group focus-ring flex flex-col rounded"
      style={{ opacity: drag.dragging ? 0.5 : 1, boxShadow: drag.target ? "0 -2px 0 0 var(--accent)" : undefined }}
    >
      <div className="flex items-center gap-1.5 rounded px-1 py-0.5 hover-bg">
        <DragGrip drag={drag} image={row} />
        <span className="w-6 shrink-0 text-right font-mono text-[10px]" style={{ color: "var(--text-muted)" }} aria-hidden="true">
          {number}
        </span>
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
          title="Field, cell, label and True/False"
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
 * A cell's settings, mounted only while open: its Field and Cell, its Label (None, a
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
          // Custom from TRUE / FALSE: the blank texts its fields show, written as they show.
          else if (value === CUSTOM && text === null) onChange({ true: "", false: "" })
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
