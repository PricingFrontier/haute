import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, cleanup, waitFor, act } from "@testing-library/react"
import GitPanel from "../GitPanel"
import { clearGitPanelCaches, readBranchHistory } from "../gitPanelCache"
import useGitStore, { resetGitStatusRequestForTests } from "../../stores/useGitStore"
import { resetGitBranchLoaderForTests } from "../../stores/gitBranchLoader"

// Regression pin for the refresh() generation guard: a refresh captured for
// the PREVIOUS branch (its fetches still in flight when a peek retargets the
// panel) must not land its rows over the newer branch's rows when it finally
// resolves. Same api-client mocking conventions as GitPanel.test.tsx.
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

// The previous (working) branch's history — the STALE payload.
const devMilestones = {
  working_branch: "pricing-dev",
  entries: [
    { sha: "m1full", short_sha: "m1abc", message: "Dev milestone", timestamp: now(), version_label: "1.0" },
  ],
}
const devPending = {
  saves: [{ sha: "p1", short_sha: "p1abc", message: "dev pending save", timestamp: now(), files: [] }],
}

// The peeked branch's history — the payload that must stay on screen.
const spurMilestones = {
  working_branch: "pricing-dev",
  entries: [
    { sha: "b1full", short_sha: "b1abcd", message: "Spur milestone", timestamp: now(), version_label: null },
  ],
}

const emptyGraph = { working_branch: null, order: [], branches: [] }

describe("GitPanel stale refresh (generation guard)", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clearGitPanelCaches()
    resetGitBranchLoaderForTests()
    globalThis.ResizeObserver = MockResizeObserver as unknown as typeof ResizeObserver
    resetGitStatusRequestForTests()
    useGitStore.setState({ status: null, loading: false, statusError: null, branches: [], branchesLoaded: false, branchesLoading: false, branchesError: null, modal: null, pendingAction: null, peekBranch: null, historyNonce: 0, commitNonce: 0, branchesExpandNonce: 0, moveTarget: null, comparison: null })
    mockGetWorkingBranch.mockResolvedValue(readyStatus)
    mockGetMilestoneSaves.mockResolvedValue({ saves: [] })
    mockGetWorkingBranches.mockResolvedValue({ current: "pricing-dev", branches: [] })
    mockGetGitGraph.mockResolvedValue(emptyGraph)
  })

  afterEach(() => {
    cleanup()
    resetGitStatusRequestForTests()
  })

  it("peeking triggers one refresh without replaying active nonce effects", async () => {
    useGitStore.setState({ historyNonce: 1, commitNonce: 1 })
    mockGetMilestones.mockImplementation((_n: number, branch?: string | null) =>
      Promise.resolve(branch === "pricing/nick/spur" ? spurMilestones : devMilestones),
    )
    mockGetPendingSaves.mockResolvedValue({ saves: [] })

    render(<GitPanel onClose={vi.fn()} />)
    // Mount refresh + the already-active history and commit nonces.
    await waitFor(() => expect(mockGetMilestones).toHaveBeenCalledTimes(3))

    act(() => useGitStore.getState().setPeekBranch("pricing/nick/spur"))

    await waitFor(() =>
      expect(mockGetMilestones).toHaveBeenCalledWith(50, "pricing/nick/spur"),
    )
    // Changing only the peek target must not replay the history/commit effects
    // through a changed refresh callback identity.
    expect(mockGetMilestones).toHaveBeenCalledTimes(4)
  })

  it("an earlier slow refresh resolving after a later fast one does not overwrite the later branch's rows", async () => {
    // The initial refresh (working branch, viewBranch null) hangs; the peeked
    // branch's refresh resolves immediately.
    let resolveDevMilestones!: (value: unknown) => void
    let resolveDevPending!: (value: unknown) => void
    mockGetMilestones.mockImplementation((_n: number, branch?: string | null) =>
      branch === "pricing/nick/spur"
        ? Promise.resolve(spurMilestones)
        : new Promise((resolve) => { resolveDevMilestones = resolve }),
    )
    mockGetPendingSaves.mockImplementation((branch?: string | null) =>
      branch === "pricing/nick/spur"
        ? Promise.resolve({ saves: [] })
        : new Promise((resolve) => { resolveDevPending = resolve }),
    )

    render(<GitPanel onClose={vi.fn()} />)
    await waitFor(() => expect(mockGetMilestones).toHaveBeenCalledWith(50, null))

    // Peek the spur while the working branch's refresh is still in flight —
    // the later (spur) refresh lands first.
    act(() => useGitStore.getState().setPeekBranch("pricing/nick/spur"))
    await waitFor(() => expect(mockGetMilestones).toHaveBeenCalledWith(50, "pricing/nick/spur"))
    await waitFor(() => expect(screen.getByText("Spur milestone")).toBeInTheDocument())

    // NOW the earlier refresh resolves, carrying the previous branch's rows.
    // Flush its whole application path (Promise.all → graph race → setState).
    await act(async () => {
      resolveDevMilestones(devMilestones)
      resolveDevPending(devPending)
      await new Promise((r) => setTimeout(r, 0))
      await new Promise((r) => setTimeout(r, 0))
    })

    // The later branch's rows stay on screen; the stale rows never land.
    expect(screen.getByText("Spur milestone")).toBeInTheDocument()
    expect(screen.queryByText("Dev milestone")).not.toBeInTheDocument()
    expect(screen.queryByText("dev pending save")).not.toBeInTheDocument()
    expect(screen.queryByTestId("git-panel-pending")).not.toBeInTheDocument()
    // The stale response is not snapshotted into the session cache either —
    // only the refresh that applied wrote its branch's entry.
    expect(readBranchHistory("pricing-dev")).toBeUndefined()
    expect(readBranchHistory("pricing/nick/spur")).toBeDefined()
  })

  it("a superseded refresh settling does not clear the newer refresh's loading state", async () => {
    // Both refreshes hang; the FIRST (working branch) then resolves while the
    // second (spur) is still in flight — the spinner must survive it.
    let resolveDevMilestones!: (value: unknown) => void
    mockGetMilestones.mockImplementation((_n: number, branch?: string | null) =>
      branch === "pricing/nick/spur"
        ? new Promise(() => {})
        : new Promise((resolve) => { resolveDevMilestones = resolve }),
    )
    mockGetPendingSaves.mockResolvedValue({ saves: [] })

    render(<GitPanel onClose={vi.fn()} />)
    await waitFor(() => expect(mockGetMilestones).toHaveBeenCalledWith(50, null))

    act(() => useGitStore.getState().setPeekBranch("pricing/nick/spur"))
    await waitFor(() => expect(mockGetMilestones).toHaveBeenCalledWith(50, "pricing/nick/spur"))
    await waitFor(() => expect(screen.getByTestId("git-panel-loading")).toBeInTheDocument())

    await act(async () => {
      resolveDevMilestones(devMilestones)
      await new Promise((r) => setTimeout(r, 0))
      await new Promise((r) => setTimeout(r, 0))
    })

    // Still loading the spur's history — the stale refresh neither painted its
    // rows nor killed the spinner.
    expect(screen.getByTestId("git-panel-loading")).toBeInTheDocument()
    expect(screen.queryByText("Dev milestone")).not.toBeInTheDocument()
  })
})
