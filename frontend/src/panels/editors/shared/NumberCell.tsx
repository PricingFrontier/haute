import { useId, type InputHTMLAttributes } from "react"
import { DraftTextField } from "../../../components/form/CommittedTextField"
import { useNumberDraft } from "../../../components/form/useNumberDraft"
import { CellIssueMark } from "./CellIssueMark"
import { cellIssueStyle } from "./cellIssueStyle"

/**
 * A grid's number cell (a relativity, a breakpoint's boundary): the shared number field's
 * draft (`useNumberDraft`) drawn as a cell. The typed text is the draft until blur or Enter,
 * read then by the one grammar for a typed number; a commit writes the number, or null for
 * a cleared cell; text refused, a decimal comma among it, is kept, marked with its reason
 * and not committed. An outside change of the value (Undo, Redo, a paste) replaces the draft.
 */
export function NumberCell({ value, onCommit, row, style, className, ...rest }: {
  value: number | null
  onCommit: (value: number | null) => void
  /** The identity of the cell's row (its key from `useRowKeys`): a draft stays with its row. */
  row?: number
} & Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "onBlur" | "type" | "inputMode" | "step" | "min" | "max">) {
  const { draft, problem } = useNumberDraft(value, onCommit, {}, { owner: row })
  const issueId = useId()

  return (
    <div className="relative">
      <DraftTextField
        {...rest}
        draft={draft}
        type="text"
        inputMode="decimal"
        aria-invalid={problem !== null ? true : rest["aria-invalid"]}
        aria-describedby={problem !== null ? issueId : rest["aria-describedby"]}
        title={problem ?? rest.title}
        className={`${className ?? ""}${problem !== null ? " pr-5" : ""}`}
        style={problem !== null ? cellIssueStyle(style ?? {}, "error") : style}
      />
      {problem !== null && <CellIssueMark id={issueId} message={problem} severity="error" />}
    </div>
  )
}
