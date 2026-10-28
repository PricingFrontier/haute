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
 * makes undoing it instant and safe).
 */

import {
  gitArchiveBranch,
  gitDeleteBranch,
  restoreBranch,
  setWorkingBranch,
  undeleteBranch,
} from "../api/client"
import useGitStore from "../stores/useGitStore"
import useGraphStore from "../stores/useGraphStore"
import useToastStore from "../stores/useToastStore"
import { apiErrorMessage } from "../api/errors"

/** Re-sync git state everywhere after an inverse ran: status (toolbar,
 *  branch manager) + the history nonce (Git panel refetch). */
async function resyncGit(): Promise<void> {
  await useGitStore.getState().loadStatus({ refresh: true }) // not a read begun before the inverse
  useGitStore.getState().notifyHistoryChanged()
}

/** Run one leg of an entry: toast success, resync; toast + rethrow on
 *  failure so the store puts the entry back for a retry, reading the status
 *  again since a file held open can leave the clone interrupted. */
async function leg(action: () => Promise<unknown>, doneMessage: string): Promise<void> {
  try {
    await action()
  } catch (err) {
    const detail = apiErrorMessage(err, "unknown error")
    useToastStore.getState().addToast("error", `Undo/redo failed: ${detail}`)
    void useGitStore.getState().loadStatus({ refresh: true })
    throw err
  }
  useToastStore.getState().addToast("success", doneMessage)
  await resyncGit()
}

export function recordSwitch(from: string, to: string): void {
  useGraphStore.getState().pushVcEntry({
    label: `switch to ${to}`,
    undo: () => leg(() => setWorkingBranch(from, false), `Switched back to ${from}`),
    redo: () => leg(() => setWorkingBranch(to, false), `Switched to ${to}`),
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
 *  made, and a redo's delete makes a new one, which the next undo restores. */
export function recordDelete(branch: string, trashId: string): void {
  let entry = trashId
  useGraphStore.getState().pushVcEntry({
    label: `delete ${branch}`,
    undo: () => leg(() => undeleteBranch(entry), `Restored ${branch} from trash`),
    redo: () =>
      leg(async () => {
        entry = (await gitDeleteBranch(branch, true)).trash_id
      }, `Deleted ${branch}`),
  })
}
