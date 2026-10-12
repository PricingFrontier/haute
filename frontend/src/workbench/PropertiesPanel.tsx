/**
 * The properties panel (specs/workbench): what opens on the right, on Haute's side panel,
 * when a component on the sheet is selected. Its title, when it shows, its layout (a
 * Table's rows and whether they are fixed, a Collection's columns), a Table's look (its
 * density, gridlines, row numbers and stripes), a Chart's chart (`ChartSettings`), the
 * order of the fields it shows, and the fields themselves, ticked from the schema tables
 * of its kind: many-row tables for a Table, one-row tables for a Collection, any table for
 * a Chart, which plots number columns alone. A grid holds one kind of row, so once a Table
 * or a Chart shows a field, tables whose rows do not line up with it are greyed out. A
 * Heading's panel is its text and description, when it shows, and its size and alignment.
 * Opened from the showing sheet's tab instead, it is the sheet's panel: its title and when
 * it shows; opened from a section's header, the section's: its title, when it shows, its
 * height, and adding a section under it or deleting it.
 */
import { ChevronDown, ChevronRight, GripVertical, Plus, Rows3, Sheet, TextAlignCenter, TextAlignEnd, TextAlignStart, Trash2, X, type LucideIcon } from "lucide-react"
import { useState, type KeyboardEvent, type ReactNode } from "react"
import type { FieldRef, FormSpec, HeadingWidget, Page, SchemaTable, Section as SheetSection } from "../api/types"
import { CommittedTextField, ConfigCheckbox, EditorLabel, ValidatedTextField } from "../components/form"
import ToggleButtonGroup from "../components/ToggleButtonGroup"
import { SidePanel } from "../haute-ui"
import useListReorder, { type RowDrag } from "../hooks/useListReorder"
import { INPUT_STYLE } from "../panels/editors/_shared"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchViewStore, { activePage } from "../stores/useWorkbenchViewStore"
import { leastSectionHeight } from "../utils/sheetGeometry"
import {
  addSection,
  chartGrain,
  createSection,
  fieldColumn,
  findSection,
  findWidget,
  fitSection,
  moveField,
  plottable,
  readableName,
  renamePage,
  renameSection,
  rowsShown,
  setPageShowWhen,
  setSectionHeight,
  setSectionShowWhen,
  tableGrain,
  toggleField,
  updateWidget,
  type FieldsWidget,
  type TableOptions,
} from "../utils/workbenchForm"
import ChartSettings from "./ChartSettings"
import { COLUMN_TYPES } from "./columnTypes"
import { deleteSection } from "./sheetInteractions"
import { ShowWhenEditor } from "./ShowWhenEditor"
import { COMPONENT_COLOR, WIDGET_KINDS } from "./widgetKinds"

const MIN_WIDTH = 320
/** At most half the window, so the sheet stays in view. */
const maxWidth = () => Math.max(MIN_WIDTH, Math.floor(window.innerWidth / 2))
const FIELD_CLASS = "focus-ring min-w-0 rounded-md px-1.5 py-1 text-xs"
const DENSITIES: ReadonlyArray<{ value: TableOptions["density"]; label: string }> = [
  { value: "compact", label: "Compact" },
  { value: "comfortable", label: "Comfortable" },
]
const GRIDLINES: ReadonlyArray<{ value: TableOptions["gridlines"]; label: string }> = [
  { value: "rows", label: "Between rows" },
  { value: "all", label: "Full grid" },
  { value: "none", label: "None" },
]
const HEADING_SIZES: ReadonlyArray<{ value: HeadingWidget["size"]; label: string }> = [
  { value: "large", label: "Large" },
  { value: "medium", label: "Medium" },
  { value: "small", label: "Small" },
]
const HEADING_ALIGNS: ReadonlyArray<{ value: HeadingWidget["align"]; label: string; icon: LucideIcon }> = [
  { value: "left", label: "Left", icon: TextAlignStart },
  { value: "center", label: "Centre", icon: TextAlignCenter },
  { value: "right", label: "Right", icon: TextAlignEnd },
]

const fieldKey = (field: FieldRef) => `${field.table}:${field.column}`

