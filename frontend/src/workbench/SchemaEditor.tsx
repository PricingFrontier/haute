/**
 * The workbench's schema editor (specs/workbench): the form's tables of named, typed
 * columns, which are the pipeline's tables. Every text field commits on blur or Enter, so
 * an edit is one undo step; Enter in a column's name adds the next column, and Alt+Up and
 * Alt+Down move a column, as the step editor's cards move. Removing what a sheet shows
 * asks first.
 */
import { ChevronDown, ChevronRight, KeyRound, Plus, SlidersHorizontal, Trash2, X, type LucideIcon } from "lucide-react"
import { useMemo, useState, type ButtonHTMLAttributes, type FocusEvent, type KeyboardEvent, type ReactNode } from "react"
import type { FormSpec, SchemaColumn, SchemaTable } from "../api/types"
import { CommittedTextField, ConfigCheckbox, IconSelect, ValidatedTextField } from "../components/form"
import { INPUT_STYLE } from "../panels/editors/_shared"
import useDocumentStatusStore from "../stores/useDocumentStatusStore"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import {
  addSchemaColumn,
  addSchemaTable,
  changeSchemaColumnType,
  createIndexColumn,
  createSchemaColumn,
  createSchemaTable,
  fieldUses,
  moveSchemaColumn,
  readableName,
  removeSchemaColumn,
  removeSchemaTable,
  schemaProblems,
  setSchemaTableRows,
  updateSchemaColumn,
  updateSchemaTable,
} from "../utils/workbenchForm"
import { COLUMN_TYPES, COLUMN_TYPE_OPTIONS, isNumeric } from "./columnTypes"

const ROLES: ReadonlyArray<{ value: SchemaTable["role"]; label: string; title: string }> = [
  { value: "input", label: "Input", title: "Sent to the pricing engine" },
  { value: "output", label: "Output", title: "Returned by the pricing engine" },
]

const ROWS: ReadonlyArray<{ value: SchemaTable["rows"]; label: string; title: string }> = [
  { value: "one", label: "One row", title: "One row per quote, such as policy details" },
  { value: "many", label: "Many rows", title: "Many rows per quote, such as an equipment schedule" },
]

const FIELD_CLASS = "focus-ring min-w-0 rounded-md px-1.5 py-0.5 text-xs"
const CHIP_CLASS = "focus-ring shrink-0 rounded-md px-1.5 py-0.5 text-[11px] font-semibold"
const HOVER_ONLY = "opacity-0 group-hover:opacity-100 focus-visible:opacity-100"

/** The form as the store holds it now, for an edit that builds on it. */
function currentForm(): FormSpec {
  const { form } = useWorkbenchFormStore.getState()
  if (form === null) throw new Error("The workbench's form is not loaded")
  return form
}

/** An input column's rules in a few words, shown beside its name. */
function rulesSummary(column: SchemaColumn): string {
  const parts: string[] = []
  if (column.required) parts.push("required")
  if (isNumeric(column.type) && (column.min !== null || column.max !== null)) {
    parts.push(`${column.min ?? "…"} to ${column.max ?? "…"}`)
  }
  if (column.type !== "bool" && column.type !== "date" && column.options.length > 0) {
    parts.push(`${column.options.length} ${column.options.length === 1 ? "value" : "values"}`)
  }
  return parts.join(" · ")
}

const components = (n: number) => `${n} ${n === 1 ? "component" : "components"}`

/** A new table or column gets a placeholder name; selecting it lets typing replace it. */
const selectAll = (e: FocusEvent<HTMLInputElement>) => e.target.select()

/** The props that focus a just-added name and select it, once. */
type Fresh = { autoFocus: boolean; onFocus?: (e: FocusEvent<HTMLInputElement>) => void; onBlur: () => void }

