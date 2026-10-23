/** A schema column's row: its type and name, its summary under them, dragged or moved, removed and opened for its details (specs/workbench). */
import { KeyRound, SlidersHorizontal, X } from "lucide-react"
import { useRef, type KeyboardEvent } from "react"
import type { FormSpec, SchemaColumn, SchemaTable } from "../../api/types"
import { COMPACT_FIELD_CLASS, CommittedTextField, IconSelect, INPUT_STYLE } from "../../components/form"
import type { RowDrag } from "../../hooks/useListReorder"
import { altArrowMove, type RefocusAfterMove } from "../../hooks/useRefocusAfterMove"
import { confirmed } from "../../stores/useQuestionStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { changeSchemaColumnType, conditionUses, fieldUses, moveSchemaColumn, QUOTE_DETAILS_MANAGED, removeSchemaColumn, updateSchemaColumn } from "../../utils/workbenchForm"
import { COLUMN_TYPES, COLUMN_TYPE_OPTIONS } from "../columnTypes"
import { ColumnDetails } from "./ColumnDetails"
import { DragGrip } from "./DragGrip"
import { IconButton } from "./IconButton"
import { columnSummary, type Fresh, HOVER_ONLY, usedBy } from "./schemaText"

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
  /** In Preview: the column shows as it is, its details too, nothing in it changed. */
  readOnly: boolean
  /** While the schema is searched: the column is not moved, nor does Enter add the next. */
  filtering: boolean
  /** Its part in dragging the table's columns into a new order; none for the index or in quote_details, which stay. */
  drag: RowDrag | undefined
}

export function ColumnRow({ form, table, column, index, refocus, fresh, open, onToggleDetails, onInsertAfter, locked, readOnly, filtering, drag }: ColumnRowProps) {
  const change = useWorkbenchFormStore((s) => s.change)
  const row = useRef<HTMLDivElement>(null)
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
  // Its name and type stay, and it stays: in quote_details, and in Preview.
  const fixed = locked || readOnly
  const summary = columnSummary(table, column)
  const set = (patch: Partial<SchemaColumn>) => change((f) => updateSchemaColumn(f, table.id, column.id, patch))

  // Enter in the last column's name adds the next column, and in any other only commits it;
  // Alt+Up and Alt+Down move this one.
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (fixed || filtering) return
    if (e.key === "Enter") {
      e.preventDefault()
      if (index === table.columns.length - 1) onInsertAfter()
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
    <div
      ref={row}
      className="flex flex-col gap-1 rounded"
      data-testid={`schema-column-${column.id}`}
      onDragOver={drag?.onOver}
      onDrop={drag?.onDrop}
      data-drop-target={drag?.target ? "true" : undefined}
      style={{ opacity: drag?.dragging ? 0.5 : 1, boxShadow: drag?.target ? "0 -2px 0 0 var(--accent)" : undefined }}
    >
      <div className="group flex items-stretch gap-1 rounded">
        <DragGrip drag={drag} image={row} />
        {column.index || fixed ? (
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
          aria-keyshortcuts={fixed || filtering ? undefined : "Alt+ArrowUp Alt+ArrowDown"}
          value={column.name}
          onCommit={(name) => set({ name })}
          {...fresh}
          readOnly={fixed}
          onKeyDown={onKeyDown}
          className={`${COMPACT_FIELD_CLASS} flex-1 font-mono`}
          style={INPUT_STYLE}
        />
        {table.rows === "many" && (
          <IconButton
            icon={KeyRound}
            onClick={() => set({ key: !column.key })}
            aria-pressed={column.key}
            disabled={readOnly}
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
        {fixed ? (
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
      {summary && (
        // Under the name, from its text's left edge: past the grip, the type marker and the field's edge.
        <p className="ml-11 pl-[7px] text-[11px]" style={{ color: "var(--text-muted)" }} data-testid={`schema-column-${column.id}-summary`}>
          {summary}
        </p>
      )}
      {open && <ColumnDetails table={table} column={column} managed={managed} readOnly={readOnly} set={set} />}
    </div>
  )
}
