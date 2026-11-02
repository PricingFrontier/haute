import { useRef, useState } from "react"
import { buildTsv } from "../shared/tableClipboard"

type CellSelection = { anchorRow: number; anchorCol: number; focusRow: number; focusCol: number }

/** The grid's row and column labels in display order, and the text a copy gives the cell where they meet. */
type GridCells = {
  rows: readonly string[]
  cols: readonly string[]
  cellText: (row: string, col: string) => string
}

function hasSelectedText(target: EventTarget): boolean {
  return (
    target instanceof HTMLInputElement &&
    target.selectionStart !== null &&
    target.selectionEnd !== null &&
    target.selectionStart !== target.selectionEnd
  )
}

/**
 * A rating grid's range of selected cells. Pressing a cell starts a range and
 * focuses the grid's region; dragging over cells extends it; releasing on the
 * cell pressed focuses that cell's input; Escape clears it. A copy in the
 * region writes the selected cells as TSV, row by row, unless text inside an
 * input is selected, which the browser copies as usual.
 */
export function useGridCellSelection() {
  const regionRef = useRef<HTMLDivElement>(null)
  const [selection, setSelection] = useState<CellSelection | null>(null)
  const [isDraggingSelection, setIsDraggingSelection] = useState(false)

  const range = selection
    ? {
      startRow: Math.min(selection.anchorRow, selection.focusRow),
      endRow: Math.max(selection.anchorRow, selection.focusRow),
      startCol: Math.min(selection.anchorCol, selection.focusCol),
      endCol: Math.max(selection.anchorCol, selection.focusCol),
    }
    : null

  const isCellSelected = (rowIndex: number, colIndex: number) =>
    Boolean(range &&
      rowIndex >= range.startRow &&
      rowIndex <= range.endRow &&
      colIndex >= range.startCol &&
      colIndex <= range.endCol)

  /** Handlers for the focusable region that holds the grid. */
  const regionProps = ({ rows, cols, cellText }: GridCells) => ({
    onCopy: (e: React.ClipboardEvent<HTMLElement>) => {
      if (hasSelectedText(e.target) || !range) return
      const text = buildTsv(rows
        .slice(range.startRow, range.endRow + 1)
        .map(row => cols.slice(range.startCol, range.endCol + 1).map(col => cellText(row, col))))
      if (!text) return
      e.preventDefault()
      e.clipboardData.setData("text/plain", text)
    },
    onKeyDown: (e: React.KeyboardEvent<HTMLElement>) => {
      if (e.key !== "Escape") return
      setSelection(null)
      setIsDraggingSelection(false)
    },
    onMouseLeave: () => setIsDraggingSelection(false),
    onMouseUp: () => setIsDraggingSelection(false),
  })

  /** Selection state and handlers for the editable cell at a row and column. */
  const cellProps = (rowIndex: number, colIndex: number) => ({
    "data-selected": isCellSelected(rowIndex, colIndex) || undefined,
    "aria-selected": isCellSelected(rowIndex, colIndex) || undefined,
    onMouseDown: () => {
      regionRef.current?.focus({ preventScroll: true })
      setSelection({ anchorRow: rowIndex, anchorCol: colIndex, focusRow: rowIndex, focusCol: colIndex })
      setIsDraggingSelection(true)
    },
    onMouseEnter: () => {
      if (isDraggingSelection) {
        setSelection(current => current ? { ...current, focusRow: rowIndex, focusCol: colIndex } : current)
      }
    },
    onMouseUp: (e: React.MouseEvent<HTMLElement>) => {
      setIsDraggingSelection(false)
      if (
        selection?.anchorRow === rowIndex &&
        selection.anchorCol === colIndex &&
        selection.focusRow === rowIndex &&
        selection.focusCol === colIndex
      ) {
        e.currentTarget.querySelector("input")?.focus()
      }
    },
  })

  return { regionRef, regionProps, cellProps, isCellSelected }
}
