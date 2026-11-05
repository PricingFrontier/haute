/**
 * A many-row input table's index and key as the schema editor marks them (specs/workbench).
 * Neither is a control: the operations give a table its index and take it away
 * (`withIndexAsNeeded`), and a table with inputs is keyed by the column giving its rows or by
 * its index (`keyTable`), which no underwriter types.
 */
import { KeyRound } from "lucide-react"
import type { SchemaColumn } from "../../api/types"
import { COLUMN_TYPES } from "../columnTypes"
import { MARKER_STYLE } from "./schemaText"

/** The index's type marker, a fixed Integer: each row's number, from 1. */
export function IndexMarker() {
  const { icon: Icon, color } = COLUMN_TYPES.int
  return (
    <span className={`relative flex w-6 shrink-0 items-center justify-center rounded-md ${color}`} style={MARKER_STYLE} title="Integer: each row's number, from 1">
      <Icon size={12} aria-hidden="true" />
    </span>
  )
}

/**
 * Whether a column of a many-row table with inputs keys it, blue on the key (its index, or
 * the column giving its rows) and grey on every other column.
 */
export function KeyMark({ column, isKey }: { column: SchemaColumn; isKey: boolean }) {
  const title = !isKey ? "Not the key" : column.index ? "The key: each row's number" : "The key: a row for each value"
  return (
    <span className="flex size-5 shrink-0 items-center justify-center self-center" title={title}>
      <KeyRound
        size={12}
        role="img"
        aria-label={isKey ? `${column.name} is the key` : `${column.name} is not the key`}
        style={{ color: isKey ? "var(--accent)" : "var(--text-muted)" }}
      />
    </span>
  )
}
