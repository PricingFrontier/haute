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
    useGitStore.setState({ status: null, loading: false, modal: null, pendingSave: false })
    vi.clearAllMocks()
  })
  afterEach(() => {
    useGitStore.setState({ status: null, loading: false, modal: null, pendingSave: false })
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

  it("openModal with pendingSave sets both", () => {
    useGitStore.getState().openModal("select", { pendingSave: true })
    expect(useGitStore.getState().modal).toBe("select")
    expect(useGitStore.getState().pendingSave).toBe(true)
  })

  it("openModal without pendingSave preserves the existing flag", () => {
    useGitStore.setState({ pendingSave: true })
    useGitStore.getState().openModal("divergence")
    expect(useGitStore.getState().pendingSave).toBe(true)
  })

  it("closeModal clears a queued save (regression: stale pendingSave)", () => {
    useGitStore.getState().openModal("select", { pendingSave: true })
    useGitStore.getState().closeModal()
    expect(useGitStore.getState().modal).toBeNull()
    expect(useGitStore.getState().pendingSave).toBe(false)
  })

  it("setLastSaveSha updates only when a status exists", () => {
    useGitStore.getState().setLastSaveSha("zzz")
    expect(useGitStore.getState().status).toBeNull() // no-op without status
    useGitStore.setState({ status: READY })
    useGitStore.getState().setLastSaveSha("new999")
    expect(useGitStore.getState().status?.last_save_sha).toBe("new999")
  })
})
