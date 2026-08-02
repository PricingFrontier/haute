import { getWorkingBranches } from "../api/client"
import type { GitManagedBranch } from "../api/types"
import { gitErrorMessage } from "../utils/gitError"

interface GitBranchState {
  branches: GitManagedBranch[]
  branchesLoaded: boolean
  branchesLoading: boolean
  branchesError: string | null
}

type SetGitBranchState = (state: Partial<GitBranchState>) => void

let inFlight: Promise<GitManagedBranch[]> | null = null
let queuedRefresh: Promise<GitManagedBranch[]> | null = null

/** Tests that hold a mocked getWorkingBranches open forever leave the
 *  single-flight stuck, starving every later loadBranches() in the same file. */
export function resetGitBranchLoaderForTests(): void {
  inFlight = null
  queuedRefresh = null
}

/**
 * Fetch and publish the shared branch listing.
 *
 * A normal concurrent read shares the active request. A refresh requested by a
 * completed mutation while that read is active queues exactly one follow-up so
 * the older response cannot become the final published state.
 */
export function loadGitBranches(
  set: SetGitBranchState,
  refresh = false,
): Promise<GitManagedBranch[]> {
  if (inFlight) {
    if (!refresh) return inFlight
    if (!queuedRefresh) {
      queuedRefresh = inFlight
        .catch(() => [])
        .then(() => {
          queuedRefresh = null
          return loadGitBranches(set)
        })
    }
    return queuedRefresh
  }

  set({ branchesLoading: true, branchesError: null })
  inFlight = getWorkingBranches()
    .then((result) => {
      set({
        branches: result.branches,
        branchesLoaded: true,
        branchesLoading: false,
        branchesError: null,
      })
      return result.branches
    })
    .catch((error: unknown) => {
      set({
        branchesLoading: false,
        branchesError: gitErrorMessage(error, "Unable to load branches"),
      })
      throw error
    })
    .finally(() => {
      inFlight = null
    })
  return inFlight
}
