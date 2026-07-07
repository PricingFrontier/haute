import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import GitPanel from "../GitPanel"
import useGitStore from "../../stores/useGitStore"

// Mirror of GitPanel.test.tsx's client mock — the panel reads working-branch
// status from useGitStore and fetches milestone/ledger history directly.
const mockGetWorkingBranch = vi.fn()
const mockGetMilestones = vi.fn()
const mockGetMilestoneSaves = vi.fn()
const mockGetPendingSaves = vi.fn()
const mockCreateWorkingBranch = vi.fn()
const mockGetWorkingBranches = vi.fn()

vi.mock("../../api/client", () => ({
  getWorkingBranch: (...a: unknown[]) => mockGetWorkingBranch(...a),
  getMilestones: (...a: unknown[]) => mockGetMilestones(...a),
  getMilestoneSaves: (...a: unknown[]) => mockGetMilestoneSaves(...a),
  getPendingSaves: (...a: unknown[]) => mockGetPendingSaves(...a),
  getWorkingBranches: (...a: unknown[]) => mockGetWorkingBranches(...a),
  // Benign empty graph payload — the rail stays absent in these tests.
  getGitGraph: vi.fn(() => Promise.resolve({ working_branch: null, order: [], branches: [] })),
  setWorkingBranch: vi.fn(),
  createWorkingBranch: (...a: unknown[]) => mockCreateWorkingBranch(...a),
  gitArchiveBranch: vi.fn(),
  gitDeleteBranch: vi.fn(),
  restoreBranch: vi.fn(),
  getGitPrefs: vi.fn(() => Promise.resolve({ skip_switch_confirm: false })),
  setGitPrefs: vi.fn(),
  getGitRemotes: vi.fn(() => Promise.resolve({ remotes: [], working_branch: null })),
  gitPush: vi.fn(),
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

const milestones = {
  working_branch: "pricing-dev",
  entries: [
    { sha: "m1full", short_sha: "m1abc", message: "First milestone", timestamp: now(), version_label: "1.0" },
    { sha: "m2full", short_sha: "m2def", message: "Second milestone", timestamp: now(), version_label: null },
  ],
}

describe("GitPanel — uncovered fork/view/peek paths", () => {
  const defaultProps = { onClose: vi.fn() }

  beforeEach(() => {
    vi.clearAllMocks()
    useGitStore.setState({ status: null, loading: false, modal: null, pendingAction: null, peekBranch: null, historyNonce: 0, commitNonce: 0, selectLatestSaveNonce: 0, selectSaveNonce: 0, selectSaveTarget: null, branchesExpandNonce: 0, moveTarget: null, comparison: null })
    mockGetWorkingBranch.mockResolvedValue(readyStatus)
    mockGetMilestones.mockResolvedValue(milestones)
    mockGetPendingSaves.mockResolvedValue({ saves: [] })
    mockGetMilestoneSaves.mockResolvedValue({ saves: [] })
    mockCreateWorkingBranch.mockResolvedValue({ working_branch: "x", moved: false, switched: false, last_save_sha: null })
    mockGetWorkingBranches.mockResolvedValue({ current: "pricing-dev", branches: [] })
  })

  afterEach(cleanup)

  it("reloads the page after a fork that switches the working branch", async () => {
    // A fork whose creation switches the active working branch forces a full
    // reload so the editor re-mounts on the new branch (~259-262).
    const reload = vi.fn()
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, reload },
    })
    mockCreateWorkingBranch.mockResolvedValue({
      working_branch: "spur", moved: false, switched: true, last_save_sha: null,
    })

    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone").length).toBe(2))
    fireEvent.contextMenu(screen.getAllByTestId("git-panel-milestone")[0])
    fireEvent.click(screen.getByTestId("git-panel-fork-here"))
    await waitFor(() => expect(screen.getByTestId("git-panel-fork-name")).toBeInTheDocument())
    fireEvent.change(screen.getByTestId("git-panel-fork-name"), { target: { value: "spur" } })
    // Refresh should NOT be re-invoked once we commit to a reload.
    mockGetMilestones.mockClear()
    fireEvent.click(screen.getByTestId("git-panel-fork-create"))

    await waitFor(() => expect(reload).toHaveBeenCalledOnce())
    expect(mockGetMilestones).not.toHaveBeenCalled()
  })

  it("shows an error toast and does not crash when fork creation fails", async () => {
    // The fork submit error path surfaces a toast and clears the busy flag,
    // leaving the dialog in place (~264-269).
    mockCreateWorkingBranch.mockRejectedValue(new Error("branch exists"))

    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone").length).toBe(2))
    fireEvent.contextMenu(screen.getAllByTestId("git-panel-milestone")[0])
    fireEvent.click(screen.getByTestId("git-panel-fork-here"))
    await waitFor(() => expect(screen.getByTestId("git-panel-fork-name")).toBeInTheDocument())
    fireEvent.change(screen.getByTestId("git-panel-fork-name"), { target: { value: "spur" } })
    fireEvent.click(screen.getByTestId("git-panel-fork-create"))

    await waitFor(() => expect(mockCreateWorkingBranch).toHaveBeenCalled())
    // The dialog survives the failure (the busy flag is reset, not torn down).
    await waitFor(() => expect(screen.getByTestId("git-panel-fork-dialog")).toBeInTheDocument())
    expect(screen.getByTestId("git-panel")).toBeInTheDocument()
  })

  it("submits a fork with move:true via 'new branch & move work here'", async () => {
    // The create-&-move affordance on the latest milestone forks with move:true
    // and reports the moved-work success (~248-257).
    mockCreateWorkingBranch.mockResolvedValue({
      working_branch: "spur", moved: true, switched: false, last_save_sha: null,
    })

    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone").length).toBe(2))
    fireEvent.contextMenu(screen.getAllByTestId("git-panel-milestone")[0])
    fireEvent.click(screen.getByTestId("git-panel-fork-move"))
    await waitFor(() => expect(screen.getByTestId("git-panel-fork-name")).toBeInTheDocument())
    fireEvent.change(screen.getByTestId("git-panel-fork-name"), { target: { value: "spur" } })
    fireEvent.click(screen.getByTestId("git-panel-fork-create"))

    await waitFor(() =>
      expect(mockCreateWorkingBranch).toHaveBeenCalledWith("spur", { at: "m1full", move: true }),
    )
  })

  it("the view affordance opens the read-only comparison on that version (S11)", async () => {
    // The eye/view button opens the side-by-side comparison without switching
    // or moving (~741-765).
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone").length).toBe(2))
    expect(useGitStore.getState().comparison).toBeNull()

    fireEvent.click(screen.getAllByTestId("git-panel-view")[0])

    expect(useGitStore.getState().comparison).toEqual({ sha: "m1full", label: "1.0" })
  })

  it("while peeking, the row menu opens with view/move but no fork items (~292)", async () => {
    // The row menu ALWAYS opens now (never falls through to the browser menu),
    // but fork-from-history is only meaningful on the current branch's own
    // history, so while peeking the menu carries only the view/move items —
    // the fork-here / fork-&-move items are gated out.
    useGitStore.setState({ peekBranch: "some-other-branch" })

    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("git-panel-peeking")).toBeInTheDocument())
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone").length).toBe(2))

    // fireEvent returns false → the handler preventDefaulted the browser menu.
    const notDefaulted = fireEvent.contextMenu(screen.getAllByTestId("git-panel-milestone")[0])
    expect(notDefaulted).toBe(false)

    await waitFor(() => expect(screen.getByTestId("git-panel-fork-menu")).toBeInTheDocument())
    expect(screen.queryByTestId("git-panel-fork-here")).not.toBeInTheDocument()
    expect(screen.queryByTestId("git-panel-fork-move")).not.toBeInTheDocument()
    expect(screen.getByTestId("git-panel-menu-view")).toBeInTheDocument()
    expect(screen.getByTestId("git-panel-menu-move")).toBeInTheDocument()
  })
})
