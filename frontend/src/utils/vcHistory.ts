/**
 * Undoable version-control operations (feedback round 2): after a branch
 * switch / archive / restore / delete completes, record a VC entry on the
 * graph store's history stacks so toolbar Undo/Redo replays its inverse.
 *
 * Closures here touch STORES only (never component state), so an entry keeps
 * working long after the panel that pushed it unmounted. Each inverse is a
 * cheap in-app operation: switch is a working-pair checkout the websocket
 * sync lands on the canvas; archive/restore/delete/undelete are pure
 * ref/state flips (delete is trash-preserving server-side, which is what
 * makes undoing it instant and safe). A switch's legs replace the canvas and the
 * workbench's form as the switch did, so they ask about unsaved edits first.
 */

import {
  type GitBranchSeen,
  gitArchiveBranch,
  gitDeleteBranch,
  restoreBranch,
  setWorkingBranch,
  undeleteBranch,
} from "../api/client"
import { leaveUnsavedEdits } from "../hooks/useDirtyNavigation"
import { chosenOverParts, discardCanvasEdits, discardFormEdits } from "../hooks/useProjectDirty"
import useGitStore from "../stores/useGitStore"
import useGraphStore from "../stores/useGraphStore"
import useToastStore from "../stores/useToastStore"
import { apiErrorMessage } from "../api/errors"
import { documentPart } from "../stores/useDocumentStatusStore"
import { answer } from "./answers"

/** Re-sync git state everywhere after an inverse ran: status (toolbar,
 *  branch manager) + the history nonce (Git panel refetch). The status is the git store's,
 *  read again whatever came of the editor meanwhile. */
async function resyncGit(): Promise<void> {
  const historyChanged = () => useGitStore.getState().notifyHistoryChanged()
  // Not a read begun before the inverse.
  await answer([documentPart()], useGitStore.getState().loadStatus({ refresh: true }), {
    ok: historyChanged,
    stale: historyChanged,
  })
}

/** Run one leg of an entry: toast success, resync; toast + rethrow on
 *  failure so the store puts the entry back for a retry, reading the status
 *  again since a file held open can leave the clone interrupted. An entry is the
 *  history's, run after the panel that pushed it may be gone: what it did is said either
 *  way. */
async function leg(action: () => Promise<unknown>, doneMessage: string): Promise<void> {
  const done = () => {
    useToastStore.getState().addToast("success", doneMessage)
    return resyncGit()
  }
  const failed = (err: unknown): never => {
    const detail = apiErrorMessage(err, "unknown error")
    useToastStore.getState().addToast("error", `Undo/redo failed: ${detail}`)
    void useGitStore.getState().loadStatus({ refresh: true })
    throw err
  }
  await answer([documentPart()], action, {
    ok: done,
    failed,
    stale: (settled) => ("value" in settled ? done() : failed(settled.error)),
  })
}

/** A leg the analyst cancelled at the unsaved-edits question: nothing ran or failed. */
class SwitchCancelled extends Error {
  constructor() {
    super("The switch was cancelled at the unsaved-edits question.")
  }
}

/**
 * One leg of a switch: unsaved edits are asked about first, `onSave` the project's save
 * for Save first. Cancel rejects before anything runs or is reported, so the store puts
 * the entry back, as a document replaced while it was asked does; otherwise the checkout
 * runs as a leg, and the edits chosen over it are dropped once it succeeds (nothing, when
 * Save first left the project clean), unless an edit was made while it ran.
 */
async function switchLeg(
  branch: string,
  question: string,
  doneMessage: string,
  onSave?: () => Promise<boolean>,
): Promise<void> {
  const cancelled = (): never => {
    throw new SwitchCancelled()
  }
  await answer([documentPart()], leaveUnsavedEdits(question, onSave), {
    ok: async (leave) => {
      if (!leave) cancelled()
      await leg(() => answer(chosenOverParts(), setWorkingBranch(branch, false), {
        ok: async (_switched, asked) => {
          discardCanvasEdits()
          await asked.answer(discardFormEdits(), {})
        },
        stale: (settled) => {
          if ("error" in settled) throw settled.error
        },
      }), doneMessage)
    },
    stale: cancelled,
  })
}

/** `onSave` is the project's save, which a leg's Save first runs. */
export function recordSwitch(from: string, to: string, onSave?: () => Promise<boolean>): void {
  useGraphStore.getState().pushVcEntry({
    label: `switch to ${to}`,
    undo: () => switchLeg(from, `Switch back to ${from}?`, `Switched back to ${from}`, onSave),
    redo: () => switchLeg(to, `Switch to ${to}?`, `Switched to ${to}`, onSave),
  })
}

/** Archive renames the branch (archive/ prefix) — the inverse legs must use
 *  the right name on each side: restore targets `archivedAs`, a re-archive
 *  targets the live `branch` name. */
export function recordArchive(branch: string, archivedAs: string): void {
  useGraphStore.getState().pushVcEntry({
    label: `archive ${branch}`,
    undo: () => leg(() => restoreBranch(archivedAs), `Restored ${branch}`),
    redo: () => leg(() => gitArchiveBranch(branch), `Archived ${branch}`),
  })
}

/** Mirror of recordArchive: re-archiving targets the restored live name,
 *  re-restoring the archived one. */
export function recordRestore(archivedName: string, restoredAs: string): void {
  useGraphStore.getState().pushVcEntry({
    label: `restore ${restoredAs}`,
    undo: () => leg(() => gitArchiveBranch(restoredAs), `Archived ${restoredAs}`),
    redo: () => leg(() => restoreBranch(archivedName), `Restored ${restoredAs}`),
  })
}

/** Each delete is its own trash entry: undo restores the entry the delete
 *  made, and a redo's delete makes a new one, which the next undo restores.
 *  An undo brings the branch back exactly, so the redo names it as the delete
 *  saw it, and is refused if the branch moved in between. */
export function recordDelete(branch: string, trashId: string, seen: GitBranchSeen): void {
  let entry = trashId
  useGraphStore.getState().pushVcEntry({
    label: `delete ${branch}`,
    undo: () => leg(() => undeleteBranch(entry), `Restored ${branch} from trash`),
    redo: () =>
      leg(async () => {
        entry = (await gitDeleteBranch(branch, seen, true)).trash_id
      }, `Deleted ${branch}`),
  })
}
