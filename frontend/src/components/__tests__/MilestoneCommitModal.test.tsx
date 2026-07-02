import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const mockCommit = vi.fn()
const mockGetWorkingBranch = vi.fn()

// Spread the real module so `ApiError` (used for the 409 fork-gate path) stays
// the genuine class; only the two network calls are stubbed.
vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client")
  return {
    ...actual,
    commitMilestone: (...a: unknown[]) => mockCommit(...a),
    getWorkingBranch: (...a: unknown[]) => mockGetWorkingBranch(...a),
  }
})

import MilestoneCommitModal from "../MilestoneCommitModal"
import useGitStore from "../../stores/useGitStore"
import { ApiError } from "../../api/client"

const WORKING_BRANCH = {
  working_branch: "dev",
  state: "ready" as const,
  errors: [],
  current_branch: "dev-save",
  last_save_sha: "abc1234",
  eligible_branches: ["dev"],
  identity_set: true,
  user_name: "U",
  user_email: "u@x.y",
}

describe("MilestoneCommitModal", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockCommit.mockResolvedValue({
      sha: "abc1234def",
      short_sha: "abc1234",
      working_branch: "dev",
      version_label: null,
    })
    mockGetWorkingBranch.mockResolvedValue(WORKING_BRANCH)
    useGitStore.setState({
      status: WORKING_BRANCH,
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
    await waitFor(() =>
      expect(mockCommit).toHaveBeenCalledWith("New banding", "2.1", { allowFork: false }),
    )
    await waitFor(() => expect(onConfirmed).toHaveBeenCalledOnce())
  })

  it("passes null version label when left blank", async () => {
    render(<MilestoneCommitModal onConfirmed={vi.fn()} onClose={vi.fn()} />)
    fireEvent.change(screen.getByTestId("milestone-message"), {
      target: { value: "Just a message" },
    })
    fireEvent.click(screen.getByTestId("milestone-confirm"))
    await waitFor(() =>
      expect(mockCommit).toHaveBeenCalledWith("Just a message", null, { allowFork: false }),
    )
  })

  it("warns on a 409 fork, then commits anyway on override (U4/D4)", async () => {
    const onConfirmed = vi.fn()
    const fork = {
      status: "would_fork",
      remote: "origin",
      working: { status: "behind", ahead: 0, behind: 1 },
      message: "Saving a milestone now will fork 'origin'. Commit anyway to create a fork.",
    }
    // First attempt forks; the override (allowFork: true) succeeds.
    mockCommit.mockRejectedValueOnce(
      new ApiError("HTTP 409", 409, JSON.stringify({ detail: fork }), { detail: fork }),
    )
    render(<MilestoneCommitModal onConfirmed={onConfirmed} onClose={vi.fn()} />)
    fireEvent.change(screen.getByTestId("milestone-message"), {
      target: { value: "My milestone" },
    })
    fireEvent.click(screen.getByTestId("milestone-confirm"))

    await waitFor(() => expect(screen.getByTestId("milestone-fork-confirm")).toBeInTheDocument())
    expect(screen.getByTestId("milestone-fork-confirm")).toHaveTextContent("fork")
    expect(onConfirmed).not.toHaveBeenCalled() // not committed yet — it's a warning

    fireEvent.click(screen.getByTestId("milestone-fork-anyway"))
    await waitFor(() =>
      expect(mockCommit).toHaveBeenLastCalledWith("My milestone", null, { allowFork: true }),
    )
    await waitFor(() => expect(onConfirmed).toHaveBeenCalledOnce())
  })

  it("can back out of the fork warning without committing", async () => {
    const fork = {
      status: "would_fork",
      remote: "origin",
      working: { status: "behind", ahead: 0, behind: 2 },
      message: "would fork 'origin'",
    }
    mockCommit.mockRejectedValueOnce(
      new ApiError("HTTP 409", 409, JSON.stringify({ detail: fork }), { detail: fork }),
    )
    render(<MilestoneCommitModal onConfirmed={vi.fn()} onClose={vi.fn()} />)
    fireEvent.change(screen.getByTestId("milestone-message"), { target: { value: "Mine" } })
    fireEvent.click(screen.getByTestId("milestone-confirm"))
    await waitFor(() => expect(screen.getByTestId("milestone-fork-confirm")).toBeInTheDocument())
    fireEvent.click(screen.getByText("Back"))
    await waitFor(() =>
      expect(screen.queryByTestId("milestone-fork-confirm")).not.toBeInTheDocument(),
    )
    expect(mockCommit).toHaveBeenCalledTimes(1) // no second (override) call
  })
})