export default function PropertiesPanel() {
  const form = useWorkbenchFormStore((s) => s.form)
  const selectedId = useWorkbenchViewStore((s) => s.selectedId)
  const sheetSelected = useWorkbenchViewStore((s) => s.sheetSelected)
  const sectionId = useWorkbenchViewStore((s) => s.sectionId)
  const pageId = useWorkbenchViewStore((s) => s.pageId)
  const select = useWorkbenchViewStore((s) => s.select)
  const panelWidth = useWorkbenchViewStore((s) => s.panelWidth)
  const setPanelWidth = useWorkbenchViewStore((s) => s.setPanelWidth)
  if (form === null) return null
  const found = selectedId !== null ? findWidget(form, selectedId) : null
  const panel = { width: panelWidth, onWidthChange: setPanelWidth, minWidth: MIN_WIDTH, maxWidth, onClose: () => select(null), testId: "workbench-properties" }
  if (sheetSelected) {
    return (
      <SidePanel {...panel} title="Sheet" icon={<Sheet size={14} style={{ color: COMPONENT_COLOR }} />}>
        <SheetSettings form={form} page={activePage(form, pageId)} />
      </SidePanel>
    )
  }
  const section = sectionId !== null ? findSection(form, sectionId) : null
  if (section !== null) {
    return (
      <SidePanel {...panel} title="Section" icon={<Rows3 size={14} style={{ color: COMPONENT_COLOR }} />}>
        {/* Keyed by the section, so a height refused for one is never shown, or saved, for the next. */}
        <SectionSettings key={section.section.id} form={form} page={section.page} section={section.section} index={section.index} />
      </SidePanel>
    )
  }
  if (found === null) return null
  const { label, icon: Icon, color } = WIDGET_KINDS[found.widget.type]

  return (
    <SidePanel {...panel} title={label} icon={<Icon size={14} style={{ color }} />}>
      {found.widget.type === "heading" ? <HeadingSettings form={form} widget={found.widget} /> : <FieldsSettings form={form} widget={found.widget} />}
    </SidePanel>
  )
}

/** The showing sheet's panel: its title (a blank one kept out) and when it shows. */
function SheetSettings({ form, page }: { form: FormSpec; page: Page }) {
  const change = useWorkbenchFormStore((s) => s.change)
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto px-3 py-3 text-xs">
      <Section title="Title">
        <CommittedTextField
          aria-label="Sheet title"
          value={page.title}
          onCommit={(title) => {
            const trimmed = title.trim()
            if (trimmed) change((spec) => renamePage(spec, page.id, trimmed))
          }}
          className={`${FIELD_CLASS} w-full`}
          style={INPUT_STYLE}
        />
      </Section>
      <Section title="Show when">
        <ShowWhenEditor
          form={form}
          showWhen={page.show_when}
          onChange={(next) => change((spec) => setPageShowWhen(spec, page.id, next))}
        />
      </Section>
    </div>
  )
}

/**
 * A section's panel: its title (a blank one kept out), when it shows, its height in whole
 * pixels, never under what its components reach, with Fit, and adding a section under it
 * or deleting it, asking first when components are in it, while it is not the sheet's only
 * section.
 */
