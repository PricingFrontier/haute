/**
 * The workbench's schema editor (specs/workbench): the form's tables of named, typed
 * columns, which are the pipeline's tables. Every text field commits on blur or Enter, so
 * an edit is one undo step; Enter in the last column's name adds the next column. A table or a
 * column is dragged by its grip, or moved by Alt+Up and Alt+Down in its name, as the step
 * editor's cards move. Removing what a sheet shows asks first. Haute's quote_details table,
 * first, keeps its structure and its place: its columns' labels, groups and help, and the
 * rules and defaults of those the underwriter types, are the project's. Under each table's
 * columns are those the quote store keeps in it that the form does not, locked, as the newest
 * fetch of the workbench's tables names them. Read only, in Preview, it shows the schema as it
 * is: a table still collapses and a column's details still open, but nothing in it changes.
 * It opens again as it was left: its tables collapsed, the column whose details are open, its
 * scroll and its search are the view store's. The search shows only the columns whose name or
 * label holds its text, in the tables holding one, and while it does nothing is added or
 * moved, so each edit is to a column it shows.
 */
import { useLayoutEffect, useMemo, useRef, useState } from "react"
import type { SchemaTable, WorkbenchStoreColumn } from "../api/types"
import { QuietAction, SearchField } from "../components/form"
import useListReorder from "../hooks/useListReorder"
import useRefocusAfterMove from "../hooks/useRefocusAfterMove"
import useDocumentStatusStore from "../stores/useDocumentStatusStore"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchStore from "../stores/useWorkbenchStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import { addSchemaTable, createSchemaTable, isQuoteDetails, moveSchemaTable, schemaProblems } from "../utils/workbenchForm"
import { columnMatches } from "./schema/schemaText"
import { TableBlock } from "./schema/TableBlock"

/** A table the quote store keeps nothing more in. */
const NOTHING_KEPT: readonly WorkbenchStoreColumn[] = []

/** The schema's pane's editor: the form's tables of named, typed columns. */
export default function SchemaEditor({ readOnly = false }: { readOnly?: boolean }) {
  const form = useWorkbenchFormStore((s) => s.form)
  const change = useWorkbenchFormStore((s) => s.change)
  const storeColumns = useWorkbenchStore((s) => s.storeColumns)
  const reservedLabels = useDocumentStatusStore((s) => s.capabilities?.reserved_api_input_frame_labels)
  const reserved = useMemo(() => new Set(reservedLabels ?? []), [reservedLabels])
  // The table or column just added, whose name takes the focus.
  const [added, setAdded] = useState<string | null>(null)
  // The column whose label and rules are showing, and the tables shown collapsed.
  const details = useWorkbenchViewStore((s) => s.schemaDetails)
  const setDetails = useWorkbenchViewStore((s) => s.setSchemaDetails)
  const collapsed = useWorkbenchViewStore((s) => s.schemaCollapsed)
  const toggleCollapsed = useWorkbenchViewStore((s) => s.toggleSchemaCollapsed)
  const setScroll = useWorkbenchViewStore((s) => s.setSchemaScroll)
  const search = useWorkbenchViewStore((s) => s.schemaSearch)
  const setSearch = useWorkbenchViewStore((s) => s.setSchemaSearch)
  // Scrolled where it was left, once it is drawn.
  const scroller = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    if (scroller.current !== null) scroller.current.scrollTop = useWorkbenchViewStore.getState().schemaScroll
  }, [])
  const dragTable = useListReorder((from, to) => change((f) => moveSchemaTable(f, from, to)))
  // A table moved by Alt+arrows: its name field takes the focus again in its new place.
  const refocus = useRefocusAfterMove<HTMLInputElement>()
  if (form === null) throw new Error("The schema editor needs the form loaded")
  const problems = schemaProblems(form, reserved)
  const { tables } = form.schema
  const query = search.trim()
  const filtering = query !== ""
  // While searched, the columns of a table the search matches, by id; null while it is not.
  const matched = (table: SchemaTable): ReadonlySet<string> | null =>
    filtering ? new Set(table.columns.filter((column) => columnMatches(column, query)).map((column) => column.id)) : null
  const shownTables = tables.map((table, index) => ({ table, index, shown: matched(table) })).filter(({ shown }) => shown === null || shown.size > 0)

  const addTable = () => {
    const table = createSchemaTable(form)
    change((f) => addSchemaTable(f, table))
    setAdded(table.id)
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="schema-editor">
      <div className="max-w-[640px] px-4 pt-3 pb-1.5">
        <SearchField value={search} onChange={setSearch} label="Search fields" />
      </div>
      <div
        ref={scroller}
        onScroll={(event) => setScroll(event.currentTarget.scrollTop)}
        className="min-h-0 flex-1 overflow-auto"
        data-testid="schema-tables"
      >
        <div className="flex max-w-[640px] flex-col gap-1.5 px-4 pt-1.5 pb-3 text-xs">
          {tables.length === 0 && (
            <p className="px-1" style={{ color: "var(--text-muted)" }}>
              No tables yet. A table holds related columns, such as <code className="font-mono">policy_details</code>.
            </p>
          )}
          {tables.length > 0 && shownTables.length === 0 && (
            <p className="px-1" style={{ color: "var(--text-muted)" }}>
              No fields match
            </p>
          )}
          {shownTables.map(({ table, index, shown }) => (
            <TableBlock
              key={table.id}
              form={form}
              table={table}
              index={index}
              refocus={refocus}
              drag={readOnly || filtering || isQuoteDetails(table) ? undefined : dragTable(index)}
              readOnly={readOnly}
              matched={shown}
              kept={storeColumns[table.id] ?? NOTHING_KEPT}
              added={added}
              setAdded={setAdded}
              details={details}
              setDetails={setDetails}
              collapsed={!filtering && collapsed.has(table.id)}
              onToggleCollapsed={() => toggleCollapsed(table.id)}
              problems={problems.filter((p) => p.tableId === table.id).map((p) => p.message)}
            />
          ))}
          {!readOnly && !filtering && <QuietAction onClick={addTable} className="justify-self-start">Add table</QuietAction>}
        </div>
      </div>
    </div>
  )
}
