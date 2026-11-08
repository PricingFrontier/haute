/**
 * A schema table's block: its name and rows, its problems, its shared Keys followed by
 * Inputs and Outputs, each with Add column even while empty in Build. Ordinary columns
 * move within their block or into the other; keys keep
 * their canonical placement while shown once. Managed metadata is hidden, with its
 * database problems available in the table's details (specs/workbench).
 */
import { ChevronDown, ChevronRight, Lock, Trash2 } from "lucide-react"
import { useRef, useState, type DragEvent, type KeyboardEvent } from "react"
import type { FormSpec, SchemaTable } from "../../api/types"
import { CommittedTextField, INPUT_STYLE, QuietAction } from "../../components/form"
import useListReorder, { type RowDrag } from "../../hooks/useListReorder"
import useRefocusAfterMove, { altArrowMove, type RefocusAfterMove } from "../../hooks/useRefocusAfterMove"
import { confirmed } from "../../stores/useQuestionStore"
import useToastStore from "../../stores/useToastStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchViewStore, { type SchemaDetails } from "../../stores/useWorkbenchViewStore"
import { type Block, IDENTITY_NAMES, isIdentityColumn } from "../../utils/quoteIdentity"
import { columnKey } from "../../utils/sheetVisibility"
import { addSchemaColumn, buttonUses, createSchemaColumn, conditionUses, fieldUses, isQuoteDetails, moveSchemaColumn, moveSchemaTable, nameTaken, removeSchemaTable, setSchemaTableRows, updateSchemaTable } from "../../utils/workbenchForm"
import { DatabaseMark } from "../StoreCompare"
import { TableDatabaseDetails } from "../ColumnDatabaseDetails"
import { ColumnRow } from "./ColumnRow"
import { DragGrip } from "./DragGrip"
import { IconButton } from "./IconButton"
import { CHIP_CLASS, DETAILS_TITLE, type Fresh, HOVER_ONLY, currentForm, schemaColumns, selectAll, usedBy } from "./schemaText"

const ROWS: ReadonlyArray<{ value: SchemaTable["rows"]; label: string; title: string }> = [
  { value: "one", label: "One row", title: "One row per quote, such as policy details" },
  { value: "many", label: "Many rows", title: "Many rows per quote, such as an equipment schedule" },
]

/** A table's two blocks under their headings: what an underwriter types, sent to the pricing engine, and what it returns. */
const BLOCKS: ReadonlyArray<{ block: Block; title: string; color: string }> = [
  { block: "inputs", title: "Inputs", color: "var(--accent)" },
  { block: "outputs", title: "Outputs", color: "var(--success)" },
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
  details: SchemaDetails | null
  setDetails: (details: SchemaDetails | null) => void
  collapsed: boolean
  onToggleCollapsed: () => void
  problems: string[]
}

