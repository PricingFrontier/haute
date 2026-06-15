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
})
