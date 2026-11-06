/**
 * A many-row table's index as the schema editor offers it (specs/workbench): how it gets its
 * values, chosen beside it. The operations give a table its index, numbered, move it between
 * its blocks and take it away (`withIndexAsNeeded`); nothing marks it, or any column, as a
 * key, every table's key being the quote's identity and a many-row table's its index too.
 */
import type { SchemaColumn } from "../../api/types"
import { COMPACT_FIELD_CLASS, INPUT_STYLE } from "../../components/form"
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
 * for each value (`setIndexKind`), each choice one edit. Choosing a row for each value calls
 * `onValues`, so the index's details open on the allowed values that give its rows.
 */
export function IndexKindSelect({
  tableId,
  column,
  disabled,
  onValues,
}: {
  tableId: string
  column: SchemaColumn
  disabled: boolean
  onValues: () => void
}) {
  const change = useWorkbenchFormStore((s) => s.change)
  return (
    <select
      aria-label={`How ${column.name} gets its values`}
      value={column.index ?? undefined}
      disabled={disabled}
      onChange={(e) => {
        const kind = e.target.value as IndexKind
        change((f) => setIndexKind(f, tableId, kind))
        if (kind === "values") onValues()
      }}
      className={`${COMPACT_FIELD_CLASS} shrink-0 self-center`}
      style={INPUT_STYLE}
    >
      {INDEX_KINDS.map(({ value, label }) => (
        <option key={value} value={value}>
          {label}
        </option>
      ))}
    </select>
  )
}
