import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it } from "vitest"

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

describe("BranchIndicator", () => {
  beforeEach(() => {
    useGitStore.setState({ status: null, loading: false, modal: null, pendingAction: null })
    useUIStore.setState({ gitOpen: false })
  })
  afterEach(cleanup)

  it("renders nothing until status is loaded", () => {
    const { container } = render(<BranchIndicator />)
    expect(container.querySelector("[data-testid='toolbar-branch-indicator']")).toBeNull()
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
