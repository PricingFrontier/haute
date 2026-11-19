import { useId, type InputHTMLAttributes } from "react"
import { DraftTextField } from "../../../components/form/CommittedTextField"
import { useCommittedDraft } from "../../../components/form/useCommittedDraft"
import { CellIssueMark } from "./CellIssueMark"
import { cellIssueStyle } from "./cellIssueStyle"

/** What a cell's typed text reads as: the text to store, or why it is refused. */
export type CellRead = { ok: true; value: string } | { ok: false; problem: string }

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
  /**
   * How the typed text is read before it is stored (a number box's: by the one grammar for a
   * typed number); text it refuses stays in the cell, marked with its reason, uncommitted.
   */
  read?: (text: string) => CellRead
}

/** A grid's text cell: its edit is one undo step, and an Undo that moves the row is what it shows. */
export function GridCell({ value, row, onCommit, onEnter, read, ...rest }: GridCellProps) {
  const draft = useCommittedDraft(
    value,
    (next) => {
      if (read === undefined) return onCommit(next)
      const typed = read(next)
      if (!typed.ok) return { ok: false }
      if (typed.value !== value) onCommit(typed.value)
    },
    { owner: row },
  )
  const typed = read !== undefined && draft.editing ? read(draft.shown) : null
  const problem = typed !== null && !typed.ok ? typed.problem : null
  const problemId = useId()
  return (
    <>
      <DraftTextField
        {...rest}
        draft={draft}
        aria-invalid={problem !== null ? true : rest["aria-invalid"]}
        aria-describedby={problem !== null ? problemId : rest["aria-describedby"]}
        title={problem ?? rest.title}
        className={problem !== null && !(rest.className ?? "").includes("pr-5") ? `${rest.className ?? ""} pr-5` : rest.className}
        style={problem !== null ? cellIssueStyle(rest.style ?? {}, "error") : rest.style}
        onKeyDown={(e) => {
          if (e.key === "Enter" && onEnter?.(draft.shown)) {
            e.preventDefault()
            draft.discard()
          }
        }}
      />
      {problem !== null && <CellIssueMark id={problemId} message={problem} severity="error" />}
    </>
  )
}
