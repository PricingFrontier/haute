import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import BranchManager from "../BranchManager"
import useGitStore from "../../stores/useGitStore"

const mockGetWorkingBranches = vi.fn()
const mockSetWorkingBranch = vi.fn()
const mockArchive = vi.fn()
const mockDelete = vi.fn()
const mockRestore = vi.fn()
const mockGetWorkingBranch = vi.fn() // useGitStore.loadStatus

vi.mock("../../api/client", () => ({
  getWorkingBranches: (...a: unknown[]) => mockGetWorkingBranches(...a),
  setWorkingBranch: (...a: unknown[]) => mockSetWorkingBranch(...a),
  gitArchiveBranch: (...a: unknown[]) => mockArchive(...a),
  gitDeleteBranch: (...a: unknown[]) => mockDelete(...a),
  restoreBranch: (...a: unknown[]) => mockRestore(...a),
  getWorkingBranch: (...a: unknown[]) => mockGetWorkingBranch(...a),
}))

const branch = (over: Partial<Record<string, unknown>> = {}) => ({
  name: "x",
  is_current: false,
  is_archived: false,
  has_unmerged_saves: false,
  has_uncommitted_changes: false,
  ...over,
})

const listing = {
  current: "demo",
  branches: [
    branch({ name: "demo", is_current: true }),
    branch({ name: "experiment", has_unmerged_saves: true }),
    branch({ name: "archive/old", is_archived: true }),
  ],
}

describe("BranchManager", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useGitStore.setState({ status: null, loading: false, modal: null, pendingAction: null })
    mockGetWorkingBranches.mockResolvedValue(listing)
    mockSetWorkingBranch.mockResolvedValue({})
    mockArchive.mockResolvedValue({ archived_as: "archive/x" })
    mockDelete.mockResolvedValue({ status: "deleted", branch: "x" })
    mockRestore.mockResolvedValue({ restored_as: "old" })
    mockGetWorkingBranch.mockResolvedValue({ working_branch: "demo", state: "ready" })
  })
  afterEach(cleanup)

  it("lists active + archived with the current marker", async () => {
    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-current")).toBeInTheDocument())
    expect(screen.getByText("demo")).toBeInTheDocument()
    expect(screen.getByTestId("branch-manager-archived")).toHaveTextContent("archive/old")
  })

  it("creates a branch", async () => {
    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-create-input")).toBeInTheDocument())
    fireEvent.change(screen.getByTestId("branch-manager-create-input"), { target: { value: "new-line" } })
    fireEvent.click(screen.getByTestId("branch-manager-create"))
    await waitFor(() => expect(mockSetWorkingBranch).toHaveBeenCalledWith("new-line", true))
  })

  it("switches to a non-current branch", async () => {
    render(<BranchManager />)
    await waitFor(() => expect(screen.getAllByTestId("branch-manager-switch").length).toBeGreaterThan(0))
    fireEvent.click(screen.getAllByTestId("branch-manager-switch")[0])
    await waitFor(() => expect(mockSetWorkingBranch).toHaveBeenCalledWith("experiment", false))
  })

  it("archives a non-current branch", async () => {
    render(<BranchManager />)
    await waitFor(() => expect(screen.getAllByTestId("branch-manager-archive").length).toBeGreaterThan(1))
    // [0] = demo (current), [1] = experiment
    fireEvent.click(screen.getAllByTestId("branch-manager-archive")[1])
    await waitFor(() => expect(mockArchive).toHaveBeenCalledWith("experiment"))
  })

  it("deletes an unmerged branch with confirm=true after warning", async () => {
    render(<BranchManager />)
    await waitFor(() => expect(screen.getAllByTestId("branch-manager-delete").length).toBeGreaterThan(1))
    fireEvent.click(screen.getAllByTestId("branch-manager-delete")[1]) // experiment (unmerged)
    await waitFor(() => expect(screen.getByTestId("branch-manager-confirm")).toHaveTextContent("not yet committed"))
    fireEvent.click(screen.getByTestId("branch-manager-confirm-delete"))
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("experiment", true))
  })

  it("restores an archived branch", async () => {
    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-restore")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("branch-manager-restore"))
    await waitFor(() => expect(mockRestore).toHaveBeenCalledWith("archive/old"))
  })

  it("greys out archive/delete for a branch with uncommitted changes", async () => {
    mockGetWorkingBranches.mockResolvedValue({
      current: "demo",
      branches: [branch({ name: "demo", is_current: true, has_uncommitted_changes: true })],
    })
    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-blocked")).toBeInTheDocument())
    expect(screen.getByTestId("branch-manager-archive")).toBeDisabled()
    expect(screen.getByTestId("branch-manager-delete")).toBeDisabled()
  })

  it("shows a persistent error banner when an action fails", async () => {
    mockArchive.mockRejectedValue(new Error("can't archive: unsaved changes"))
    render(<BranchManager />)
    await waitFor(() => expect(screen.getAllByTestId("branch-manager-archive").length).toBeGreaterThan(1))
    fireEvent.click(screen.getAllByTestId("branch-manager-archive")[1]) // experiment
    await waitFor(() => expect(screen.getByTestId("branch-manager-error")).toHaveTextContent("unsaved changes"))
    // persists until dismissed
    expect(screen.getByTestId("branch-manager-error")).toBeInTheDocument()
  })
})
