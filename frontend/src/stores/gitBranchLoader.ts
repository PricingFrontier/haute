import { getWorkingBranches } from "../api/client"
import type { GitDeletedBranch, GitManagedBranch } from "../api/types"
import { apiErrorMessage } from "../api/errors"
import { answer, newest, requestKind } from "../utils/answers"
import { createSingleFlight } from "./singleFlight"

interface GitBranchState {
  branches: GitManagedBranch[]
  deletedBranches: GitDeletedBranch[]
  branchesLoaded: boolean
  branchesLoading: boolean
  branchesError: string | null
}

type SetGitBranchState = (state: Partial<GitBranchState>) => void

const branchesFlight = createSingleFlight<GitManagedBranch[]>()
/** The branch request that owns the single-flight's slot: each one issued takes a turn, and a
 *  reset or a stall detaching it ends its turn, so its answer publishes nothing. */
const branchesAsked = requestKind()

/** Tests that hold a mocked getWorkingBranches open forever leave the
 *  single-flight stuck, starving every later loadBranches() in the same file.
 *  Called globally from setupTests; safe because every settle path is
 *  answered as the newest branch request, so a detached request can neither
 *  publish state nor clobber a newer request's slot. */
export function resetGitBranchLoaderForTests(): void {
  branchesFlight.reset()
  branchesAsked.bump()
}

/**
 * Fetch and publish the shared branch listing.
 *
 * A normal concurrent read shares the active request. A refresh requested by a
 * completed mutation while that read is active queues exactly one follow-up
 * per active request, so the older response cannot become the final published
 * state. The queue/anchor/reset/stall bookkeeping lives in the shared
 * single-flight (./singleFlight); this module owns only publication.
 */
export function loadGitBranches(
  set: SetGitBranchState,
  refresh = false,
): Promise<GitManagedBranch[]> {
  return branchesFlight.load(
    () => {
      let listed: GitManagedBranch[] = []
      return answer([newest(branchesAsked)], getWorkingBranches(), {
        ok: (result) => {
          listed = result.branches
          set({
            branches: result.branches,
            deletedBranches: result.deleted,
            branchesLoaded: true,
            branchesLoading: false,
            branchesError: null,
          })
        },
        failed: (error) => {
          set({
            branchesLoading: false,
            branchesError: apiErrorMessage(error, "Unable to load branches"),
          })
          throw error
        },
        // Detached by a test reset: it settles for its own awaiters, but
        // publishes nothing over a newer request's state.
        stale: (settled) => {
          if ("error" in settled) throw settled.error
          listed = settled.value.branches
        },
      }).then(() => listed)
    },
    {
      refresh,
      detachedValue: () => [],
      onStart: () => set({ branchesLoading: true, branchesError: null }),
      onStale: (error) => {
        branchesAsked.bump()
        set({
          branchesLoading: false,
          branchesError: apiErrorMessage(error, "Unable to load branches"),
        })
      },
    },
  )
}
