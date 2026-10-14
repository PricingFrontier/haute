/**
 * The workbench's schema editor (specs/workbench): the form's tables of named, typed
 * columns, which are the pipeline's tables. Every text field commits on blur or Enter, so
 * an edit is one undo step; Enter in a column's name adds the next column, and Alt+Up and
 * Alt+Down move a column, as the step editor's cards move. Removing what a sheet shows
 * asks first. Haute's quote_details table, first, keeps its structure: its columns' labels,
 * groups and help, and the rules and defaults of those the underwriter types, are the
 * project's.
 */
import { ChevronDown, ChevronRight, KeyRound, Lock, Plus, RefreshCw, SlidersHorizontal, Trash2, X, type LucideIcon } from "lucide-react"
import { useEffect, useId, useMemo, useRef, useState, type ButtonHTMLAttributes, type FocusEvent, type KeyboardEvent, type ReactNode } from "react"
import { apiErrorMessage } from "../api/errors"
import { listProjectFiles } from "../api/projectFiles"
import type { FormSpec, SchemaColumn, SchemaTable } from "../api/types"
import { fetchParamFile } from "../api/workbench"
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
  conditionUses,
  fieldUses,
  isQuoteDetails,
  moveSchemaColumn,
  QUOTE_DETAILS_MANAGED,
  removeSchemaColumn,
  removeSchemaTable,
  schemaProblems,
  setRowPerValue,
  setSchemaTableRows,
  updateSchemaColumn,
  updateSchemaTable,
} from "../utils/workbenchForm"
import { presetColumn } from "../utils/sheetValues"
import { PIVOT_DECIMAL_PLACES_MAX, PIVOT_NUMBER_FORMATS } from "../panels/explore/pivotConfig"
import { NUMBER_FORMAT_LABELS } from "../panels/explore/pivotNumberFormat"
import { COLUMN_TYPES, COLUMN_TYPE_OPTIONS, isNumeric } from "./columnTypes"

const ROLES: ReadonlyArray<{ value: SchemaTable["role"]; label: string; title: string }> = [
  { value: "input", label: "Input", title: "Sent to the pricing engine" },
  { value: "output", label: "Output", title: "Returned by the pricing engine" },
]

const ROWS: ReadonlyArray<{ value: SchemaTable["rows"]; label: string; title: string }> = [
  { value: "one", label: "One row", title: "One row per quote, such as policy details" },
  { value: "many", label: "Many rows", title: "Many rows per quote, such as an equipment schedule" },
]

const DECIMAL_PLACES = Array.from({ length: PIVOT_DECIMAL_PLACES_MAX + 1 }, (_, places) => places)

const FIELD_CLASS = "focus-ring min-w-0 rounded-md px-1.5 py-0.5 text-xs"
const CHIP_CLASS = "focus-ring shrink-0 rounded-md px-1.5 py-0.5 text-[11px] font-semibold"
const HOVER_ONLY = "opacity-0 group-hover:opacity-100 focus-visible:opacity-100"

/** What Haute's quote_details table is, on its lock. */
const DETAILS_TITLE =
  "Haute's table of a quote's details: the underwriter app keeps it with each quote and lists it on the Quotes page. " +
  "Its columns are fixed; place them on the sheets, and give them labels, help and rules."

/** The form as the store holds it now, for an edit that builds on it. */
function currentForm(): FormSpec {
  const { form } = useWorkbenchFormStore.getState()
  if (form === null) throw new Error("The workbench's form is not loaded")
  return form
}

/** An input column's rules in a few words, shown beside its name, and whether it gives its table's rows. */
function rulesSummary(table: SchemaTable, column: SchemaColumn): string {
  const parts: string[] = []
  if (column.required) parts.push("required")
  if (isNumeric(column.type) && (column.min !== null || column.max !== null)) {
    parts.push(`${column.min ?? "…"} to ${column.max ?? "…"}`)
  }
  if (column.type !== "bool" && column.type !== "date" && column.options.length > 0) {
    const values = `${column.options.length} ${column.options.length === 1 ? "value" : "values"}`
    parts.push(column.options_source === null ? values : `${values} from ${column.options_source.file}`)
  }
  if (presetColumn(table)?.id === column.id) parts.push("a row each")
  return parts.join(" · ")
}

