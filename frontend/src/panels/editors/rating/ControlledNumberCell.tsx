import { useId, useState } from "react"
import { CellIssueMark } from "../shared/CellIssueMark"
import { cellIssueStyle } from "../shared/cellIssueStyle"
import { parseRelativity } from "./ratingTableUtils"

const INVALID_MESSAGE = "Enter a number, such as 1.25."

/**
 * A relativity cell: the typed text stays local until blur, and an outside change
 * of the value (undo, redo, a paste) replaces it. A blur commits a number, or
 * `null` for a cleared cell; text that is not a number is kept, marked and not
 * committed.
 */
export function ControlledNumberCell({ val, onCommit, style, className, ...rest }: {
  val: number
  onCommit: (value: number | null) => void
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "onBlur" | "type" | "inputMode" | "step">) {
  const committed = String(val)
  const [draft, setDraft] = useState<string | null>(null)
  const [lastCommitted, setLastCommitted] = useState(committed)
  if (lastCommitted !== committed) {
    setLastCommitted(committed)
    setDraft(null)
  }
  const shown = draft ?? committed
  const parsed = parseRelativity(shown)
  const invalid = parsed.kind === "invalid"
  const issueId = useId()

  const commit = () => {
    if (draft === null) return
    if (draft === committed) {
      setDraft(null)
      return
    }
    if (parsed.kind === "invalid") return
    onCommit(parsed.kind === "blank" ? null : parsed.value)
    setDraft(null)
  }

  return (
    <div className="relative">
      <input
        {...rest}
        type="text"
        inputMode="decimal"
        value={shown}
        aria-invalid={invalid ? true : undefined}
        aria-describedby={invalid ? issueId : undefined}
        title={invalid ? INVALID_MESSAGE : rest.title}
        className={`${className ?? ""}${invalid ? " pr-5" : ""}`}
        style={invalid ? cellIssueStyle(style ?? {}, "error") : style}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
      />
      {invalid && <CellIssueMark id={issueId} message={INVALID_MESSAGE} severity="error" />}
    </div>
  )
}
