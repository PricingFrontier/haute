import { describe, it, expect, vi, beforeEach } from "vitest"

vi.mock("../../api/client", () => ({
  getWorkingBranches: vi.fn(),
}))

import { loadGitBranches, resetGitBranchLoaderForTests } from "../gitBranchLoader"
import { getWorkingBranches } from "../../api/client"
import type { GitManagedBranch } from "../../api/types"

type BranchesPayload = { current: string; branches: GitManagedBranch[] }

function branch(name: string, overrides: Partial<GitManagedBranch> = {}): GitManagedBranch {
  return {
    name, is_current: false, is_archived: false, has_unmerged_saves: false,
    has_uncommitted_changes: false, ...overrides,
  }
}

function deferred() {
  let resolve!: (value: BranchesPayload) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<BranchesPayload>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}

const flush = () => new Promise((tick) => setTimeout(tick, 0))

describe("gitBranchLoader single-flight", () => {
  let published: Array<Record<string, unknown>>
  const set = (state: Record<string, unknown>) => { published.push(state) }

  beforeEach(() => {
    published = []
    // The global setupTests hook already resets the loader; repeated here so
    // these tests stay valid in isolation.
    resetGitBranchLoaderForTests()
    vi.mocked(getWorkingBranches).mockReset()
  })

  it("a refresh requested during a successor request re-queues behind it instead of joining the stale queue", async () => {
    // The two-microtask window: a queued refresh's continuation sits two hops
    // behind the primary request, so a reaction registered directly on the
    // primary can start request B — and a mutation can request a refresh —
    // before the old queue's continuation runs. That refresh must produce a
    // request C after B, not be swallowed by the stale queue.
    const a = deferred()
    const b = deferred()
    const c = deferred()
    vi.mocked(getWorkingBranches)
      .mockReturnValueOnce(a.promise)
      .mockReturnValueOnce(b.promise)
      .mockReturnValueOnce(c.promise)

    const first = loadGitBranches(set)
    const staleQueue = loadGitBranches(set, true)
    let duringWindow: Promise<GitManagedBranch[]> | null = null
    const window = first.then(() => {
      loadGitBranches(set)                    // request B starts in the window
      duringWindow = loadGitBranches(set, true) // mutation refresh during B
    })

    a.resolve({ current: "main", branches: [branch("a")] })
    await window
    await flush()
    expect(getWorkingBranches).toHaveBeenCalledTimes(2) // A, B — C only after B settles

    b.resolve({ current: "main", branches: [branch("b")] })
    await flush()
    expect(getWorkingBranches).toHaveBeenCalledTimes(3) // C: the refresh survived

    const fresh = [branch("c", { is_current: true })]
    c.resolve({ current: "c", branches: fresh })
    await expect(duringWindow!).resolves.toEqual(fresh)
    await expect(staleQueue).resolves.toEqual(fresh) // superseded queue joins the newer work
    expect(published.at(-1)).toMatchObject({ branches: fresh, branchesLoaded: true })
  })

  it("a queued refresh detached by a reset neither spawns a request nor clears the newer generation's queue", async () => {
    const a = deferred()
    const b = deferred()
    const c = deferred()
    vi.mocked(getWorkingBranches)
      .mockReturnValueOnce(a.promise)
      .mockReturnValueOnce(b.promise)
      .mockReturnValueOnce(c.promise)

    loadGitBranches(set)             // A
    loadGitBranches(set, true)       // stale queue behind A
    resetGitBranchLoaderForTests()
    const second = loadGitBranches(set)        // B
    const secondQueue = loadGitBranches(set, true) // fresh queue behind B

    a.resolve({ current: "stale", branches: [branch("stale")] })
    await flush()
    // The detached queue must not have started a request of its own.
    expect(getWorkingBranches).toHaveBeenCalledTimes(2)
    expect(published.some((s) => Array.isArray(s.branches) && (s.branches as GitManagedBranch[]).some((x) => x.name === "stale"))).toBe(false)

    b.resolve({ current: "b", branches: [branch("b")] })
    await second
    await flush()
    // The fresh queue still fires its follow-up: the reset did not eat it.
    expect(getWorkingBranches).toHaveBeenCalledTimes(3)
    const fresh = [branch("c", { is_current: true })]
    c.resolve({ current: "c", branches: fresh })
    await expect(secondQueue).resolves.toEqual(fresh)
    expect(published.at(-1)).toMatchObject({ branches: fresh, branchesLoaded: true })
  })

  it("a rejection settling after a reset rejects its own awaiters without publishing an error over the newer request", async () => {
    const a = deferred()
    const b = deferred()
    vi.mocked(getWorkingBranches)
      .mockReturnValueOnce(a.promise)
      .mockReturnValueOnce(b.promise)

    const stale = loadGitBranches(set)
    resetGitBranchLoaderForTests()
    const fresh = loadGitBranches(set)

    a.reject(new Error("stale failure"))
    await expect(stale).rejects.toThrow("stale failure")
    expect(published.some((s) => typeof s.branchesError === "string")).toBe(false)

    const branches = [branch("b", { is_current: true })]
    b.resolve({ current: "b", branches })
    await expect(fresh).resolves.toEqual(branches)
    expect(published.at(-1)).toMatchObject({ branches, branchesLoaded: true, branchesError: null })
  })
})
