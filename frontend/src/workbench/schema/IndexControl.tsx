/** A many-row table's index, added and removed (specs/workbench). */
import type { FormSpec, SchemaTable } from "../../api/types"
import { ConfigCheckbox } from "../../components/form"
import { confirmed } from "../../stores/useQuestionStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { identityCount } from "../../utils/quoteIdentity"
import { addSchemaColumn, createIndexColumn, fieldUses, removeSchemaColumn } from "../../utils/workbenchForm"
import { components, currentForm } from "./schemaText"

/**
 * A many-row table's index: ticked, a column first in the table after the quote's reference
 * and option, holding each row's number, from 1 (`row_number` until renamed in its row, where it can also be made the
 * key). Unticked, it goes, asking first when a widget shows it.
 */
export function IndexControl({ form, table, onAdded }: { form: FormSpec; table: SchemaTable; onAdded: (id: string) => void }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const index = table.columns.find((column) => column.index)
  const uses = index ? fieldUses(form, table.id, index.id).length : 0
  // An index a sheet shows is removed once the question is answered yes.
  const remove = async () => {
    if (index === undefined) return
    const text = `The index is shown in ${components(uses)}; removing it takes it out of ${uses === 1 ? "that" : "them"} too.`
    if (uses > 0 && !(await confirmed({ title: `Remove the index of ${table.name}?`, text, confirm: "Remove", danger: true }))) return
    change((f) => removeSchemaColumn(f, table.id, index.id))
  }
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1">
      <ConfigCheckbox
        label={`Add index to ${table.name}`}
        checked={index !== undefined}
        onChange={(checked) => {
          if (checked) {
            const column = createIndexColumn(currentForm(), table.id)
            change((f) => addSchemaColumn(f, table.id, column, identityCount(table)))
            onAdded(column.id)
          } else void remove()
        }}
      />
    </div>
  )
}
