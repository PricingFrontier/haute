import type { InputHTMLAttributes } from "react"
import { DraftTextField } from "../../../components/form/CommittedTextField"
import { useCommittedDraft } from "../../../components/form/useCommittedDraft"

type GridCellProps = Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "onKeyDown"> & {
  /** The cell's stored text. */
  value: string
  /** The identity of the cell's row (its key from `useRowKeys`): a draft stays with its row. */
  row: number
  /** The cell's edit, once, on leaving the cell or Enter. */
  onCommit: (next: string) => void
  /**
   * Enter, with what the cell shows: returns true when the grid wrote it itself (with a row it
   * added in the same update), so the cell commits nothing more.
   */
  onEnter?: (shown: string) => boolean
}

/** A grid's text cell: its edit is one undo step, and an Undo that moves the row is what it shows. */
export function GridCell({ value, row, onCommit, onEnter, ...rest }: GridCellProps) {
  const draft = useCommittedDraft(value, onCommit, { owner: row })
  return (
    <DraftTextField
      {...rest}
      draft={draft}
      onKeyDown={(e) => {
        if (e.key === "Enter" && onEnter?.(draft.shown)) {
          e.preventDefault()
          draft.discard()
        }
      }}
    />
  )
}
