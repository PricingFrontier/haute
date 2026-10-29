import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("../../api/client", () => ({
  setWorkingBranch: vi.fn(() =>
    Promise.resolve({ working_branch: "dev", state: "ready", last_save_sha: "sha" }),
  ),
  setGitIdentity: vi.fn(() =>
    Promise.resolve({ user_name: "A", user_email: "a@b.c", scope: "local" }),
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
      user_name: "A",
      user_email: "a@b.c",
    }),
  ),
}))

import WorkingBranchModal from "../WorkingBranchModal"
import useGitStore, { resetGitStatusRequestForTests } from "../../stores/useGitStore"
import { getWorkingBranch, setGitIdentity, setWorkingBranch } from "../../api/client"
import type { GitWorkingBranchResponse } from "../../api/types"
import { makeGitWorkingBranch } from "../../test-utils/factories"

function status(overrides: Partial<GitWorkingBranchResponse>): GitWorkingBranchResponse {
  return makeGitWorkingBranch({
    working_branch: null,
    state: "unset",
    errors: [],
    current_branch: "main",
    last_save_sha: null,
    eligible_branches: ["dev", "feature-x"],
    identity_set: true,
    user_name: "A",
    user_email: "a@b.c",
    ...overrides,
  })
}

describe("WorkingBranchModal", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useGitStore.setState({ status: status({}), loading: false, modal: "select", pendingAction: null })
  })
  afterEach(cleanup)

  it("lists every reason an invalid project gives, not only the first", () => {
    useGitStore.setState({
      status: status({
        state: "invalid",
        errors: ["The working branch 'dev' no longer exists.", "Its save history 'dev-save' is missing."],
      }),
    })
    render(<WorkingBranchModal onConfirmed={vi.fn()} onClose={vi.fn()} />)

    const reasons = screen.getByTestId("working-branch-invalid").querySelectorAll("li")
    expect([...reasons].map((reason) => reason.textContent)).toEqual([
      "The working branch 'dev' no longer exists.",
      "Its save history 'dev-save' is missing.",
    ])
  })

  it("on a project left on an earlier version, starts on a new branch and says why", () => {
    useGitStore.setState({
      status: status({ state: "detached", current_branch: "", head_sha: "a".repeat(40) }),
    })
    render(<WorkingBranchModal onConfirmed={vi.fn()} onClose={vi.fn()} />)
    const select = screen.getByTestId("working-branch-select") as HTMLSelectElement
    expect(select.value).toBe("__create__")
    expect(screen.getByTestId("working-branch-detached")).toHaveTextContent(
      "Choosing an existing branch opens its latest save",
    )
  })

  it("off an earlier version, keeps the existing default and says nothing more", () => {
    render(<WorkingBranchModal onConfirmed={vi.fn()} onClose={vi.fn()} />)
    expect((screen.getByTestId("working-branch-select") as HTMLSelectElement).value).toBe("dev")
    expect(screen.queryByTestId("working-branch-detached")).not.toBeInTheDocument()
  })

  it("lists eligible branches plus a create option", () => {
    render(<WorkingBranchModal onConfirmed={vi.fn()} onClose={vi.fn()} />)
    const select = screen.getByTestId("working-branch-select") as HTMLSelectElement
    const values = Array.from(select.options).map((o) => o.value)
    expect(values).toContain("dev")
    expect(values).toContain("feature-x")
    expect(values).toContain("__create__")
  })

  it("adopts an existing branch and calls onConfirmed", async () => {
    const onConfirmed = vi.fn()
    render(<WorkingBranchModal onConfirmed={onConfirmed} onClose={vi.fn()} />)
    fireEvent.click(screen.getByTestId("working-branch-confirm"))
    await waitFor(() => expect(onConfirmed).toHaveBeenCalledOnce())
    expect(setWorkingBranch).toHaveBeenCalledWith("dev", false)
  })

  it("reads the status the new branch left, not an older read still under way", async () => {
    // A refusal's status read is still under way, slow, when a corrected name
    // succeeds: joining it would leave the toolbar on the version before.
    resetGitStatusRequestForTests()
    let answerOlderRead: (value: GitWorkingBranchResponse) => void = () => {}
    vi.mocked(getWorkingBranch)
      .mockImplementationOnce(() => new Promise((resolve) => { answerOlderRead = resolve }))
      .mockImplementationOnce(() => Promise.resolve(status({ state: "ready", working_branch: "dev" })))
    void useGitStore.getState().loadStatus({ refresh: true })
    const onConfirmed = vi.fn()
    render(<WorkingBranchModal onConfirmed={onConfirmed} onClose={vi.fn()} />)

    fireEvent.click(screen.getByTestId("working-branch-confirm"))
    await waitFor(() => expect(setWorkingBranch).toHaveBeenCalledWith("dev", false))
    answerOlderRead(status({ state: "detached" }))

    await waitFor(() => expect(onConfirmed).toHaveBeenCalledOnce())
    expect(useGitStore.getState().status?.state).toBe("ready")
  })

  it("creates a new branch when the create option is chosen", async () => {
    render(<WorkingBranchModal onConfirmed={vi.fn()} onClose={vi.fn()} />)
    fireEvent.change(screen.getByTestId("working-branch-select"), {
      target: { value: "__create__" },
    })
    fireEvent.change(screen.getByTestId("working-branch-new"), {
      target: { value: "new-line" },
    })
    fireEvent.click(screen.getByTestId("working-branch-confirm"))
    await waitFor(() => expect(setWorkingBranch).toHaveBeenCalledWith("new-line", true))
  })

  it("confirm is disabled until a new branch name is typed", () => {
    render(<WorkingBranchModal onConfirmed={vi.fn()} onClose={vi.fn()} />)
    fireEvent.change(screen.getByTestId("working-branch-select"), {
      target: { value: "__create__" },
    })
    expect(screen.getByTestId("working-branch-confirm")).toBeDisabled()
  })

  it("prompts for identity when missing and sets it before the branch", async () => {
    useGitStore.setState({
      status: status({ identity_set: false, user_name: null, user_email: null }),
    })
    render(<WorkingBranchModal onConfirmed={vi.fn()} onClose={vi.fn()} />)
    // confirm blocked until identity filled
    expect(screen.getByTestId("working-branch-confirm")).toBeDisabled()
    fireEvent.change(screen.getByTestId("identity-name"), { target: { value: "Jane" } })
    fireEvent.change(screen.getByTestId("identity-email"), {
      target: { value: "jane@x.y" },
    })
    fireEvent.click(screen.getByTestId("working-branch-confirm"))
    await waitFor(() => expect(setGitIdentity).toHaveBeenCalledWith("Jane", "jane@x.y", false))
    expect(setWorkingBranch).toHaveBeenCalled()
  })
})
