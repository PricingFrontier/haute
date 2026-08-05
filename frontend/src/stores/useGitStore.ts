/**
 * Zustand store for the working-branch model (P2/P3).
 *
 * Holds the latest working-branch readiness signal (from GET /api/git/working-branch),
 * the current branch and last-save identity used by comparison flows, and the
 * open/close state of the git modals (working-branch chooser, divergence,
 * milestone commit). The toolbar indicator consumes only the branch identity.
 *
 * The graph-shaped state and dirty tracking live in useGraphStore; chrome/panel
 * state lives in useUIStore. This store owns only the git working-branch concern.
 */
import { create } from "zustand"

import {
  acknowledgeGitBind,
  bindGitStorage,
  checkGitUpstream,
  forkGitStorage,
  getWorkingBranch,
  pullGitUpstream,
  retryGitStorageSync,
} from "../api/client"
import type { GitBindStorageResponse, GitFastForwardResponse, GitForkStorageResponse, GitManagedBranch, GitUpstreamStatus, GitWorkingBranchResponse } from "../api/types"

let statusInFlight: Promise<GitWorkingBranchResponse | null> | null = null

/** Which modal is open. */
export type GitModalMode =
  | "select"
  | "divergence"
  | "milestone"
  | "storage"
  | "upstream"
  | "identity"

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
  statusError: string | null
  branches: GitManagedBranch[]
  branchesLoaded: boolean
  branchesLoading: boolean
  branchesError: string | null
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
  /** The version under read-only inspection in the side-by-side comparison view,
   *  or null when not comparing (S11). Drives the dual-canvas overlay. */
  comparison: GitComparison | null
  /** A version the user has asked to MOVE to (a real detached checkout), pending
   *  the pre-move save/discard/confirm prompt (P6 §3.4). null when no move is
   *  pending. Distinct from `comparison` (read-only view) — a move mutates the
   *  working tree. */
  moveTarget: GitComparison | null

  loadStatus: () => Promise<GitWorkingBranchResponse | null>
  loadBranches: (options?: { refresh?: boolean }) => Promise<GitManagedBranch[]>
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
  /** Open the read-only comparison view on a commit (S11). */
  openComparison: (comparison: GitComparison) => void
  /** Close the comparison view, returning to the live editor (S11). */
  closeComparison: () => void
  /** Begin a move: open the pre-move save/discard/confirm prompt for *target*
   *  (P6 §3.4). The actual checkout runs only once the prompt is confirmed. */
  requestMove: (target: GitComparison) => void
  /** Dismiss the pre-move prompt without moving. */
  closeMove: () => void
  /** Update just the last-save SHA after a save (cheaper than a full reload). */
  setLastSaveSha: (sha: string | null) => void
  /** Bind the state volume to a remote for durable storage, then refresh readiness. */
  bindStorage: (remoteUrl: string) => Promise<GitBindStorageResponse>
  /** Fork a held uc:// location's published state into an empty location. */
  forkStorage: (sourceUrl: string, targetUrl: string) => Promise<GitForkStorageResponse>
  /** Clear a finished bind result after the dialog has reported it. */
  acknowledgeBind: () => Promise<void>
  /** Measure this fork against its parent. On demand only — the server
   *  downloads the parent's whole stored bundle to answer. */
  checkUpstream: () => Promise<GitUpstreamStatus>
  /** Catch this fork up to its parent, then refresh readiness (the catch-up
   *  publishes to the fork's own location, so the sync state moves). */
  pullUpstream: () => Promise<GitFastForwardResponse>
  /** Retry a failed sync to the bound remote, refreshing readiness afterwards. */
  retrySync: () => Promise<GitWorkingBranchResponse | null>
}

const useGitStore = create<GitState>()((set, get) => ({
  status: null,
  loading: false,
  statusError: null,
  branches: [],
  branchesLoaded: false,
  branchesLoading: false,
  branchesError: null,
  modal: null,
  pendingAction: null,
  peekBranch: null,
  branchesExpandNonce: 0,
  historyNonce: 0,
  commitNonce: 0,
  comparison: null,
  moveTarget: null,

  loadStatus: () => {
    if (statusInFlight) return statusInFlight
    set({ loading: true, statusError: null })
    statusInFlight = getWorkingBranch()
      .then((status) => {
        set({ status, loading: false, statusError: null })
        // Binding runs in the background, so its dialog is closed by the time
        // a failure lands. Bring it back — the user asked for durable storage
        // and must find out they haven't got it. Never steals focus from
        // another open modal.
        if (status?.storage_bind?.state === "failed" && get().modal === null) {
          set({ modal: "storage" })
        }
        return status
      })
      .catch(async (error: unknown) => {
        // Readiness is best-effort editor chrome. Keep the last successful
        // state for gating, but expose this failure for an explicit retry.
        const { gitErrorMessage } = await import("../utils/gitError")
        set({
          loading: false,
          statusError: gitErrorMessage(error, "Unable to check Git status"),
        })
        return null
      })
      .finally(() => {
        statusInFlight = null
      })
    return statusInFlight
  },

  loadBranches: (options) =>
    import("./gitBranchLoader").then(({ loadGitBranches }) =>
      loadGitBranches(set, options?.refresh)),

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
  openComparison: (comparison) => set({ comparison }),
  closeComparison: () => set({ comparison: null }),
  requestMove: (target) => set({ moveTarget: target }),
  closeMove: () => set({ moveTarget: null }),

  setLastSaveSha: (sha) =>
    set((s) => (s.status ? { status: { ...s.status, last_save_sha: sha } } : s)),

  bindStorage: async (remoteUrl) => {
    const result = await bindGitStorage(remoteUrl)
    await get().loadStatus()
    return result
  },
  acknowledgeBind: async () => {
    const status = await acknowledgeGitBind()
    set({ status })
  },
  forkStorage: async (sourceUrl, targetUrl) => {
    // No readiness refresh: forking writes only to the volume — this
    // session's own binding is untouched until the user binds the fork.
    return forkGitStorage(sourceUrl, targetUrl)
  },
  checkUpstream: async () => checkGitUpstream(),
  pullUpstream: async () => {
    const result = await pullGitUpstream()
    await get().loadStatus()
    return result
  },
  retrySync: async () => {
    const status = await retryGitStorageSync()
    set({ status })
    return status
  },
}))

export default useGitStore
