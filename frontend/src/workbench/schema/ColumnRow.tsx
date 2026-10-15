/** A schema column's row: its type, name and summary, moved, removed and opened for its details (specs/workbench). */
import { KeyRound, SlidersHorizontal, X } from "lucide-react"
import { type KeyboardEvent } from "react"
import type { FormSpec, SchemaColumn, SchemaTable } from "../../api/types"
import { COMPACT_FIELD_CLASS, CommittedTextField, IconSelect, INPUT_STYLE } from "../../components/form"
import { altArrowMove, type RefocusAfterMove } from "../../hooks/useRefocusAfterMove"
import { confirmed } from "../../stores/useQuestionStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { changeSchemaColumnType, conditionUses, fieldUses, moveSchemaColumn, QUOTE_DETAILS_MANAGED, removeSchemaColumn, updateSchemaColumn } from "../../utils/workbenchForm"
import { COLUMN_TYPES, COLUMN_TYPE_OPTIONS } from "../columnTypes"
import { ColumnDetails } from "./ColumnDetails"
import { IconButton } from "./IconButton"
import { type Fresh, HOVER_ONLY, rulesSummary, usedBy } from "./schemaText"

interface ColumnRowProps {
  form: FormSpec
  table: SchemaTable
  column: SchemaColumn
  index: number
  /** The table's columns' name fields, one focused again after Alt+arrows moves it. */
  refocus: RefocusAfterMove<HTMLInputElement>
  fresh: Fresh
  open: boolean
  onToggleDetails: () => void
  onInsertAfter: () => void
  /** In Haute's quote_details table: the column's name and type are fixed, and it stays. */
  locked: boolean
}

export function ColumnRow({ form, table, column, index, refocus, fresh, open, onToggleDetails, onInsertAfter, locked }: ColumnRowProps) {
  const change = useWorkbenchFormStore((s) => s.change)
  const shown = fieldUses(form, table.id, column.id).length
  const tested = conditionUses(form, table.id, column.id).length
  const uses = shown + tested
  // A column a sheet shows or a condition tests is removed once the question is answered yes.
  const remove = async () => {
    const text = `${usedBy(shown, tested).replace(/^./, (first) => first.toUpperCase())}; removing it takes it out of ${uses === 1 ? "that" : "them"} too.`
    if (uses > 0 && !(await confirmed({ title: `Remove column ${column.name}?`, text, confirm: "Remove", danger: true }))) return
    change((f) => removeSchemaColumn(f, table.id, column.id))
  }
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
      return
    }
    const to = altArrowMove(e, "vertical", index, table.columns.length)
    if (to === null) return
    // Its name field takes the focus again in its new place.
    refocus.moved(column.id)
    change((f) => moveSchemaColumn(f, table.id, column.id, to))
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
          ref={refocus.register(column.id)}
          aria-label="Column name"
          aria-keyshortcuts="Alt+ArrowUp Alt+ArrowDown"
          value={column.name}
          onCommit={(name) => set({ name })}
          {...fresh}
          readOnly={locked}
          onKeyDown={onKeyDown}
          className={`${COMPACT_FIELD_CLASS} flex-1 font-mono`}
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
            onClick={() => void remove()}
            aria-label={`Remove column ${column.name}`}
            title="Remove column"
            className={`icon-danger-btn self-center ${HOVER_ONLY}`}
          />
        )}
      </div>
      {open && <ColumnDetails table={table} column={column} managed={managed} set={set} />}
    </div>
  )
}
