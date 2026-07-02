import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("../../api/client", () => ({
  getWorkingBranch: vi.fn(),
}))

import useGitStore from "../useGitStore"
import { getWorkingBranch } from "../../api/client"
import type { GitWorkingBranchResponse } from "../../api/types"

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
    useGitStore.setState({ status: null, loading: false, modal: null, pendingAction: null })
    vi.clearAllMocks()
  })
  afterEach(() => {
    useGitStore.setState({ status: null, loading: false, modal: null, pendingAction: null })
  })

  it("loadStatus stores the result and returns it", async () => {
    vi.mocked(getWorkingBranch).mockResolvedValue(READY)
    const result = await useGitStore.getState().loadStatus()
    expect(result).toEqual(READY)
    expect(useGitStore.getState().status).toEqual(READY)
    expect(useGitStore.getState().loading).toBe(false)
  })

  it("loadStatus swallows errors and leaves status null", async () => {
    vi.mocked(getWorkingBranch).mockRejectedValue(new Error("not a git repo"))
    const result = await useGitStore.getState().loadStatus()
    expect(result).toBeNull()
    expect(useGitStore.getState().status).toBeNull()
    expect(useGitStore.getState().loading).toBe(false)
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
    useGitStore.getState().requestSelectLatestSave()
    const after = useGitStore.getState()
    expect(after.branchesExpandNonce).toBe(before.branchesExpandNonce + 1)
    expect(after.historyNonce).toBe(before.historyNonce + 1)
    expect(after.commitNonce).toBe(before.commitNonce + 1)
    expect(after.selectLatestSaveNonce).toBe(before.selectLatestSaveNonce + 1)
  })

  it("requestSelectSave records the target sha and bumps its nonce", () => {
    const before = useGitStore.getState().selectSaveNonce
    useGitStore.getState().requestSelectSave("deadbeef")
    expect(useGitStore.getState().selectSaveTarget).toBe("deadbeef")
    expect(useGitStore.getState().selectSaveNonce).toBe(before + 1)
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