export function TableBlock({ form, table, index, refocus: refocusTable, drag, readOnly, matched, added, setAdded, details, setDetails, collapsed, onToggleCollapsed, problems }: TableBlockProps) {
  const change = useWorkbenchFormStore((s) => s.change)
  const addToast = useToastStore((s) => s.addToast)
  const schemaTableRenamed = useWorkbenchViewStore((s) => s.schemaTableRenamed)
  const block = useRef<HTMLElement>(null)
  const shown = fieldUses(form, table.name).length
  const tested = conditionUses(form, table.name).length
  const used = buttonUses(form, table.name).length
  const uses = shown + tested + used
  // A table a sheet shows, a condition tests or an Import or an Export uses is deleted once the question is answered yes.
  const remove = async () => {
    const text = `Its columns are ${usedBy(shown, tested, used)}; deleting it takes them out of ${uses === 1 ? "it" : "them"} too.`
    if (uses > 0 && !(await confirmed({ title: `Delete table ${table.name}?`, text, confirm: "Delete", danger: true }))) return
    change((f) => removeSchemaTable(f, table.name))
  }
  const refocus = useRefocusAfterMove<HTMLInputElement>()
  // Each block's columns dragged into a new order within it; a column carried into the other
  // block is moved there (`carried`), at the row it is dropped on or, on its heading, at its end.
  const reorderInputs = useListReorder((from, to) => change((f) => moveSchemaColumn(f, table.name, table.inputs[from].name, "inputs", to)))
  const reorderOutputs = useListReorder((from, to) => change((f) => moveSchemaColumn(f, table.name, table.outputs[from].name, "outputs", to)))
  const reorder: Record<Block, (at: number) => RowDrag> = { inputs: reorderInputs, outputs: reorderOutputs }
  const [carried, setCarried] = useState<{ block: Block; name: string } | null>(null)
  const [landing, setLanding] = useState<{ block: Block; at: number } | null>(null)
  const crossing = (into: Block) => carried !== null && carried.block !== into
  const carryOver = (into: Block, at: number) => (event: DragEvent<HTMLElement>) => {
    if (!crossing(into)) return
    event.preventDefault()
    if (event.dataTransfer) event.dataTransfer.dropEffect = "move"
    if (landing?.block !== into || landing.at !== at) setLanding({ block: into, at })
  }
  const carryIn = (into: Block, at: number) => (event: DragEvent<HTMLElement>) => {
    if (carried === null || !crossing(into)) return
    event.preventDefault()
    event.stopPropagation()
    const { block: from, name } = carried
    change((f) => moveSchemaColumn(f, table.name, name, into, at))
    // The row it was carried from may be gone, and with it the end of its drag.
    reorder[from](0).onEnd()
    setCarried(null)
    setLanding(null)
  }
  const dragColumn = (own: Block, at: number): RowDrag => {
    const within = reorder[own](at)
    return {
      ...within,
      onStart: (event) => {
        within.onStart(event)
        setCarried({ block: own, name: table[own][at].name })
      },
      onOver: (event) => (crossing(own) ? carryOver(own, at)(event) : within.onOver(event)),
      onDrop: (event) => (crossing(own) ? carryIn(own, at)(event) : within.onDrop(event)),
      onEnd: () => {
        within.onEnd()
        setCarried(null)
        setLanding(null)
      },
      target: within.target || (crossing(own) && landing?.block === own && landing.at === at),
    }
  }
  const fresh = (id: string): Fresh => ({
    autoFocus: added === id,
    onFocus: added === id ? selectAll : undefined,
    onBlur: () => setAdded(null),
  })
  const visible = new Set(schemaColumns(table).map((column) => column.name))
  const count = visible.size
  // Haute's quote_details table keeps its structure, and in Preview every table keeps it.
  const locked = isQuoteDetails(table)
  const fixed = locked || readOnly
  const filtering = matched !== null
  // Keep each row's canonical block and position for edits and drag targets, even though
  // the shared keys are displayed separately from the two blocks.
  const columns = BLOCKS.flatMap(({ block: own }) => table[own].map((column, at) => ({
    column, own, at,
    isKey: column.index !== null || isIdentityColumn(table, column) || (locked && IDENTITY_NAMES.has(column.name)),
  }))).filter(({ column }) => visible.has(column.name) && (matched === null || matched.has(column.name)))
  const keys = columns.filter(({ isKey }) => isKey)

  const insertAt = (into: Block, at: number) => {
    const column = createSchemaColumn(currentForm(), table.name, into, at)
    change((f) => addSchemaColumn(f, table.name, into, column, at))
    setAdded(columnKey(table.name, column.name))
  }

  const renderColumn = ({ column, own, at, isKey }: (typeof columns)[number]) => (
    <ColumnRow
      key={column.name}
      form={form}
      table={table}
      column={column}
      block={own}
      index={at}
      refocus={refocus}
      fresh={fresh(columnKey(table.name, column.name))}
      details={details?.table === table.name && details.column === column.name ? details.view : null}
      onToggleDetails={(view) => setDetails(details?.table === table.name && details.column === column.name && details.view === view
        ? null : { table: table.name, column: column.name, view })}
      onInsertAfter={() => {
        if (!isKey) insertAt(own, at + 1)
      }}
      locked={locked}
      readOnly={readOnly}
      filtering={filtering}
      drag={fixed || filtering || isKey ? undefined : dragColumn(own, at)}
    />
  )

  // Alt+Up and Alt+Down in its name move the table; its name field takes the focus again in its new place.
  const onNameKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (fixed || filtering) return
    const to = altArrowMove(e, "vertical", index, form.schema.tables.length)
    if (to === null) return
    refocusTable.moved(table.name)
    change((f) => moveSchemaTable(f, index, to))
  }

  return (
    <section
      ref={block}
      aria-label={`Table ${table.name}`}
      data-testid={`schema-table-${table.name}`}
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
          ref={refocusTable.register(table.name)}
          aria-label="Table name"
          aria-keyshortcuts={fixed || filtering ? undefined : "Alt+ArrowUp Alt+ArrowDown"}
          value={table.name}
          onCommit={(name) => {
            if (name === table.name) return
            if (nameTaken(form.schema.tables.map((other) => other.name), name, table.name)) {
              addToast("error", `A table is already named ${name}`)
              return
            }
            change((f) => updateSchemaTable(f, table.name, { name }))
            schemaTableRenamed(table.name, name)
          }}
          {...fresh(table.name)}
          onKeyDown={onNameKeyDown}
          readOnly={fixed}
          title={locked ? DETAILS_TITLE : undefined}
          className="focus-ring min-w-0 flex-1 rounded-md border border-transparent bg-transparent px-1.5 py-0.5 font-mono font-semibold"
          style={{ color: "var(--text-primary)" }}
        />
        {locked && <Lock size={11} aria-label="Haute's table" className="shrink-0" style={{ color: "var(--text-muted)" }} />}
        <select
          aria-label={`Rows of ${table.name}`}
          disabled={fixed}
          value={table.rows}
          onChange={(e) => change((f) => setSchemaTableRows(f, table.name, e.target.value as SchemaTable["rows"]))}
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
        <DatabaseMark table={table} />
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
          {keys.length > 0 && (
            <div role="group" aria-label={`${table.name} Keys`} className="flex flex-col gap-1">
              <p className="px-1 text-[11px] font-semibold" style={{ color: "var(--text-accent)" }}>Keys</p>
              {keys.map(renderColumn)}
            </div>
          )}
          {BLOCKS.map(({ block: own, title, color }) => {
            const ordinary = columns.filter((entry) => entry.own === own && !entry.isKey)
            // quote_details holds inputs alone and is given no column; in Preview or while searched nothing is added.
            const adding = !fixed && !filtering
            if (ordinary.length === 0 && !adding) return null
            const landed = crossing(own) && landing?.block === own && landing.at === table[own].length
            return (
              <div key={own} role="group" aria-label={`${table.name} ${title}`} className="flex flex-col gap-1">
                <p
                  className="px-1 text-[11px] font-semibold"
                  style={{ color, boxShadow: landed ? `0 2px 0 0 var(--accent)` : undefined }}
                  onDragOver={carryOver(own, table[own].length)}
                  onDrop={carryIn(own, table[own].length)}
                  data-drop-target={landed ? "true" : undefined}
                >
                  {title}
                </p>
                {ordinary.map(renderColumn)}
                {adding && (
                  <QuietAction
                    onClick={() => insertAt(own, table[own].length)}
                    onDragOver={carryOver(own, table[own].length)}
                    onDrop={carryIn(own, table[own].length)}
                    data-drop-target={landed ? "true" : undefined}
                    style={{ boxShadow: landed ? "0 2px 0 0 var(--accent)" : undefined }}
                    className="self-start"
                  >
                    Add column
                  </QuietAction>
                )}
              </div>
            )
          })}
          {!filtering && <TableDatabaseDetails table={table} />}
          {locked && (
            <p className="px-1 text-[11px]" style={{ color: "var(--text-muted)" }}>
              Quote details for the form. Haute manages workflow, history and storage fields automatically.
            </p>
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
