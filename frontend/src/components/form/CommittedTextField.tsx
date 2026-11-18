import {
  type CSSProperties,
  type InputHTMLAttributes,
  type Ref,
  type TextareaHTMLAttributes,
} from "react"
import { useCommittedDraft, type CommitResult, type CommittedDraft } from "./useCommittedDraft"

// ─── The shared typed fields ──────────────────────────────────────
//
// A controlled-looking text/number `<input>` (or a `<textarea>`) whose
// keystrokes stay in the shared draft (`useCommittedDraft`) and commit once on
// blur — and, for the single-line field, Enter — NOT on every keystroke, so a
// field edit is ONE undo step, and an Undo, a Redo or a sync frame that moves
// the value is what the field shows.
//
// `CommittedTextField` and `CommittedTextArea` hold their own draft;
// `DraftTextField` and `DraftTextArea` show a draft their caller holds, for a
// value whose draft outlives the field or that the caller validates.
// `ValidatedTextField` below adds the validation the API Input and Output
// editors' label, path and column-name fields need. The visible trade-off
// is deliberate: a node's canvas label updates on commit, not per
// keystroke — matching apiInput frame labels and the right-click Rename
// dialog.

type DraftFieldProps = Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange"> & {
  /** The input itself, passed through as React 19 passes a ref. */
  ref?: Ref<HTMLInputElement>
  /** The draft the field shows and commits. */
  draft: CommittedDraft<string>
}

/**
 * A single-line field showing *draft*: typing edits it, blur commits it, and Enter commits
 * it unless the caller's `onKeyDown`, which runs first, prevented the default.
 */
export function DraftTextField({ draft, onBlur, onKeyDown, ...rest }: DraftFieldProps) {
  return (
    <input
      {...rest}
      value={draft.shown}
      onChange={(e) => draft.edit(e.target.value)}
      onBlur={(e) => {
        draft.commit()
        onBlur?.(e)
      }}
      onKeyDown={(e) => {
        onKeyDown?.(e)
        if (e.key === "Enter" && !e.defaultPrevented) draft.commit()
      }}
    />
  )
}

type DraftAreaProps = Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "value" | "onChange"> & {
  /** The draft the area shows and commits. */
  draft: CommittedDraft<string>
}

/** A text area showing *draft*. Enter inserts a newline, so only blur commits it. */
export function DraftTextArea({ draft, onBlur, ...rest }: DraftAreaProps) {
  return (
    <textarea
      {...rest}
      value={draft.shown}
      onChange={(e) => draft.edit(e.target.value)}
      onBlur={(e) => {
        draft.commit()
        onBlur?.(e)
      }}
    />
  )
}

type CommittedTextFieldProps = Omit<DraftFieldProps, "draft"> & {
  /** The committed value from state — the source of truth when idle. */
  value: string
  /** Called once per commit boundary (blur / Enter) with the final
   *  value. Never called per keystroke — that is the whole point. */
  onCommit: (next: string) => void
  /** What the value belongs to (a row's identity), when the field is reused for another. */
  owner?: unknown
}

export default function CommittedTextField({ value, onCommit, owner, ...rest }: CommittedTextFieldProps) {
  return <DraftTextField {...rest} draft={useCommittedDraft(value, onCommit, { owner })} />
}

type CommittedTextAreaProps = Omit<DraftAreaProps, "draft"> & {
  /** The committed value from state — the source of truth when idle. */
  value: string
  /** Called once per commit boundary (blur) with the final value. */
  onCommit: (next: string) => void
}

/** Multi-line sibling of CommittedTextField. Commits on blur only — Enter
 *  inserts a newline in a textarea, so it is never a commit boundary here. */
export function CommittedTextArea({ value, onCommit, ...rest }: CommittedTextAreaProps) {
  return <DraftTextArea {...rest} draft={useCommittedDraft(value, onCommit)} />
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
  /** The input's id, for a `<label htmlFor>` outside the field. */
  id?: string
  /** The input's name for a screen reader, where no `<label>` names it. */
  ariaLabel?: string
  containerClassName: string
  className: string
  style: CSSProperties
  placeholder?: string
  /** The on-screen keyboard to offer, such as `decimal` for a number typed as text. */
  inputMode?: InputHTMLAttributes<HTMLInputElement>["inputMode"]
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
  id,
  ariaLabel,
  containerClassName,
  className,
  style,
  placeholder,
  inputMode,
}: ValidatedTextFieldProps) {
  const draft = useCommittedDraft(value, (next) =>
    validate(next) === null ? onCommit(next) : { ok: false },
  )
  const error = validate(draft.shown) ?? commitError
  return (
    <div className={containerClassName}>
      <DraftTextField
        draft={draft}
        data-testid={dataTestId}
        id={id}
        type="text"
        inputMode={inputMode}
        aria-label={ariaLabel}
        aria-invalid={error !== null ? true : undefined}
        placeholder={placeholder}
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