/** The Schema section: the form's tables of named, typed columns. */
export default function SchemaEditor() {
  const form = useWorkbenchFormStore((s) => s.form)
  const change = useWorkbenchFormStore((s) => s.change)
  const reservedLabels = useDocumentStatusStore((s) => s.capabilities?.reserved_api_input_frame_labels)
  const reserved = useMemo(() => new Set(reservedLabels ?? []), [reservedLabels])
  // The table or column just added, whose name takes the focus.
  const [added, setAdded] = useState<string | null>(null)
  // The column whose label and rules are showing.
  const [details, setDetails] = useState<string | null>(null)
  // The tables shown collapsed: a view setting, neither saved nor undone.
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set())
  if (form === null) throw new Error("The schema editor needs the form loaded")
  const problems = schemaProblems(form, reserved)
  const { tables } = form.schema

  const addTable = () => {
    const table = createSchemaTable(form)
    change((f) => addSchemaTable(f, table))
    setAdded(table.id)
  }
  const toggleCollapsed = (id: string) =>
    setCollapsed((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  return (
    <div className="min-h-0 flex-1 overflow-auto" data-testid="schema-editor">
      <div className="flex max-w-[640px] flex-col gap-1.5 px-4 py-3 text-xs">
        {tables.length === 0 && (
          <p className="px-1" style={{ color: "var(--text-muted)" }}>
            No tables yet. A table holds related columns, such as <code className="font-mono">policy_details</code>.
          </p>
        )}
        {tables.map((table) => (
          <TableBlock
            key={table.id}
            form={form}
            table={table}
            added={added}
            setAdded={setAdded}
            details={details}
            setDetails={setDetails}
            collapsed={collapsed.has(table.id)}
            onToggleCollapsed={() => toggleCollapsed(table.id)}
            problems={problems.filter((p) => p.tableId === table.id).map((p) => p.message)}
          />
        ))}
        <AddButton onClick={addTable}>Add table</AddButton>
      </div>
    </div>
  )
}

interface TableBlockProps {
  form: FormSpec
  table: SchemaTable
  added: string | null
  setAdded: (id: string | null) => void
  details: string | null
  setDetails: (id: string | null) => void
  collapsed: boolean
  onToggleCollapsed: () => void
  problems: string[]
}

function TableBlock({ form, table, added, setAdded, details, setDetails, collapsed, onToggleCollapsed, problems }: TableBlockProps) {
  const change = useWorkbenchFormStore((s) => s.change)
  const uses = fieldUses(form, table.id).length
  const [confirming, setConfirming] = useState(false)
  const remove = () => change((f) => removeSchemaTable(f, table.id))
  const fresh = (id: string): Fresh => ({
    autoFocus: added === id,
    onFocus: added === id ? selectAll : undefined,
    onBlur: () => setAdded(null),
  })
  const count = table.columns.length

  const insertAt = (index: number) => {
    const column = createSchemaColumn(currentForm(), table.id)
    change((f) => addSchemaColumn(f, table.id, column, index))
    setAdded(column.id)
  }

  return (
    <section
      aria-label={`Table ${table.name}`}
      data-testid={`schema-table-${table.id}`}
      className="rounded-md border"
      style={{ borderColor: "var(--border)", background: "var(--bg-elevated)" }}
    >
      <div className="group flex items-center gap-1 px-1 py-1">
        <IconButton
          icon={collapsed ? ChevronRight : ChevronDown}
          size={14}
          onClick={onToggleCollapsed}
          aria-expanded={!collapsed}
          aria-label={`${collapsed ? "Expand" : "Collapse"} ${table.name}`}
          title={collapsed ? "Show columns" : "Hide columns"}
          style={{ color: "var(--text-muted)" }}
        />
        <CommittedTextField
          aria-label="Table name"
          value={table.name}
          onCommit={(name) => change((f) => updateSchemaTable(f, table.id, { name }))}
          {...fresh(table.id)}
          className="focus-ring min-w-0 flex-1 rounded-md border border-transparent bg-transparent px-1.5 py-0.5 font-mono font-semibold"
          style={{ color: "var(--text-primary)" }}
        />
        <select
          aria-label={`Role of ${table.name}`}
          value={table.role}
          onChange={(e) => change((f) => updateSchemaTable(f, table.id, { role: e.target.value as SchemaTable["role"] }))}
          title={ROLES.find((role) => role.value === table.role)?.title}
          className={CHIP_CLASS}
          style={{ ...INPUT_STYLE, color: table.role === "input" ? "var(--accent)" : "var(--success)" }}
        >
          {ROLES.map((role) => (
            <option key={role.value} value={role.value}>
              {role.label}
            </option>
          ))}
        </select>
        <select
          aria-label={`Rows of ${table.name}`}
          value={table.rows}
          onChange={(e) => change((f) => setSchemaTableRows(f, table.id, e.target.value as SchemaTable["rows"]))}
          title={ROWS.find((rows) => rows.value === table.rows)?.title}
          className={CHIP_CLASS}
          style={{ ...INPUT_STYLE, color: "var(--text-secondary)" }}
        >
          {ROWS.map((rows) => (
            <option key={rows.value} value={rows.value}>
              {rows.label}
            </option>
          ))}
        </select>
        <span
          className="shrink-0 px-1 text-[11px]"
          style={{ color: problems.length > 0 ? "var(--warning)" : "var(--text-muted)" }}
          title={problems.join("\n") || undefined}
        >
          {count} {count === 1 ? "col" : "cols"}
          {problems.length > 0 && `, ${problems.length} ${problems.length === 1 ? "problem" : "problems"}`}
        </span>
        <IconButton
          icon={Trash2}
          onClick={() => (uses > 0 ? setConfirming(true) : remove())}
          aria-label={`Delete table ${table.name}`}
          title="Delete table"
          className={`icon-danger-btn ${HOVER_ONLY}`}
        />
      </div>
      {confirming && (
        <ConfirmBar
          message={`Its columns are shown in ${components(uses)}; deleting it takes them out of ${uses === 1 ? "it" : "them"} too.`}
          action="Delete"
          onConfirm={() => {
            setConfirming(false)
            remove()
          }}
          onCancel={() => setConfirming(false)}
          className="mx-1.5 mb-1.5"
        />
      )}
      {!collapsed && (
        <div className="flex flex-col gap-1 px-1.5 py-1.5" style={{ borderTop: "1px solid var(--border)" }}>
          {table.columns.map((column, index) => (
            <ColumnRow
              key={column.id}
              form={form}
              table={table}
              column={column}
              index={index}
              fresh={fresh(column.id)}
              open={details === column.id}
              onToggleDetails={() => setDetails(details === column.id ? null : column.id)}
              onInsertAfter={() => insertAt(index + 1)}
            />
          ))}
          <div className="flex items-center gap-3">
            <AddButton onClick={() => insertAt(count)}>Add column</AddButton>
            {table.rows === "many" && <IndexControl form={form} table={table} onAdded={setAdded} />}
          </div>
          {problems.map((message) => (
            <p key={message} className="px-1 text-[11px]" style={{ color: "var(--warning)" }}>
              {message}
            </p>
          ))}
        </div>
      )}
    </section>
  )
}

interface ColumnRowProps {
  form: FormSpec
  table: SchemaTable
  column: SchemaColumn
  index: number
  fresh: Fresh
  open: boolean
  onToggleDetails: () => void
  onInsertAfter: () => void
}

function ColumnRow({ form, table, column, index, fresh, open, onToggleDetails, onInsertAfter }: ColumnRowProps) {
  const change = useWorkbenchFormStore((s) => s.change)
  const uses = fieldUses(form, table.id, column.id).length
  const [confirming, setConfirming] = useState(false)
  const remove = () => change((f) => removeSchemaColumn(f, table.id, column.id))
  const { label, icon: Icon, color } = COLUMN_TYPES[column.type]
  const summary = column.index ? "row number" : table.role === "input" ? rulesSummary(column) : ""
  const set = (patch: Partial<SchemaColumn>) => change((f) => updateSchemaColumn(f, table.id, column.id, patch))

  // Enter adds the next column; Alt+Up and Alt+Down move this one.
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault()
      onInsertAfter()
    } else if (e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
      e.preventDefault()
      const to = index + (e.key === "ArrowUp" ? -1 : 1)
      if (to >= 0 && to < table.columns.length) change((f) => moveSchemaColumn(f, table.id, column.id, to))
    }
  }

  const markerStyle = { background: "var(--chrome-hover)", border: "1px solid var(--border)" }
  return (
    <div className="flex flex-col gap-1" data-testid={`schema-column-${column.id}`}>
      <div className="group flex items-stretch gap-1 rounded">
        {column.index ? (
          // The index is always an Integer.
          <span
            className={`relative flex w-6 shrink-0 items-center justify-center rounded-md ${color}`}
            style={markerStyle}
            title="Integer: each row's number, from 1"
          >
            <Icon size={12} aria-hidden="true" />
          </span>
        ) : (
          <IconSelect
            icon={Icon}
            value={column.type}
            options={COLUMN_TYPE_OPTIONS}
            onChange={(type) => change((f) => changeSchemaColumnType(f, table.id, column.id, type))}
            ariaLabel={`Type of ${column.name}`}
            title={`${label} (change the type)`}
            className={`w-6 rounded-md ${color}`}
            style={markerStyle}
          />
        )}
        <CommittedTextField
          aria-label="Column name"
          aria-keyshortcuts="Alt+ArrowUp Alt+ArrowDown"
          value={column.name}
          onCommit={(name) => set({ name })}
          {...fresh}
          onKeyDown={onKeyDown}
          className={`${FIELD_CLASS} flex-1 font-mono`}
          style={INPUT_STYLE}
        />
        {summary && (
          <span className="shrink-0 self-center px-1 text-[11px]" style={{ color: "var(--text-muted)" }}>
            {summary}
          </span>
        )}
        {table.rows === "many" && (
          <IconButton
            icon={KeyRound}
            onClick={() => set({ key: !column.key })}
            aria-pressed={column.key}
            aria-label={`Key: ${column.name}`}
            title={column.key ? "A key: it says which row is which (click to unmark)" : "Make this a key: it says which row is which"}
            className="self-center"
            style={{ color: column.key ? "var(--accent)" : "var(--text-muted)" }}
          />
        )}
        <IconButton
          icon={SlidersHorizontal}
          onClick={onToggleDetails}
          aria-expanded={open}
          aria-label={`Label and rules of ${column.name}`}
          title="Label and rules"
          className={`self-center ${open ? "" : HOVER_ONLY}`}
          style={{ color: open ? "var(--text-primary)" : "var(--text-muted)" }}
        />
        <IconButton
          icon={X}
          onClick={() => (uses > 0 ? setConfirming(true) : remove())}
          aria-label={`Remove column ${column.name}`}
          title="Remove column"
          className={`icon-danger-btn self-center ${HOVER_ONLY}`}
        />
      </div>
      {confirming && (
        <ConfirmBar
          message={`Shown in ${components(uses)}; removing it takes it out of ${uses === 1 ? "that" : "them"} too.`}
          action="Remove"
          onConfirm={() => {
            setConfirming(false)
            remove()
          }}
          onCancel={() => setConfirming(false)}
          className="mr-12 ml-7"
        />
      )}
      {open && <ColumnDetails table={table} column={column} set={set} />}
    </div>
  )
}

