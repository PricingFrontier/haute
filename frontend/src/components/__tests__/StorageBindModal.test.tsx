import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("../../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api/client")>()
  return {
    ...original,
    bindGitStorage: vi.fn(() =>
      Promise.resolve({
        outcome: "pending" as const,
        remote_url: "uc://workspace.default.projects/demo",
        message: "Saving…",
      }),
    ),
    forkGitStorage: vi.fn(),
    acknowledgeGitBind: vi.fn(() => Promise.resolve(status({}))),
    getWorkingBranch: vi.fn(() => Promise.resolve(status({}))),
  }
})

import { acknowledgeGitBind, bindGitStorage, forkGitStorage } from "../../api/client"
import type { GitStorageBind, GitWorkingBranchResponse } from "../../api/types"
import useGitStore from "../../stores/useGitStore"
import useToastStore from "../../stores/useToastStore"
import StorageBindModal from "../StorageBindModal"

function status(overrides: Partial<GitWorkingBranchResponse>): GitWorkingBranchResponse {
  return {
    working_branch: null,
    state: "unset",
    errors: [],
    current_branch: "main",
    last_save_sha: null,
    eligible_branches: [],
    identity_set: true,
    user_name: "A",
    user_email: "a@b.c",
    storage: "unbound",
    storage_remote: null,
    sync: null,
    storage_bind: null,
    ...overrides,
  }
}

function bindState(overrides: Partial<GitStorageBind>): GitStorageBind {
  return {
    state: "idle",
    outcome: null,
    message: null,
    claim: null,
    remote_url: null,
    ...overrides,
  }
}

const CLAIM = {
  app_name: "other-app",
  user: "colleague@example.com",
  refreshed_at: "2026-08-04T17:00:00+00:00",
  message:
    "This storage location is in use by app 'other-app' (bound by colleague@example.com). "
    + "Bind a different location, or fork this one to work on a copy.",
}

describe("StorageBindModal — asynchronous binding", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useGitStore.setState({ status: status({}), modal: "storage" })
    useToastStore.setState({ toasts: [] })
  })
  afterEach(cleanup)

  it("closes as soon as the bind is accepted, without waiting for the publish", async () => {
    const onClose = vi.fn()
    render(<StorageBindModal onClose={onClose} />)
    fireEvent.change(screen.getByTestId("storage-bind-url"), {
      target: { value: "uc://workspace.default.projects/demo" },
    })
    fireEvent.click(screen.getByTestId("storage-bind-confirm"))

    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(bindGitStorage).toHaveBeenCalledWith("uc://workspace.default.projects/demo")
    // The user is told work is happening, not that it finished.
    const messages = useToastStore.getState().toasts.map((t) => t.text)
    expect(messages.some((m) => m.includes("keep working"))).toBe(true)
  })

  it("reports a background success as a toast and closes", async () => {
    const onClose = vi.fn()
    useGitStore.setState({
      status: status({ storage_bind: bindState({ state: "succeeded", outcome: "adopted" }) }),
    })
    render(<StorageBindModal onClose={onClose} />)

    await waitFor(() => expect(onClose).toHaveBeenCalled())
    const messages = useToastStore.getState().toasts.map((t) => t.text)
    expect(messages.some((m) => m.includes("now saved to storage"))).toBe(true)
    // The result is cleared so it is reported exactly once.
    expect(acknowledgeGitBind).toHaveBeenCalled()
  })

  it("shows the restart instruction rather than a toast when a restart is needed", async () => {
    useGitStore.setState({
      status: status({
        storage_bind: bindState({ state: "succeeded", outcome: "restart-required" }),
      }),
    })
    render(<StorageBindModal onClose={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByTestId("storage-bind-restart-message")).toBeInTheDocument()
    })
    expect(screen.getByTestId("storage-bind-restart-message").textContent).toContain("restart")
  })

  it("shows a background failure with the URL still filled in", async () => {
    useGitStore.setState({
      status: status({
        storage_bind: bindState({
          state: "failed",
          message: "Could not reach the storage volume.",
          remote_url: "uc://workspace.default.projects/demo",
        }),
      }),
    })
    render(<StorageBindModal onClose={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByTestId("storage-bind-error").textContent).toContain(
        "Could not reach the storage volume.",
      )
    })
    const input = screen.getByTestId("storage-bind-url") as HTMLInputElement
    expect(input.value).toBe("uc://workspace.default.projects/demo")
  })

  it("steers to a fork when the location is held by another app", async () => {
    useGitStore.setState({
      status: status({
        storage_bind: bindState({
          state: "failed",
          message: CLAIM.message,
          claim: CLAIM,
          remote_url: "uc://workspace.default.projects/demo",
        }),
      }),
    })
    render(<StorageBindModal onClose={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByTestId("storage-bind-claimed-message")).toBeInTheDocument()
    })
    expect(screen.getByTestId("storage-bind-claimed-message").textContent).toContain("other-app")
    expect(screen.getByTestId("storage-fork-url")).toBeInTheDocument()
  })

  it("forks the held location and binds the copy", async () => {
    const onClose = vi.fn()
    vi.mocked(forkGitStorage).mockResolvedValueOnce({
      outcome: "forked",
      target_url: "uc://workspace.default.projects/demo-copy",
      parent_url: "uc://workspace.default.projects/demo",
      parent_generation: 3,
      message: "Forked generation 3.",
    })
    useGitStore.setState({
      status: status({
        storage_bind: bindState({
          state: "failed",
          message: CLAIM.message,
          claim: CLAIM,
          remote_url: "uc://workspace.default.projects/demo",
        }),
      }),
    })
    render(<StorageBindModal onClose={onClose} />)
    await waitFor(() => expect(screen.getByTestId("storage-fork-url")).toBeInTheDocument())

    fireEvent.change(screen.getByTestId("storage-fork-url"), {
      target: { value: "uc://workspace.default.projects/demo-copy" },
    })
    fireEvent.click(screen.getByTestId("storage-fork-confirm"))

    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(forkGitStorage).toHaveBeenCalledWith(
      "uc://workspace.default.projects/demo",
      "uc://workspace.default.projects/demo-copy",
    )
    // The bind that followed targeted the fork, not the held parent.
    expect(bindGitStorage).toHaveBeenCalledWith("uc://workspace.default.projects/demo-copy")
  })

  it("keeps a synchronous rejection on the error line", async () => {
    const { ApiError } = await import("../../api/client")
    vi.mocked(bindGitStorage).mockRejectedValueOnce(
      new ApiError("HTTP 400", 400, "A repository URL cannot contain spaces."),
    )
    const onClose = vi.fn()
    render(<StorageBindModal onClose={onClose} />)
    fireEvent.change(screen.getByTestId("storage-bind-url"), { target: { value: "bad url" } })
    fireEvent.click(screen.getByTestId("storage-bind-confirm"))

    await waitFor(() => {
      expect(screen.getByTestId("storage-bind-error").textContent).toContain(
        "A repository URL cannot contain spaces.",
      )
    })
    expect(onClose).not.toHaveBeenCalled()
  })
})
