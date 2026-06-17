import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import GitPanel from "../GitPanel"
import useGitStore from "../../stores/useGitStore"

// The panel reads working-branch status from useGitStore (which calls
// getWorkingBranch) and fetches the milestone/ledger history directly.
const mockGetWorkingBranch = vi.fn()
const mockGetMilestones = vi.fn()
const mockGetMilestoneSaves = vi.fn()
const mockGetPendingSaves = vi.fn()
const mockCreateWorkingBranch = vi.fn()

vi.mock("../../api/client", () => ({
  getWorkingBranch: (...a: unknown[]) => mockGetWorkingBranch(...a),
  getMilestones: (...a: unknown[]) => mockGetMilestones(...a),
  getMilestoneSaves: (...a: unknown[]) => mockGetMilestoneSaves(...a),
  getPendingSaves: (...a: unknown[]) => mockGetPendingSaves(...a),
  // GitPanel now embeds <BranchManager/>, which loads working branches + prefs.
  getWorkingBranches: vi.fn(() => Promise.resolve({ current: null, branches: [] })),
  setWorkingBranch: vi.fn(),
  createWorkingBranch: (...a: unknown[]) => mockCreateWorkingBranch(...a),
  gitArchiveBranch: vi.fn(),
  gitDeleteBranch: vi.fn(),
  restoreBranch: vi.fn(),
  getGitPrefs: vi.fn(() => Promise.resolve({ skip_switch_confirm: false })),
  setGitPrefs: vi.fn(),
}))

const now = () => new Date().toISOString()

const readyStatus = {
  working_branch: "pricing-dev",
  current_branch: "pricing-dev-save",
  state: "ready",
  eligible_branches: [],
  identity_set: true,
  user_name: "Nick",
  user_email: "n@example.com",
  last_save_sha: "abc12345",
  errors: [],
}

const unsetStatus = {
  ...readyStatus,
  working_branch: null,
  state: "unset",
  last_save_sha: null,
}

const milestones = {
  working_branch: "pricing-dev",
  entries: [
    { sha: "m1full", short_sha: "m1abc", message: "First milestone", timestamp: now(), version_label: "1.0" },
    { sha: "m2full", short_sha: "m2def", message: "Second milestone", timestamp: now(), version_label: null },
  ],
}

