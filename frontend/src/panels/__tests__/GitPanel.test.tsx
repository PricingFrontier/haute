import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor, within } from "@testing-library/react"
import GitPanel from "../GitPanel"
import useGitStore from "../../stores/useGitStore"
import useGraphStore from "../../stores/useGraphStore"
import useToastStore from "../../stores/useToastStore"

// The panel reads working-branch status from useGitStore (which calls
// getWorkingBranch) and fetches the milestone/ledger history directly.
const mockGetWorkingBranch = vi.fn()
const mockGetMilestones = vi.fn()
const mockGetMilestoneSaves = vi.fn()
const mockGetPendingSaves = vi.fn()
const mockCreateWorkingBranch = vi.fn()
const mockGetWorkingBranches = vi.fn()
const mockGetGitGraph = vi.fn()
const mockSetWorkingBranch = vi.fn()

vi.mock("../../api/client", () => ({
  getWorkingBranch: (...a: unknown[]) => mockGetWorkingBranch(...a),
  getMilestones: (...a: unknown[]) => mockGetMilestones(...a),
  getMilestoneSaves: (...a: unknown[]) => mockGetMilestoneSaves(...a),
  getPendingSaves: (...a: unknown[]) => mockGetPendingSaves(...a),
  // GitPanel now embeds <BranchManager/>, which loads working branches + prefs.
  getWorkingBranches: (...a: unknown[]) => mockGetWorkingBranches(...a),
  getGitGraph: (...a: unknown[]) => mockGetGitGraph(...a),
  setWorkingBranch: (...a: unknown[]) => mockSetWorkingBranch(...a),
  createWorkingBranch: (...a: unknown[]) => mockCreateWorkingBranch(...a),
  gitArchiveBranch: vi.fn(),
  gitDeleteBranch: vi.fn(),
  restoreBranch: vi.fn(),
  undeleteBranch: vi.fn(),
  getGitPrefs: vi.fn(() => Promise.resolve({ skip_switch_confirm: false })),
  setGitPrefs: vi.fn(),
  getGitRemotes: vi.fn(() => Promise.resolve({ remotes: [], working_branch: null })),
  gitPush: vi.fn(),
}))

// jsdom does not provide ResizeObserver; the rail overlay measures the
// milestones box with one (same idiom as DataPreview.test.tsx). jsdom rects
// are all zero-height, so overlay assertions here are presence-only — never
// geometry.
class MockResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

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

// Benign default for the rail's graph fetch: layout maps it to an empty rail,
// so every test that doesn't opt into a topology renders exactly as before.
const emptyGraph = { working_branch: null, order: [], branches: [] }

// Two-branch topology matching the `milestones` fixture: the viewed spine
// (m1 folds two saves; m2 is the root) plus a child forked at m2.
const graphTwoBranch = {
  working_branch: "pricing-dev",
  order: ["pricing-dev", "pricing/nick/spur"],
  branches: [
    {
      name: "pricing-dev", is_archived: false, is_current: true, tip_sha: "m1full",
      fork_point_sha: null, fork_of: null, forked_from: null,
      fork_source_sha: null, fork_credit_sha: null, truncated: false,
      entries: [
        { sha: "m1full", short_sha: "m1abc", message: "First milestone", timestamp: now(), version_label: "1.0", is_root: false, parents: ["m2full", "s2"] },
        { sha: "m2full", short_sha: "m2def", message: "Second milestone", timestamp: now(), version_label: null, is_root: true, parents: [] },
      ],
    },
    {
      name: "pricing/nick/spur", is_archived: false, is_current: false, tip_sha: "b1full",
      fork_point_sha: "m2full", fork_of: "pricing-dev", forked_from: null,
      fork_source_sha: null, fork_credit_sha: null, truncated: false,
      entries: [
        { sha: "b1full", short_sha: "b1abcd", message: "Spur milestone", timestamp: now(), version_label: null, is_root: false, parents: ["m2full", "s9"] },
        { sha: "m2full", short_sha: "m2def", message: "Second milestone", timestamp: now(), version_label: null, is_root: true, parents: [] },
      ],
    },
  ],
}

