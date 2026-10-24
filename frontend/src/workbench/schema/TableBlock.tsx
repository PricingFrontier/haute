/** A schema table's block: its name, role and rows, its problems, and its columns, dragged or moved (specs/workbench). */
import { ChevronDown, ChevronRight, Lock, Trash2 } from "lucide-react"
import { useRef, type KeyboardEvent } from "react"
import type { FormSpec, SchemaTable } from "../../api/types"
import { CommittedTextField, INPUT_STYLE, QuietAction } from "../../components/form"
import useListReorder, { type RowDrag } from "../../hooks/useListReorder"
import useRefocusAfterMove, { altArrowMove, type RefocusAfterMove } from "../../hooks/useRefocusAfterMove"
import { confirmed } from "../../stores/useQuestionStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { isIdentityColumn } from "../../utils/quoteIdentity"
import { addSchemaColumn, createSchemaColumn, conditionUses, fieldUses, isQuoteDetails, moveSchemaColumn, moveSchemaTable, QUOTE_DETAILS_COLUMNS, QUOTE_DETAILS_MANAGED, removeSchemaTable, setSchemaTableRows, updateSchemaTable } from "../../utils/workbenchForm"
import { ColumnRow } from "./ColumnRow"
import { DragGrip } from "./DragGrip"
import { IconButton } from "./IconButton"
import { IndexControl } from "./IndexControl"
import { CHIP_CLASS, DETAILS_TITLE, type Fresh, HOVER_ONLY, currentForm, selectAll, usedBy } from "./schemaText"

/** The quote_details columns the app sets, by name, in order: "a, b and c". */
const appSetNames = QUOTE_DETAILS_COLUMNS.filter(([id]) => QUOTE_DETAILS_MANAGED.has(id)).map(([, name]) => name)
const APP_SET = `${appSetNames.slice(0, -1).join(", ")} and ${appSetNames[appSetNames.length - 1]}`

const ROLES: ReadonlyArray<{ value: SchemaTable["role"]; label: string; title: string }> = [
  { value: "input", label: "Input", title: "Sent to the pricing engine" },
  { value: "output", label: "Output", title: "Returned by the pricing engine" },
]

const ROWS: ReadonlyArray<{ value: SchemaTable["rows"]; label: string; title: string }> = [
  { value: "one", label: "One row", title: "One row per quote, such as policy details" },
  { value: "many", label: "Many rows", title: "Many rows per quote, such as an equipment schedule" },
]

interface TableBlockProps {
  form: FormSpec
  table: SchemaTable
  /** Its place among the schema's tables. */
  index: number
  /** The tables' name fields, one focused again after Alt+arrows moves its table. */
  refocus: RefocusAfterMove<HTMLInputElement>
  /** Its part in dragging the tables into a new order; none for quote_details, which stays first, or in Preview. */
  drag: RowDrag | undefined
  /** In Preview: the table shows as it is, nothing in it changed. */
  readOnly: boolean
  /**
   * While the schema is searched, its columns the search matches, the only ones shown, the
   * table open and nothing in it added or moved; null while it is not.
   */
  matched: ReadonlySet<string> | null
  added: string | null
  setAdded: (id: string | null) => void
  details: string | null
  setDetails: (id: string | null) => void
  collapsed: boolean
  onToggleCollapsed: () => void
  problems: string[]
}

