import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("../../api/client", () => ({
  getWorkingBranch: vi.fn(),
  getWorkingBranches: vi.fn(),
  bindGitStorage: vi.fn(),
  acknowledgeGitBind: vi.fn(),
  forkGitStorage: vi.fn(),
  checkGitUpstream: vi.fn(),
  pullGitUpstream: vi.fn(),
  retryGitStorageSync: vi.fn(),
}))

import useGitStore from "../useGitStore"
import {
  acknowledgeGitBind,
  bindGitStorage,
  checkGitUpstream,
  forkGitStorage,
  getWorkingBranch,
  getWorkingBranches,
  pullGitUpstream,
  retryGitStorageSync,
} from "../../api/client"
import type { GitManagedBranch, GitWorkingBranchResponse } from "../../api/types"

const READY: GitWorkingBranchResponse = {
  working_branch: "dev",
  state: "ready",
  errors: [],
  current_branch: "dev-save",
  last_save_sha: "abc1234",
  eligible_branches: ["dev"],
  identity_set: true,
  user_name: "U",
  user_email: "u@x.y",
}

describe("useGitStore", () => {
  beforeEach(() => {
    useGitStore.setState({
      status: null, loading: false, statusError: null, branches: [], branchesLoaded: false,
      branchesLoading: false, branchesError: null, modal: null, pendingAction: null,
    })
    vi.clearAllMocks()
  })
  afterEach(() => {
    useGitStore.setState({
      status: null, loading: false, statusError: null, branches: [], branchesLoaded: false,
      branchesLoading: false, branchesError: null, modal: null, pendingAction: null,
    })
  })

  it("loadStatus stores the result and returns it", async () => {
    vi.mocked(getWorkingBranch).mockResolvedValue(READY)
    const result = await useGitStore.getState().loadStatus()
    expect(result).toEqual(READY)
    expect(useGitStore.getState().status).toEqual(READY)
    expect(useGitStore.getState().loading).toBe(false)
  })

  it("loadStatus records an error without discarding the previous good status", async () => {
    useGitStore.setState({ status: READY })
    vi.mocked(getWorkingBranch).mockRejectedValue(new Error("not a git repo"))
    const result = await useGitStore.getState().loadStatus()
    expect(result).toBeNull()
    expect(useGitStore.getState().status).toEqual(READY)
    expect(useGitStore.getState().statusError).toBe("not a git repo")
    expect(useGitStore.getState().loading).toBe(false)
  })

  it("de-duplicates concurrent status loads", async () => {
    let resolve!: (value: GitWorkingBranchResponse) => void
    vi.mocked(getWorkingBranch).mockReturnValue(new Promise((done) => { resolve = done }))
    const first = useGitStore.getState().loadStatus()
    const second = useGitStore.getState().loadStatus()
    expect(getWorkingBranch).toHaveBeenCalledOnce()
    resolve(READY)
    await expect(Promise.all([first, second])).resolves.toEqual([READY, READY])
  })

  it("de-duplicates concurrent branch loads and publishes the shared listing", async () => {
    const branches: GitManagedBranch[] = [{
      name: "dev", is_current: true, is_archived: false, has_unmerged_saves: false,
      has_uncommitted_changes: false,
    }]
    let resolve!: (value: { current: string; branches: GitManagedBranch[] }) => void
    vi.mocked(getWorkingBranches).mockReturnValue(new Promise((done) => { resolve = done }))
    const first = useGitStore.getState().loadBranches()
    const second = useGitStore.getState().loadBranches()
    await vi.waitFor(() => expect(getWorkingBranches).toHaveBeenCalledOnce())
    resolve({ current: "dev", branches })
    await expect(Promise.all([first, second])).resolves.toEqual([branches, branches])
    expect(useGitStore.getState()).toMatchObject({ branches, branchesLoaded: true, branchesLoading: false })
  })

  it("openModal with a pendingAction sets both", () => {
    useGitStore.getState().openModal("select", { pendingAction: "commit" })
    expect(useGitStore.getState().modal).toBe("select")
    expect(useGitStore.getState().pendingAction).toBe("commit")
  })

  it("openModal without a pendingAction opt preserves the existing one", () => {
    useGitStore.setState({ pendingAction: "save" })
    useGitStore.getState().openModal("divergence")
    expect(useGitStore.getState().pendingAction).toBe("save")
  })

  it("closeModal clears a queued action (regression: stale pendingAction)", () => {
    useGitStore.getState().openModal("select", { pendingAction: "save" })
    useGitStore.getState().closeModal()
    expect(useGitStore.getState().modal).toBeNull()
    expect(useGitStore.getState().pendingAction).toBeNull()
  })

  it("setLastSaveSha updates only when a status exists", () => {
    useGitStore.getState().setLastSaveSha("zzz")
    expect(useGitStore.getState().status).toBeNull() // no-op without status
    useGitStore.setState({ status: READY })
    useGitStore.getState().setLastSaveSha("new999")
    expect(useGitStore.getState().status?.last_save_sha).toBe("new999")
  })

  it("clearPendingAction drops a queued action without touching the modal", () => {
    useGitStore.setState({ modal: "divergence", pendingAction: "commit" })
    useGitStore.getState().clearPendingAction()
    expect(useGitStore.getState().pendingAction).toBeNull()
    expect(useGitStore.getState().modal).toBe("divergence")
  })

  it("setPeekBranch sets and clears the peeked branch", () => {
    useGitStore.getState().setPeekBranch("pricing/u/feature")
    expect(useGitStore.getState().peekBranch).toBe("pricing/u/feature")
    useGitStore.getState().setPeekBranch(null)
    expect(useGitStore.getState().peekBranch).toBeNull()
  })

  it("the refresh/select nonces each bump by one when their action fires", () => {
    const before = useGitStore.getState()
    useGitStore.getState().requestExpandBranches()
    useGitStore.getState().notifyHistoryChanged()
    useGitStore.getState().notifyMilestoneCommitted()
    const after = useGitStore.getState()
    expect(after.branchesExpandNonce).toBe(before.branchesExpandNonce + 1)
    expect(after.historyNonce).toBe(before.historyNonce + 1)
    expect(after.commitNonce).toBe(before.commitNonce + 1)
  })

  it("openComparison/closeComparison toggle the read-only comparison view", () => {
    const comparison = { sha: "abc1234", label: "v2.0" }
    useGitStore.getState().openComparison(comparison)
    expect(useGitStore.getState().comparison).toEqual(comparison)
    useGitStore.getState().closeComparison()
    expect(useGitStore.getState().comparison).toBeNull()
  })

  it("requestMove/closeMove toggle the pending move target", () => {
    const target = { sha: "abc1234", label: "v2.0" }
    useGitStore.getState().requestMove(target)
    expect(useGitStore.getState().moveTarget).toEqual(target)
    useGitStore.getState().closeMove()
    expect(useGitStore.getState().moveTarget).toBeNull()
  })
})

