import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import BranchManager from "../BranchManager"
import useGitStore from "../../stores/useGitStore"

const mockGetWorkingBranches = vi.fn()
const mockSetWorkingBranch = vi.fn()
const mockCreateWorkingBranch = vi.fn()
const mockArchive = vi.fn()
const mockDelete = vi.fn()
const mockRestore = vi.fn()
const mockGetWorkingBranch = vi.fn()
const mockGetPrefs = vi.fn()
const mockSetPrefs = vi.fn()

vi.mock("../../api/client", () => ({
  getWorkingBranches: (...a: unknown[]) => mockGetWorkingBranches(...a),
  setWorkingBranch: (...a: unknown[]) => mockSetWorkingBranch(...a),
  createWorkingBranch: (...a: unknown[]) => mockCreateWorkingBranch(...a),
  gitArchiveBranch: (...a: unknown[]) => mockArchive(...a),
  gitDeleteBranch: (...a: unknown[]) => mockDelete(...a),
  restoreBranch: (...a: unknown[]) => mockRestore(...a),
  getWorkingBranch: (...a: unknown[]) => mockGetWorkingBranch(...a),
  getGitPrefs: (...a: unknown[]) => mockGetPrefs(...a),
  setGitPrefs: (...a: unknown[]) => mockSetPrefs(...a),
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
    useGitStore.setState({ status: null, loading: false, modal: null, pendingAction: null, peekBranch: null })
    mockGetWorkingBranches.mockResolvedValue(listing)
    mockSetWorkingBranch.mockResolvedValue({})
    mockCreateWorkingBranch.mockResolvedValue({ working_branch: "x", moved: false, switched: false, last_save_sha: null })
    mockArchive.mockResolvedValue({ archived_as: "archive/x" })
    mockDelete.mockResolvedValue({ status: "deleted", branch: "x" })
    mockRestore.mockResolvedValue({ restored_as: "old" })
    mockGetWorkingBranch.mockResolvedValue({ working_branch: "demo", state: "ready" })
    mockGetPrefs.mockResolvedValue({ skip_switch_confirm: false })
    mockSetPrefs.mockResolvedValue({ skip_switch_confirm: true })
  })
  afterEach(cleanup)

  it("lists the current branch (marked) + others + archived", async () => {
    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-current")).toBeInTheDocument())
    expect(screen.getByText("demo")).toBeInTheDocument()
    expect(screen.getByText("experiment")).toBeInTheDocument()
    expect(screen.getByTestId("branch-manager-archived")).toHaveTextContent("archive/old")
  })

  it("offers Archive on the current branch too (save-and-archive)", async () => {
    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-current")).toBeInTheDocument())
    // current (demo) + non-current (experiment); archived has no archive button
    expect(screen.getAllByTestId("branch-manager-archive")).toHaveLength(2)
  })

  it("peeks a branch on name click without switching", async () => {
    const onPeek = vi.fn()
    render(<BranchManager onPeek={onPeek} />)
    await waitFor(() => expect(screen.getByText("experiment")).toBeInTheDocument())
    fireEvent.click(screen.getByText("experiment"))
    expect(onPeek).toHaveBeenCalledWith("experiment")
    expect(mockSetWorkingBranch).not.toHaveBeenCalled() // peek ≠ switch
  })

  it("creates a parallel branch (no move) at the latest milestone", async () => {
    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-create-input")).toBeInTheDocument())
    fireEvent.change(screen.getByTestId("branch-manager-create-input"), { target: { value: "new-line" } })
    fireEvent.click(screen.getByTestId("branch-manager-create"))
    await waitFor(() => expect(mockCreateWorkingBranch).toHaveBeenCalledWith("new-line", { move: false }))
  })

  it("creates & moves via the dropdown after confirming", async () => {
    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-create-input")).toBeInTheDocument())
    fireEvent.change(screen.getByTestId("branch-manager-create-input"), { target: { value: "moved" } })
    fireEvent.click(screen.getByTestId("branch-manager-create-menu"))
    fireEvent.click(screen.getByTestId("branch-manager-create-move"))
    await waitFor(() => expect(screen.getByTestId("branch-manager-confirm-move")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("branch-manager-confirm-move-go"))
    await waitFor(() => expect(mockCreateWorkingBranch).toHaveBeenCalledWith("moved", { move: true }))
  })

  it("switches only after the confirm dialog", async () => {
    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-switch")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("branch-manager-switch"))
    await waitFor(() => expect(screen.getByTestId("branch-manager-confirm-switch")).toBeInTheDocument())
    expect(mockSetWorkingBranch).not.toHaveBeenCalled() // not yet
    fireEvent.click(screen.getByTestId("branch-manager-confirm-switch-go"))
    await waitFor(() => expect(mockSetWorkingBranch).toHaveBeenCalledWith("experiment", false))
  })

  it("persists 'don't ask again' on switch", async () => {
    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-switch")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("branch-manager-switch"))
    await waitFor(() => expect(screen.getByTestId("branch-manager-dont-ask")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("branch-manager-dont-ask"))
    fireEvent.click(screen.getByTestId("branch-manager-confirm-switch-go"))
    await waitFor(() => expect(mockSetPrefs).toHaveBeenCalledWith({ skip_switch_confirm: true }))
  })

  it("archives a non-current branch directly", async () => {
    render(<BranchManager />)
    await waitFor(() => expect(screen.getAllByTestId("branch-manager-archive").length).toBe(2))
    // archive order: demo(current)[0], experiment[1]
    fireEvent.click(screen.getAllByTestId("branch-manager-archive")[1])
    await waitFor(() => expect(mockArchive).toHaveBeenCalledWith("experiment"))
  })

  it("routes archive of a dirty current branch through a save dialog", async () => {
    mockGetWorkingBranches.mockResolvedValue({
      current: "demo",
      branches: [branch({ name: "demo", is_current: true, has_uncommitted_changes: true })],
    })
    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-archive")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("branch-manager-archive"))
    await waitFor(() => expect(screen.getByTestId("branch-manager-archive-dirty")).toBeInTheDocument())
    expect(mockArchive).not.toHaveBeenCalled() // gated on a save
  })

  it("deletes an unmerged branch with confirm=true after warning", async () => {
    render(<BranchManager />)
    await waitFor(() => expect(screen.getAllByTestId("branch-manager-delete").length).toBeGreaterThan(1))
    // delete order: current(demo)[0], experiment[1]
    fireEvent.click(screen.getAllByTestId("branch-manager-delete")[1])
    await waitFor(() => expect(screen.getByTestId("branch-manager-confirm")).toHaveTextContent("not yet in a milestone"))
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

  it("keeps delete enabled even with uncommitted changes (always deletable)", async () => {
    mockGetWorkingBranches.mockResolvedValue({
      current: "demo",
      branches: [branch({ name: "demo", is_current: true, has_uncommitted_changes: true })],
    })
    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-uncommitted")).toBeInTheDocument())
    expect(screen.getByTestId("branch-manager-delete")).not.toBeDisabled()
  })

  it("shows a persistent error banner when an action fails", async () => {
    mockArchive.mockRejectedValue(new Error("can't archive: unsaved changes"))
    render(<BranchManager />)
    await waitFor(() => expect(screen.getAllByTestId("branch-manager-archive").length).toBe(2))
    fireEvent.click(screen.getAllByTestId("branch-manager-archive")[1]) // experiment
    await waitFor(() => expect(screen.getByTestId("branch-manager-error")).toHaveTextContent("unsaved changes"))
    expect(screen.getByTestId("branch-manager-error")).toBeInTheDocument()
  })
})
