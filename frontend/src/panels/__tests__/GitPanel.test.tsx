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

vi.mock("../../api/client", () => ({
  getWorkingBranch: (...a: unknown[]) => mockGetWorkingBranch(...a),
  getMilestones: (...a: unknown[]) => mockGetMilestones(...a),
  getMilestoneSaves: (...a: unknown[]) => mockGetMilestoneSaves(...a),
  getPendingSaves: (...a: unknown[]) => mockGetPendingSaves(...a),
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
    useGitStore.setState({ status: null, loading: false, modal: null, pendingAction: null })
    mockGetWorkingBranch.mockResolvedValue(readyStatus)
    mockGetMilestones.mockResolvedValue(milestones)
    mockGetPendingSaves.mockResolvedValue({ saves: [] })
    mockGetMilestoneSaves.mockResolvedValue({ saves: [] })
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

  it("renders a rename as old → new in a save's file list", async () => {
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
    await waitFor(() =>
      expect(screen.getByTestId("git-panel-file")).toHaveTextContent("config/a.json → config/b.json"),
    )
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

  it("survives a milestones load failure without crashing", async () => {
    mockGetMilestones.mockRejectedValue(new Error("boom"))
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(mockGetMilestones).toHaveBeenCalled())
    expect(screen.getByTestId("git-panel")).toBeInTheDocument()
  })
})
