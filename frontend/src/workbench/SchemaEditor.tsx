/**
 * The workbench's schema editor (specs/workbench): the form's tables of named, typed
 * columns, which are the pipeline's tables. Every text field commits on blur or Enter, so
 * an edit is one undo step; Enter in a column's name adds the next column, and Alt+Up and
 * Alt+Down move a column, as the step editor's cards move. Removing what a sheet shows
 * asks first. Haute's quote_details table, first, keeps its structure: its columns' labels,
 * groups and help, and the rules and defaults of those the underwriter types, are the
 * project's.
 */
import { useMemo, useState } from "react"
import { QuietAction } from "../components/form"
import useCollapsedSet from "../hooks/useCollapsedSet"
import useDocumentStatusStore from "../stores/useDocumentStatusStore"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import { addSchemaTable, createSchemaTable, schemaProblems } from "../utils/workbenchForm"
import { TableBlock } from "./schema/TableBlock"

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
  // The tables shown collapsed.
  const { collapsed, toggle: toggleCollapsed } = useCollapsedSet()
  if (form === null) throw new Error("The schema editor needs the form loaded")
  const problems = schemaProblems(form, reserved)
  const { tables } = form.schema

  const addTable = () => {
    const table = createSchemaTable(form)
    change((f) => addSchemaTable(f, table))
    setAdded(table.id)
  }

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
        <QuietAction onClick={addTable} className="justify-self-start">Add table</QuietAction>
      </div>
    </div>
  )
}
