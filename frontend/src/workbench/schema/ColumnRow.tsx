/**
 * A schema column's row in its block, the table's inputs or its outputs: its type and name, its
 * summary to their right, dragged or moved within its block or into the other, removed and opened
 * for its details; and the row of a column the quote store keeps (specs/workbench).
 */
import { KeyRound, SlidersHorizontal, X, type LucideIcon } from "lucide-react"
import { useRef, type KeyboardEvent } from "react"
import type { FormSpec, SchemaColumn, SchemaTable, WorkbenchStoreColumn } from "../../api/types"
import { COMPACT_FIELD_CLASS, CommittedTextField, IconSelect, INPUT_STYLE } from "../../components/form"
import type { RowDrag } from "../../hooks/useListReorder"
import { altArrowMove, type RefocusAfterMove } from "../../hooks/useRefocusAfterMove"
import { confirmed } from "../../stores/useQuestionStore"
import useToastStore from "../../stores/useToastStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { type Block, hasInputs, isIdentityColumn, tableColumns } from "../../utils/quoteIdentity"
import { buttonUses, changeSchemaColumnType, conditionUses, fieldUses, moveSchemaColumn, nameTaken, QUOTE_DETAILS_MANAGED, sampleHolds, removeSchemaColumn, updateSchemaColumn } from "../../utils/workbenchForm"
import { keyTable } from "../../utils/workbenchTables"
import { COLUMN_TYPES, COLUMN_TYPE_OPTIONS } from "../columnTypes"
import { DatabaseMark } from "../StoreCompare"
import { ColumnDetails } from "./ColumnDetails"
import { DragGrip } from "./DragGrip"
import { IconButton } from "./IconButton"
import { IndexMarker, KeyMark } from "./IndexControl"
import { COLUMN_NAME_WIDTH, columnSummary, type Fresh, LOCKED_FIELD_STYLE, MARKER_STYLE, usedBy } from "./schemaText"

/** What a column the quote store keeps holds, drawn as the marker of the column type nearest it. */
const KEPT_KINDS: Record<WorkbenchStoreColumn["kind"], { label: string; icon: LucideIcon; color: string }> = {
  int: COLUMN_TYPES.int,
  str: COLUMN_TYPES.str,
  datetime: { ...COLUMN_TYPES.date, label: "Date and time" },
}

interface ColumnRowProps {
  form: FormSpec
  table: SchemaTable
  column: SchemaColumn
  /** The block holding it, the table's inputs or its outputs. */
  block: Block
  /** Its place in its block. */
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
  /** Its part in dragging the table's columns into a new order; none for the quote's identity, the index or in quote_details, which stay. */
  drag: RowDrag | undefined
}

