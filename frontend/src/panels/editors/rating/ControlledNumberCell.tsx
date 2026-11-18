import { useId } from "react"
import { DraftTextField } from "../../../components/form/CommittedTextField"
import { useCommittedDraft } from "../../../components/form/useCommittedDraft"
import { CellIssueMark } from "../shared/CellIssueMark"
import { cellIssueStyle } from "../shared/cellIssueStyle"
import { parseRelativity } from "./ratingTableUtils"

const INVALID_MESSAGE = "Enter a number, such as 1.25."

/**
 * A relativity cell: the typed text is the shared draft until blur or Enter, and an
 * outside change of the value (undo, redo, a paste) replaces it. A commit writes a
 * number, or `null` for a cleared cell; text that is not a number is kept, marked and
 * not committed.
 */
export function ControlledNumberCell({ val, onCommit, style, className, ...rest }: {
  val: number
  onCommit: (value: number | null) => void
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "onBlur" | "type" | "inputMode" | "step">) {
  const draft = useCommittedDraft(String(val), (typed) => {
    const parsed = parseRelativity(typed)
    if (parsed.kind === "invalid") return { ok: false }
    onCommit(parsed.kind === "blank" ? null : parsed.value)
  })
  const invalid = parseRelativity(draft.shown).kind === "invalid"
  const issueId = useId()

  return (
    <div className="relative">
      <DraftTextField
        {...rest}
        draft={draft}
        type="text"
        inputMode="decimal"
        aria-invalid={invalid ? true : undefined}
        aria-describedby={invalid ? issueId : undefined}
        title={invalid ? INVALID_MESSAGE : rest.title}
        className={`${className ?? ""}${invalid ? " pr-5" : ""}`}
        style={invalid ? cellIssueStyle(style ?? {}, "error") : style}
      />
      {invalid && <CellIssueMark id={issueId} message={INVALID_MESSAGE} severity="error" />}
    </div>
  )
}
