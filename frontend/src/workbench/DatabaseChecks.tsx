import { QuietAction } from "../components/form"
import { Undo2 } from "lucide-react"
import { useDatabaseComparison } from "../stores/useStoreCompareStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import DatabaseDisclosure, { DatabaseExtras } from "./DatabaseDisclosure"

/** Diagnostics without a current field or table, including explicit orphaned choices. */
export default function DatabaseChecks() {
  const { state, form, answer } = useDatabaseComparison()
  const readOnly = useWorkbenchViewStore((s) => s.mode === "preview")
  if (answer === null || form === null) return null
  const messages = [...new Set([
    ...answer.differences.filter((each) => each.table === null).map((each) => each.message),
    ...answer.blocked,
  ])]
  const extras = answer.extras.filter((each) => each.form_table === null)
  const orphaned = state.renames.filter((rename) => !form.schema.tables.some((table) =>
    table.name === rename.table && [...table.inputs, ...table.outputs].some((column) => column.name === rename.column)))
  if (messages.length + extras.length + orphaned.length === 0) return null
  return <DatabaseDisclosure title="Database checks" className="text-[11px] text-[var(--text-secondary)]">
    <div role="region" aria-label="Database checks" className="flex flex-col gap-2 py-1 leading-relaxed">
      {messages.map((message) => <p key={message} className="break-words text-[var(--warning)]">{message}</p>)}
      <DatabaseExtras extras={extras} />
      {orphaned.map((rename) => <div key={`${rename.table}.${rename.column}`}>
        <p><code>{rename.table}.{rename.column}</code> is no longer in the schema. Its rename choice still uses <code>{rename.was}</code>.</p>
        <QuietAction icon={Undo2} disabled={readOnly} onClick={() => state.unpair(rename.table, rename.column)}>Remove rename choice</QuietAction>
      </div>)}
    </div>
  </DatabaseDisclosure>
}