const components = (n: number) => `${n} ${n === 1 ? "component" : "components"}`

/** What removing a column or a table takes with it: the components showing it and the conditions testing it. */
function usedBy(shown: number, tested: number): string {
  const parts = shown > 0 ? [`shown in ${components(shown)}`] : []
  if (tested > 0) parts.push(`tested by the conditions of ${tested} ${tested === 1 ? "sheet, section or component" : "sheets, sections or components"}`)
  return parts.join(" and ")
}

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
  const shown = fieldUses(form, table.id).length
  const tested = conditionUses(form, table.id).length
  const uses = shown + tested
  const [confirming, setConfirming] = useState(false)
  const remove = () => change((f) => removeSchemaTable(f, table.id))
  const fresh = (id: string): Fresh => ({
    autoFocus: added === id,
    onFocus: added === id ? selectAll : undefined,
    onBlur: () => setAdded(null),
  })
  const count = table.columns.length
  // Haute's quote_details table keeps its structure.
  const locked = isQuoteDetails(table)

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
          readOnly={locked}
          title={locked ? DETAILS_TITLE : undefined}
          className="focus-ring min-w-0 flex-1 rounded-md border border-transparent bg-transparent px-1.5 py-0.5 font-mono font-semibold"
          style={{ color: "var(--text-primary)" }}
        />
        {locked && <Lock size={11} aria-label="Haute's table" className="shrink-0" style={{ color: "var(--text-muted)" }} />}
        <select
          aria-label={`Role of ${table.name}`}
          disabled={locked}
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
          disabled={locked}
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
        {!locked && (
          <IconButton
            icon={Trash2}
            onClick={() => (uses > 0 ? setConfirming(true) : remove())}
            aria-label={`Delete table ${table.name}`}
            title="Delete table"
            className={`icon-danger-btn ${HOVER_ONLY}`}
          />
        )}
      </div>
      {confirming && (
        <ConfirmBar
          message={`Its columns are ${usedBy(shown, tested)}; deleting it takes them out of ${uses === 1 ? "it" : "them"} too.`}
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
              locked={locked}
            />
          ))}
          {locked ? (
            <p className="px-1 text-[11px]" style={{ color: "var(--text-muted)" }}>
              Kept with each quote and listed on the Quotes page; the app sets reference, stage, assignee and archived.
            </p>
          ) : (
            <div className="flex items-center gap-3">
              <AddButton onClick={() => insertAt(count)}>Add column</AddButton>
              {table.rows === "many" && <IndexControl form={form} table={table} onAdded={setAdded} />}
            </div>
          )}
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
  /** In Haute's quote_details table: the column's name and type are fixed, and it stays. */
  locked: boolean
}

