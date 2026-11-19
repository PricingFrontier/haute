import { readNumberDraft, type NumberRule } from "../../utils/numberRule"
import { useCommittedDraft, type CommitResult, type CommittedDraft } from "./useCommittedDraft"

// The draft of a number box (specs/frontend-shared high-level, "Typed numbers"): the shared
// number field's, and a grid cell's that draws its own input.

/** A stored number as a number field shows it: its shortest text, a blank for none. */
export const numberShown = (value: number | null | undefined): string => (value === null || value === undefined ? "" : String(value))

export type NumberDraft = {
  /** The shared draft of the field's text. */
  draft: CommittedDraft<string>
  /** Why the text being typed, or a stored number, is refused; null when nothing is. */
  problem: string | null
}

/**
 * The draft of a number field showing *value*: on a commit its text is read under *rule*
 * and *onCommit* takes the number, or null for a blank optional field; text refused stays
 * in the draft with its reason. For a box that draws its own input (a grid's cell).
 */
export function useNumberDraft(
  value: number | null | undefined,
  onCommit: (next: number | null) => CommitResult | void,
  rule: NumberRule = {},
  { owner }: { owner?: unknown } = {},
): NumberDraft {
  const shown = numberShown(value)
  const draft = useCommittedDraft(
    shown,
    (typed) => {
      const read = readNumberDraft(typed, rule)
      if (!read.ok) return { ok: false }
      if (read.value === (value ?? null)) return
      const result = onCommit(read.value)
      return result ? { ok: result.ok } : undefined
    },
    { owner },
  )
  // A stored number the rule refuses (from a settings file) is marked without any typing; a
  // blank waits for the analyst to type.
  const read = draft.editing || shown !== "" ? readNumberDraft(draft.shown, rule) : null
  return { draft, problem: read !== null && !read.ok ? read.problem : null }
}