// Variant where the spur was spawned from the ledger save s2, which milestone
// m1 folds: the chip credits m1 while collapsed and moves onto the save row
// once m1 is expanded.
const graphSaveFork = {
  ...graphTwoBranch,
  branches: [
    graphTwoBranch.branches[0],
    {
      ...graphTwoBranch.branches[1],
      fork_source_sha: "s2",
      fork_credit_sha: "m1full",
    },
  ],
}

describe("GitPanel", () => {
  const defaultProps = { onClose: vi.fn() }

  beforeEach(() => {
    vi.clearAllMocks()
    globalThis.ResizeObserver = MockResizeObserver as unknown as typeof ResizeObserver
    useGitStore.setState({ status: null, loading: false, modal: null, pendingAction: null, peekBranch: null, historyNonce: 0, commitNonce: 0, selectLatestSaveNonce: 0, selectSaveNonce: 0, selectSaveTarget: null, branchesExpandNonce: 0, moveTarget: null, comparison: null })
    // Switches record undoable VC entries on the graph store's history stacks.
    useGraphStore.setState({ undoStack: [], redoStack: [], vcBusy: false })
    mockGetWorkingBranch.mockResolvedValue(readyStatus)
    mockSetWorkingBranch.mockResolvedValue({})
    mockGetMilestones.mockResolvedValue(milestones)
    mockGetPendingSaves.mockResolvedValue({ saves: [] })
    mockGetMilestoneSaves.mockResolvedValue({ saves: [] })
    mockCreateWorkingBranch.mockResolvedValue({ working_branch: "x", moved: false, switched: false, last_save_sha: null })
    mockGetWorkingBranches.mockResolvedValue({ current: "pricing-dev", branches: [] })
    mockGetGitGraph.mockResolvedValue(emptyGraph)
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

  it("titles the panel 'Version Control' (branch/commit live in the toolbar)", async () => {
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("git-panel")).toBeInTheDocument())
    expect(screen.getByText("Version Control")).toBeInTheDocument()
    // Branch + commit are no longer duplicated in the panel header.
    expect(screen.queryByTestId("git-panel-working-branch")).not.toBeInTheDocument()
    expect(screen.queryByTestId("git-panel-ledger-sha")).not.toBeInTheDocument()
  })

  it("lists milestones with a version-label tag", async () => {
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone")).toHaveLength(2))
    expect(screen.getByText("First milestone")).toBeInTheDocument()
    expect(screen.getByTestId("git-panel-milestone-label")).toHaveTextContent("1.0")
  })

  it("a milestone's move affordance requests a move to that version (P6)", async () => {
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone")).toHaveLength(2))
    expect(useGitStore.getState().moveTarget).toBeNull()

    fireEvent.click(screen.getAllByTestId("git-panel-move")[0])

    expect(useGitStore.getState().moveTarget).toEqual({ sha: "m1full", label: "1.0" })
  })

  it("shows an 'init' tag on the root (initial) milestone instead of a version label", async () => {
    mockGetMilestones.mockResolvedValue({
      working_branch: "pricing-dev",
      entries: [
        { sha: "m1full", short_sha: "m1abc", message: "Add factor", timestamp: now(), version_label: "1.0" },
        { sha: "root", short_sha: "root00", message: "Initial pricing project", timestamp: now(), version_label: null, is_root: true },
      ],
    })
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("git-panel-milestone-init")).toBeInTheDocument())
    expect(screen.getByTestId("git-panel-milestone-init")).toHaveTextContent("init")
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

  it("selects (highlights) a save only on click — nothing is auto-selected on open", async () => {
    mockGetPendingSaves.mockResolvedValue({
      saves: [
        { sha: "a", short_sha: "aabc", message: "a", timestamp: now(), files: [] },
        { sha: "b", short_sha: "babc", message: "b", timestamp: now(), files: [] },
      ],
    })
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-pending-save").length).toBe(2))
    // No auto-select on open (S38): selection is click-driven only.
    expect(screen.getAllByTestId("git-panel-pending-save")[0]).not.toHaveAttribute("data-selected")
    fireEvent.click(screen.getAllByTestId("git-panel-pending-save")[1])
    expect(screen.getAllByTestId("git-panel-pending-save")[1]).toHaveAttribute("data-selected")
  })

  it("selects the new milestone after a commit (commit nonce)", async () => {
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone").length).toBe(2))
    // Nothing selected on open.
    expect(screen.getAllByTestId("git-panel-milestone")[0]).not.toHaveAttribute("data-selected")
    // A milestone commit bumps the commit nonce → the top milestone is selected.
    useGitStore.getState().notifyMilestoneCommitted()
    await waitFor(() =>
      expect(screen.getAllByTestId("git-panel-milestone")[0]).toHaveAttribute("data-selected"),
    )
  })

  it("selects the latest out-of-version save when the toolbar SHA is clicked", async () => {
    mockGetPendingSaves.mockResolvedValue({
      saves: [
        { sha: "newest", short_sha: "new123", message: "newest", timestamp: now(), files: [] },
        { sha: "older", short_sha: "old123", message: "older", timestamp: now(), files: [] },
      ],
    })
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-pending-save").length).toBe(2))
    // The newest pending save (the ledger tip the toolbar SHA points at) is selected.
    useGitStore.getState().requestSelectLatestSave()
    await waitFor(() =>
      expect(screen.getAllByTestId("git-panel-pending-save")[0]).toHaveAttribute("data-selected"),
    )
    expect(screen.getAllByTestId("git-panel-pending-save")[1]).not.toHaveAttribute("data-selected")
  })

  it("expands the latest milestone and selects its newest save when no pending saves exist", async () => {
    mockGetPendingSaves.mockResolvedValue({ saves: [] })
    mockGetMilestoneSaves.mockResolvedValue({
      saves: [
        { sha: "m1newest", short_sha: "m1new", message: "newest in milestone", timestamp: now(), files: [] },
        { sha: "m1older", short_sha: "m1old", message: "older", timestamp: now(), files: [] },
      ],
    })
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone").length).toBe(2))
    useGitStore.getState().requestSelectLatestSave()
    // The latest milestone expands (both its saves render) and its newest save is selected.
    await waitFor(() => expect(screen.getAllByTestId("git-panel-save").length).toBe(2))
    expect(screen.getAllByTestId("git-panel-save")[0]).toHaveAttribute("data-selected")
    expect(screen.getAllByTestId("git-panel-save")[1]).not.toHaveAttribute("data-selected")
    expect(mockGetMilestoneSaves).toHaveBeenCalledWith("m1full")
  })

  it("selects a specific commit when asked (toolbar SHA while comparing, S11)", async () => {
    mockGetPendingSaves.mockResolvedValue({
      saves: [
        { sha: "newest", short_sha: "new123", message: "newest", timestamp: now(), files: [] },
        { sha: "older", short_sha: "old123", message: "older", timestamp: now(), files: [] },
      ],
    })
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-pending-save").length).toBe(2))
    // Ask for the OLDER save specifically (not the latest tip).
    useGitStore.getState().requestSelectSave("older")
    await waitFor(() =>
      expect(screen.getAllByTestId("git-panel-pending-save")[1]).toHaveAttribute("data-selected"),
    )
    expect(screen.getAllByTestId("git-panel-pending-save")[0]).not.toHaveAttribute("data-selected")
  })

  it("a save auto-refresh does NOT select the new milestone", async () => {
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone").length).toBe(2))
    mockGetMilestones.mockClear()
    // A plain save bumps the history nonce → refresh only, no selection move.
    useGitStore.getState().notifyHistoryChanged()
    await waitFor(() => expect(mockGetMilestones).toHaveBeenCalled())
    expect(screen.getAllByTestId("git-panel-milestone")[0]).not.toHaveAttribute("data-selected")
  })

  it("auto-refreshes on a save/commit without collapsing an expanded milestone", async () => {
    mockGetMilestoneSaves.mockResolvedValue({
      saves: [{ sha: "s9", short_sha: "s9abc", message: "kept", timestamp: now(), files: [] }],
    })
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone").length).toBe(2))
    fireEvent.click(screen.getAllByTestId("git-panel-milestone")[0]) // expand it
    await waitFor(() => expect(screen.getByTestId("git-panel-save")).toBeInTheDocument())
    mockGetMilestones.mockClear()
    // A save elsewhere bumps the history nonce → the panel re-fetches…
    useGitStore.getState().notifyHistoryChanged()
    await waitFor(() => expect(mockGetMilestones).toHaveBeenCalled())
    // …but the milestone the user opened stays expanded.
    expect(screen.getByTestId("git-panel-save")).toBeInTheDocument()
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

  it("back-links a spawning milestone to its branch and peeks on click", async () => {
    mockGetWorkingBranches.mockResolvedValue({
      current: "pricing-dev",
      branches: [
        {
          name: "pricing/nick/spur", is_current: false, is_archived: false,
          has_unmerged_saves: false, has_uncommitted_changes: false,
          forked_from: "m1full",
        },
      ],
    })
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("git-panel-fork-link")).toBeInTheDocument())
    expect(screen.getByTestId("git-panel-fork-link")).toHaveTextContent("spur")
    fireEvent.click(screen.getByTestId("git-panel-fork-link"))
    // peeking the spawned branch (view, not switch) → the peek banner appears
    await waitFor(() => expect(screen.getByTestId("git-panel-peeking")).toBeInTheDocument())
  })

  it("survives a milestones load failure without crashing", async () => {
    mockGetMilestones.mockRejectedValue(new Error("boom"))
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(mockGetMilestones).toHaveBeenCalled())
    expect(screen.getByTestId("git-panel")).toBeInTheDocument()
  })

  // ── Graph rail ─────────────────────────────────────────────────────────

  it("renders the graph rail from the graph payload: dots, stubs, chips", async () => {
    mockGetGitGraph.mockResolvedValue(graphTwoBranch)
    mockGetPendingSaves.mockResolvedValue({
      saves: [{ sha: "p1", short_sha: "p1abc", message: "pending save", timestamp: now(), files: [] }],
    })
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-graph-dot")).toHaveLength(3))

    const dots = screen.getAllByTestId("git-graph-dot")
    const milestoneDots = dots.filter((d) => d.getAttribute("data-kind") === "milestone")
    expect(milestoneDots).toHaveLength(2)
    // Both spine dots sit on the viewed lane with the order-derived colour.
    expect(milestoneDots[0]).toHaveAttribute("data-sha", "m1full")
    expect(milestoneDots[0]).toHaveAttribute("data-lane", "0")
    expect(milestoneDots[0]).toHaveAttribute("data-branch", "pricing-dev")
    expect(milestoneDots[0]).toHaveAttribute("data-color-index", "0")
    // The pending save renders as a hollow dot on the viewed lane.
    const pendingDot = dots.find((d) => d.getAttribute("data-kind") === "pending")
    expect(pendingDot).toHaveAttribute("data-sha", "p1")
    expect(pendingDot).toHaveAttribute("data-lane", "0")
    // The child forked at m2 departs as a spawn stub (no lane of its own) with
    // a peekable top chip.
    const chip = screen.getByTestId("git-graph-branch-chip")
    expect(chip).toHaveAttribute("data-branch", "pricing/nick/spur")
    const stub = screen.getByTestId("git-graph-spawn")
    expect(stub).toHaveAttribute("data-branch", "pricing/nick/spur")
    expect(stub).toHaveAttribute("data-slot", "0")
    const edgeKinds = screen.getAllByTestId("git-graph-edge").map((e) => e.getAttribute("data-edge-kind"))
    expect(edgeKinds).toContain("spawn")
    expect(edgeKinds).toContain("spine")
    expect(edgeKinds).not.toContain("fork") // the v1 departure lanes are gone
    expect(screen.getAllByTestId("git-graph-rail").length).toBeGreaterThan(0)
  })

  it("the magnifier toggles a fold-carrying milestone open and closed (data-expanded)", async () => {
    mockGetGitGraph.mockResolvedValue(graphTwoBranch)
    mockGetMilestoneSaves.mockResolvedValue({
      saves: [{ sha: "s2", short_sha: "s2abc", message: "folded save", timestamp: now(), files: [] }],
    })
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("git-graph-magnifier")).toBeInTheDocument())
    // Only m1 qualifies: it folds saves (2 parents); m2 is zero-fold AND the
    // window-final row. Collapsed → zoom-in, no data-expanded.
    expect(screen.getAllByTestId("git-graph-magnifier")).toHaveLength(1)
    expect(screen.getByTestId("git-graph-magnifier")).toHaveAttribute("data-expands", "m1full")
    expect(screen.getByTestId("git-graph-magnifier")).not.toHaveAttribute("data-expanded")

    fireEvent.click(screen.getByTestId("git-graph-magnifier"))
    // The click expands (stopPropagation keeps the row button from toggling it
    // straight back) …
    await waitFor(() => expect(screen.getByTestId("git-panel-save")).toBeInTheDocument())
    expect(mockGetMilestoneSaves).toHaveBeenCalledWith("m1full")
    // … and the magnifier flips to the zoom-out (collapse) affordance.
    await waitFor(() =>
      expect(screen.getByTestId("git-graph-magnifier")).toHaveAttribute("data-expanded", "true"),
    )

    // Second click folds the saves back away.
    fireEvent.click(screen.getByTestId("git-graph-magnifier"))
    await waitFor(() => expect(screen.queryByTestId("git-panel-save")).not.toBeInTheDocument())
    expect(screen.getByTestId("git-graph-magnifier")).not.toHaveAttribute("data-expanded")
  })

  it("degrades to no rail when the graph fetch fails: list intact, no toast", async () => {
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
    mockGetGitGraph.mockRejectedValue(new Error("boom"))
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone")).toHaveLength(2))
    expect(screen.queryAllByTestId("git-graph-rail")).toHaveLength(0)
    expect(screen.queryByTestId("git-graph-header")).not.toBeInTheDocument()
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it("right-clicking a milestone dot opens the commit menu: View / Move fire the store actions", async () => {
    mockGetGitGraph.mockResolvedValue(graphTwoBranch)
    render(<GitPanel {...defaultProps} />)
    await waitFor(() =>
      expect(
        screen.getAllByTestId("git-graph-dot").filter((d) => d.getAttribute("data-kind") === "milestone"),
      ).toHaveLength(2),
    )
    const dot = screen
      .getAllByTestId("git-graph-dot")
      .find((d) => d.getAttribute("data-sha") === "m1full")!

    fireEvent.contextMenu(dot)
    await waitFor(() => expect(screen.getByTestId("git-graph-dot-menu")).toBeInTheDocument())

    // View side-by-side → opens the read-only comparison with the row's label.
    fireEvent.click(screen.getByTestId("git-graph-dot-menu-view"))
    expect(useGitStore.getState().comparison).toEqual({ sha: "m1full", label: "1.0" })
    expect(screen.queryByTestId("git-graph-dot-menu")).not.toBeInTheDocument()

    // Move to this version → requests the gated move.
    fireEvent.contextMenu(dot)
    await waitFor(() => expect(screen.getByTestId("git-graph-dot-menu")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("git-graph-dot-menu-move"))
    expect(useGitStore.getState().moveTarget).toEqual({ sha: "m1full", label: "1.0" })
    expect(screen.queryByTestId("git-graph-dot-menu")).not.toBeInTheDocument()
    // The context menu never toggled the row's expansion.
    expect(mockGetMilestoneSaves).not.toHaveBeenCalled()
  })

  it("the lane menu's Switch performs an in-app switch and records an undoable VC entry", async () => {
    // Peek the spur so its lane 0 line is a switchable (non-current) branch.
    useGitStore.setState({ peekBranch: "pricing/nick/spur" })
    mockGetGitGraph.mockResolvedValue(graphTwoBranch)
    mockGetMilestones.mockResolvedValue({
      working_branch: "pricing-dev",
      entries: [
        { sha: "b1full", short_sha: "b1abcd", message: "Spur milestone", timestamp: now(), version_label: null },
        { sha: "m2full", short_sha: "m2def", message: "Second milestone", timestamp: now(), version_label: null, is_root: true },
      ],
    })
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-graph-rail").length).toBeGreaterThan(0))

    const spurEdge = screen
      .getAllByTestId("git-graph-edge")
      .find((e) => e.getAttribute("data-branch") === "pricing/nick/spur")!
    fireEvent.contextMenu(spurEdge)
    await waitFor(() => expect(screen.getByTestId("git-graph-lane-menu")).toBeInTheDocument())

    fireEvent.click(screen.getByTestId("git-graph-lane-menu-switch"))
    // NB: asserted on the branch argument only — performSwitch currently
    // omits the client's required `create` flag (a known source-side type
    // error); this pin survives that fix.
    await waitFor(() => expect(mockSetWorkingBranch).toHaveBeenCalled())
    expect(mockSetWorkingBranch.mock.calls[0][0]).toBe("pricing/nick/spur")
    // No page reload: the switch lands in-app and records its inverse.
    await waitFor(() => expect(useGraphStore.getState().undoStack).toHaveLength(1))
    const entry = useGraphStore.getState().undoStack[0]
    expect(entry).toMatchObject({ kind: "vc", label: "switch to pricing/nick/spur" })
    // The panel returns to the (new) current branch's view.
    await waitFor(() => expect(useGitStore.getState().peekBranch).toBeNull())
  })

  it("the lane menu disables Switch and View on the already-current, already-viewed lane", async () => {
    mockGetGitGraph.mockResolvedValue(graphSaveFork)
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-graph-rail").length).toBeGreaterThan(0))

    // Viewing the working branch: its own lane line still opens the menu, but
    // both actions are no-ops there and must be disabled.
    const laneEdge = screen
      .getAllByTestId("git-graph-edge")
      .find((e) => e.getAttribute("data-branch") === "pricing-dev")!
    fireEvent.contextMenu(laneEdge)
    await waitFor(() => expect(screen.getByTestId("git-graph-lane-menu")).toBeInTheDocument())
    expect(screen.getByTestId("git-graph-lane-menu")).toHaveTextContent("pricing-dev")
    expect(screen.getByTestId("git-graph-lane-menu-switch")).toBeDisabled()
    expect(screen.getByTestId("git-graph-lane-menu-view")).toBeDisabled()
  })

  it("derives in-row spawn chips from the graph even without forks.json backing (twin-a)", async () => {
    // getWorkingBranches reports NO forked_from entries (a branch created in
    // another clone) — the graph payload alone must still chip the row.
    mockGetGitGraph.mockResolvedValue(graphTwoBranch)
    mockGetWorkingBranches.mockResolvedValue({ current: "pricing-dev", branches: [] })
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("git-panel-fork-link")).toBeInTheDocument())
    const chip = screen.getByTestId("git-panel-fork-link")
    expect(chip).toHaveTextContent("spur")
    // The chip anchors on the fork-point milestone row (m2).
    const rows = screen.getAllByTestId("git-panel-milestone")
    expect(within(rows[1]).getByTestId("git-panel-fork-link")).toBe(chip)

    fireEvent.click(chip)
    await waitFor(() => expect(screen.getByTestId("git-panel-peeking")).toBeInTheDocument())
    expect(screen.getByTestId("git-panel-peeking")).toHaveTextContent("pricing/nick/spur")
  })

  it("moves the spawn chip from the credit milestone onto the source save row when expanded", async () => {
    mockGetGitGraph.mockResolvedValue(graphSaveFork)
    mockGetMilestoneSaves.mockResolvedValue({
      saves: [{ sha: "s2", short_sha: "s2abc", message: "spawned here", timestamp: now(), files: [] }],
    })
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("git-panel-fork-link")).toBeInTheDocument())
    // Collapsed: the credit milestone (m1, which folds s2) wears the chip.
    const milestoneRows = screen.getAllByTestId("git-panel-milestone")
    expect(within(milestoneRows[0]).getByTestId("git-panel-fork-link")).toHaveTextContent("spur")

    fireEvent.click(milestoneRows[0]) // expand m1
    await waitFor(() => expect(screen.getByTestId("git-panel-save")).toBeInTheDocument())

    // Expanded: the chip sits on the source save row, not the milestone.
    await waitFor(() =>
      expect(within(screen.getByTestId("git-panel-save")).getByTestId("git-panel-fork-link")).toBeInTheDocument(),
    )
    expect(
      within(screen.getAllByTestId("git-panel-milestone")[0]).queryByTestId("git-panel-fork-link"),
    ).not.toBeInTheDocument()
  })

  it("clicking a top branch chip peeks that branch (A-15)", async () => {
    mockGetGitGraph.mockResolvedValue(graphTwoBranch)
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("git-graph-branch-chip")).toBeInTheDocument())

    fireEvent.click(screen.getByTestId("git-graph-branch-chip"))

    await waitFor(() => expect(screen.getByTestId("git-panel-peeking")).toBeInTheDocument())
    expect(screen.getByTestId("git-panel-peeking")).toHaveTextContent("pricing/nick/spur")
  })

  it("drops the rail during a peek's in-flight window instead of mislabelling old rows", async () => {
    mockGetGitGraph.mockResolvedValue(graphTwoBranch)
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-graph-rail").length).toBeGreaterThan(0))

    // Peek the spur, holding its milestones fetch open: the loaded rows still
    // belong to pricing-dev, so the rail must render nothing in the window.
    let resolveMilestones!: (value: unknown) => void
    mockGetMilestones.mockImplementation(
      () => new Promise((resolve) => { resolveMilestones = resolve }),
    )
    fireEvent.click(screen.getByTestId("git-graph-branch-chip"))
    await waitFor(() =>
      expect(mockGetMilestones).toHaveBeenCalledWith(50, "pricing/nick/spur"),
    )
    expect(screen.queryAllByTestId("git-graph-rail")).toHaveLength(0)

    resolveMilestones({
      working_branch: "pricing-dev",
      entries: [
        { sha: "b1full", short_sha: "b1abcd", message: "Spur milestone", timestamp: now(), version_label: null },
        { sha: "m2full", short_sha: "m2def", message: "Second milestone", timestamp: now(), version_label: null, is_root: true },
      ],
    })
    // The landed rows are the spur's — the rail comes back.
    await waitFor(() => expect(screen.getAllByTestId("git-graph-rail").length).toBeGreaterThan(0))
  })

  // ── Rail mode: legacy row behaviours with the graph present ─────────────
  // The rail restructures the row DOM (rail cell as first flex child, padding
  // moved onto the content side); re-run the highest-value list behaviours
  // under a real topology to pin that the affordances still fire.
  describe("with the graph rail present", () => {
    beforeEach(() => {
      mockGetGitGraph.mockResolvedValue(graphTwoBranch)
    })

    it("opens the fork menu on right-click", async () => {
      render(<GitPanel {...defaultProps} />)
      await waitFor(() => expect(screen.getAllByTestId("git-graph-rail").length).toBeGreaterThan(0))
      fireEvent.contextMenu(screen.getAllByTestId("git-panel-milestone")[0])
      await waitFor(() => expect(screen.getByTestId("git-panel-fork-menu")).toBeInTheDocument())
      expect(screen.getByTestId("git-panel-fork-here")).toBeInTheDocument()
      expect(screen.getByTestId("git-panel-fork-move")).toBeInTheDocument()
    })

    it("Eye and Move affordances fire on a milestone row", async () => {
      render(<GitPanel {...defaultProps} />)
      await waitFor(() => expect(screen.getAllByTestId("git-graph-rail").length).toBeGreaterThan(0))
      fireEvent.click(screen.getAllByTestId("git-panel-view")[0])
      expect(useGitStore.getState().comparison).toEqual({ sha: "m1full", label: "1.0" })
      fireEvent.click(screen.getAllByTestId("git-panel-move")[0])
      expect(useGitStore.getState().moveTarget).toEqual({ sha: "m1full", label: "1.0" })
    })

    it("row click toggles a milestone's expansion", async () => {
      mockGetMilestoneSaves.mockResolvedValue({
        saves: [{ sha: "s2", short_sha: "s2abc", message: "folded save", timestamp: now(), files: [] }],
      })
      render(<GitPanel {...defaultProps} />)
      await waitFor(() => expect(screen.getAllByTestId("git-graph-rail").length).toBeGreaterThan(0))
      const first = screen.getAllByTestId("git-panel-milestone")[0]
      fireEvent.click(first)
      await waitFor(() => expect(screen.getByTestId("git-panel-save")).toBeInTheDocument())
      fireEvent.click(first)
      await waitFor(() => expect(screen.queryByTestId("git-panel-save")).not.toBeInTheDocument())
    })

    it("selects a pending-save row on click", async () => {
      mockGetPendingSaves.mockResolvedValue({
        saves: [
          { sha: "p1", short_sha: "p1abc", message: "pending", timestamp: now(), files: [] },
          { sha: "p2", short_sha: "p2abc", message: "older pending", timestamp: now(), files: [] },
        ],
      })
      render(<GitPanel {...defaultProps} />)
      await waitFor(() => expect(screen.getAllByTestId("git-panel-pending-save")).toHaveLength(2))
      expect(screen.getAllByTestId("git-panel-pending-save")[1]).not.toHaveAttribute("data-selected")
      fireEvent.click(screen.getAllByTestId("git-panel-pending-save")[1])
      expect(screen.getAllByTestId("git-panel-pending-save")[1]).toHaveAttribute("data-selected")
    })
  })
})
