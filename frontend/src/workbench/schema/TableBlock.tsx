/** A schema table's block: its name, role and rows, its problems, and its columns (specs/workbench). */
import { ChevronDown, ChevronRight, Lock, Trash2 } from "lucide-react"
import type { FormSpec, SchemaTable } from "../../api/types"
import { CommittedTextField, INPUT_STYLE, QuietAction } from "../../components/form"
import useRefocusAfterMove from "../../hooks/useRefocusAfterMove"
import { confirmed } from "../../stores/useQuestionStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { addSchemaColumn, createSchemaColumn, conditionUses, fieldUses, isQuoteDetails, removeSchemaTable, setSchemaTableRows, updateSchemaTable } from "../../utils/workbenchForm"
import { ColumnRow } from "./ColumnRow"
import { IconButton } from "./IconButton"
import { IndexControl } from "./IndexControl"
import { CHIP_CLASS, DETAILS_TITLE, type Fresh, HOVER_ONLY, currentForm, selectAll, usedBy } from "./schemaText"

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
  added: string | null
  setAdded: (id: string | null) => void
  details: string | null
  setDetails: (id: string | null) => void
  collapsed: boolean
  onToggleCollapsed: () => void
  problems: string[]
}

export function TableBlock({ form, table, added, setAdded, details, setDetails, collapsed, onToggleCollapsed, problems }: TableBlockProps) {
  const change = useWorkbenchFormStore((s) => s.change)
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
            onClick={() => void remove()}
            aria-label={`Delete table ${table.name}`}
            title="Delete table"
            className={`icon-danger-btn ${HOVER_ONLY}`}
          />
        )}
      </div>
      {!collapsed && (
        <div className="flex flex-col gap-1 px-1.5 py-1.5" style={{ borderTop: "1px solid var(--border)" }}>
          {table.columns.map((column, index) => (
            <ColumnRow
              key={column.id}
              form={form}
              table={table}
              column={column}
              index={index}
              refocus={refocus}
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