function ColumnRow({ form, table, column, index, fresh, open, onToggleDetails, onInsertAfter, locked }: ColumnRowProps) {
  const change = useWorkbenchFormStore((s) => s.change)
  const shown = fieldUses(form, table.id, column.id).length
  const tested = conditionUses(form, table.id, column.id).length
  const uses = shown + tested
  const [confirming, setConfirming] = useState(false)
  const remove = () => change((f) => removeSchemaColumn(f, table.id, column.id))
  const { label, icon: Icon, color } = COLUMN_TYPES[column.type]
  // A column the underwriter app sets takes no rules.
  const managed = locked && QUOTE_DETAILS_MANAGED.has(column.id)
  const summary = table.role === "input" ? rulesSummary(table, column) : ""
  const set = (patch: Partial<SchemaColumn>) => change((f) => updateSchemaColumn(f, table.id, column.id, patch))

  // Enter adds the next column; Alt+Up and Alt+Down move this one.
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (locked) return
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
        {column.index || locked ? (
          // The index is always an Integer; Haute's columns keep their types.
          <span
            className={`relative flex w-6 shrink-0 items-center justify-center rounded-md ${color}`}
            style={markerStyle}
            title={column.index ? "Integer: each row's number, from 1" : label}
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
          readOnly={locked}
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
          title="Label, help, rules and format"
          className={`self-center ${open ? "" : HOVER_ONLY}`}
          style={{ color: open ? "var(--text-primary)" : "var(--text-muted)" }}
        />
        {locked ? (
          <span className="size-5 shrink-0" aria-hidden="true" />
        ) : (
          <IconButton
            icon={X}
            onClick={() => (uses > 0 ? setConfirming(true) : remove())}
            aria-label={`Remove column ${column.name}`}
            title="Remove column"
            className={`icon-danger-btn self-center ${HOVER_ONLY}`}
          />
        )}
      </div>
      {confirming && (
        <ConfirmBar
          message={`${usedBy(shown, tested).replace(/^./, (first) => first.toUpperCase())}; removing it takes it out of ${uses === 1 ? "that" : "them"} too.`}
          action="Remove"
          onConfirm={() => {
            setConfirming(false)
            remove()
          }}
          onCancel={() => setConfirming(false)}
          className="mr-12 ml-7"
        />
      )}
      {open && <ColumnDetails table={table} column={column} managed={managed} set={set} />}
    </div>
  )
}

/**
 * A column's label, group and help; in an input table, what a new quote starts with in it
 * (in a one-row table), what an empty cell shows, the rules its value must meet and, in a
 * many-row table while it has allowed values, whether the table has a row for each; for a
 * number, how the sheets show it. The boxes hold no hint text: the docs say what each is.
 */
function ColumnDetails({
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
        className={`${FIELD_CLASS} w-full`}
        style={INPUT_STYLE}
      />
      <span style={{ color: "var(--text-muted)" }}>Group</span>
      <CommittedTextField
        aria-label={`Group of ${column.name}`}
        value={column.group}
        list={groupsId}
        onCommit={(group) => set({ group: group.trim() })}
        className={`${FIELD_CLASS} w-full`}
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
        className={`${FIELD_CLASS} w-full`}
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
              className={FIELD_CLASS}
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
              className={FIELD_CLASS}
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
            className={`${FIELD_CLASS} w-full`}
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
                  className={`${FIELD_CLASS} w-full`}
                  style={INPUT_STYLE}
                />
                <span style={{ color: "var(--text-muted)" }}>to</span>
                <ValidatedTextField
                  dataTestId={`schema-column-${column.id}-max`}
                  value={column.max === null ? "" : String(column.max)}
                  onCommit={(text) => set({ max: bound(text) })}
                  validate={numberOrNothing}
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
              <AllowedField column={column} set={set} />
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

/** Whether a file in the params folder is a CSV, whatever the case of its extension. */
const isCsv = (name: string): boolean => name.toLowerCase().endsWith(".csv")

/**
 * A column's allowed values: the distinct values of a column of a CSV in the pipeline's params
 * folder, its file chosen and then its column, copied in and read from the file again by
 * Refresh. The blank choice takes them away, with a row for each. Why a file or a column
 * cannot give values shows under them.
 */
function AllowedField({ column, set }: { column: SchemaColumn; set: (patch: Partial<SchemaColumn>) => void }) {
  const source = column.options_source
  // The CSV files in the params folder, read as the details open.
  const [files, setFiles] = useState<readonly string[]>([])
  // Each file's header as read, its columns to pick from.
  const [headers, setHeaders] = useState<Readonly<Record<string, readonly string[]>>>({})
  // A file chosen whose column is still to be picked: the form changes once it is.
  const [pendingFile, setPendingFile] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  // A read answers while the details are open, and a read of values only while it is the latest.
  const mounted = useRef(false)
  const latest = useRef(0)
  useEffect(() => {
    mounted.current = true
    void listProjectFiles("pipeline", "params")
      .then((folder) => {
        if (mounted.current) setFiles(folder.entries.filter((entry) => entry.kind === "file" && isCsv(entry.name)).map((entry) => entry.name))
      })
      // A project with no pipeline folder has no params files to choose.
      .catch(() => undefined)
    return () => {
      mounted.current = false
    }
  }, [])
  const file = pendingFile ?? source?.file ?? ""
  const header = headers[file]
  useEffect(() => {
    if (file === "" || header !== undefined) return
    void fetchParamFile(file)
      .then(({ columns }) => {
        if (mounted.current) setHeaders((read) => ({ ...read, [file]: columns }))
      })
      .catch((error: unknown) => {
        if (mounted.current) setProblem(apiErrorMessage(error))
      })
  }, [file, header])

  const readValues = (from: string, name: string, then: (values: string[]) => void) => {
    const ticket = ++latest.current
    setProblem(null)
    void fetchParamFile(from, name)
      .then(({ columns, values }) => {
        if (!mounted.current || ticket !== latest.current) return
        setHeaders((read) => ({ ...read, [from]: columns }))
        then(values)
      })
      .catch((error: unknown) => {
        if (mounted.current && ticket === latest.current) setProblem(apiErrorMessage(error))
      })
  }
  const chooseFile = (chosen: string) => {
    // A pick or a refresh still being read no longer answers.
    latest.current += 1
    setProblem(null)
    if (chosen !== "") setPendingFile(chosen === source?.file ? null : chosen)
    else {
      setPendingFile(null)
      if (source !== null || column.options.length > 0) set({ options: [], options_source: null, row_per_value: false })
    }
  }
  const chooseColumn = (name: string) =>
    readValues(file, name, (values) => {
      setPendingFile(null)
      set({ options_source: { file, column: name }, options: values })
    })
  const refresh = () => {
    if (source === null) return
    readValues(source.file, source.column, (values) => {
      if (JSON.stringify(values) !== JSON.stringify(column.options)) set({ options: values })
    })
  }

  const fileChoices = file === "" || files.includes(file) ? files : [...files, file]
  // The column the values came from stays offered when the file no longer has it.
  const columnChoices =
    pendingFile === null && source !== null && !(header ?? []).includes(source.column) ? [...(header ?? []), source.column] : (header ?? [])
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div className="flex min-w-0 items-center gap-1.5">
        <select
          aria-label={`Where the allowed values of ${column.name} come from`}
          value={file}
          onChange={(e) => chooseFile(e.target.value)}
          disabled={fileChoices.length === 0}
          className={`${FIELD_CLASS} ${file === "" ? "flex-1" : "max-w-[50%]"}`}
          style={INPUT_STYLE}
        >
          <option value="" />
          {fileChoices.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        {file !== "" && (
          <>
            <select
              aria-label={`Column the allowed values of ${column.name} come from`}
              value={pendingFile === null ? (source?.column ?? "") : ""}
              onChange={(e) => chooseColumn(e.target.value)}
              className={`${FIELD_CLASS} min-w-0 flex-1`}
              style={INPUT_STYLE}
            >
              {pendingFile !== null && <option value="" />}
              {columnChoices.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
            {pendingFile === null && (
              <IconButton
                icon={RefreshCw}
                onClick={refresh}
                aria-label={`Read the allowed values of ${column.name} again`}
                title={`Read the values from ${file} again`}
                style={{ color: "var(--text-muted)" }}
              />
            )}
          </>
        )}
      </div>
      {problem !== null && (
        <p role="alert" className="text-[11px]" style={{ color: "var(--danger)" }}>
          {problem}
        </p>
      )}
    </div>
  )
}

/**
 * What a new quote starts a one-row input column with, as its cells take a value: ticked
 * or not, one of the allowed values while it has some (one no longer allowed kept in view,
 * for its problem to name), a date, or typed. Blank or unticked, it has none.
 */
function DefaultField({ column, set }: { column: SchemaColumn; set: (patch: Partial<SchemaColumn>) => void }) {
  if (column.type === "bool") {
    return (
      <ConfigCheckbox
        label={`${column.name} starts ticked`}
        checked={column.default_value === true}
        onChange={(ticked) => set({ default_value: ticked ? true : "" })}
      />
    )
  }
  const text = typeof column.default_value === "string" ? column.default_value : ""
  const label = `Default of ${column.name}`
  if (column.options.length > 0) {
    return (
      <select
        aria-label={label}
        value={text}
        onChange={(e) => set({ default_value: e.target.value })}
        className={`${FIELD_CLASS} w-full`}
        style={INPUT_STYLE}
      >
        <option value="" />
        {[...column.options, ...(text === "" || column.options.includes(text) ? [] : [text])].map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    )
  }
  return (
    <CommittedTextField
      aria-label={label}
      type={column.type === "date" ? "date" : "text"}
      value={text}
      onCommit={(value) => set({ default_value: value.trim() })}
      className={`${FIELD_CLASS} w-full`}
      style={INPUT_STYLE}
    />
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
