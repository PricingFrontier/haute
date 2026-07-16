import {
  useState,
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
// This mirrors the commit-on-blur behaviour already proven by
// ApiInputEditor/OutputEditor's local `CommittedTextInput` (which also
// carry path-grammar validation + non-canonical/conflict hints those
// editors need). This is the general-purpose base for plain config
// fields with no such grammar; folding the two richer variants onto it
// is a later, separate refactor. The visible trade-off is deliberate:
// a node's canvas label updates on commit, not per keystroke — matching
// apiInput frame labels and the right-click Rename dialog.

/** Shared draft-buffer logic. Holds keystrokes locally; the external
 *  committed value wins whenever it changes out from under an open edit
 *  (undo/redo, programmatic edit, or the field being reused for a
 *  different node via a positional key); no-op commits are skipped so a
 *  blur with no change never churns state / the undo stack. */
function useCommittedDraft(value: string, onCommit: (next: string) => void) {
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
    onCommit(draft)
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
