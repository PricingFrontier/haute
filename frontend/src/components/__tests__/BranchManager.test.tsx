import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import BranchManager from "../BranchManager"
import useGitStore from "../../stores/useGitStore"

const mockGetWorkingBranches = vi.fn()
const mockSetWorkingBranch = vi.fn()
const mockArchive = vi.fn()
const mockDelete = vi.fn()
const mockRestore = vi.fn()
const mockGetWorkingBranch = vi.fn()

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

  it("lists the current branch (marked) + others + archived", async () => {
    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-current")).toBeInTheDocument())
    expect(screen.getByText("demo")).toBeInTheDocument()
    expect(screen.getByText("experiment")).toBeInTheDocument()
    expect(screen.getByTestId("branch-manager-archived")).toHaveTextContent("archive/old")
  })

  it("does not offer Archive on the current branch", async () => {
    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-current")).toBeInTheDocument())
    // only the non-current 'experiment' has an archive button
    expect(screen.getAllByTestId("branch-manager-archive")).toHaveLength(1)
  })

  it("peeks a branch on name click without switching", async () => {
    const onPeek = vi.fn()
    render(<BranchManager onPeek={onPeek} />)
    await waitFor(() => expect(screen.getByText("experiment")).toBeInTheDocument())
    fireEvent.click(screen.getByText("experiment"))
    expect(onPeek).toHaveBeenCalledWith("experiment")
    expect(mockSetWorkingBranch).not.toHaveBeenCalled() // peek ≠ switch
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
    await waitFor(() => expect(screen.getByTestId("branch-manager-switch")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("branch-manager-switch"))
    await waitFor(() => expect(mockSetWorkingBranch).toHaveBeenCalledWith("experiment", false))
  })

  it("archives a non-current branch", async () => {
    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-archive")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("branch-manager-archive"))
    await waitFor(() => expect(mockArchive).toHaveBeenCalledWith("experiment"))
  })

  it("deletes an unmerged branch with confirm=true after warning", async () => {
    render(<BranchManager />)
    await waitFor(() => expect(screen.getAllByTestId("branch-manager-delete").length).toBeGreaterThan(1))
    // delete order: current(demo)[0], experiment[1], archived[2]
    fireEvent.click(screen.getAllByTestId("branch-manager-delete")[1])
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

  it("shows both uncommitted and unsaved indicators on the current branch", async () => {
    mockGetWorkingBranches.mockResolvedValue({
      current: "demo",
      branches: [branch({ name: "demo", is_current: true, has_uncommitted_changes: true, has_unmerged_saves: true })],
    })
    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-uncommitted")).toBeInTheDocument())
    expect(screen.getByTestId("branch-manager-unsaved")).toBeInTheDocument()
  })

  it("greys delete for a branch with uncommitted changes", async () => {
    mockGetWorkingBranches.mockResolvedValue({
      current: "demo",
      branches: [branch({ name: "demo", is_current: true, has_uncommitted_changes: true })],
    })
    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-uncommitted")).toBeInTheDocument())
    expect(screen.getByTestId("branch-manager-delete")).toBeDisabled()
  })

  it("shows a persistent error banner when an action fails", async () => {
    mockArchive.mockRejectedValue(new Error("can't archive: unsaved changes"))
    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-archive")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("branch-manager-archive")) // experiment
    await waitFor(() => expect(screen.getByTestId("branch-manager-error")).toHaveTextContent("unsaved changes"))
    expect(screen.getByTestId("branch-manager-error")).toBeInTheDocument()
  })
})
