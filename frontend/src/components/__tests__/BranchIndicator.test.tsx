import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import BranchIndicator from "../BranchIndicator"
import useGitStore from "../../stores/useGitStore"
import useUIStore from "../../stores/useUIStore"
import type { GitWorkingBranchResponse } from "../../api/types"

function status(overrides: Partial<GitWorkingBranchResponse>): GitWorkingBranchResponse {
  return {
    working_branch: "dev",
    state: "ready",
    errors: [],
    current_branch: "dev-save",
    last_save_sha: "abc1234def5678",
    eligible_branches: ["dev"],
    identity_set: true,
    user_name: "U",
    user_email: "u@x.y",
    storage: "unsupported",
    storage_remote: null,
    sync: null,
    ...overrides,
  }
}

const realLoadStatus = useGitStore.getState().loadStatus

describe("BranchIndicator", () => {
  beforeEach(() => {
    useGitStore.setState({
      status: null,
      loading: false,
      statusError: null,
      modal: null,
      pendingAction: null,
      comparison: null,
      loadStatus: realLoadStatus,
    })
    useUIStore.setState({ gitOpen: false })
  })
  afterEach(cleanup)

  it("renders nothing until status is loaded", () => {
    const { container } = render(<BranchIndicator />)
    expect(container.querySelector("[data-testid='toolbar-branch-indicator']")).toBeNull()
  })

  it("shows a checking state while Git status is loading", () => {
    useGitStore.setState({ loading: true })
    render(<BranchIndicator />)
    expect(screen.getByTestId("toolbar-branch-indicator")).toHaveTextContent("Checking Git")
  })

  it("shows a retryable Git-unavailable error", () => {
    const loadStatus = vi.fn()
    useGitStore.setState({ statusError: "Git service stopped", loadStatus })
    render(<BranchIndicator />)
    expect(screen.getByTestId("toolbar-branch-indicator")).toHaveTextContent("Git unavailable: Git service stopped")
    fireEvent.click(screen.getByTestId("branch-indicator-retry"))
    expect(loadStatus).toHaveBeenCalledOnce()
  })

  it("shows a non-interactive label when no git executable exists", () => {
    useGitStore.setState({ status: status({ state: "git-unavailable", working_branch: null }) })
    render(<BranchIndicator />)
    const indicator = screen.getByTestId("toolbar-branch-indicator")
    expect(indicator).toHaveTextContent("Git unavailable")
    expect(indicator).toHaveAttribute("data-branch-state", "git-unavailable")
  })

  it("shows that Git has not been initialised when there is no repository", () => {
    useGitStore.setState({ status: status({ state: "no-repository", working_branch: null }) })
    render(<BranchIndicator />)
    expect(screen.getByTestId("toolbar-branch-indicator")).toHaveTextContent("Git not initialised")
  })

  it("labels detached, invalid, and divergent Git states distinctly", () => {
    const cases = [
      [status({ state: "detached", head_sha: "1234567890" }), "Detached at 1234567"],
      [status({ state: "invalid" }), "Git needs attention"],
      [status({ state: "divergent" }), "Branch changed externally"],
    ] as const
    for (const [gitStatus, label] of cases) {
      useGitStore.setState({ status: gitStatus })
      const { unmount } = render(<BranchIndicator />)
      expect(screen.getByTestId("toolbar-branch-indicator")).toHaveTextContent(label)
      unmount()
    }
  })

  it("shows branch name and short SHA when ready", () => {
    useGitStore.setState({ status: status({}) })
    render(<BranchIndicator />)
    expect(screen.getByTestId("branch-indicator-name")).toHaveTextContent("dev")
    expect(screen.getByTestId("branch-indicator-sha")).toHaveTextContent("abc1234")
  })

  it("clicking the branch name opens the Git panel (hosts the manager, S28)", () => {
    useGitStore.setState({ status: status({}) })
    render(<BranchIndicator />)
    fireEvent.click(screen.getByTestId("branch-indicator-name"))
    expect(useUIStore.getState().gitOpen).toBe(true)
  })

  it("clicking the SHA opens the history panel and asks it to select the latest save", () => {
    useGitStore.setState({ status: status({}) })
    const before = useGitStore.getState().selectLatestSaveNonce
    render(<BranchIndicator />)
    fireEvent.click(screen.getByTestId("branch-indicator-sha"))
    expect(useUIStore.getState().gitOpen).toBe(true)
    expect(useGitStore.getState().peekBranch).toBeNull()
    expect(useGitStore.getState().selectLatestSaveNonce).toBe(before + 1)
  })

  it("while comparing, the SHA shows the inspected version and selecting targets it (S11)", () => {
    useGitStore.setState({
      status: status({}),
      comparison: { sha: "feedbeef0000aaaa", label: "v9" },
    })
    const before = useGitStore.getState().selectSaveNonce
    render(<BranchIndicator />)

    const sha = screen.getByTestId("branch-indicator-sha")
    expect(sha).toHaveTextContent("feedbee") // compared sha, not the last save
    expect(sha).toHaveAttribute("data-comparing", "true")

    fireEvent.click(sha)
    expect(useUIStore.getState().gitOpen).toBe(true)
    expect(useGitStore.getState().selectSaveTarget).toBe("feedbeef0000aaaa")
    expect(useGitStore.getState().selectSaveNonce).toBe(before + 1)
  })

  it("shows a 'set branch' prompt when unset and opens the select modal", () => {
    useGitStore.setState({ status: status({ state: "unset", working_branch: null }) })
    render(<BranchIndicator />)
    const btn = screen.getByTestId("toolbar-branch-indicator")
    expect(btn).toHaveTextContent("Set branch")
    fireEvent.click(btn)
    expect(useGitStore.getState().modal).toBe("select")
  })

  it("opens the divergence modal from the prompt when divergent", () => {
    useGitStore.setState({ status: status({ state: "divergent", current_branch: "main" }) })
    render(<BranchIndicator />)
    fireEvent.click(screen.getByTestId("toolbar-branch-indicator"))
    expect(useGitStore.getState().modal).toBe("divergence")
  })

  describe("durable storage surface", () => {
    it("renders nothing extra when storage is unsupported (every local session)", () => {
      useGitStore.setState({ status: status({ storage: "unsupported" }) })
      render(<BranchIndicator />)
      expect(screen.queryByTestId("storage-indicator")).toBeNull()
    })

    it("shows a clickable 'Not stored' affordance when unbound", () => {
      useGitStore.setState({ status: status({ storage: "unbound" }) })
      render(<BranchIndicator />)
      const chip = screen.getByTestId("storage-indicator")
      expect(chip).toHaveTextContent("Not stored")
      expect(chip).toHaveAttribute("data-storage-state", "unbound")
      fireEvent.click(chip)
      expect(useGitStore.getState().modal).toBe("storage")
    })

    it("shows a quiet Synced chip when bound and synced", () => {
      useGitStore.setState({
        status: status({
          storage: "bound",
          sync: { state: "synced", pending: 0, failure: null, message: null },
        }),
      })
      render(<BranchIndicator />)
      const chip = screen.getByTestId("storage-indicator")
      expect(chip).toHaveTextContent("Synced")
      expect(chip).toHaveAttribute("data-sync-state", "synced")
    })

    it("shows the unpublished count while pending", () => {
      useGitStore.setState({
        status: status({
          storage: "bound",
          sync: { state: "pending", pending: 3, failure: null, message: null },
        }),
      })
      render(<BranchIndicator />)
      const chip = screen.getByTestId("storage-indicator")
      expect(chip).toHaveTextContent("3 unpublished")
      expect(chip).toHaveAttribute("data-sync-state", "pending")
    })

    it("shows the failure message and a working retry action when failed", () => {
      const retrySync = vi.fn()
      useGitStore.setState({
        status: status({
          storage: "bound",
          sync: { state: "failed", pending: 1, failure: "transport", message: "Could not reach the remote" },
        }),
        retrySync,
      })
      render(<BranchIndicator />)
      const chip = screen.getByTestId("storage-indicator")
      expect(chip).toHaveTextContent("Could not reach the remote")
      expect(chip).toHaveAttribute("data-sync-state", "failed")
      fireEvent.click(screen.getByTestId("storage-retry"))
      expect(retrySync).toHaveBeenCalledOnce()
    })
  })
})
