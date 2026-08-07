import { getWorkingBranches } from "../api/client"
import type { GitManagedBranch } from "../api/types"
import { gitErrorMessage } from "../utils/gitError"
import { createSingleFlight } from "./singleFlight"

interface GitBranchState {
  branches: GitManagedBranch[]
  branchesLoaded: boolean
  branchesLoading: boolean
  branchesError: string | null
}

type SetGitBranchState = (state: Partial<GitBranchState>) => void

const branchesFlight = createSingleFlight<GitManagedBranch[]>()

/** Tests that hold a mocked getWorkingBranches open forever leave the
 *  single-flight stuck, starving every later loadBranches() in the same file.
 *  Called globally from setupTests; safe because every settle path is
 *  identity-guarded, so a detached request can neither publish state nor
 *  clobber a newer request's slot. */
export function resetGitBranchLoaderForTests(): void {
  branchesFlight.reset()
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
    (isCurrent) =>
      getWorkingBranches()
        .then((result) => {
          // Identity guard: after a test reset this request is detached —
          // resolve for its own awaiters but do not publish state over a
          // newer request's.
          if (isCurrent()) {
            set({
              branches: result.branches,
              branchesLoaded: true,
              branchesLoading: false,
              branchesError: null,
            })
          }
          return result.branches
        })
        .catch((error: unknown) => {
          if (isCurrent()) {
            set({
              branchesLoading: false,
              branchesError: gitErrorMessage(error, "Unable to load branches"),
            })
          }
          throw error
        }),
    {
      refresh,
      detachedValue: () => [],
      onStart: () => set({ branchesLoading: true, branchesError: null }),
      onStale: (error) =>
        set({
          branchesLoading: false,
          branchesError: gitErrorMessage(error, "Unable to load branches"),
        }),
    },
  )
}