/** A column's label and, in an input table, the rules its value must meet. */
function ColumnDetails({ table, column, set }: { table: SchemaTable; column: SchemaColumn; set: (patch: Partial<SchemaColumn>) => void }) {
  // The index takes no rules: nothing is typed into it.
  const input = table.role === "input" && !column.index
  const numberOrNothing = (text: string): string | null =>
    text.trim() === "" || Number.isFinite(Number(text)) ? null : "Enter a number."
  const bound = (text: string): number | null => (text.trim() === "" ? null : Number(text))
  return (
    <div
      className="mr-12 ml-7 grid grid-cols-[4rem_minmax(0,1fr)] items-center gap-x-2 gap-y-1 rounded-md px-2 py-1.5 text-[11px]"
      style={{ border: "1px solid var(--border)", background: "var(--bg-base)" }}
    >
      <span style={{ color: "var(--text-muted)" }}>Label</span>
      <CommittedTextField
        aria-label={`Label of ${column.name}`}
        value={column.label}
        placeholder={readableName(column.name)}
        onCommit={(label) => set({ label })}
        className={`${FIELD_CLASS} w-full`}
        style={INPUT_STYLE}
      />
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
                  placeholder="Min"
                  containerClassName="w-24"
                  className={`${FIELD_CLASS} w-full`}
                  style={INPUT_STYLE}
                />
                <span style={{ color: "var(--text-muted)" }}>to</span>
                <ValidatedTextField
                  dataTestId={`schema-column-${column.id}-max`}
                  value={column.max === null ? "" : String(column.max)}
                  onCommit={(text) => set({ max: bound(text) })}
                  validate={numberOrNothing}
                  placeholder="Max"
                  containerClassName="w-24"
                  className={`${FIELD_CLASS} w-full`}
                  style={INPUT_STYLE}
                />
              </div>
            </>
          )}
          {column.type !== "bool" && column.type !== "date" && (
            <>
              <span style={{ color: "var(--text-muted)" }}>Allowed</span>
              <CommittedTextField
                aria-label={`Allowed values of ${column.name}`}
                value={column.options.join(", ")}
                placeholder="Any value, or a list such as ACV, RC"
                onCommit={(text) => set({ options: text.split(",").map((value) => value.trim()).filter(Boolean) })}
                className={`${FIELD_CLASS} w-full`}
                style={INPUT_STYLE}
              />
            </>
          )}
        </>
      )}
    </div>
  )
}