function SectionSettings({ form, page, section, index }: { form: FormSpec; page: Page; section: SheetSection; index: number }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const selectSection = useWorkbenchViewStore((s) => s.selectSection)
  const least = leastSectionHeight(section.widgets)
  const height = (text: string) =>
    /^\d+$/.test(text.trim()) && Number.isSafeInteger(Number(text)) && Number(text) >= least ? null : `Enter a whole number of pixels, ${least} or more.`
  const addBelow = () => {
    const added = createSection(page)
    change((spec) => addSection(spec, page.id, added, index + 1))
    selectSection(added.id)
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto px-3 py-3 text-xs">
      <Section title="Title">
        <CommittedTextField
          aria-label="Section title"
          value={section.title}
          onCommit={(title) => {
            const trimmed = title.trim()
            if (trimmed) change((spec) => renameSection(spec, section.id, trimmed))
          }}
          className={`${FIELD_CLASS} w-full`}
          style={INPUT_STYLE}
        />
      </Section>
      <Section title="Show when">
        <ShowWhenEditor
          form={form}
          showWhen={section.show_when}
          onChange={(next) => change((spec) => setSectionShowWhen(spec, section.id, next))}
        />
      </Section>
      <Section title="Layout">
        <div className="flex items-start gap-2">
          <label className="flex items-center gap-2">
            <span className="w-14" style={{ color: "var(--text-muted)" }}>
              Height
            </span>
            <ValidatedTextField
              dataTestId="section-height"
              value={String(section.height)}
              onCommit={(text) => change((spec) => setSectionHeight(spec, section.id, Number(text)))}
              validate={height}
              containerClassName="w-20"
              className={`${FIELD_CLASS} w-full tabular-nums`}
              style={INPUT_STYLE}
            />
          </label>
          <button
            type="button"
            onClick={() => change((spec) => fitSection(spec, section.id))}
            title="Fit the section to its components"
            className="quiet-action focus-ring rounded px-1.5 py-1 text-[11px] font-medium"
          >
            Fit
          </button>
        </div>
      </Section>
      <div className="flex flex-col items-start gap-1">
        <button type="button" onClick={addBelow} className="quiet-action focus-ring inline-flex items-center gap-1 rounded px-1 py-0.5 -ml-1 text-[11px] font-medium">
          <Plus size={11} aria-hidden="true" />
          Add section below
        </button>
        <button
          type="button"
          disabled={page.sections.length === 1}
          title={page.sections.length === 1 ? "A sheet keeps at least one section" : undefined}
          onClick={() => deleteSection(page, section)}
          className="focus-ring inline-flex items-center gap-1 rounded px-1 py-0.5 -ml-1 text-[11px] font-medium hover-bg disabled:opacity-50"
          style={{ color: "var(--danger)" }}
        >
          <Trash2 size={11} aria-hidden="true" />
          Delete section
        </button>
      </div>
    </div>
  )
}

