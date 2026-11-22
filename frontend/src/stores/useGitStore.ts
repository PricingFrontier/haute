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
import type { GitBindStorageResponse, GitDeletedBranch, GitFastForwardResponse, GitForkStorageResponse, GitManagedBranch, GitUpstreamStatus, GitWorkingBranchResponse } from "../api/types"
import { answer, newest, requestKind, under, type Part, type Settled } from "../utils/answers"
import { createSingleFlight } from "./singleFlight"
import { clearingTheWay } from "../utils/clearingTheWay"

const statusFlight = createSingleFlight<GitWorkingBranchResponse | null>()
/** The status request that owns the single-flight's slot: each one issued takes a turn, and a
 *  reset or a stall detaching it ends its turn, so its answer publishes nothing. */
const statusAsked = requestKind()
/** What the store's own writes are asked under: a reset (tests) ends it. */
const storeAsked = requestKind()

/** The git store as it is, as an answer's part: a git write's follow-ups that belong to no
 *  component (a repair's toast, the readiness read again) hold until the store is reset. */
export function gitStorePart(): Part {
  return under(storeAsked)
}

/** A write's answer handed to its caller whatever came of the store meanwhile. */
function settledValue<T>(settled: Settled<T>): T {
  if ("error" in settled) throw settled.error
  return settled.value
}

/** Tests that hold a mocked getWorkingBranch open forever leave the
 *  single-flight stuck, starving every later loadStatus() in the same file. */
export const resetGitStatusRequestForTests = () => {
  statusFlight.reset()
  statusAsked.bump()
}

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

/** The store's data half. Every field added here MUST get an initial value in
 *  `freshGitData` below (TypeScript enforces it for required members), and
 *  `resetForTests` spreads that object — so a new field here cannot silently
 *  leak between shuffled tests. Two conventions keep that guarantee honest:
 *  keep every field REQUIRED (zustand's merge-mode set() never clears absent
 *  keys, so an optional field would dodge both the compiler and the reset),
 *  and file data here, never in GitActions. */
interface GitData {
  /** Latest readiness signal, or null before the first load. */
  status: GitWorkingBranchResponse | null
  /** True while a status fetch is in flight (suppresses premature modal logic). */
  loading: boolean
  statusError: string | null
  branches: GitManagedBranch[]
  /** The trash: deleted branches, newest first, each restorable. */
  deletedBranches: GitDeletedBranch[]
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
  /** A version of the current branch the user has asked to RESTORE (written back as
   *  a new save on the branch), pending the save/discard/confirm prompt. null when
   *  no restore is pending. Distinct from `comparison` (read-only view) — a restore
   *  rewrites the files. */
  restoreTarget: GitComparison | null
  /** The files, relative to the project, that writes reported uncaptured (a capture
   *  skipped for want of an identity, or failed), kept by `reportSaveCapture` until
   *  `captureUncaptured` takes them. */
  uncaptured: string[]
}

interface GitActions {
  /** Load readiness. `refresh: true` guarantees the published state comes from
   *  a request issued no earlier than the call — required after a mutation,
   *  where joining an already-in-flight request would publish pre-mutation
   *  state as final. */
  loadStatus: (options?: { refresh?: boolean }) => Promise<GitWorkingBranchResponse | null>
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
  /** Begin a restore: open the save/discard/confirm prompt for *target*. The
   *  restore runs only once the prompt is confirmed. */
  requestRestore: (target: GitComparison) => void
  /** Dismiss the restore prompt without restoring. */
  closeRestore: () => void
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
  /** Restore every `GitData` field to its initial value (test isolation only). */
  resetForTests: () => void
}

type GitState = GitData & GitActions

/** Compile-time guard closing the one filing mistake the split can't stop
 *  structurally: a DATA field mis-declared in GitActions would type-check,
 *  yet never join the reset. Resolves to a key union that must be never —
 *  adding a non-function member to GitActions turns this into a type error. */
type NonFunctionGitActionKeys = {
  [K in keyof GitActions]: GitActions[K] extends (...args: never[]) => unknown ? never : K
}[keyof GitActions]
type StaticAssert<T extends true> = T
/** Exported only so noUnusedLocals keeps the assertion alive; never import it.
 * @public (knip: a compile-time assertion has no importer) */
export type GitActionsHoldOnlyFunctions = StaticAssert<
  [NonFunctionGitActionKeys] extends [never] ? true : false
>

/** Initial value for every data field. `resetForTests` restores exactly this
 *  shape, so the reset can never drift behind the store's. A factory, not a
 *  shared const: `branches` must be a FRESH array per reset — with a shared
 *  reference, one in-place mutation would corrupt the baseline permanently and
 *  every later "reset" would restore the corruption. */
const freshGitData = (): GitData => ({
  status: null,
  loading: false,
  statusError: null,
  branches: [],
  deletedBranches: [],
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
  restoreTarget: null,
  uncaptured: [],
})

