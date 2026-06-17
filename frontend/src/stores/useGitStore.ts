/**
 * Zustand store for the working-branch model (P2/P3).
 *
 * Holds the latest working-branch readiness signal (from GET /api/git/working-branch),
 * which branch + last-save SHA the toolbar indicator displays, and the open/close
 * state of the git modals (working-branch chooser, divergence, milestone commit).
 *
 * The graph-shaped state and dirty tracking live in useGraphStore; chrome/panel
 * state lives in useUIStore. This store owns only the git working-branch concern.
 */
import { create } from "zustand"

import { getWorkingBranch } from "../api/client"
import type { GitWorkingBranchResponse } from "../api/types"

/** Which modal is open. */
export type GitModalMode = "select" | "divergence" | "milestone"

/** The action queued behind a working-branch selection (the save-gate, S5/S13).
 *  "save" → run the pipeline save; "commit" → flush-save then open the milestone
 *  modal. */
export type GitPendingAction = "save" | "commit" | null

interface GitState {
  /** Latest readiness signal, or null before the first load. */
  status: GitWorkingBranchResponse | null
  /** True while a status fetch is in flight (suppresses premature modal logic). */
  loading: boolean
  /** Which modal is open, or null. */
  modal: GitModalMode | null
  /** An action queued behind branch selection (the save-gate). */
  pendingAction: GitPendingAction
  /** Branch whose history the Git panel is PEEKING (not switched to), or null
   *  for the current working branch. Lifted here so the toolbar indicator can
   *  return to current without the panel being open (S38). */
  peekBranch: string | null

  loadStatus: () => Promise<GitWorkingBranchResponse | null>
  openModal: (mode: GitModalMode, opts?: { pendingAction?: GitPendingAction }) => void
  closeModal: () => void
  clearPendingAction: () => void
  /** Peek a branch's history (null returns to the current working branch). */
  setPeekBranch: (branch: string | null) => void
  /** Update just the last-save SHA after a save (cheaper than a full reload). */
  setLastSaveSha: (sha: string | null) => void
}

const useGitStore = create<GitState>()((set, get) => ({
  status: null,
  loading: false,
  modal: null,
  pendingAction: null,
  peekBranch: null,

  loadStatus: async () => {
    set({ loading: true })
    try {
      const status = await getWorkingBranch()
      set({ status, loading: false })
      return status
    } catch {
      // Git status is best-effort chrome — a non-git project or transient
      // error must not break the editor. Leave status null; the indicator
      // simply renders nothing and saves proceed ungated.
      set({ loading: false })
      return null
    }
  },

  openModal: (mode, opts) =>
    set({
      modal: mode,
      pendingAction:
        opts && "pendingAction" in opts ? (opts.pendingAction ?? null) : get().pendingAction,
    }),
  // Closing always clears any queued action: a dismissed modal must not leave a
  // pending action that fires on a later, unrelated modal confirmation.
  closeModal: () => set({ modal: null, pendingAction: null }),
  clearPendingAction: () => set({ pendingAction: null }),
  setPeekBranch: (branch) => set({ peekBranch: branch }),

  setLastSaveSha: (sha) =>
    set((s) => (s.status ? { status: { ...s.status, last_save_sha: sha } } : s)),
}))

export default useGitStore
