/**
 * A Text Box on a sheet (specs/workbench): its column's label over a box filling the rest of
 * the component, which takes text over as many lines as are typed, committed as the box is
 * left, as the sheet's other cells write theirs (`useSheetValues`).
 */
import { CommittedTextArea, INPUT_STYLE } from "../../components/form"
import { cellKey } from "../../utils/sheetValues"
import { columnKey } from "../../utils/sheetVisibility"
import { takesText, type ShownField } from "../../utils/workbenchForm"
import { useSheetValues } from "../useSheetValues"
import { FieldLabel } from "./Cell"
import { fieldLabel, typeHere } from "./fieldCells"
import { type SheetColours } from "./sheetColours"

/** A box breaking its column's rules, as a cell is marked. */
const PROBLEM_BORDER = "1px solid var(--danger)"

/**
 * Its column's label, its required mark and its help, over a box of the sheets' input look
 * taking its column's text in the one-row table's row, read only while the values are or
 * the app sets the column, and while its column takes no text; while building with no column,
 * the prompt to choose one, and in Preview nothing.
 */
export function TextBoxBody({ fields, colours, previewing }: { fields: readonly ShownField[]; colours: SheetColours; previewing: boolean }) {
  const { rows, setCell, problems, readOnly, locked } = useSheetValues()
  const found = fields[0]?.found ?? null
  if (found === null) {
    return previewing ? null : (
      <div
        className="grid h-full place-items-center rounded-md text-center text-[11px]"
        style={{ border: "1px dashed var(--border)", color: "var(--text-secondary)" }}
      >
        Choose its field in the panel on the right
      </div>
    )
  }
  const { table, column } = found
  const value = rows[table.name]?.[0]?.[column.name]
  const problem = problems[cellKey(table.name, 0, column.name)] ?? null
  const label = fieldLabel(found)
  return (
    <div className="flex h-full min-h-0 flex-col gap-1">
      <span className="truncate text-xs font-medium" style={{ color: colours.font ?? "var(--text-secondary)" }}>
        <FieldLabel found={found} />
      </span>
      <div className="pointer-events-auto flex min-h-0 flex-1" onPointerDown={typeHere}>
        <CommittedTextArea
          aria-label={label}
          {...(column.required ? { "aria-required": true as const } : {})}
          {...(problem === null ? {} : { "aria-invalid": true as const, title: problem })}
          value={typeof value === "string" ? value : ""}
          onCommit={(text) => setCell(table.name, 0, column.name, text)}
          placeholder={column.placeholder || undefined}
          disabled={readOnly || locked.has(columnKey(table.name, column.name)) || !takesText(table, column)}
          className="min-h-0 w-full flex-1 resize-none rounded-md px-2.5 py-1.5 text-xs"
          style={{ ...INPUT_STYLE, ...(problem === null ? {} : { border: PROBLEM_BORDER }) }}
        />
      </div>
    </div>
  )
}