const useGitStore = create<GitState>()((set, get) => ({
  ...freshGitData(),

  loadStatus: (options) =>
    // Single-flight/queue/reset/stall bookkeeping lives in ./singleFlight;
    // this fetcher owns only publication, as the newest status request: after
    // resetGitStatusRequestForTests() (or a stall detach) this request must
    // neither publish state nor null out a NEWER request's slot. Each settle
    // path performs a single set(), checked in the turn it runs.
    statusFlight.load(
      () => {
        let read: GitWorkingBranchResponse | null = null
        return answer([newest(statusAsked)], getWorkingBranch(), {
          ok: (status) => {
            read = status
            // Binding runs in the background, so its dialog is closed by the
            // time a failure lands. Bring it back — the user asked for durable
            // storage and must find out they haven't got it. Never steals
            // focus from another open modal.
            const surfaceBindFailure =
              status?.storage_bind?.state === "failed" && get().modal === null
            set({
              status,
              loading: false,
              statusError: null,
              ...(surfaceBindFailure ? { modal: "storage" as const } : {}),
            })
          },
          failed: async (error, asked) => {
            // Readiness is best-effort editor chrome. Keep the last successful
            // state for gating, but expose this failure for an explicit retry.
            const { apiErrorMessage } = await import("../api/errors")
            asked.apply(() => set({
              loading: false,
              statusError: apiErrorMessage(error, "Unable to check Git status"),
            }))
          },
          // A detached request still answers its own awaiters.
          stale: (settled) => {
            if ("value" in settled) read = settled.value
          },
        }).then(() => read)
      },
      {
        refresh: options?.refresh,
        detachedValue: () => null,
        onStart: () => set({ loading: true, statusError: null }),
        onStale: (error) => {
          statusAsked.bump()
          set({ loading: false, statusError: error.message })
        },
        // loadStatus never rejects — awaiters (mutations, modals) branch on
        // null instead of wrapping in try/catch.
        staleResult: () => null,
      },
    ),

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
  requestRestore: (target) => set({ restoreTarget: target }),
  closeRestore: () => set({ restoreTarget: null }),

  setLastSaveSha: (sha) =>
    set((s) => (s.status ? { status: { ...s.status, last_save_sha: sha } } : s)),

  bindStorage: async (remoteUrl) => {
    let bound!: GitBindStorageResponse
    await answer([gitStorePart()], bindGitStorage(remoteUrl), {
      ok: async (result) => {
        bound = result
        // refresh: joining a status request issued BEFORE the bind would publish
        // pre-bind readiness as the final state.
        await get().loadStatus({ refresh: true })
      },
      stale: (settled) => {
        bound = settledValue(settled)
      },
    })
    return bound
  },
  acknowledgeBind: async () => {
    await answer([gitStorePart()], acknowledgeGitBind(), { ok: (status) => set({ status }) })
  },
  forkStorage: async (sourceUrl, targetUrl) => {
    // No readiness refresh: forking writes only to the volume — this
    // session's own binding is untouched until the user binds the fork.
    return forkGitStorage(sourceUrl, targetUrl)
  },
  checkUpstream: async () => checkGitUpstream(),
  pullUpstream: async () => {
    let pulled!: GitFastForwardResponse
    await answer([gitStorePart()], clearingTheWay((...settled) => pullGitUpstream(...settled)), {
      ok: async (result) => {
        pulled = result
        // refresh: same pre-mutation-join hazard as bindStorage.
        await get().loadStatus({ refresh: true })
      },
      stale: (settled) => {
        pulled = settledValue(settled)
      },
    })
    return pulled
  },
  retrySync: async () => {
    let retried!: GitWorkingBranchResponse | null
    await answer([gitStorePart()], retryGitStorageSync(), {
      ok: (status) => {
        retried = status
        set({ status })
      },
      stale: (settled) => {
        retried = settledValue(settled)
      },
    })
    return retried
  },

  resetForTests: () => {
    storeAsked.bump()
    set(freshGitData())
  },
}))

/** One-call reset of the whole git-store family between tests: the data state
 *  plus both module-level single-flights (the status request above and the
 *  branch loader's). Async because the loader import must stay dynamic, like
 *  `loadBranches`' — a static import would fold the split chunk back into the
 *  main bundle. */
/** The working branch this tab shows, which each of the project save's writes names so that
 *  none lands on a branch the project has moved to since (`branch_changed`). */
export function shownBranch(): string | null {
  return useGitStore.getState().status?.working_branch ?? null
}

/**
 * The project's branch may have moved, whoever moved it: live sync's `git_branch_changed`
 * (another tab, a terminal) or a save refused as `branch_changed`. The readiness is read
 * again as after this tab's own git actions, so the toolbar follows; a project moved off its
 * working branch opens the dialog that asks what to do, unless another dialog is open; and
 * the history and the branch list are read again.
 */
export async function gitBranchMayHaveChanged(): Promise<void> {
  const status = await useGitStore.getState().loadStatus({ refresh: true })
  const store = useGitStore.getState()
  if (status?.state === "divergent" && store.modal === null) store.openModal("divergence")
  store.notifyHistoryChanged()
}

export const resetGitStoreForTests = async (): Promise<void> => {
  resetGitStatusRequestForTests()
  const { resetGitBranchLoaderForTests } = await import("./gitBranchLoader")
  resetGitBranchLoaderForTests()
  useGitStore.getState().resetForTests()
}

export default useGitStore
