import useToastStore from "../../../stores/useToastStore"
import { apiErrorMessage } from "../../../api/errors"
import { buildTsv, parsePastedGrid, writeClipboardText } from "./tableClipboard"

/** A grid of two text columns: its fields in column order, the headers a copy is written under, and what a failed copy calls it. */
type TwoColumnGrid<F extends string> = {
  name: string
  fields: readonly [F, F]
  copyHeaders: readonly [string, string]
}

type GridRow<F extends string> = Record<F, string>

function startsWithHeaders(cols: readonly string[], expected: readonly string[]): boolean {
  if (cols.length < expected.length) return false
  return expected.every((header, index) => cols[index]?.trim().toLowerCase() === header.toLowerCase())
}

/** A pasted row is the grid's header when it starts with the copy headers or the field names, in any case. */
export function isGridHeaderRow<F extends string>(grid: TwoColumnGrid<F>, cols: readonly string[]): boolean {
  return startsWithHeaders(cols, grid.copyHeaders) || startsWithHeaders(cols, grid.fields)
}

/** Writes the pasted cells from a row and field onward, adding blank rows below; cells past the last field are dropped. */
function applyPastedRange<F extends string>(
  grid: TwoColumnGrid<F>,
  rows: readonly GridRow<F>[],
  rowIndex: number,
  fieldIndex: number,
  matrix: string[][],
): GridRow<F>[] {
  const next = [...rows]

  for (let rowOffset = 0; rowOffset < matrix.length; rowOffset++) {
    const targetRow = rowIndex + rowOffset
    const row = next[targetRow]
      ? { ...next[targetRow] }
      : (Object.fromEntries(grid.fields.map((field) => [field, ""])) as GridRow<F>)

    for (let colOffset = 0; colOffset < matrix[rowOffset].length; colOffset++) {
      const field = grid.fields[fieldIndex + colOffset]
      if (!field) continue
      row[field] = matrix[rowOffset][colOffset]
    }
    next[targetRow] = row
  }

  return next
}

function gridTsv<F extends string>(grid: TwoColumnGrid<F>, rows: readonly GridRow<F>[]): string {
  return buildTsv([
    grid.copyHeaders,
    ...rows.map((row) => grid.fields.map((field) => row[field] ?? "")),
  ])
}

/**
 * The clipboard of a two-column grid: `handleCellPaste` fills a pasted range of
 * cells from the cell pasted into, and `copyTsv` copies the whole grid, headers
 * first, with a toast when the clipboard refuses.
 */
export function useTwoColumnGridClipboard<F extends string>(
  grid: TwoColumnGrid<F>,
  rows: readonly GridRow<F>[],
  onUpdate: (rows: GridRow<F>[]) => void,
) {
  const addToast = useToastStore((s) => s.addToast)

  const handleCellPaste = (
    e: React.ClipboardEvent<HTMLInputElement>,
    rowIndex: number,
    fieldIndex: number,
  ) => {
    const text = e.clipboardData.getData("text/plain")
    // One cell's text is left to the input.
    if (!text.includes("\t") && !text.includes("\n") && !text.includes("\r")) return

    e.preventDefault()
    e.stopPropagation()

    const pasted = parsePastedGrid(text)
    // A copied grid starts with its header, which lines up only when pasted into the first column.
    const matrix = fieldIndex === 0 && pasted.length > 0 && isGridHeaderRow(grid, pasted[0]) ? pasted.slice(1) : pasted
    if (matrix.length === 0) return

    onUpdate(applyPastedRange(grid, rows, rowIndex, fieldIndex, matrix))
  }

  const copyTsv = () => {
    void writeClipboardText(gridTsv(grid, rows)).catch((error: unknown) => {
      addToast("error", `Could not copy ${grid.name} TSV: ${apiErrorMessage(error)}`)
    })
  }

  return { handleCellPaste, copyTsv }
}
