import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("../../api/client", () => ({
  setWorkingBranch: vi.fn(() =>
    Promise.resolve({ working_branch: "dev", state: "ready", last_save_sha: "sha" }),
  ),
  getWorkingBranch: vi.fn(() =>
    Promise.resolve({
      working_branch: "dev",
      state: "ready",
      errors: [],
      current_branch: "dev-save",
      last_save_sha: "sha",
      eligible_branches: ["dev"],
      identity_set: true,
      user_name: "U",
      user_email: "u@x.y",
    }),
  ),
}))

import DivergenceModal from "../DivergenceModal"
import useGitStore from "../../stores/useGitStore"
import useUIStore from "../../stores/useUIStore"
import { setWorkingBranch } from "../../api/client"
import type { GitWorkingBranchResponse } from "../../api/types"

function divergent(overrides: Partial<GitWorkingBranchResponse> = {}): GitWorkingBranchResponse {
  return {
    working_branch: "dev",
    state: "divergent",
    errors: [],
    current_branch: "main",
    last_save_sha: null,
    eligible_branches: ["dev", "feature-x"],
    identity_set: true,
    user_name: "U",
    user_email: "u@x.y",
    ...overrides,
  }
}

describe("DivergenceModal", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useGitStore.setState({ status: divergent(), modal: "divergence", pendingAction: null, loading: false })
    useUIStore.setState({ gitOpen: false })
  })
  afterEach(cleanup)

  it("names the recorded and current branches", () => {
    render(<DivergenceModal onConfirmed={vi.fn()} onClose={vi.fn()} />)
    const dialog = screen.getByTestId("divergence-modal")
    expect(dialog).toHaveTextContent("dev")
    expect(dialog).toHaveTextContent("main")
  })

  it("go home returns to the recorded working branch", async () => {
    const onConfirmed = vi.fn()
    render(<DivergenceModal onConfirmed={onConfirmed} onClose={vi.fn()} />)
    // "home" is the default selection
    fireEvent.click(screen.getByTestId("divergence-confirm"))
    await waitFor(() => expect(onConfirmed).toHaveBeenCalledOnce())
    expect(setWorkingBranch).toHaveBeenCalledWith("dev", false)
  })

  it("stay-here adopts the current branch when eligible", async () => {
    useGitStore.setState({
      status: divergent({ current_branch: "feature-x" }), // eligible
    })
    render(<DivergenceModal onConfirmed={vi.fn()} onClose={vi.fn()} />)
    fireEvent.click(screen.getByLabelText(/Make feature-x my working branch/i))
    fireEvent.click(screen.getByTestId("divergence-confirm"))
    await waitFor(() => expect(setWorkingBranch).toHaveBeenCalledWith("feature-x", false))
  })

  it("stay-here is disabled when the current branch is not eligible", () => {
    // current_branch "main" is not in eligible_branches
    render(<DivergenceModal onConfirmed={vi.fn()} onClose={vi.fn()} />)
    const stay = screen.getByLabelText(/can't be a working branch/i) as HTMLInputElement
    expect(stay).toBeDisabled()
  })

  it("branch-manager opens the git panel without setting a branch", () => {
    const onClose = vi.fn()
    render(<DivergenceModal onConfirmed={vi.fn()} onClose={onClose} />)
    fireEvent.click(screen.getByLabelText(/Open the branch manager/i))
    fireEvent.click(screen.getByTestId("divergence-confirm"))
    expect(useUIStore.getState().gitOpen).toBe(true)
    expect(setWorkingBranch).not.toHaveBeenCalled()
    expect(onClose).toHaveBeenCalled()
  })
})
