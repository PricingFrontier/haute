import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("../../api/client", () => ({
  commitMilestone: vi.fn(() =>
    Promise.resolve({
      sha: "abc1234def",
      short_sha: "abc1234",
      working_branch: "dev",
      version_label: null,
    }),
  ),
  getWorkingBranch: vi.fn(() =>
    Promise.resolve({
      working_branch: "dev",
      state: "ready",
      errors: [],
      current_branch: "dev-save",
      last_save_sha: "abc1234",
      eligible_branches: ["dev"],
      identity_set: true,
      user_name: "U",
      user_email: "u@x.y",
    }),
  ),
}))

import MilestoneCommitModal from "../MilestoneCommitModal"
import useGitStore from "../../stores/useGitStore"
import { commitMilestone } from "../../api/client"

describe("MilestoneCommitModal", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useGitStore.setState({
      status: {
        working_branch: "dev",
        state: "ready",
        errors: [],
        current_branch: "dev-save",
        last_save_sha: "abc1234",
        eligible_branches: ["dev"],
        identity_set: true,
        user_name: "U",
        user_email: "u@x.y",
      },
      modal: "milestone",
      pendingAction: null,
      loading: false,
    })
  })
  afterEach(cleanup)

  it("disables Commit until a message is entered", () => {
    render(<MilestoneCommitModal onConfirmed={vi.fn()} onClose={vi.fn()} />)
    expect(screen.getByTestId("milestone-confirm")).toBeDisabled()
    fireEvent.change(screen.getByTestId("milestone-message"), {
      target: { value: "New banding" },
    })
    expect(screen.getByTestId("milestone-confirm")).not.toBeDisabled()
  })

  it("commits with message and optional version label", async () => {
    const onConfirmed = vi.fn()
    render(<MilestoneCommitModal onConfirmed={onConfirmed} onClose={vi.fn()} />)
    fireEvent.change(screen.getByTestId("milestone-message"), {
      target: { value: "New banding" },
    })
    fireEvent.change(screen.getByTestId("milestone-version"), { target: { value: "2.1" } })
    fireEvent.click(screen.getByTestId("milestone-confirm"))
    await waitFor(() => expect(commitMilestone).toHaveBeenCalledWith("New banding", "2.1"))
    await waitFor(() => expect(onConfirmed).toHaveBeenCalledOnce())
  })

  it("passes null version label when left blank", async () => {
    render(<MilestoneCommitModal onConfirmed={vi.fn()} onClose={vi.fn()} />)
    fireEvent.change(screen.getByTestId("milestone-message"), {
      target: { value: "Just a message" },
    })
    fireEvent.click(screen.getByTestId("milestone-confirm"))
    await waitFor(() => expect(commitMilestone).toHaveBeenCalledWith("Just a message", null))
  })
})