describe("useGitStore durable-storage actions", () => {
  const BOUND: GitWorkingBranchResponse = {
    ...READY,
    storage: "bound",
    storage_remote: "uc://cat.sch.vol/projects/demo",
    sync: { state: "synced", pending: 0, failure: null, message: null },
  }

  beforeEach(() => {
    useGitStore.setState({ status: null, loading: false, statusError: null })
    vi.clearAllMocks()
  })

  it("binds, then refreshes readiness so the chip reflects the new binding", async () => {
    // Bind is asynchronous: the route accepts and the real outcome arrives
    // later through the polled bind status, not from this call.
    const bindResult = {
      outcome: "pending" as const,
      remote_url: "uc://cat.sch.vol/projects/demo",
      message: "Saving this project to storage — you can keep working.",
    }
    vi.mocked(bindGitStorage).mockResolvedValue(bindResult)
    vi.mocked(getWorkingBranch).mockResolvedValue(BOUND)

    const returned = await useGitStore.getState().bindStorage("uc://cat.sch.vol/projects/demo")

    expect(returned).toEqual(bindResult)
    expect(bindGitStorage).toHaveBeenCalledWith("uc://cat.sch.vol/projects/demo")
    expect(useGitStore.getState().status?.storage).toBe("bound")
  })

  it("acknowledging a finished bind stores the readiness the server returns", async () => {
    vi.mocked(acknowledgeGitBind).mockResolvedValue(BOUND)

    await useGitStore.getState().acknowledgeBind()

    expect(useGitStore.getState().status).toEqual(BOUND)
  })

  it("forking does NOT refresh readiness — this session's binding is untouched", async () => {
    const fork = { remote_url: "uc://cat.sch.vol/projects/fork", forked_from: "uc://cat.sch.vol/projects/demo" }
    vi.mocked(forkGitStorage).mockResolvedValue(fork as never)

    const returned = await useGitStore.getState().forkStorage(
      "uc://cat.sch.vol/projects/demo",
      "uc://cat.sch.vol/projects/fork",
    )

    expect(returned).toEqual(fork)
    expect(getWorkingBranch).not.toHaveBeenCalled()
  })

  it("checking upstream returns the measurement without touching stored state", async () => {
    const upstream = { ahead: 0, behind: 2, can_fast_forward: true }
    vi.mocked(checkGitUpstream).mockResolvedValue(upstream as never)

    expect(await useGitStore.getState().checkUpstream()).toEqual(upstream)
    expect(useGitStore.getState().status).toBeNull()
  })

  it("pulling upstream refreshes readiness, since the ledger moved", async () => {
    vi.mocked(pullGitUpstream).mockResolvedValue({ working_branch: "dev" } as never)
    vi.mocked(getWorkingBranch).mockResolvedValue(BOUND)

    await useGitStore.getState().pullUpstream()

    expect(getWorkingBranch).toHaveBeenCalled()
    expect(useGitStore.getState().status?.storage).toBe("bound")
  })

  it("retrying a failed sync stores the refreshed readiness it returns", async () => {
    const pending: GitWorkingBranchResponse = {
      ...BOUND,
      sync: { state: "pending", pending: 2, failure: null, message: null },
    }
    vi.mocked(retryGitStorageSync).mockResolvedValue(pending)

    const returned = await useGitStore.getState().retrySync()

    expect(returned).toEqual(pending)
    expect(useGitStore.getState().status?.sync?.pending).toBe(2)
  })
})

