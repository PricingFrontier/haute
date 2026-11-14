/**
 * A many-row table's index as the schema editor offers it (specs/workbench): how it gets its
 * values, chosen in its config panel. The operations give a table its index, numbered, move it between
 * its blocks and take it away (`withIndexAsNeeded`); no key toggle is needed, every table's
 * key being the quote's identity and a many-row table's its index too.
 */
import type { SchemaColumn } from "../../api/types"
import { Select } from "../../haute-ui"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import type { IndexKind } from "../../utils/quoteIdentity"
import { setIndexKind } from "../../utils/workbenchForm"

/** The ways an index gets its values, in the order the choice offers them. */
const INDEX_KINDS: ReadonlyArray<{ value: IndexKind; label: string }> = [
  { value: "numbered", label: "Numbered" },
  { value: "typed", label: "Typed" },
  { value: "values", label: "A row for each value" },
]

/**
 * How the index `column` of the table `tableId` gets its values: Numbered, Typed or A row
 * for each value (`setIndexKind`), each choice one edit. The containing config fieldset
 * disables it in Preview and shows the allowed values when the index takes its rows from them.
 */
export function IndexKindSelect({
  tableId,
  column,
}: {
  tableId: string
  column: SchemaColumn
}) {
  const change = useWorkbenchFormStore((s) => s.change)
  return (
    <Select
      aria-label={`How ${column.name} gets its values`}
      value={column.index ?? undefined}
      onChange={(e) => {
        const kind = e.target.value as IndexKind
        change((f) => setIndexKind(f, tableId, kind))
      }}
      fieldSize="compact"
      className="w-full"
    >
      {INDEX_KINDS.map(({ value, label }) => (
        <option key={value} value={value}>
          {label}
        </option>
      ))}
    </Select>
  )
}
