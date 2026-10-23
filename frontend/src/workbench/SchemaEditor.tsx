/**
 * The workbench's schema editor (specs/workbench): the form's tables of named, typed
 * columns, which are the pipeline's tables. Every text field commits on blur or Enter, so
 * an edit is one undo step; Enter in the last column's name adds the next column. A table or a
 * column is dragged by its grip, or moved by Alt+Up and Alt+Down in its name, as the step
 * editor's cards move. Removing what a sheet shows asks first. Haute's quote_details table,
 * first, keeps its structure and its place: its columns' labels, groups and help, and the
 * rules and defaults of those the underwriter types, are the project's. Read only, in
 * Preview, it shows the schema as it is: a table still collapses and a column's details still
 * open, but nothing in it changes. It opens again as it was left: its tables collapsed, the
 * column whose details are open and its scroll are the view store's.
 */
import { useLayoutEffect, useMemo, useRef, useState } from "react"
import { QuietAction } from "../components/form"
import useListReorder from "../hooks/useListReorder"
import useRefocusAfterMove from "../hooks/useRefocusAfterMove"
import useDocumentStatusStore from "../stores/useDocumentStatusStore"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import { addSchemaTable, createSchemaTable, isQuoteDetails, moveSchemaTable, schemaProblems } from "../utils/workbenchForm"
import { TableBlock } from "./schema/TableBlock"

/** The schema's pane's editor: the form's tables of named, typed columns. */
export default function SchemaEditor({ readOnly = false }: { readOnly?: boolean }) {
  const form = useWorkbenchFormStore((s) => s.form)
  const change = useWorkbenchFormStore((s) => s.change)
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

  const addTable = () => {
    const table = createSchemaTable(form)
    change((f) => addSchemaTable(f, table))
    setAdded(table.id)
  }

  return (
    <div
      ref={scroller}
      onScroll={(event) => setScroll(event.currentTarget.scrollTop)}
      className="min-h-0 flex-1 overflow-auto"
      data-testid="schema-editor"
    >
      <div className="flex max-w-[640px] flex-col gap-1.5 px-4 py-3 text-xs">
        {tables.length === 0 && (
          <p className="px-1" style={{ color: "var(--text-muted)" }}>
            No tables yet. A table holds related columns, such as <code className="font-mono">policy_details</code>.
          </p>
        )}
        {tables.map((table, index) => (
          <TableBlock
            key={table.id}
            form={form}
            table={table}
            index={index}
            refocus={refocus}
            drag={readOnly || isQuoteDetails(table) ? undefined : dragTable(index)}
            readOnly={readOnly}
            added={added}
            setAdded={setAdded}
            details={details}
            setDetails={setDetails}
            collapsed={collapsed.has(table.id)}
            onToggleCollapsed={() => toggleCollapsed(table.id)}
            problems={problems.filter((p) => p.tableId === table.id).map((p) => p.message)}
          />
        ))}
        {!readOnly && <QuietAction onClick={addTable} className="justify-self-start">Add table</QuietAction>}
      </div>
    </div>
  )
}
