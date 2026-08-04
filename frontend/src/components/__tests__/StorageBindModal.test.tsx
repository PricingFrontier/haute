import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("../../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api/client")>()
  return {
    ...original,
    bindGitStorage: vi.fn(),
    forkGitStorage: vi.fn(),
    getWorkingBranch: vi.fn(() =>
      Promise.resolve({
        working_branch: null,
        state: "ready",
        errors: [],
        current_branch: "main",
        last_save_sha: null,
        eligible_branches: [],
        identity_set: true,
        user_name: "A",
        user_email: "a@b.c",
      }),
    ),
  }
})

import { ApiError, bindGitStorage, forkGitStorage } from "../../api/client"
import StorageBindModal from "../StorageBindModal"

const CLAIM_DETAIL = {
  app_name: "other-app",
  user: "colleague@example.com",
  refreshed_at: "2026-08-04T17:00:00+00:00",
  message:
    "This storage location is in use by app 'other-app' (bound by "
    + "colleague@example.com) — its last heartbeat was 12 seconds ago. "
    + "Bind a different location, or fork this one to work on a copy.",
}

function claimedError(): ApiError {
  return new ApiError("HTTP 409", 409, JSON.stringify(CLAIM_DETAIL), { detail: CLAIM_DETAIL }, CLAIM_DETAIL)
}

async function bindToClaimedLocation() {
  render(<StorageBindModal onClose={vi.fn()} />)
  fireEvent.change(screen.getByTestId("storage-bind-url"), {
    target: { value: "uc://workspace.default.projects/demo" },
  })
  fireEvent.click(screen.getByTestId("storage-bind-confirm"))
  await waitFor(() => {
    expect(screen.getByTestId("storage-bind-claimed-message")).toBeInTheDocument()
  })
}

describe("StorageBindModal — claimed uc:// locations", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })
  afterEach(cleanup)

  it("shows the holder and steers to a fork when the location is claimed", async () => {
    vi.mocked(bindGitStorage).mockRejectedValueOnce(claimedError())
    await bindToClaimedLocation()

    expect(screen.getByTestId("storage-bind-claimed-message").textContent).toContain("other-app")
    expect(screen.getByTestId("storage-fork-url")).toBeInTheDocument()
    expect(screen.getByTestId("storage-bind-back")).toBeInTheDocument()
  })

  it("forks the claimed location and binds the copy", async () => {
    vi.mocked(bindGitStorage)
      .mockRejectedValueOnce(claimedError())
      .mockResolvedValueOnce({
        outcome: "restart-required",
        remote_url: "uc://workspace.default.projects/demo-copy",
        message: "Binding saved. Restart the app to load it.",
      })
    vi.mocked(forkGitStorage).mockResolvedValueOnce({
      outcome: "forked",
      target_url: "uc://workspace.default.projects/demo-copy",
      parent_url: "uc://workspace.default.projects/demo",
      parent_generation: 3,
      message: "Forked generation 3.",
    })
    await bindToClaimedLocation()

    fireEvent.change(screen.getByTestId("storage-fork-url"), {
      target: { value: "uc://workspace.default.projects/demo-copy" },
    })
    fireEvent.click(screen.getByTestId("storage-fork-confirm"))

    await waitFor(() => {
      expect(screen.getByTestId("storage-bind-restart-message")).toBeInTheDocument()
    })
    expect(forkGitStorage).toHaveBeenCalledWith(
      "uc://workspace.default.projects/demo",
      "uc://workspace.default.projects/demo-copy",
    )
    // The bind that followed targeted the fork, not the claimed parent.
    expect(vi.mocked(bindGitStorage).mock.calls[1][0]).toBe(
      "uc://workspace.default.projects/demo-copy",
    )
  })

  it("keeps non-claim errors on the ordinary error surface", async () => {
    vi.mocked(bindGitStorage).mockRejectedValueOnce(
      new ApiError("HTTP 400", 400, "A repository URL cannot contain spaces."),
    )
    render(<StorageBindModal onClose={vi.fn()} />)
    fireEvent.change(screen.getByTestId("storage-bind-url"), {
      target: { value: "bad url" },
    })
    fireEvent.click(screen.getByTestId("storage-bind-confirm"))

    await waitFor(() => {
      expect(screen.getByTestId("storage-bind-error").textContent).toContain(
        "A repository URL cannot contain spaces.",
      )
    })
    expect(screen.queryByTestId("storage-fork-url")).not.toBeInTheDocument()
  })
})