export function ColumnRow({ form, table, column, block, index, refocus, fresh, open, onToggleDetails, onInsertAfter, locked, readOnly, filtering, drag }: ColumnRowProps) {
  const change = useWorkbenchFormStore((s) => s.change)
  const addToast = useToastStore((s) => s.addToast)
  const schemaColumnRenamed = useWorkbenchViewStore((s) => s.schemaColumnRenamed)
  const row = useRef<HTMLDivElement>(null)
  const shown = fieldUses(form, table.name, column.name).length
  const tested = conditionUses(form, table.name, column.name).length
  const used = buttonUses(form, table.name, column.name).length
  const uses = shown + tested + used
  // A column a sheet shows, a condition tests or an Import or an Export uses is removed once the question is answered yes.
  const remove = async () => {
    const text = `${usedBy(shown, tested, used).replace(/^./, (first) => first.toUpperCase())}; removing it takes it out of ${uses === 1 ? "that" : "them"} too.`
    if (uses > 0 && !(await confirmed({ title: `Remove column ${column.name}?`, text, confirm: "Remove", danger: true }))) return
    change((f) => removeSchemaColumn(f, table.name, column.name))
  }
  const { label, icon: Icon, color } = COLUMN_TYPES[column.type]
  // A column the underwriter app sets takes no rules.
  const managed = locked && QUOTE_DETAILS_MANAGED.has(column.name)
  // The quote's reference and option are Haute's: nothing of them changes.
  const identity = isIdentityColumn(table, column)
  // Its name and type stay, and it stays: in quote_details, the quote's identity, and in Preview.
  const fixed = locked || readOnly || identity
  const summary = columnSummary(table, column)
  const length = table[block].length
  // A many-row table of outputs alone is keyed by the outputs marked so; a table with inputs by a column no one types.
  const keyable = table.rows === "many" && block === "outputs" && !hasInputs(table) && !identity
  const keying = hasInputs(table) && keyTable(table).some((key) => key.name === column.name)
  const set = (patch: Partial<SchemaColumn>) => change((f) => updateSchemaColumn(f, table.name, column.name, patch))

  // Enter in the last column's name of its block adds the next column there, and in any other
  // only commits it; Alt+Up and Alt+Down move this one within its block.
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (fixed || filtering) return
    if (e.key === "Enter") {
      e.preventDefault()
      if (index === length - 1) onInsertAfter()
      return
    }
    const to = altArrowMove(e, "vertical", index, length)
    if (to === null) return
    // Its name field takes the focus again in its new place.
    refocus.moved(column.name)
    change((f) => moveSchemaColumn(f, table.name, column.name, block, to))
  }

  return (
    <div
      ref={row}
      className="flex flex-col gap-1 rounded"
      data-testid={`schema-column-${column.name}`}
      onDragOver={drag?.onOver}
      onDrop={drag?.onDrop}
      data-drop-target={drag?.target ? "true" : undefined}
      style={{ opacity: drag?.dragging ? 0.5 : 1, boxShadow: drag?.target ? "0 -2px 0 0 var(--accent)" : undefined }}
    >
      <div className="group flex items-stretch gap-1 rounded">
        <DragGrip drag={drag} image={row} />
        {column.index ? (
          // The index is always an Integer.
          <IndexMarker />
        ) : fixed ? (
          // Haute's columns keep their types.
          <span className={`relative flex w-6 shrink-0 items-center justify-center rounded-md ${color}`} style={MARKER_STYLE} title={label}>
            <Icon size={12} aria-hidden="true" />
          </span>
        ) : (
          <IconSelect
            icon={Icon}
            value={column.type}
            options={COLUMN_TYPE_OPTIONS}
            onChange={(type) => change((f) => changeSchemaColumnType(f, table.name, column.name, type))}
            ariaLabel={`Type of ${column.name}`}
            title={`${label} (change the type)`}
            className={`w-6 rounded-md ${color}`}
            style={MARKER_STYLE}
          />
        )}
        <CommittedTextField
          ref={refocus.register(column.name)}
          aria-label="Column name"
          aria-keyshortcuts={fixed || filtering ? undefined : "Alt+ArrowUp Alt+ArrowDown"}
          value={column.name}
          onCommit={(name) => {
            if (name === column.name) return
            if (nameTaken(tableColumns(table).map((other) => other.name), name, column.name)) {
              addToast("error", `${table.name} already has a column named ${name}`)
              return
            }
            if (sampleHolds(form, table.name, name)) {
              addToast("error", `The sample already holds values under ${name} in ${table.name}`)
              return
            }
            set({ name })
            schemaColumnRenamed(table.name, column.name, name)
          }}
          {...fresh}
          readOnly={fixed}
          onKeyDown={onKeyDown}
          title={identity ? "Haute's column" : undefined}
          className={`${COMPACT_FIELD_CLASS} ${COLUMN_NAME_WIDTH} font-mono`}
          style={identity ? LOCKED_FIELD_STYLE : INPUT_STYLE}
        />
        <DatabaseMark table={table.name} column={column.name} />
        {/* A many-row table's every column says whether it keys the table; a one-row table has no key. */}
        {table.rows === "many" &&
          (identity ? (
            // The quote's identity is never a key.
            <span className="size-5 shrink-0" aria-hidden="true" />
          ) : keyable ? (
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
          ) : (
            <KeyMark column={column} isKey={keying} />
          ))}
        {identity ? (
          // The quote's identity has no label or rules to open.
          <span className="size-5 shrink-0" aria-hidden="true" />
        ) : (
          <IconButton
            icon={SlidersHorizontal}
            onClick={onToggleDetails}
            aria-expanded={open}
            aria-label={`Label and rules of ${column.name}`}
            title="Label, help, rules and format"
            className="self-center"
            style={{ color: open ? "var(--text-primary)" : "var(--text-muted)" }}
          />
        )}
        {fixed || column.index ? (
          // The index has no remove: the operations give it and take it away.
          <span className="size-5 shrink-0" aria-hidden="true" />
        ) : (
          <IconButton
            icon={X}
            onClick={() => void remove()}
            aria-label={`Remove column ${column.name}`}
            title="Remove column"
            className="icon-danger-btn self-center"
          />
        )}
        {summary && (
          // To the right of the name and its buttons, on one line: cut short, whole in its title, as the pane narrows.
          <p
            className="min-w-0 flex-1 self-center truncate text-[11px]"
            style={{ color: "var(--text-muted)" }}
            title={summary}
            data-testid={`schema-column-${column.name}-summary`}
          >
            {summary}
          </p>
        )}
      </div>
      {open && !identity && <ColumnDetails table={table} column={column} managed={managed} readOnly={readOnly} set={set} />}
    </div>
  )
}

/** A column the quote store keeps in a table that the form does not: what it holds and its name, locked, with nothing to change. */
export function StoreColumnRow({ column }: { column: WorkbenchStoreColumn }) {
  const { label, icon: Icon, color } = KEPT_KINDS[column.kind]
  return (
    <div className="flex items-stretch gap-1 rounded" data-testid={`schema-store-column-${column.name}`}>
      <span className="w-3 shrink-0" aria-hidden="true" />
      <span className={`relative flex w-6 shrink-0 items-center justify-center rounded-md ${color}`} style={MARKER_STYLE} title={label}>
        <Icon size={12} aria-hidden="true" />
      </span>
      <span
        className={`${COLUMN_NAME_WIDTH} truncate rounded-md px-1.5 py-0.5 font-mono text-xs`}
        style={{ ...LOCKED_FIELD_STYLE, color: "var(--text-muted)" }}
        title="Kept by the quote store"
      >
        {column.name}
      </span>
    </div>
  )
}
