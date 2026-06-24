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

describe("BranchManager reload-on-state-change navigation", () => {
  let reloadSpy: ReturnType<typeof vi.fn>

  beforeEach(() => {
    vi.clearAllMocks()
    useGitStore.setState({ status: null, loading: false, modal: null, pendingAction: null, peekBranch: null })
    mockGetWorkingBranches.mockResolvedValue({
      current: "demo",
      branches: [branch({ name: "demo", is_current: true })],
    })
    mockSetWorkingBranch.mockResolvedValue({})
    mockCreateWorkingBranch.mockResolvedValue({ working_branch: "x", moved: false, switched: false, last_save_sha: null })
    mockArchive.mockResolvedValue({ archived_as: "archive/x" })
    mockDelete.mockResolvedValue({ status: "deleted", branch: "x" })
    mockRestore.mockResolvedValue({ restored_as: "old" })
    mockGetWorkingBranch.mockResolvedValue({ working_branch: "demo", state: "ready" })
    mockGetPrefs.mockResolvedValue({ skip_switch_confirm: false })
    mockSetPrefs.mockResolvedValue({ skip_switch_confirm: true })

    // window.location.reload is a no-op in jsdom; replace it so we can assert
    // the component took the reload branch.
    reloadSpy = vi.fn()
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, reload: reloadSpy },
    })
  })
  afterEach(cleanup)

  it("reloads the app when a parallel create reports switched=true", async () => {
    mockCreateWorkingBranch.mockResolvedValue({
      working_branch: "spinoff",
      moved: false,
      switched: true,
      last_save_sha: null,
    })
    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-create-input")).toBeInTheDocument())
    fireEvent.change(screen.getByTestId("branch-manager-create-input"), { target: { value: "spinoff" } })
    fireEvent.click(screen.getByTestId("branch-manager-create"))
    await waitFor(() => expect(mockCreateWorkingBranch).toHaveBeenCalledWith("spinoff", { move: false }))
    await waitFor(() => expect(reloadSpy).toHaveBeenCalled())
  })

  it("does not reload when create reports switched=false", async () => {
    mockCreateWorkingBranch.mockResolvedValue({
      working_branch: "parallel",
      moved: false,
      switched: false,
      last_save_sha: null,
    })
    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-create-input")).toBeInTheDocument())
    fireEvent.change(screen.getByTestId("branch-manager-create-input"), { target: { value: "parallel" } })
    fireEvent.click(screen.getByTestId("branch-manager-create"))
    await waitFor(() => expect(mockCreateWorkingBranch).toHaveBeenCalledWith("parallel", { move: false }))
    // refresh runs again on completion (no reload path).
    await waitFor(() => expect(mockGetWorkingBranches.mock.calls.length).toBeGreaterThan(1))
    expect(reloadSpy).not.toHaveBeenCalled()
  })

  it("reloads after deleting the current branch (is_current)", async () => {
    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-delete")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("branch-manager-delete")) // current branch row
    await waitFor(() =>
      expect(screen.getByTestId("branch-manager-confirm")).toHaveTextContent("branch chooser"),
    )
    fireEvent.click(screen.getByTestId("branch-manager-confirm-delete"))
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("demo", true))
    await waitFor(() => expect(reloadSpy).toHaveBeenCalled())
  })

  it("reloads after archiving the current (clean) branch", async () => {
    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-archive")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("branch-manager-archive")) // current branch, clean
    await waitFor(() => expect(mockArchive).toHaveBeenCalledWith("demo"))
    await waitFor(() => expect(reloadSpy).toHaveBeenCalled())
  })
})
