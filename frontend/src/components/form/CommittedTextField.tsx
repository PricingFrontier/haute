import {
  useState,
  type CSSProperties,
  type InputHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react"

// ─── CommittedTextField / CommittedTextArea ───────────────────────
//
// Drop-in replacements for a controlled text/number `<input>` (or a
// `<textarea>`) that buffer keystrokes locally and commit to state only
// on blur — and, for the single-line field, Enter — NOT on every
// keystroke.
//
// Why this exists (BUGS Undo-atomicity class / MAGINOT_LINE "Undo
// atomicity — one user gesture, multiple undo steps"): the inline
// sidebar config editors wired `onChange → onUpdate → onUpdateNode →
// setNodes`, and `setNodes` pushes an undo snapshot on every call. So
// typing an N-character value recorded N undo entries and ⌘/Ctrl-Z
// reverted one character at a time. Committing once per edit collapses
// a field edit to ONE undo step — the same one-gesture-one-snapshot
// shape as the delete/paste `setNodesAndEdges` chokepoint.
//
// `ValidatedTextField` below adds the validation the API Input and Output
// editors' label, path and column-name fields need. The visible trade-off
// is deliberate: a node's canvas label updates on commit, not per
// keystroke — matching apiInput frame labels and the right-click Rename
// dialog.

/** What a commit reports: nothing when it always lands, or `{ ok }` when
 *  its owner can refuse it — a refused commit keeps the draft on screen. */
type CommitResult = void | { ok: boolean }

/** Shared draft-buffer logic. Holds keystrokes locally; the external
 *  committed value wins whenever it changes out from under an open edit
 *  (undo/redo, programmatic edit, or the field being reused for a
 *  different node via a positional key); no-op commits are skipped so a
 *  blur with no change never churns state / the undo stack. */
function useCommittedDraft(value: string, onCommit: (next: string) => CommitResult) {
  // Raw edit buffer; null = not editing, render the committed value.
  const [draft, setDraft] = useState<string | null>(null)
  // React's adjust-state-on-render pattern: drop a stale draft the moment
  // the committed value changes, so a half-typed value is never shown for,
  // or committed into, the new value.
  const [lastValue, setLastValue] = useState(value)
  if (lastValue !== value) {
    setLastValue(value)
    setDraft(null)
  }
  const shown = draft ?? value
  const commit = () => {
    if (draft === null) return
    if (draft === value) {
      setDraft(null)
      return
    }
    const result = onCommit(draft)
    if (result && !result.ok) return
    setDraft(null)
  }
  return { shown, setDraft, commit }
}

type CommittedTextFieldProps = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "value" | "onChange"
> & {
  /** The committed value from state — the source of truth when idle. */
  value: string
  /** Called once per commit boundary (blur / Enter) with the final
   *  value. Never called per keystroke — that is the whole point. */
  onCommit: (next: string) => void
}

export default function CommittedTextField({
  value,
  onCommit,
  onBlur,
  onKeyDown,
  ...rest
}: CommittedTextFieldProps) {
  const { shown, setDraft, commit } = useCommittedDraft(value, onCommit)
  return (
    <input
      {...rest}
      value={shown}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={(e) => {
        commit()
        onBlur?.(e)
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit()
        onKeyDown?.(e)
      }}
    />
  )
}

type CommittedTextAreaProps = Omit<
  TextareaHTMLAttributes<HTMLTextAreaElement>,
  "value" | "onChange"
> & {
  /** The committed value from state — the source of truth when idle. */
  value: string
  /** Called once per commit boundary (blur) with the final value. */
  onCommit: (next: string) => void
}

/** Multi-line sibling of CommittedTextField. Commits on blur only — Enter
 *  inserts a newline in a textarea, so it is never a commit boundary here. */
export function CommittedTextArea({
  value,
  onCommit,
  onBlur,
  ...rest
}: CommittedTextAreaProps) {
  const { shown, setDraft, commit } = useCommittedDraft(value, onCommit)
  return (
    <textarea
      {...rest}
      value={shown}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={(e) => {
        commit()
        onBlur?.(e)
      }}
    />
  )
}

type ValidatedTextFieldProps = {
  /** The committed value from config — the source of truth when idle. */
  value: string
  /** Called once per commit boundary (blur / Enter) with a valid value.
   *  Returning `{ ok: false }` refuses it and keeps the draft. */
  onCommit: (next: string) => CommitResult
  /** User-facing error for a candidate; null = valid. An invalid candidate
   *  is never committed, and an invalid committed value (from disk or an
   *  inference merge) shows its error without any interaction. */
  validate: (candidate: string) => string | null
  /** The commit owner's rejection, shown while the value itself is valid. */
  commitError?: string | null
  /** A non-blocking advisory, shown only when there is no error. */
  warning?: string | null
  dataTestId: string
  containerClassName: string
  className: string
  style: CSSProperties
  placeholder?: string
}

/** The API Input and Output editors' schema fields (labels, paths, column
 *  names): a committed single-line field that refuses invalid values at the
 *  commit boundary, keeping the draft and a visible error so nothing
 *  destructive reaches config, and the user can fix or revert. The error
 *  sits under the field as `${dataTestId}-error`, the warning as
 *  `${dataTestId}-warning`. */
export function ValidatedTextField({
  value,
  onCommit,
  validate,
  commitError = null,
  warning = null,
  dataTestId,
  containerClassName,
  className,
  style,
  placeholder,
}: ValidatedTextFieldProps) {
  const { shown, setDraft, commit } = useCommittedDraft(value, (next) =>
    validate(next) === null ? onCommit(next) : { ok: false },
  )
  const error = validate(shown) ?? commitError
  return (
    <div className={containerClassName}>
      <input
        data-testid={dataTestId}
        type="text"
        value={shown}
        aria-invalid={error !== null ? true : undefined}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit()
        }}
        className={className}
        style={error !== null ? { ...style, border: "1px solid var(--danger-border-strong)" } : style}
      />
      {error !== null && (
        <div
          data-testid={`${dataTestId}-error`}
          className="mt-0.5 px-1.5 py-0.5 rounded text-[10px] leading-snug"
          style={{ background: "var(--danger-soft)", color: "var(--danger-text)" }}
        >
          {error}
        </div>
      )}
      {error === null && warning && (
        <div
          data-testid={`${dataTestId}-warning`}
          className="mt-0.5 px-1.5 py-0.5 rounded text-[10px] leading-snug"
          style={{ background: "var(--warning-soft)", color: "var(--warning-strong)" }}
        >
          {warning}
        </div>
      )}
    </div>
  )
}
