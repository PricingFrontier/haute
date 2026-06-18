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

/** A version being inspected read-only in the side-by-side comparison view (S11).
 *  `sha` is the commit materialised on the LEFT (historical) canvas; `label` is a
 *  human string for the floating chip (version label, message, or short sha). */
export interface GitComparison {
  sha: string
  label: string
}

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
  /** Bumped to ask the branch manager to expand its (possibly-collapsed)
   *  section — e.g. when the toolbar branch name is clicked (S38). */
  branchesExpandNonce: number
  /** Bumped after a plain SAVE so the Git panel re-fetches its history without a
   *  manual refresh. A save must NOT move the selection (S38). */
  historyNonce: number
  /** Bumped after a milestone COMMIT. Like historyNonce it triggers a re-fetch,
   *  but a commit is a deliberate action — the panel selects the new milestone
   *  it just recorded (S38). Kept separate from historyNonce precisely so the
   *  two can differ in selection behaviour. */
  commitNonce: number
  /** Bumped when the toolbar commit-SHA indicator is clicked: open the panel on
   *  the current branch and SELECT the latest save (the ledger-tip commit the
   *  indicator shows), expanding its milestone if it's folded (S38). */
  selectLatestSaveNonce: number
  /** The version under read-only inspection in the side-by-side comparison view,
   *  or null when not comparing (S11). Drives the dual-canvas overlay + the
   *  context-aware toolbar indicator (which selects the COMPARED version, not the
   *  latest save, while a comparison is open). */
  comparison: GitComparison | null

  loadStatus: () => Promise<GitWorkingBranchResponse | null>
  openModal: (mode: GitModalMode, opts?: { pendingAction?: GitPendingAction }) => void
  closeModal: () => void
  clearPendingAction: () => void
  /** Peek a branch's history (null returns to the current working branch). */
  setPeekBranch: (branch: string | null) => void
  /** Ask the branch manager to expand its section. */
  requestExpandBranches: () => void
  /** Signal that the version history changed after a SAVE (refresh, no select). */
  notifyHistoryChanged: () => void
  /** Signal that a milestone was COMMITTED (refresh + select the new milestone). */
  notifyMilestoneCommitted: () => void
  /** Ask the panel to select the latest save (toolbar commit-SHA click). */
  requestSelectLatestSave: () => void
  /** Open the read-only comparison view on a commit (S11). */
  openComparison: (comparison: GitComparison) => void
  /** Close the comparison view, returning to the live editor (S11). */
  closeComparison: () => void
  /** Update just the last-save SHA after a save (cheaper than a full reload). */
  setLastSaveSha: (sha: string | null) => void
}

const useGitStore = create<GitState>()((set, get) => ({
  status: null,
  loading: false,
  modal: null,
  pendingAction: null,
  peekBranch: null,
  branchesExpandNonce: 0,
  historyNonce: 0,
  commitNonce: 0,
  selectLatestSaveNonce: 0,
  comparison: null,

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
  requestExpandBranches: () => set((s) => ({ branchesExpandNonce: s.branchesExpandNonce + 1 })),
  notifyHistoryChanged: () => set((s) => ({ historyNonce: s.historyNonce + 1 })),
  notifyMilestoneCommitted: () => set((s) => ({ commitNonce: s.commitNonce + 1 })),
  requestSelectLatestSave: () =>
    set((s) => ({ selectLatestSaveNonce: s.selectLatestSaveNonce + 1 })),
  openComparison: (comparison) => set({ comparison }),
  closeComparison: () => set({ comparison: null }),

  setLastSaveSha: (sha) =>
    set((s) => (s.status ? { status: { ...s.status, last_save_sha: sha } } : s)),
}))

export default useGitStore
