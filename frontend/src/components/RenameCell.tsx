/**
 * Uncontrolled, commit-on-blur/Enter rename input shared by the unified
 * data-schema selectors (DESIGN_PRINCIPLES.md §1) — the output `ColumnSelector`
 * and the input binding selector.
 *
 * Deliberately UNCONTROLLED (`defaultValue`): the parent keys the surrounding
 * row by a stable identity (a column's incoming name; an input's edge id), so
 * the input keeps its in-progress draft across re-derives, reorders, and toggles
 * and never fights the cursor. Commit on blur or Enter; Escape reverts to the
 * last-committed value. This keying discipline is load-bearing — moving to a
 * controlled value reintroduces cursor-jump and lost-draft bugs.
 */
export default function RenameCell({
  initial,
  placeholder,
  onCommit,
  testId,
}: {
  initial: string
  placeholder: string
  onCommit: (value: string) => void
  testId?: string
}) {
  return (
    <input
      type="text"
      data-testid={testId}
      defaultValue={initial}
      placeholder={placeholder}
      spellCheck={false}
      className="w-full px-1.5 py-0.5 text-[11px] font-mono rounded border bg-transparent focus:outline-none focus:ring-1"
      style={{ color: "var(--text-primary)", borderColor: "var(--border)", background: "var(--bg-input)" }}
      onClick={(e) => e.stopPropagation()}
      onBlur={(e) => onCommit(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur()
        else if (e.key === "Escape") {
          e.currentTarget.value = initial
          e.currentTarget.blur()
        }
      }}
    />
  )
}