describe("GitPanel", () => {
  const defaultProps = { onClose: vi.fn() }

  beforeEach(() => {
    vi.clearAllMocks()
    useGitStore.setState({ status: null, loading: false, modal: null, pendingAction: null, peekBranch: null })
    mockGetWorkingBranch.mockResolvedValue(readyStatus)
    mockGetMilestones.mockResolvedValue(milestones)
    mockGetPendingSaves.mockResolvedValue({ saves: [] })
    mockGetMilestoneSaves.mockResolvedValue({ saves: [] })
    mockCreateWorkingBranch.mockResolvedValue({ working_branch: "x", moved: false, switched: false, last_save_sha: null })
  })

  afterEach(cleanup)

  it("renders the panel", async () => {
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("git-panel")).toBeInTheDocument())
  })

  it("close button calls onClose", async () => {
    const onClose = vi.fn()
    render(<GitPanel onClose={onClose} />)
    await waitFor(() => expect(screen.getByTestId("git-panel")).toBeInTheDocument())
    fireEvent.click(screen.getByTitle("Close"))
    expect(onClose).toHaveBeenCalledOnce()
  })

  it("shows the working branch and ledger sha", async () => {
    render(<GitPanel {...defaultProps} />)
    await waitFor(() =>
      expect(screen.getByTestId("git-panel-working-branch")).toHaveTextContent("pricing-dev"),
    )
    expect(screen.getByTestId("git-panel-ledger-sha")).toHaveTextContent("abc12345")
  })

  it("shows the no-branch message when unset", async () => {
    mockGetWorkingBranch.mockResolvedValue(unsetStatus)
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("git-panel-no-branch")).toBeInTheDocument())
  })

  it("lists milestones with a version-label tag", async () => {
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone")).toHaveLength(2))
    expect(screen.getByText("First milestone")).toBeInTheDocument()
    expect(screen.getByTestId("git-panel-milestone-label")).toHaveTextContent("1.0")
  })

  it("shows the empty state with no milestones", async () => {
    mockGetMilestones.mockResolvedValue({ working_branch: "pricing-dev", entries: [] })
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("git-panel-empty")).toBeInTheDocument())
  })

  it("expands a milestone to its ledger saves on click", async () => {
    mockGetMilestoneSaves.mockResolvedValue({
      saves: [
        {
          sha: "s1", short_sha: "s1abc", message: "save one", timestamp: now(),
          files: [{ status: "M", path: "rating.py", old_path: null }],
        },
      ],
    })
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone").length).toBeGreaterThan(0))
    fireEvent.click(screen.getAllByTestId("git-panel-milestone")[0])
    await waitFor(() => expect(screen.getByTestId("git-panel-save")).toBeInTheDocument())
    expect(screen.getByText("save one")).toBeInTheDocument()
    expect(mockGetMilestoneSaves).toHaveBeenCalledWith("m1full")
  })

  it("renders a rename with old and new paths stacked", async () => {
    mockGetMilestoneSaves.mockResolvedValue({
      saves: [
        {
          sha: "s1", short_sha: "s1abc", message: "rename", timestamp: now(),
          files: [{ status: "R", path: "config/b.json", old_path: "config/a.json" }],
        },
      ],
    })
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone").length).toBeGreaterThan(0))
    fireEvent.click(screen.getAllByTestId("git-panel-milestone")[0])
    await waitFor(() => {
      const row = screen.getByTestId("git-panel-file")
      expect(row).toHaveTextContent("config/a.json") // old
      expect(row).toHaveTextContent("config/b.json") // new (stacked below)
    })
  })

  it("collapses an expanded milestone on a second click", async () => {
    mockGetMilestoneSaves.mockResolvedValue({
      saves: [{ sha: "s1", short_sha: "s1abc", message: "save one", timestamp: now(), files: [] }],
    })
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone").length).toBeGreaterThan(0))
    const first = screen.getAllByTestId("git-panel-milestone")[0]
    fireEvent.click(first)
    await waitFor(() => expect(screen.getByTestId("git-panel-save")).toBeInTheDocument())
    fireEvent.click(first)
    await waitFor(() => expect(screen.queryByTestId("git-panel-save")).not.toBeInTheDocument())
  })

  it("shows pending unmilestoned saves", async () => {
    mockGetPendingSaves.mockResolvedValue({
      saves: [{ sha: "p1", short_sha: "p1abc", message: "pending save", timestamp: now(), files: [] }],
    })
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("git-panel-pending")).toBeInTheDocument())
    expect(screen.getByTestId("git-panel-pending-save")).toHaveTextContent("pending save")
  })

  it("does not render the unwired v0 actions", async () => {
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("git-panel")).toBeInTheDocument())
    expect(screen.queryByText("Save progress")).not.toBeInTheDocument()
    expect(screen.queryByText("Submit for review")).not.toBeInTheDocument()
    expect(screen.queryByText("Pull latest")).not.toBeInTheDocument()
  })

  it("offers 'new branch from here' (+ move on the latest) on right-click", async () => {
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone").length).toBe(2))
    // latest milestone (index 0) → both create and create&move
    fireEvent.contextMenu(screen.getAllByTestId("git-panel-milestone")[0])
    await waitFor(() => expect(screen.getByTestId("git-panel-fork-menu")).toBeInTheDocument())
    expect(screen.getByTestId("git-panel-fork-here")).toBeInTheDocument()
    expect(screen.getByTestId("git-panel-fork-move")).toBeInTheDocument()
  })

  it("an older milestone offers create-only (no move)", async () => {
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone").length).toBe(2))
    fireEvent.contextMenu(screen.getAllByTestId("git-panel-milestone")[1]) // older
    await waitFor(() => expect(screen.getByTestId("git-panel-fork-menu")).toBeInTheDocument())
    expect(screen.queryByTestId("git-panel-fork-move")).not.toBeInTheDocument()
  })

  it("creates a branch from a chosen history point", async () => {
    mockCreateWorkingBranch.mockResolvedValue({
      working_branch: "spur", moved: false, switched: false, last_save_sha: null,
    })
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone").length).toBe(2))
    fireEvent.contextMenu(screen.getAllByTestId("git-panel-milestone")[0])
    fireEvent.click(screen.getByTestId("git-panel-fork-here"))
    await waitFor(() => expect(screen.getByTestId("git-panel-fork-name")).toBeInTheDocument())
    fireEvent.change(screen.getByTestId("git-panel-fork-name"), { target: { value: "spur" } })
    fireEvent.click(screen.getByTestId("git-panel-fork-create"))
    await waitFor(() =>
      expect(mockCreateWorkingBranch).toHaveBeenCalledWith("spur", { at: "m1full", move: false }),
    )
  })

  it("survives a milestones load failure without crashing", async () => {
    mockGetMilestones.mockRejectedValue(new Error("boom"))
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(mockGetMilestones).toHaveBeenCalled())
    expect(screen.getByTestId("git-panel")).toBeInTheDocument()
  })
})