/** A Heading's panel: its text and description, when it shows, and its size and alignment. */
function HeadingSettings({ form, widget }: { form: FormSpec; widget: HeadingWidget }) {
  const change = useWorkbenchFormStore((s) => s.change)
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto px-3 py-3 text-xs">
      <Section title="Text">
        <CommittedTextField
          aria-label="Heading text"
          value={widget.text}
          placeholder="Name a section of the sheet"
          onCommit={(text) => change((spec) => updateWidget(spec, widget.id, { text }))}
          className={`${FIELD_CLASS} w-full`}
          style={INPUT_STYLE}
        />
      </Section>
      <Section title="Description">
        <CommittedTextField
          aria-label="Heading description"
          value={widget.description}
          placeholder="None"
          onCommit={(description) => change((spec) => updateWidget(spec, widget.id, { description }))}
          className={`${FIELD_CLASS} w-full`}
          style={INPUT_STYLE}
        />
      </Section>
      <Section title="Show when">
        <ShowWhenEditor
          form={form}
          showWhen={widget.show_when}
          onChange={(next) => change((spec) => updateWidget(spec, widget.id, { show_when: next }))}
        />
      </Section>
      <Section title="Look">
        <label className="flex items-center gap-2">
          <span className="w-14" style={{ color: "var(--text-muted)" }}>
            Size
          </span>
          <select
            aria-label="Size"
            value={widget.size}
            onChange={(event) => change((spec) => updateWidget(spec, widget.id, { size: event.target.value as HeadingWidget["size"] }))}
            className={FIELD_CLASS}
            style={INPUT_STYLE}
          >
            {HEADING_SIZES.map(({ value, label }) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <div className="flex items-center gap-2">
          <span className="w-14 shrink-0" style={{ color: "var(--text-muted)" }}>
            Align
          </span>
          <div className="min-w-0 flex-1">
            <ToggleButtonGroup
              ariaLabel="Align"
              value={widget.align}
              accentColor={WIDGET_KINDS.heading.color}
              options={HEADING_ALIGNS.map(({ value, label, icon: Icon }) => ({ key: value, label, icon: <Icon size={13} aria-hidden="true" /> }))}
              onChange={(align) => {
                if (align !== widget.align) change((spec) => updateWidget(spec, widget.id, { align }))
              }}
            />
          </div>
        </div>
      </Section>
    </div>
  )
}

function Section({ title, aside, children }: { title: string; aside?: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-1.5" aria-label={title}>
      <div className="flex items-baseline justify-between">
        <EditorLabel as="div">{title}</EditorLabel>
        {aside && (
          <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>
            {aside}
          </span>
        )}
      </div>
      {children}
    </section>
  )
}

function FieldsSettings({ form, widget }: { form: FormSpec; widget: FieldsWidget }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const showSection = useWorkbenchViewStore((s) => s.showSection)
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set())
  // A Chart plots either kind of table; a Table and a Collection each show one.
  const rows = widget.type === "chart" ? null : rowsShown(widget)
  const tables = rows === null ? form.schema.tables : form.schema.tables.filter((table) => table.rows === rows)
  // The rows the fields already chosen fill: columns of tables that fill other rows cannot
  // share the grid. A key-less many-row table, for one, lines up with nothing else.
  const first = widget.fields.map((field) => fieldColumn(form, field)).find((shown) => shown?.table.rows === rows)
  const grain = widget.type === "chart" ? chartGrain(form, widget) : first ? tableGrain(first.table) : null
  const toggleCollapsed = (id: string) =>
    setCollapsed((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  const wholeNumber = (min: number, max: number) => (text: string) =>
    /^\d+$/.test(text.trim()) && Number(text) >= min && Number(text) <= max ? null : `Enter a whole number from ${min} to ${max}.`

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto px-3 py-3 text-xs">
      <Section title="Title">
        <CommittedTextField
          aria-label="Title"
          value={widget.title}
          placeholder="None"
          onCommit={(title) => change((spec) => updateWidget(spec, widget.id, { title }))}
          className={`${FIELD_CLASS} w-full`}
          style={INPUT_STYLE}
        />
      </Section>
      <Section title="Show when">
        <ShowWhenEditor
          form={form}
          showWhen={widget.show_when}
          onChange={(next) => change((spec) => updateWidget(spec, widget.id, { show_when: next }))}
        />
      </Section>
      {widget.type !== "chart" && widget.fields.length > 0 && (
        <Section title="Layout">
          <label className="flex items-center gap-2">
            <span className="w-14" style={{ color: "var(--text-muted)" }}>
              {widget.type === "collection" ? "Columns" : "Rows"}
            </span>
            {widget.type === "collection" ? (
              <ValidatedTextField
                dataTestId="widget-columns"
                value={String(widget.columns)}
                onCommit={(text) => change((spec) => updateWidget(spec, widget.id, { columns: Number(text) }))}
                validate={wholeNumber(1, 12)}
                containerClassName="w-16"
                className={`${FIELD_CLASS} w-full tabular-nums`}
                style={INPUT_STYLE}
              />
            ) : (
              <ValidatedTextField
                dataTestId="widget-rows"
                value={String(widget.rows)}
                onCommit={(text) => change((spec) => updateWidget(spec, widget.id, { rows: Number(text) }))}
                validate={wholeNumber(1, 50)}
                containerClassName="w-16"
                className={`${FIELD_CLASS} w-full tabular-nums`}
                style={INPUT_STYLE}
              />
            )}
            <span style={{ color: "var(--text-muted)" }}>
              {widget.type === "collection" ? "boxes across; the rows follow" : "rows the grid shows"}
            </span>
          </label>
          {widget.type === "tableInput" && (
            <div className="flex items-center gap-2">
              <span className="w-14 shrink-0" aria-hidden="true" />
              <ConfigCheckbox
                label="Fixed rows"
                checked={widget.fixed_rows}
                onChange={(fixed_rows) => change((spec) => updateWidget(spec, widget.id, { fixed_rows }))}
              />
              <span style={{ color: "var(--text-muted)" }}>no Add row or delete</span>
            </div>
          )}
        </Section>
      )}
      {widget.type === "tableInput" && widget.fields.length > 0 && (
        <Section title="Look">
          <label className="flex items-center gap-2">
            <span className="w-14" style={{ color: "var(--text-muted)" }}>
              Density
            </span>
            <select
              aria-label="Density"
              value={widget.density}
              onChange={(event) => change((spec) => updateWidget(spec, widget.id, { density: event.target.value as TableOptions["density"] }))}
              className={FIELD_CLASS}
              style={INPUT_STYLE}
            >
              {DENSITIES.map(({ value, label }) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2">
            <span className="w-14" style={{ color: "var(--text-muted)" }}>
              Gridlines
            </span>
            <select
              aria-label="Gridlines"
              value={widget.gridlines}
              onChange={(event) => change((spec) => updateWidget(spec, widget.id, { gridlines: event.target.value as TableOptions["gridlines"] }))}
              className={FIELD_CLASS}
              style={INPUT_STYLE}
            >
              {GRIDLINES.map(({ value, label }) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <div className="flex items-center gap-2">
            <span className="w-14 shrink-0" aria-hidden="true" />
            <div className="flex items-center gap-4">
              <ConfigCheckbox
                label="Row numbers"
                checked={widget.row_numbers}
                onChange={(row_numbers) => change((spec) => updateWidget(spec, widget.id, { row_numbers }))}
              />
              <ConfigCheckbox
                label="Striped rows"
                checked={widget.striped}
                onChange={(striped) => change((spec) => updateWidget(spec, widget.id, { striped }))}
              />
            </div>
          </div>
        </Section>
      )}
      {widget.type === "chart" && widget.fields.length > 0 && (
        <Section title="Chart">
          <ChartSettings form={form} widget={widget} />
        </Section>
      )}
      {widget.fields.length > 0 && (
        <Section title="Order" aside="drag, or Alt+Up and Alt+Down">
          <FieldOrder form={form} widget={widget} />
        </Section>
      )}
      <Section title="Fields" aside={`${widget.fields.length} shown`}>
        {tables.length === 0 ? (
          <p style={{ color: "var(--text-muted)" }}>
            The schema has no {rows === null ? "" : rows === "one" ? "one-row " : "many-row "}tables yet.{" "}
            <button type="button" onClick={() => showSection("schema")} className="quiet-action focus-ring rounded">
              Open the schema
            </button>
          </p>
        ) : (
          tables.map((table) => (
            <TableFields
              key={table.id}
              widget={widget}
              table={table}
              usable={grain === null || tableGrain(table) === grain}
              plots={widget.type === "chart"}
              collapsed={collapsed.has(table.id)}
              onToggle={() => toggleCollapsed(table.id)}
            />
          ))
        )}
      </Section>
    </div>
  )
}

/** The fields a component shows, in order: drag one, or Alt+Up and Alt+Down on it, to move it. */
function FieldOrder({ form, widget }: { form: FormSpec; widget: FieldsWidget }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const dragFor = useListReorder((from, to) => change((spec) => moveField(spec, widget.id, from, to)))
  return (
    <div role="list" className="flex flex-col gap-0.5 rounded-md p-1" style={{ border: "1px solid var(--border)", background: "var(--bg-elevated)" }}>
      {widget.fields.map((field, index) => (
        <OrderRow key={fieldKey(field)} form={form} widget={widget} field={field} index={index} drag={dragFor(index)} />
      ))}
    </div>
  )
}

function OrderRow({ form, widget, field, index, drag }: { form: FormSpec; widget: FieldsWidget; field: FieldRef; index: number; drag: RowDrag }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const found = fieldColumn(form, field)
  const label = found ? found.column.label || readableName(found.column.name) : "Missing column"
  const type = found ? COLUMN_TYPES[found.column.type] : null
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!event.altKey || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) return
    event.preventDefault()
    const to = index + (event.key === "ArrowUp" ? -1 : 1)
    if (to >= 0 && to < widget.fields.length) change((spec) => moveField(spec, widget.id, index, to))
  }
  return (
    <div
      tabIndex={0}
      role="listitem"
      aria-label={`${index + 1}. ${label}`}
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
      <span className="w-4 shrink-0 text-right tabular-nums" style={{ color: "var(--text-muted)" }}>
        {index + 1}
      </span>
      {type && (
        <span title={type.label} className={`flex size-4 shrink-0 items-center justify-center ${type.color}`}>
          <type.icon size={12} aria-hidden="true" />
        </span>
      )}
      <span className="min-w-0 flex-1 truncate" style={{ color: found ? "var(--text-primary)" : "var(--warning)" }}>
        {label}
      </span>
      {found && (
        <span className="shrink-0 truncate font-mono" style={{ color: "var(--text-muted)" }}>
          {found.table.name}
        </span>
      )}
      <button
        type="button"
        onClick={() => change((spec) => toggleField(spec, widget.id, field))}
        aria-label={`Stop showing ${label}`}
        title="Stop showing it"
        className="icon-danger-btn focus-ring grid size-5 shrink-0 place-items-center rounded opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
      >
        <X size={12} aria-hidden="true" />
      </button>
    </div>
  )
}

function TableFields({
  widget,
  table,
  usable,
  plots,
  collapsed,
  onToggle,
}: {
  widget: FieldsWidget
  table: SchemaTable
  /** Whether its rows line up with the fields already chosen. */
  usable: boolean
  /** A Chart's: only the columns it can plot can be ticked. */
  plots: boolean
  collapsed: boolean
  onToggle: () => void
}) {
  const change = useWorkbenchFormStore((s) => s.change)
  const isShown = (columnId: string) => widget.fields.some((field) => field.table === table.id && field.column === columnId)
  const shown = table.columns.filter((column) => isShown(column.id)).length
  const Chevron = collapsed ? ChevronRight : ChevronDown

  return (
    <div
      className="rounded-md"
      style={{ border: "1px solid var(--border)", background: "var(--bg-elevated)", opacity: usable ? 1 : 0.5 }}
      title={usable ? undefined : "Its rows don't line up with the fields already chosen"}
      data-testid={`fields-table-${table.id}`}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={!collapsed}
        aria-label={`${collapsed ? "Expand" : "Collapse"} ${table.name}`}
        className="focus-ring hover-bg flex w-full items-center gap-1.5 rounded-md px-1.5 py-1 text-left"
      >
        <Chevron size={14} className="shrink-0" style={{ color: "var(--text-muted)" }} aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate font-mono font-semibold">{table.name}</span>
        <span className="text-[11px] font-semibold" style={{ color: table.role === "input" ? "var(--accent)" : "var(--success)" }}>
          {table.role === "input" ? "Input" : "Output"}
        </span>
        <span className="w-8 text-right text-[11px]" style={{ color: "var(--text-muted)" }}>
          {shown}/{table.columns.length}
        </span>
      </button>
      {!collapsed && (
        <div className="flex flex-col px-1 py-1" style={{ borderTop: "1px solid var(--border)" }}>
          {table.columns.length === 0 && (
            <p className="px-1.5 py-0.5" style={{ color: "var(--text-muted)" }}>
              No columns yet
            </p>
          )}
          {table.columns.map((column) => {
            const { label, icon: Icon, color } = COLUMN_TYPES[column.type]
            const checked = isShown(column.id)
            const tickable = checked || (usable && (!plots || plottable(column)))
            return (
              <label
                key={column.id}
                title={plots && !plottable(column) ? "A Chart plots number columns other than an index" : undefined}
                className={`flex items-center gap-1.5 rounded px-1.5 py-0.5 ${tickable ? "cursor-pointer hover-bg" : "cursor-not-allowed"}`}
              >
                <input
                  type="checkbox"
                  aria-label={`Show ${table.name} ${column.name}`}
                  checked={checked}
                  disabled={!tickable}
                  onChange={() => change((spec) => toggleField(spec, widget.id, { table: table.id, column: column.id }))}
                  style={{ accentColor: "var(--accent)" }}
                />
                <span title={label} className={`flex size-4 shrink-0 items-center justify-center ${color}`}>
                  <Icon size={12} aria-hidden="true" />
                </span>
                <span className="min-w-0 flex-1 truncate font-mono">{column.name}</span>
                <span className="shrink-0 truncate" style={{ color: "var(--text-muted)" }}>
                  {column.label || readableName(column.name)}
                </span>
              </label>
            )
          })}
        </div>
      )}
    </div>
  )
}
