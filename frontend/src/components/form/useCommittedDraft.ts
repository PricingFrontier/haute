import { useLayoutEffect, useState } from "react"

// ─── The shared draft ─────────────────────────────────────────────
//
// A value being edited in a panel or a node is held here and nowhere else
// (specs/frontend-shared high-level, "Panel values and Undo"). A copy held any
// other way went on showing, and sending, its own value after an Undo (the
// search space box), or wrote every keystroke as its own undo step (the Banding
// grids): `setNodes` pushes an undo snapshot on every call, so typing an
// N-character value recorded N undo entries. A draft commits once, and lets go
// whenever the value it was typed over moves. `draftsByHand.test.ts` fails for a
// panel that holds a copy another way.

/** What a commit reports: nothing when it always lands, or `{ ok }` when its owner can refuse it — a refused commit keeps the draft on screen. */
export type CommitResult = void | { ok: boolean }

/** An edit not yet committed, as a save sees it: committing it says whether it landed. */
type OpenEdit = { commit: () => boolean }

const openEdits = new Set<OpenEdit>()

/**
 * Commit every open draft, as leaving its field would: the first step of the barrier every
 * save waits on (`useGraphCommitController.settleEdits`). A draft its owner refuses stays on
 * screen with its reason, and stays open.
 */
export function commitOpenDrafts(): void {
  for (const edit of [...openEdits]) {
    if (edit.commit()) openEdits.delete(edit)
  }
}

/** Whether any field holds an edit not yet committed, asked from outside React. */
export function isDraftOpen(): boolean {
  return openEdits.size > 0
}

/**
 * Register an edit that lives outside the shared draft (a code editor's text not yet
 * flushed) while *commit* is given, so a save commits it first. `commit` returns whether
 * the edit landed.
 */
export function useOpenEdit(commit: (() => boolean) | null): void {
  useLayoutEffect(() => {
    if (!commit) return
    const edit: OpenEdit = { commit }
    openEdits.add(edit)
    return () => {
      openEdits.delete(edit)
    }
  }, [commit])
}

export type CommittedDraft<T> = {
  /** The draft once typed, otherwise the stored value. */
  shown: T
  /** Whether a draft is open. */
  editing: boolean
  /** Replace the draft with what was typed. */
  edit: (next: T) => void
  /** Commit the draft once; whether it landed (true when there was nothing to commit). */
  commit: () => boolean
  /** Drop the draft without committing it. */
  discard: () => void
}

export type DraftOptions<T> = {
  /**
   * What the value belongs to: a row's identity from `useRowKeys`, a node's id. A draft for
   * one owner is never shown for, or committed into, another.
   */
  owner?: unknown
  /** When two stored values are the same; `Object.is` by default. */
  equals?: (a: T, b: T) => boolean
}

/**
 * The draft of *value*, committed through *onCommit*. The stored value wins whenever it
 * moves under an open draft (an Undo, a Redo, a sync frame, any change from outside the
 * field) or the owner changes; a commit of a value equal to the stored one commits nothing,
 * so a blur with no change never churns state or the undo history.
 */
export function useCommittedDraft<T>(
  value: T,
  onCommit: (next: T) => CommitResult,
  { owner, equals = Object.is }: DraftOptions<T> = {},
): CommittedDraft<T> {
  // The typed value; null while nothing is typed.
  const [draft, setDraft] = useState<{ value: T } | null>(null)
  // The stored value and owner the draft was typed over. React's adjust-state-on-render
  // pattern drops a stale draft before it is shown for, or committed into, a new value.
  const [basis, setBasis] = useState({ value, owner })
  let held = draft
  if (!equals(basis.value, value) || !Object.is(basis.owner, owner)) {
    setBasis({ value, owner })
    setDraft(null)
    held = null
  }
  const commit = (): boolean => {
    if (held === null) return true
    if (!equals(held.value, value)) {
      const result = onCommit(held.value)
      if (result && !result.ok) return false
    }
    setDraft(null)
    return true
  }
  useOpenEdit(held === null ? null : commit)
  return {
    shown: held === null ? value : held.value,
    editing: held !== null,
    edit: (next) => setDraft({ value: next }),
    commit,
    discard: () => setDraft(null),
  }
}
