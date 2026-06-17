import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import BranchManagerModal from "../BranchManagerModal"
import useGitStore from "../../stores/useGitStore"

const mockGetWorkingBranches = vi.fn()
const mockSetWorkingBranch = vi.fn()
const mockArchive = vi.fn()
const mockDelete = vi.fn()
const mockGetWorkingBranch = vi.fn() // used by useGitStore.loadStatus

vi.mock("../../api/client", () => ({
  getWorkingBranches: (...a: unknown[]) => mockGetWorkingBranches(...a),
  setWorkingBranch: (...a: unknown[]) => mockSetWorkingBranch(...a),
  gitArchiveBranch: (...a: unknown[]) => mockArchive(...a),
  gitDeleteBranch: (...a: unknown[]) => mockDelete(...a),
  getWorkingBranch: (...a: unknown[]) => mockGetWorkingBranch(...a),
}))

const branches = {
  current: "demo",
  branches: [
    { name: "demo", is_current: true, is_archived: false, has_unmerged_saves: false },
    { name: "experiment", is_current: false, is_archived: false, has_unmerged_saves: true },
    { name: "archive/old", is_current: false, is_archived: true, has_unmerged_saves: false },
  ],
}

describe("BranchManagerModal", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useGitStore.setState({ status: null, loading: false, modal: null, pendingAction: null })
    mockGetWorkingBranches.mockResolvedValue(branches)
    mockSetWorkingBranch.mockResolvedValue({})
    mockArchive.mockResolvedValue({ archived_as: "archive/x" })
    mockDelete.mockResolvedValue({ status: "deleted", branch: "x" })
    mockGetWorkingBranch.mockResolvedValue({ working_branch: "demo", state: "ready" })
  })
  afterEach(cleanup)

  it("lists active + archived branches with the current marker", async () => {
    render(<BranchManagerModal onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-modal")).toBeInTheDocument())
    expect(screen.getByText("demo")).toBeInTheDocument()
    expect(screen.getByTestId("branch-manager-current")).toBeInTheDocument()
    expect(screen.getByTestId("branch-manager-archived")).toHaveTextContent("archive/old")
  })

  it("creates a branch", async () => {
    render(<BranchManagerModal onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-modal")).toBeInTheDocument())
    fireEvent.change(screen.getByTestId("branch-manager-create-input"), {
      target: { value: "new-line" },
    })
    fireEvent.click(screen.getByTestId("branch-manager-create"))
    await waitFor(() => expect(mockSetWorkingBranch).toHaveBeenCalledWith("new-line", true))
  })

  it("switches to a non-current branch", async () => {
    render(<BranchManagerModal onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-modal")).toBeInTheDocument())
    fireEvent.click(screen.getAllByTestId("branch-manager-switch")[0])
    await waitFor(() => expect(mockSetWorkingBranch).toHaveBeenCalledWith("experiment", false))
  })

  it("archives a branch", async () => {
    render(<BranchManagerModal onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-modal")).toBeInTheDocument())
    fireEvent.click(screen.getAllByTestId("branch-manager-archive")[0])
    await waitFor(() => expect(mockArchive).toHaveBeenCalledWith("demo"))
  })

  it("deletes a clean branch with confirm=false", async () => {
    render(<BranchManagerModal onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-modal")).toBeInTheDocument())
    // "demo" is the first active row, clean
    fireEvent.click(screen.getAllByTestId("branch-manager-delete")[0])
    await waitFor(() => expect(screen.getByTestId("branch-manager-confirm")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("branch-manager-confirm-delete"))
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("demo", false))
  })

  it("warns and deletes an unmerged branch with confirm=true", async () => {
    render(<BranchManagerModal onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-modal")).toBeInTheDocument())
    // "experiment" (2nd active row) has unmerged saves
    fireEvent.click(screen.getAllByTestId("branch-manager-delete")[1])
    await waitFor(() =>
      expect(screen.getByTestId("branch-manager-confirm")).toHaveTextContent("not yet committed"),
    )
    fireEvent.click(screen.getByTestId("branch-manager-confirm-delete"))
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("experiment", true))
  })
})
