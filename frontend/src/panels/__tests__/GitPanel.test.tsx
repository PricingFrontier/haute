import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import GitPanel from "../GitPanel"

// Mock API client
const mockGetStatus = vi.fn()
const mockListBranches = vi.fn()
const mockCreateBranch = vi.fn()
const mockSwitchBranch = vi.fn()
const mockGitSave = vi.fn()
const mockGitSubmit = vi.fn()
const mockGetHistory = vi.fn()
const mockGitRevert = vi.fn()
const mockGitPull = vi.fn()
const mockArchiveBranch = vi.fn()
const mockDeleteBranch = vi.fn()

vi.mock("../../api/client", () => ({
  getGitStatus: (...args: unknown[]) => mockGetStatus(...args),
  listGitBranches: (...args: unknown[]) => mockListBranches(...args),
  createGitBranch: (...args: unknown[]) => mockCreateBranch(...args),
  switchGitBranch: (...args: unknown[]) => mockSwitchBranch(...args),
  gitSave: (...args: unknown[]) => mockGitSave(...args),
  gitSubmit: (...args: unknown[]) => mockGitSubmit(...args),
  getGitHistory: (...args: unknown[]) => mockGetHistory(...args),
  gitRevert: (...args: unknown[]) => mockGitRevert(...args),
  gitPull: (...args: unknown[]) => mockGitPull(...args),
  gitArchiveBranch: (...args: unknown[]) => mockArchiveBranch(...args),
  gitDeleteBranch: (...args: unknown[]) => mockDeleteBranch(...args),
}))

const mainStatus = {
  branch: "main",
  is_main: true,
  is_read_only: true,
  changed_files: [],
  main_ahead: false,
  main_ahead_by: 0,
  main_last_updated: null,
}

const branchStatus = {
  branch: "pricing/test-user/update-factors",
  is_main: false,
  is_read_only: false,
  changed_files: ["main.py", "config/banding/area.json"],
  main_ahead: false,
  main_ahead_by: 0,
  main_last_updated: null,
}

const readOnlyBranchStatus = {
  branch: "pricing/other-user/their-feature",
  is_main: false,
  is_read_only: true,
  changed_files: [],
  main_ahead: false,
  main_ahead_by: 0,
  main_last_updated: null,
}

const defaultBranches = {
  current: "main",
  branches: [
    { name: "main", is_yours: false, is_current: true, is_archived: false, last_commit_time: "2026-03-06T10:00:00Z", commit_count: 0 },
    { name: "pricing/test-user/update-factors", is_yours: true, is_current: false, is_archived: false, last_commit_time: "2026-03-06T09:00:00Z", commit_count: 3 },
    { name: "pricing/other-user/their-feat", is_yours: false, is_current: false, is_archived: false, last_commit_time: "2026-03-06T08:00:00Z", commit_count: 1 },
  ],
}