describe("useGitStore reopens the storage dialog when a background bind fails", () => {
  const failedBind = (): GitWorkingBranchResponse => ({
    ...READY,
    storage: "unbound",
    storage_bind: {
      state: "failed",
      outcome: null,
      message: "That location is in use by another app.",
      claim: null,
      remote_url: "uc://cat.sch.vol/projects/demo",
    },
  })

  beforeEach(() => {
    useGitStore.setState({ status: null, modal: null, loading: false, statusError: null })
    vi.clearAllMocks()
  })

  it("brings the dialog back, since the user asked for durable storage and has not got it", async () => {
    vi.mocked(getWorkingBranch).mockResolvedValue(failedBind())

    await useGitStore.getState().loadStatus()

    expect(useGitStore.getState().modal).toBe("storage")
  })

  it("never steals focus from a modal the user already has open", async () => {
    useGitStore.setState({ modal: "divergence" })
    vi.mocked(getWorkingBranch).mockResolvedValue(failedBind())

    await useGitStore.getState().loadStatus()

    expect(useGitStore.getState().modal).toBe("divergence")
  })

  it("leaves the UI alone when the bind did not fail", async () => {
    vi.mocked(getWorkingBranch).mockResolvedValue({
      ...READY,
      storage: "bound",
      storage_bind: {
        state: "succeeded",
        outcome: "adopted",
        message: null,
        claim: null,
        remote_url: "uc://cat.sch.vol/projects/demo",
      },
    })

    await useGitStore.getState().loadStatus()

    expect(useGitStore.getState().modal).toBeNull()
  })

  it("leaves the UI alone when readiness carries no bind at all", async () => {
    vi.mocked(getWorkingBranch).mockResolvedValue(READY)

    await useGitStore.getState().loadStatus()

    expect(useGitStore.getState().modal).toBeNull()
  })
})
