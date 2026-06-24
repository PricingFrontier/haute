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
import useToastStore from "../../stores/useToastStore"
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

describe("DivergenceModal (gaps)", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useGitStore.setState({
      status: divergent(),
      modal: "divergence",
      pendingAction: null,
      loading: false,
    })
    useUIStore.setState({ gitOpen: false })
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
  })
  afterEach(cleanup)

  it("surfaces an error toast and does not confirm when setWorkingBranch rejects", async () => {
    vi.mocked(setWorkingBranch).mockRejectedValueOnce(new Error("boom"))
    const onConfirmed = vi.fn()
    render(<DivergenceModal onConfirmed={onConfirmed} onClose={vi.fn()} />)

    fireEvent.click(screen.getByTestId("divergence-confirm"))

    await waitFor(() =>
      expect(useToastStore.getState().toasts).toContainEqual(
        expect.objectContaining({
          type: "error",
          text: "Could not switch working branch: boom",
        }),
      ),
    )
    expect(onConfirmed).not.toHaveBeenCalled()
    // The finally block resets busy, so the button is interactive again.
    await waitFor(() =>
      expect(screen.getByTestId("divergence-confirm")).not.toBeDisabled(),
    )
  })

  it("reports 'unknown error' when the rejection is not an Error instance", async () => {
    vi.mocked(setWorkingBranch).mockRejectedValueOnce("nope")
    render(<DivergenceModal onConfirmed={vi.fn()} onClose={vi.fn()} />)

    fireEvent.click(screen.getByTestId("divergence-confirm"))

    await waitFor(() =>
      expect(useToastStore.getState().toasts).toContainEqual(
        expect.objectContaining({
          type: "error",
          text: "Could not switch working branch: unknown error",
        }),
      ),
    )
  })

  it("disables the confirm button and shows 'Working…' while in flight", async () => {
    let resolve: (() => void) | undefined
    vi.mocked(setWorkingBranch).mockImplementationOnce(
      () =>
        new Promise((res) => {
          resolve = () => res({ working_branch: "dev", state: "ready", last_save_sha: "sha" })
        }) as ReturnType<typeof setWorkingBranch>,
    )
    render(<DivergenceModal onConfirmed={vi.fn()} onClose={vi.fn()} />)

    const confirm = screen.getByTestId("divergence-confirm")
    fireEvent.click(confirm)

    await waitFor(() => expect(confirm).toBeDisabled())
    expect(confirm).toHaveTextContent("Working…")

    resolve?.()
    await waitFor(() => expect(confirm).not.toBeDisabled())
  })

  it("ignores a second submit while busy (guard returns early)", async () => {
    let resolve: (() => void) | undefined
    vi.mocked(setWorkingBranch).mockImplementationOnce(
      () =>
        new Promise((res) => {
          resolve = () => res({ working_branch: "dev", state: "ready", last_save_sha: "sha" })
        }) as ReturnType<typeof setWorkingBranch>,
    )
    render(<DivergenceModal onConfirmed={vi.fn()} onClose={vi.fn()} />)

    const form = screen.getByTestId("divergence-confirm").closest("form") as HTMLFormElement
    fireEvent.submit(form)
    await waitFor(() => expect(screen.getByTestId("divergence-confirm")).toBeDisabled())
    // A second submit while busy should hit the `if (busy) return` early-out.
    fireEvent.submit(form)

    resolve?.()
    await waitFor(() => expect(screen.getByTestId("divergence-confirm")).not.toBeDisabled())
    // Only the first submit ever reached the API.
    expect(setWorkingBranch).toHaveBeenCalledTimes(1)
  })

  it("renders '(unknown)' placeholders when status is null", () => {
    useGitStore.setState({ status: null })
    render(<DivergenceModal onConfirmed={vi.fn()} onClose={vi.fn()} />)

    const dialog = screen.getByTestId("divergence-modal")
    expect(dialog).toHaveTextContent("(unknown)")
    // With no status, stay-here is ineligible and its radio is disabled.
    const stay = screen.getByLabelText(/can't be a working branch/i) as HTMLInputElement
    expect(stay).toBeDisabled()
  })
})
