/**
 * The properties panel (specs/workbench): what opens on the right, on Haute's side panel,
 * when a component on the sheet is selected. Its title, its layout (a Table's rows, a
 * Collection's columns), the order of the fields it shows, and the fields themselves,
 * ticked from the schema tables of its kind: many-row tables for a Table, one-row tables
 * for a Collection. A grid holds one kind of row, so once a Table shows a field, tables
 * whose rows do not line up with it are greyed out.
 */
import { ChevronDown, ChevronRight, GripVertical, X } from "lucide-react"
import { useState, type KeyboardEvent, type ReactNode } from "react"
import type { FieldRef, FormSpec, SchemaTable } from "../api/types"
import { CommittedTextField, EditorLabel, ValidatedTextField } from "../components/form"
import { SidePanel } from "../haute-ui"
import useListReorder, { type RowDrag } from "../hooks/useListReorder"
import { INPUT_STYLE } from "../panels/editors/_shared"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import {
  fieldColumn,
  findWidget,
  moveField,
  readableName,
  rowsShown,
  tableGrain,
  toggleField,
  updateWidget,
  type Widget,
} from "../utils/workbenchForm"
import { COLUMN_TYPES } from "./columnTypes"
import { COMPONENT_COLOR, WIDGET_KINDS } from "./widgetKinds"

const MIN_WIDTH = 320
/** At most half the window, so the sheet stays in view. */
const maxWidth = () => Math.max(MIN_WIDTH, Math.floor(window.innerWidth / 2))
const FIELD_CLASS = "focus-ring min-w-0 rounded-md px-1.5 py-1 text-xs"

const fieldKey = (field: FieldRef) => `${field.table}:${field.column}`

export default function PropertiesPanel() {
  const form = useWorkbenchFormStore((s) => s.form)
  const selectedId = useWorkbenchViewStore((s) => s.selectedId)
  const select = useWorkbenchViewStore((s) => s.select)
  const panelWidth = useWorkbenchViewStore((s) => s.panelWidth)
  const setPanelWidth = useWorkbenchViewStore((s) => s.setPanelWidth)
  const found = form !== null && selectedId !== null ? findWidget(form, selectedId) : null
  if (form === null || found === null) return null
  const { label, icon: Icon } = WIDGET_KINDS[found.widget.type]

  return (
    <SidePanel
      width={panelWidth}
      onWidthChange={setPanelWidth}
      minWidth={MIN_WIDTH}
      maxWidth={maxWidth}
      title={label}
      onClose={() => select(null)}
      icon={<Icon size={14} style={{ color: COMPONENT_COLOR }} />}
      testId="workbench-properties"
    >
      <FieldsSettings form={form} widget={found.widget} />
    </SidePanel>
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

function FieldsSettings({ form, widget }: { form: FormSpec; widget: Widget }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const showSection = useWorkbenchViewStore((s) => s.showSection)
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set())
  const rows = rowsShown(widget)
  const tables = form.schema.tables.filter((table) => table.rows === rows)
  // The rows the fields already chosen fill: columns of tables that fill other rows cannot
  // share the grid. A key-less many-row table, for one, lines up with nothing else.
  const first = widget.fields.map((field) => fieldColumn(form, field)).find((shown) => shown?.table.rows === rows)
  const grain = first ? tableGrain(first.table) : null
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
      {widget.fields.length > 0 && (
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
            The schema has no {rows === "one" ? "one-row" : "many-row"} tables yet.{" "}
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
function FieldOrder({ form, widget }: { form: FormSpec; widget: Widget }) {
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

function OrderRow({ form, widget, field, index, drag }: { form: FormSpec; widget: Widget; field: FieldRef; index: number; drag: RowDrag }) {
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
  collapsed,
  onToggle,
}: {
  widget: Widget
  table: SchemaTable
  /** Whether its rows line up with the fields already chosen. */
  usable: boolean
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
            return (
              <label
                key={column.id}
                className={`flex items-center gap-1.5 rounded px-1.5 py-0.5 ${usable || checked ? "cursor-pointer hover-bg" : "cursor-not-allowed"}`}
              >
                <input
                  type="checkbox"
                  aria-label={`Show ${table.name} ${column.name}`}
                  checked={checked}
                  disabled={!usable && !checked}
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
