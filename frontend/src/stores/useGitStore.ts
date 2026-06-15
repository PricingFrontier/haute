/**
 * Zustand store for the working-branch model (P2).
 *
 * Holds the latest working-branch readiness signal (from GET /api/git/working-branch),
 * which branch + last-save SHA the toolbar indicator displays, and the open/close
 * state of the working-branch and divergence modals.
 *
 * The graph-shaped state and dirty tracking live in useGraphStore; chrome/panel
 * state lives in useUIStore. This store owns only the git working-branch concern.
 */
import { create } from "zustand"

import { getWorkingBranch } from "../api/client"
import type { GitWorkingBranchResponse } from "../api/types"

/** Which modal the startup flow / save-gate wants shown. */
export type GitModalMode = "select" | "divergence"

interface GitState {
  /** Latest readiness signal, or null before the first load. */
  status: GitWorkingBranchResponse | null
  /** True while a status fetch is in flight (suppresses premature modal logic). */
  loading: boolean
  /** Which modal is open, or null. */
  modal: GitModalMode | null
  /** A save is queued behind branch selection (the save-gate, S5/S13). */
  pendingSave: boolean

  loadStatus: () => Promise<GitWorkingBranchResponse | null>
  openModal: (mode: GitModalMode, opts?: { pendingSave?: boolean }) => void
  closeModal: () => void
  clearPendingSave: () => void
  /** Update just the last-save SHA after a save (cheaper than a full reload). */
  setLastSaveSha: (sha: string | null) => void
}

const useGitStore = create<GitState>()((set, get) => ({
  status: null,
  loading: false,
  modal: null,
  pendingSave: false,

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
    set({ modal: mode, pendingSave: opts?.pendingSave ?? get().pendingSave }),
  // Closing always clears any queued save: a dismissed modal must not leave a
  // pendingSave that fires on a later, unrelated modal confirmation.
  closeModal: () => set({ modal: null, pendingSave: false }),
  clearPendingSave: () => set({ pendingSave: false }),

  setLastSaveSha: (sha) =>
    set((s) => (s.status ? { status: { ...s.status, last_save_sha: sha } } : s)),
}))

export default useGitStore