/**
 * A many-row table's index: ticked, a column first in the table holding each row's
 * number, from 1 (`row_number` until renamed in its row, where it can also be made the
 * key). Unticked, it goes, asking first when a widget shows it.
 */
function IndexControl({ form, table, onAdded }: { form: FormSpec; table: SchemaTable; onAdded: (id: string) => void }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const index = table.columns.find((column) => column.index)
  const uses = index ? fieldUses(form, table.id, index.id).length : 0
  const [confirming, setConfirming] = useState(false)
  const remove = () => {
    if (index) change((f) => removeSchemaColumn(f, table.id, index.id))
  }
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1">
      <ConfigCheckbox
        label={`Add index to ${table.name}`}
        checked={index !== undefined}
        onChange={(checked) => {
          if (checked) {
            const column = createIndexColumn(currentForm(), table.id)
            change((f) => addSchemaColumn(f, table.id, column, 0))
            onAdded(column.id)
          } else if (uses > 0) setConfirming(true)
          else remove()
        }}
      />
      {confirming && (
        <ConfirmBar
          message={`The index is shown in ${components(uses)}; removing it takes it out of ${uses === 1 ? "that" : "them"} too.`}
          action="Remove"
          onConfirm={() => {
            setConfirming(false)
            remove()
          }}
          onCancel={() => setConfirming(false)}
          className="self-start"
        />
      )}
    </div>
  )
}