describe("GitPanel", () => {
  const defaultProps = { onClose: vi.fn() }

  beforeEach(() => {
    vi.clearAllMocks()
    mockGetStatus.mockResolvedValue(mainStatus)
    mockListBranches.mockResolvedValue(defaultBranches)
    mockGetHistory.mockResolvedValue({ entries: [] })
  })

  afterEach(cleanup)

  // ---------------------------------------------------------------------------
  // Rendering
  // ---------------------------------------------------------------------------

  it("renders header and close button", async () => {
    render(<GitPanel {...defaultProps} />)
    expect(screen.getByText("Git")).toBeInTheDocument()
    expect(screen.getByTitle("Close")).toBeInTheDocument()
  })

  it("close button calls onClose", async () => {
    render(<GitPanel {...defaultProps} />)
    fireEvent.click(screen.getByTitle("Close"))
    expect(defaultProps.onClose).toHaveBeenCalledOnce()
  })

  it("shows current branch name", async () => {
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByText("main")).toBeInTheDocument())
  })

  it("shows read-only badge on main", async () => {
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByText("read-only")).toBeInTheDocument())
  })

  it("fetches status and branches on mount", async () => {
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => {
      expect(mockGetStatus).toHaveBeenCalledOnce()
      expect(mockListBranches).toHaveBeenCalledOnce()
    })
  })

  // ---------------------------------------------------------------------------
  // On main — create branch
  // ---------------------------------------------------------------------------

  it("shows 'Start editing' button on main", async () => {
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByText(/Start editing/)).toBeInTheDocument())
  })

  it("shows branch creation form when clicking Start editing", async () => {
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => screen.getByText(/Start editing/))
    fireEvent.click(screen.getByText(/Start editing/))
    expect(screen.getByPlaceholderText("Update area factors")).toBeInTheDocument()
  })

  it("creates a branch on form submit", async () => {
    mockCreateBranch.mockResolvedValue({ branch: "pricing/test-user/my-feat" })
    mockGetStatus.mockResolvedValueOnce(mainStatus).mockResolvedValueOnce(branchStatus)

    render(<GitPanel {...defaultProps} />)
    await waitFor(() => screen.getByText(/Start editing/))
    fireEvent.click(screen.getByText(/Start editing/))

    const input = screen.getByPlaceholderText("Update area factors")
    fireEvent.change(input, { target: { value: "My new feature" } })
    fireEvent.click(screen.getByText("Create branch"))

    await waitFor(() => expect(mockCreateBranch).toHaveBeenCalledWith("My new feature"))
  })

  it("disables create button when description is empty", async () => {
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => screen.getByText(/Start editing/))
    fireEvent.click(screen.getByText(/Start editing/))
    expect(screen.getByText("Create branch")).toBeDisabled()
  })

  // ---------------------------------------------------------------------------
  // On an editable branch
  // ---------------------------------------------------------------------------

  it("shows changed files on active branch", async () => {
    mockGetStatus.mockResolvedValue(branchStatus)
    mockListBranches.mockResolvedValue({ current: branchStatus.branch, branches: defaultBranches.branches })

    render(<GitPanel {...defaultProps} />)
    await waitFor(() => {
      expect(screen.getByText("main.py")).toBeInTheDocument()
      expect(screen.getByText("config/banding/area.json")).toBeInTheDocument()
    })
  })

  it("shows Save progress and Submit buttons", async () => {
    mockGetStatus.mockResolvedValue(branchStatus)
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => {
      expect(screen.getByText("Save progress")).toBeInTheDocument()
      expect(screen.getByText("Submit for review")).toBeInTheDocument()
    })
  })

  it("calls gitSave when clicking Save progress", async () => {
    mockGetStatus.mockResolvedValue(branchStatus)
    mockGitSave.mockResolvedValue({
      commit_sha: "abc123",
      message: "Updated main",
      timestamp: "2026-03-06T10:00:00Z",
      pushed: false,
      push_error: null,
    })

    render(<GitPanel {...defaultProps} />)
    await waitFor(() => screen.getByText("Save progress"))
    fireEvent.click(screen.getByText("Save progress"))
    await waitFor(() => expect(mockGitSave).toHaveBeenCalledOnce())
  })

  it("calls gitSubmit and opens URL on Submit", async () => {
    mockGetStatus.mockResolvedValue(branchStatus)
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null)
    mockGitSubmit.mockResolvedValue({
      compare_url: "https://github.com/org/repo/compare/main...feat",
      branch: branchStatus.branch,
      pushed: true,
      push_error: null,
    })

    render(<GitPanel {...defaultProps} />)
    await waitFor(() => screen.getByText("Submit for review"))
    fireEvent.click(screen.getByText("Submit for review"))
    await waitFor(() => {
      expect(mockGitSubmit).toHaveBeenCalledOnce()
      expect(openSpy).toHaveBeenCalledWith("https://github.com/org/repo/compare/main...feat", "_blank")
    })

    openSpy.mockRestore()
  })

  // ---------------------------------------------------------------------------
  // Pull latest
  // ---------------------------------------------------------------------------

  it("shows pull latest when main is ahead", async () => {
    mockGetStatus.mockResolvedValue({ ...branchStatus, main_ahead: true, main_ahead_by: 3 })

    render(<GitPanel {...defaultProps} />)
    await waitFor(() => {
      expect(screen.getByText(/Main updated/)).toBeInTheDocument()
      expect(screen.getByText("Pull latest")).toBeInTheDocument()
    })
  })

  it("calls gitPull on click", async () => {
    mockGetStatus.mockResolvedValue({ ...branchStatus, main_ahead: true, main_ahead_by: 1 })
    mockGitPull.mockResolvedValue({ success: true, conflict: false, conflict_message: null, commits_pulled: 1 })

    render(<GitPanel {...defaultProps} />)
    await waitFor(() => screen.getByText("Pull latest"))
    fireEvent.click(screen.getByText("Pull latest"))
    await waitFor(() => expect(mockGitPull).toHaveBeenCalledOnce())
  })

  it("shows conflict error on pull conflict", async () => {
    mockGetStatus.mockResolvedValue({ ...branchStatus, main_ahead: true, main_ahead_by: 1 })
    mockGitPull.mockResolvedValue({ success: false, conflict: true, conflict_message: "Ask engineer for help", commits_pulled: 0 })

    render(<GitPanel {...defaultProps} />)
    await waitFor(() => screen.getByText("Pull latest"))
    fireEvent.click(screen.getByText("Pull latest"))
    await waitFor(() => expect(mockGitPull).toHaveBeenCalledOnce())
    // The error is set from the conflict_message — wait for it to appear
    await waitFor(() => expect(screen.getByText(/Ask engineer for help/)).toBeInTheDocument())
  })

  // ---------------------------------------------------------------------------
  // Version history
  // ---------------------------------------------------------------------------

  it("shows version history toggle on branch", async () => {
    mockGetStatus.mockResolvedValue(branchStatus)
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByText("Version history")).toBeInTheDocument())
  })

  it("loads history on toggle", async () => {
    mockGetStatus.mockResolvedValue(branchStatus)
    mockGetHistory.mockResolvedValue({
      entries: [
        { sha: "abc123", short_sha: "abc123", message: "Updated area table", timestamp: "2026-03-06T10:00:00Z", files_changed: ["main.py"] },
      ],
    })

    render(<GitPanel {...defaultProps} />)
    await waitFor(() => screen.getByText("Version history"))
    fireEvent.click(screen.getByText("Version history"))
    await waitFor(() => expect(screen.getByText("Updated area table")).toBeInTheDocument())
  })

  // ---------------------------------------------------------------------------
  // Branch switching
  // ---------------------------------------------------------------------------

  it("opens branch dropdown on click", async () => {
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => screen.getByText("main"))

    // Click the branch dropdown button
    const branchBtn = screen.getByRole("button", { name: /main/i })
    fireEvent.click(branchBtn)

    await waitFor(() => {
      expect(screen.getByText("Your branches")).toBeInTheDocument()
    })
  })

  it("calls switchBranch on branch item click", async () => {
    mockSwitchBranch.mockResolvedValue({ status: "ok", branch: "pricing/test-user/update-factors" })

    render(<GitPanel {...defaultProps} />)
    await waitFor(() => screen.getByText("main"))

    // Open dropdown
    const branchBtn = screen.getByRole("button", { name: /main/i })
    fireEvent.click(branchBtn)

    await waitFor(() => screen.getByText("update-factors"))
    fireEvent.click(screen.getByText("update-factors"))

    await waitFor(() => expect(mockSwitchBranch).toHaveBeenCalledWith("pricing/test-user/update-factors"))
  })

  // ---------------------------------------------------------------------------
  // Archive / Delete
  // ---------------------------------------------------------------------------

  it("shows archive and delete buttons on branch", async () => {
    mockGetStatus.mockResolvedValue(branchStatus)
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => {
      expect(screen.getByText("Archive")).toBeInTheDocument()
      expect(screen.getByText("Delete")).toBeInTheDocument()
    })
  })

  it("shows confirmation before archive", async () => {
    mockGetStatus.mockResolvedValue(branchStatus)
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => screen.getByText("Archive"))
    fireEvent.click(screen.getByText("Archive"))
    expect(screen.getByText(/You can restore it later/)).toBeInTheDocument()
  })

  it("shows confirmation before delete", async () => {
    mockGetStatus.mockResolvedValue(branchStatus)
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => screen.getByText("Delete"))
    // Click the Delete button in the branch management area (not the confirmation)
    const deleteButtons = screen.getAllByText("Delete")
    fireEvent.click(deleteButtons[0])
    expect(screen.getByText(/cannot be undone/)).toBeInTheDocument()
  })

  // ---------------------------------------------------------------------------
  // Read-only (someone else's branch)
  // ---------------------------------------------------------------------------

  it("shows read-only message on other's branch", async () => {
    mockGetStatus.mockResolvedValue(readOnlyBranchStatus)
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByText(/someone else's branch/)).toBeInTheDocument())
  })

  it("does not show save/submit on read-only branch", async () => {
    mockGetStatus.mockResolvedValue(readOnlyBranchStatus)
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => screen.getByText(/someone else's branch/))
    expect(screen.queryByText("Save progress")).not.toBeInTheDocument()
    expect(screen.queryByText("Submit for review")).not.toBeInTheDocument()
  })

  // ---------------------------------------------------------------------------
  // Error handling
  // ---------------------------------------------------------------------------

  it("shows error when API fails", async () => {
    mockGetStatus.mockRejectedValue(new Error("Network error"))

    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByText("Network error")).toBeInTheDocument())
  })

  it("error can be dismissed", async () => {
    mockGetStatus.mockRejectedValue(new Error("Test error"))

    render(<GitPanel {...defaultProps} />)
    await waitFor(() => screen.getByText("Test error"))
    fireEvent.click(screen.getByText("✕"))
    expect(screen.queryByText("Test error")).not.toBeInTheDocument()
  })

  // ---------------------------------------------------------------------------
  // U1: dropdown closes on outside click
  // ---------------------------------------------------------------------------

  it("closes branch dropdown when clicking outside", async () => {
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => screen.getByText("main"))

    // Open the branch dropdown
    const branchBtn = screen.getByRole("button", { name: /main/i })
    fireEvent.click(branchBtn)

    await waitFor(() => {
      expect(screen.getByText("Your branches")).toBeInTheDocument()
    })

    // Click outside the dropdown (on the document body)
    fireEvent.mouseDown(document.body)

    // Dropdown section headers should disappear
    await waitFor(() => {
      expect(screen.queryByText("Your branches")).not.toBeInTheDocument()
    })
  })
})