export function TableBlock({ form, table, index, refocus: refocusTable, drag, readOnly, matched, added, setAdded, details, setDetails, collapsed, onToggleCollapsed, problems }: TableBlockProps) {
  const change = useWorkbenchFormStore((s) => s.change)
  const block = useRef<HTMLElement>(null)
  const shown = fieldUses(form, table.id).length
  const tested = conditionUses(form, table.id).length
  const uses = shown + tested
  // A table a sheet shows or a condition tests is deleted once the question is answered yes.
  const remove = async () => {
    const text = `Its columns are ${usedBy(shown, tested)}; deleting it takes them out of ${uses === 1 ? "it" : "them"} too.`
    if (uses > 0 && !(await confirmed({ title: `Delete table ${table.name}?`, text, confirm: "Delete", danger: true }))) return
    change((f) => removeSchemaTable(f, table.id))
  }
  const refocus = useRefocusAfterMove<HTMLInputElement>()
  const dragColumn = useListReorder((from, to) => change((f) => moveSchemaColumn(f, table.id, table.columns[from].id, to)))
  const fresh = (id: string): Fresh => ({
    autoFocus: added === id,
    onFocus: added === id ? selectAll : undefined,
    onBlur: () => setAdded(null),
  })
  const count = table.columns.length
  // Haute's quote_details table keeps its structure, and in Preview every table keeps it.
  const locked = isQuoteDetails(table)
  const fixed = locked || readOnly
  const filtering = matched !== null

  const insertAt = (at: number) => {
    const column = createSchemaColumn(currentForm(), table.id, at)
    change((f) => addSchemaColumn(f, table.id, column, at))
    setAdded(column.id)
  }

  // Alt+Up and Alt+Down in its name move the table; its name field takes the focus again in its new place.
  const onNameKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (fixed || filtering) return
    const to = altArrowMove(e, "vertical", index, form.schema.tables.length)
    if (to === null) return
    refocusTable.moved(table.id)
    change((f) => moveSchemaTable(f, index, to))
  }

  return (
    <section
      ref={block}
      aria-label={`Table ${table.name}`}
      data-testid={`schema-table-${table.id}`}
      onDragOver={drag?.onOver}
      onDrop={drag?.onDrop}
      data-drop-target={drag?.target ? "true" : undefined}
      className="rounded-md border"
      style={{
        borderColor: "var(--border)",
        background: "var(--bg-elevated)",
        opacity: drag?.dragging ? 0.5 : 1,
        boxShadow: drag?.target ? "0 -2px 0 0 var(--accent)" : undefined,
      }}
    >
      <div className="group flex items-center gap-1 px-1 py-1">
        <DragGrip drag={drag} image={block} />
        <IconButton
          icon={collapsed ? ChevronRight : ChevronDown}
          size={14}
          onClick={onToggleCollapsed}
          disabled={filtering}
          aria-expanded={!collapsed}
          aria-label={`${collapsed ? "Expand" : "Collapse"} ${table.name}`}
          title={collapsed ? "Show columns" : "Hide columns"}
          style={{ color: "var(--text-muted)" }}
        />
        <CommittedTextField
          ref={refocusTable.register(table.id)}
          aria-label="Table name"
          aria-keyshortcuts={fixed || filtering ? undefined : "Alt+ArrowUp Alt+ArrowDown"}
          value={table.name}
          onCommit={(name) => change((f) => updateSchemaTable(f, table.id, { name }))}
          {...fresh(table.id)}
          onKeyDown={onNameKeyDown}
          readOnly={fixed}
          title={locked ? DETAILS_TITLE : undefined}
          className="focus-ring min-w-0 flex-1 rounded-md border border-transparent bg-transparent px-1.5 py-0.5 font-mono font-semibold"
          style={{ color: "var(--text-primary)" }}
        />
        {locked && <Lock size={11} aria-label="Haute's table" className="shrink-0" style={{ color: "var(--text-muted)" }} />}
        <select
          aria-label={`Role of ${table.name}`}
          disabled={fixed}
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
          disabled={fixed}
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
        {!fixed && (
          <IconButton
            icon={Trash2}
            onClick={() => void remove()}
            aria-label={`Delete table ${table.name}`}
            title="Delete table"
            className={`icon-danger-btn ${HOVER_ONLY}`}
          />
        )}
      </div>
      {!collapsed && (
        <div className="flex flex-col gap-1 px-1.5 py-1.5" style={{ borderTop: "1px solid var(--border)" }}>
          {table.columns.map((column, at) => matched !== null && !matched.has(column.id) ? null : (
            <ColumnRow
              key={column.id}
              form={form}
              table={table}
              column={column}
              index={at}
              refocus={refocus}
              fresh={fresh(column.id)}
              open={details === column.id}
              onToggleDetails={() => setDetails(details === column.id ? null : column.id)}
              onInsertAfter={() => insertAt(at + 1)}
              locked={locked}
              readOnly={readOnly}
              filtering={filtering}
              drag={fixed || filtering || column.index || isIdentityColumn(column) ? undefined : dragColumn(at)}
            />
          ))}
          {locked ? (
            <p className="px-1 text-[11px]" style={{ color: "var(--text-muted)" }}>
              Kept with each quote and listed on the Quotes page; the app sets {APP_SET}.
            </p>
          ) : readOnly || filtering ? null : (
            <div className="flex items-center gap-3">
              <QuietAction onClick={() => insertAt(count)} className="justify-self-start">Add column</QuietAction>
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
