import { useState } from "react"

import useProjectDirty from "./useProjectDirty"

/** The open question's answers, as `GitNavigationConfirm` takes them. */
export interface DirtyNavigationQuestion {
  onCancel: () => void
  onDiscard: () => void
  onSave: () => Promise<void>
}

/**
 * The git surfaces' one guard before a navigation replaces the project's files
 * (specs/frontend-git-ui). `guard(proceed)` runs `proceed` at once while the project is
 * clean, the canvas and the workbench's form alike, and otherwise opens the question:
 * `confirm`, which each caller renders as `GitNavigationConfirm` where it belongs, null
 * while none is open. Cancel drops the action, Discard runs it, and Save first runs it only
 * once `onSave` has answered true; a save that answers false or throws keeps the question
 * open, its failure reported by the save.
 */
export default function useDirtyNavigation(onSave?: () => Promise<boolean>) {
  const dirty = useProjectDirty()
  const [pending, setPending] = useState<(() => void) | null>(null)

  const guard = (proceed: () => void) => {
    if (dirty) setPending(() => proceed)
    else proceed()
  }

  const confirm: DirtyNavigationQuestion | null = pending === null ? null : {
    onCancel: () => setPending(null),
    onDiscard: () => {
      setPending(null)
      pending()
    },
    onSave: async () => {
      if (!(await savedFirst(onSave))) return
      setPending(null)
      pending()
    },
  }

  return { guard, confirm }
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
