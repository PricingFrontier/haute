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

  it("shows the branch name when ready, without the save SHA", () => {
    useGitStore.setState({ status: status({}) })
    render(<BranchIndicator />)
    expect(screen.getByTestId("branch-indicator-name")).toHaveTextContent("dev")
    expect(screen.queryByTestId("branch-indicator-sha")).toBeNull()
    // The commit code belongs to the history panel, not the toolbar.
    expect(screen.getByTestId("toolbar-branch-indicator")).not.toHaveTextContent("abc1234")
  })

  it("clicking the branch name opens the Git panel (hosts the manager, S28)", () => {
    useGitStore.setState({ status: status({}) })
    render(<BranchIndicator />)
    fireEvent.click(screen.getByTestId("branch-indicator-name"))
    expect(useUIStore.getState().gitOpen).toBe(true)
  })

  it("stays branch-only while comparing — the version is named by the comparison breadcrumb (S11)", () => {
    useGitStore.setState({
      status: status({}),
      comparison: { sha: "feedbeef0000aaaa", label: "v9" },
    })
    render(<BranchIndicator />)

    expect(screen.queryByTestId("branch-indicator-sha")).toBeNull()
    const indicator = screen.getByTestId("toolbar-branch-indicator")
    expect(indicator).toHaveTextContent("dev")
    expect(indicator).not.toHaveTextContent("feedbee")
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
})
