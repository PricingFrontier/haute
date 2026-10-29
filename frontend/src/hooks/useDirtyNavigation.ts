import { useEffect, useRef, useState } from "react"

import useQuestionStore from "../stores/useQuestionStore"
import useProjectDirty, { isProjectDirty } from "./useProjectDirty"

/** The question a branch change asks, after "You have unsaved changes." */
export const CHANGING_BRANCHES = "Save them before changing branches?"

/** The open question, as `GitNavigationConfirm` takes it: what it asks, and its answers. */
export interface DirtyNavigationQuestion {
  question: string
  onCancel: () => void
  onDiscard: () => void
  onSave: () => Promise<void>
}

/**
 * The git surfaces' one guard before a navigation replaces the project's files
 * (specs/frontend-git-ui). `guard(proceed, question)` runs `proceed` at once while the
 * project is clean, the canvas and the workbench's form alike, and otherwise opens the
 * question, `question` naming what the action changes: `confirm`, which each caller renders
 * as `GitNavigationConfirm` where it belongs, null while none is open. Cancel drops the
 * action, Discard runs it, and Save first runs it only once `onSave` has answered true; a
 * save that answers false or throws keeps the question open, its failure reported by the
 * save. A Cancel, another question or the surface going away while the save runs ends the
 * question, and the save's answer then runs nothing.
 */
export default function useDirtyNavigation(onSave?: () => Promise<boolean>) {
  const dirty = useProjectDirty()
  const [pending, setPending] = useState<PendingNavigation | null>(null)
  // The open question, as Save first reads it once its save has answered.
  const asking = useRef<PendingNavigation | null>(null)
  useEffect(() => () => { asking.current = null }, [])

  const ask = (next: PendingNavigation | null) => {
    asking.current = next
    setPending(next)
  }

  const guard = (proceed: () => void, question: string = CHANGING_BRANCHES) => {
    if (dirty) ask({ proceed, question })
    else proceed()
  }

  const confirm: DirtyNavigationQuestion | null = pending === null ? null : {
    question: pending.question,
    onCancel: () => ask(null),
    onDiscard: () => {
      ask(null)
      pending.proceed()
    },
    onSave: async () => {
      if (!(await savedFirst(onSave)) || asking.current !== pending) return
      ask(null)
      pending.proceed()
    },
  }

  return { guard, confirm }
}

interface PendingNavigation {
  proceed: () => void
  question: string
}

/**
 * The same guard for an action no component runs, a toolbar undo's or redo's switch,
 * asked through the page's question (`useQuestionStore`) since the git panel may be
 * closed: Cancel, Save first (when there is a save to run) and Discard. True to go ahead,
 * with the project clean, its edits to be discarded, or saved; false for Cancel. A save
 * that answers false or throws asks again.
 */
export async function leaveUnsavedEdits(title: string, onSave?: () => Promise<boolean>): Promise<boolean> {
  if (!isProjectDirty()) return true
  const answer = await useQuestionStore.getState().ask({
    title,
    text: `You have unsaved changes. ${CHANGING_BRANCHES}`,
    confirm: "Discard",
    danger: true,
    ...(onSave === undefined ? {} : { other: "Save first" }),
  })
  if (answer === null) return false
  if (answer.choice === "confirm") return true
  return (await savedFirst(onSave)) || leaveUnsavedEdits(title, onSave)
}

/** Whether Save first saved: false for a save that answered false, threw, or was not given. */
async function savedFirst(onSave?: () => Promise<boolean>): Promise<boolean> {
  try {
    return (await onSave?.()) === true
  } catch {
    // A save that throws keeps the question open, as one that answers false does.
    return false
  }
}
