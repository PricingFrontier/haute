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
      const queued: Promise<GitManagedBranch[]> = inFlight
        .catch(() => [])
        .then(() => {
          // Identity guard: a test reset may have detached this continuation;
          // a detached refresh must neither clear a newer queue slot nor spawn
          // a fresh request.
          if (queuedRefresh !== queued) return []
          queuedRefresh = null
          return loadGitBranches(set)
        })
      queuedRefresh = queued
    }
    return queuedRefresh
  }

  set({ branchesLoading: true, branchesError: null })
  const request: Promise<GitManagedBranch[]> = getWorkingBranches()
    .then((result) => {
      // Identity guard: after a test reset this request is detached — resolve
      // for its own awaiters but do not publish state over a newer request's.
      if (inFlight === request) {
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
      if (inFlight === request) {
        set({
          branchesLoading: false,
          branchesError: gitErrorMessage(error, "Unable to load branches"),
        })
      }
      throw error
    })
    .finally(() => {
      if (inFlight === request) inFlight = null
    })
  inFlight = request
  return request
}