/** A small icon button; its colour is the caller's. */
function IconButton({
  icon: Icon,
  size = 12,
  className = "",
  ...props
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, "type" | "children"> & { icon: LucideIcon; size?: number }) {
  return (
    <button type="button" {...props} className={`focus-ring hover-bg grid size-5 shrink-0 place-items-center rounded ${className}`.trim()}>
      <Icon size={size} aria-hidden="true" />
    </button>
  )
}

/** Asks before removing what a widget on a sheet shows. */
function ConfirmBar({
  message,
  action,
  onConfirm,
  onCancel,
  className,
}: {
  message: string
  action: string
  onConfirm: () => void
  onCancel: () => void
  className: string
}) {
  return (
    <div
      role="alert"
      className={`flex items-center gap-2 rounded-md px-2 py-1 text-[11px] ${className}`}
      style={{ border: "1px solid var(--warning)", background: "var(--warning-soft)", color: "var(--text-primary)" }}
    >
      <span className="min-w-0 flex-1">{message}</span>
      <button type="button" onClick={onConfirm} className="focus-ring rounded px-1.5 py-0.5 font-semibold" style={{ color: "var(--danger)" }}>
        {action}
      </button>
      <button type="button" onClick={onCancel} className="focus-ring hover-bg rounded px-1.5 py-0.5" style={{ color: "var(--text-secondary)" }}>
        Cancel
      </button>
    </div>
  )
}

/** The quiet "+ Add …" text action under a list, as the step editor's. */
function AddButton({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="quiet-action focus-ring inline-flex items-center gap-1 justify-self-start rounded px-1 py-0.5 -ml-1 text-[11px] font-medium"
    >
      <Plus size={11} aria-hidden="true" />
      {children}
    </button>
  )
}
