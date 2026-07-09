import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import GitPanel from "../GitPanel"
import { clearGitPanelCaches } from "../gitPanelCache"
import useGitStore from "../../stores/useGitStore"
import type { GitWorkingBranchResponse } from "../../api/types"

// Perf behaviours of the Version Control panel:
//  (a) a byte-identical refresh applies NO state (row/rail identity preserved,
//      rail layout not recomputed),
//  (b) a changed payload does apply,
//  (c) a remount hydrates from the module-level session cache (no loading
//      flash) and reconciles when the revalidate lands changed data,
//  (d) milestone saves are cached by their immutable merge sha — re-expanding
//      never refetches, across remounts too.

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

// Counts rail layout recomputations: the layout memo only re-runs when the
// graph/rows state identities actually change, so a short-circuited refresh
// must leave this untouched. The factory wraps the real implementation.
const layoutSpy = vi.fn()
vi.mock("../gitgraph/layout", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../gitgraph/layout")>()
  return {
    ...actual,
    computeGitGraphLayout: (
      ...args: Parameters<typeof actual.computeGitGraphLayout>
    ) => {
      layoutSpy()
      return actual.computeGitGraphLayout(...args)
    },
  }
})

class MockResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const readyStatus: GitWorkingBranchResponse = {
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

// Frozen timestamps so structuredClone copies stay byte-identical across calls.
const T = "2026-07-08T10:00:00.000Z"

const milestonesPayload = {
  working_branch: "pricing-dev",
  entries: [
    { sha: "m1full", short_sha: "m1abc", message: "First milestone", timestamp: T, version_label: "1.0" },
    { sha: "m2full", short_sha: "m2def", message: "Second milestone", timestamp: T, version_label: null },
  ],
}

const graphTwoBranch = {
  working_branch: "pricing-dev",
  order: ["pricing-dev", "pricing/nick/spur"],
  branches: [
    {
      name: "pricing-dev", is_archived: false, is_current: true, tip_sha: "m1full",
      fork_point_sha: null, fork_of: null, forked_from: null,
      fork_source_sha: null, fork_credit_sha: null, truncated: false,
      entries: [
        { sha: "m1full", short_sha: "m1abc", message: "First milestone", timestamp: T, version_label: "1.0", is_root: false, parents: ["m2full", "s2"] },
        { sha: "m2full", short_sha: "m2def", message: "Second milestone", timestamp: T, version_label: null, is_root: true, parents: [] },
      ],
    },
    {
      name: "pricing/nick/spur", is_archived: false, is_current: false, tip_sha: "b1full",
      fork_point_sha: "m2full", fork_of: "pricing-dev", forked_from: null,
      fork_source_sha: null, fork_credit_sha: null, truncated: false,
      entries: [
        { sha: "b1full", short_sha: "b1abcd", message: "Spur milestone", timestamp: T, version_label: null, is_root: false, parents: ["m2full", "s9"] },
        { sha: "m2full", short_sha: "m2def", message: "Second milestone", timestamp: T, version_label: null, is_root: true, parents: [] },
      ],
    },
  ],
}

const emptyGraph = { working_branch: null, order: [], branches: [] }

/** Waits for an in-flight refresh cycle to finish (the toolbar refresh button
 *  is disabled while `loading`). */
const waitForRefreshSettled = async () => {
  await waitFor(() => expect(screen.getByTestId("git-panel-refresh")).not.toBeDisabled())
}

describe("GitPanel session cache + unchanged-payload short-circuit", () => {
  const defaultProps = { onClose: vi.fn() }

  beforeEach(() => {
    vi.clearAllMocks()
    clearGitPanelCaches()
    globalThis.ResizeObserver = MockResizeObserver as unknown as typeof ResizeObserver
    useGitStore.setState({ status: null, loading: false, modal: null, pendingAction: null, peekBranch: null, historyNonce: 0, commitNonce: 0, selectLatestSaveNonce: 0, selectSaveNonce: 0, selectSaveTarget: null, branchesExpandNonce: 0, moveTarget: null, comparison: null })
    mockGetWorkingBranch.mockResolvedValue(readyStatus)
    mockSetWorkingBranch.mockResolvedValue({})
    // Fresh, deep-equal object per call: proves the short-circuit works on
    // byte-identical CONTENT, not on accidental object identity.
    mockGetMilestones.mockImplementation(() => Promise.resolve(structuredClone(milestonesPayload)))
    mockGetPendingSaves.mockImplementation(() => Promise.resolve({ saves: [] }))
    mockGetMilestoneSaves.mockResolvedValue({ saves: [] })
    mockCreateWorkingBranch.mockResolvedValue({ working_branch: "x", moved: false, switched: false, last_save_sha: null })
    mockGetWorkingBranches.mockImplementation(() => Promise.resolve({ current: "pricing-dev", branches: [] }))
    mockGetGitGraph.mockImplementation(() => Promise.resolve(structuredClone(graphTwoBranch)))
  })

  afterEach(cleanup)

  it("(a) a byte-identical refresh applies no state: rail layout untouched, row nodes stable", async () => {
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-graph-rail").length).toBeGreaterThan(0))
    await waitForRefreshSettled()

    const layoutCallsAfterMount = layoutSpy.mock.calls.length
    const rowBefore = screen.getAllByTestId("git-panel-milestone")[0]

    // A save elsewhere bumps the history nonce → refresh refetches; every
    // payload comes back byte-identical (fresh clones, same content).
    useGitStore.getState().notifyHistoryChanged()
    await waitFor(() => expect(mockGetMilestones).toHaveBeenCalledTimes(2))
    await waitForRefreshSettled()

    // No rail layout recompute and the row DOM node survives untouched: the
    // short-circuit skipped every setState, so all memo inputs kept identity.
    expect(layoutSpy.mock.calls.length).toBe(layoutCallsAfterMount)
    expect(screen.getAllByTestId("git-panel-milestone")[0]).toBe(rowBefore)
  })

  it("(b) a changed payload does apply", async () => {
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone")).toHaveLength(2))
    await waitForRefreshSettled()
    const layoutCallsAfterMount = layoutSpy.mock.calls.length

    mockGetMilestones.mockImplementation(() => Promise.resolve({
      working_branch: "pricing-dev",
      entries: [
        { sha: "m3full", short_sha: "m3abc", message: "Third milestone", timestamp: T, version_label: null },
        ...structuredClone(milestonesPayload.entries),
      ],
    }))
    useGitStore.getState().notifyHistoryChanged()

    await waitFor(() => expect(screen.getByText("Third milestone")).toBeInTheDocument())
    expect(screen.getAllByTestId("git-panel-milestone")).toHaveLength(3)
    // The rows changed, so the rail layout DID recompute this time.
    await waitFor(() => expect(layoutSpy.mock.calls.length).toBeGreaterThan(layoutCallsAfterMount))
  })

  it("(c) a warm remount paints cached rows synchronously, then reconciles the changed revalidate", async () => {
    // Status present in the store (the toolbar loads it at startup) — it
    // resolves the cache key for the current working branch.
    useGitStore.setState({ status: readyStatus })

    const first = render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone")).toHaveLength(2))
    await waitForRefreshSettled()
    first.unmount()

    // Second mount: hold the milestones fetch open — hydration must not wait.
    let resolveMilestones!: (value: unknown) => void
    mockGetMilestones.mockImplementation(
      () => new Promise((resolve) => { resolveMilestones = resolve }),
    )
    render(<GitPanel {...defaultProps} />)

    // Immediately on first paint: cached rows, no loading placeholder.
    expect(screen.getAllByTestId("git-panel-milestone")).toHaveLength(2)
    expect(screen.getByText("First milestone")).toBeInTheDocument()
    expect(screen.queryByTestId("git-panel-loading")).not.toBeInTheDocument()

    // The revalidate lands CHANGED data → it swaps in.
    resolveMilestones({
      working_branch: "pricing-dev",
      entries: [
        { sha: "m9full", short_sha: "m9abc", message: "Rebuilt milestone", timestamp: T, version_label: null },
      ],
    })
    await waitFor(() => expect(screen.getByText("Rebuilt milestone")).toBeInTheDocument())
    expect(screen.queryByText("First milestone")).not.toBeInTheDocument()
    expect(screen.getAllByTestId("git-panel-milestone")).toHaveLength(1)
  })

  it("(c2) a warm remount with an UNCHANGED revalidate keeps the cached rows as-is", async () => {
    useGitStore.setState({ status: readyStatus })

    const first = render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone")).toHaveLength(2))
    await waitForRefreshSettled()
    first.unmount()

    render(<GitPanel {...defaultProps} />)
    expect(screen.getAllByTestId("git-panel-milestone")).toHaveLength(2)
    const rowBefore = screen.getAllByTestId("git-panel-milestone")[0]

    // Revalidate settles byte-identical → nothing re-applies.
    await waitForRefreshSettled()
    expect(screen.getAllByTestId("git-panel-milestone")[0]).toBe(rowBefore)
    expect(screen.getByText("First milestone")).toBeInTheDocument()
  })

  it("(d) milestone saves are sha-cached: re-expanding never refetches, across remounts too", async () => {
    mockGetGitGraph.mockResolvedValue(structuredClone(emptyGraph))
    mockGetMilestoneSaves.mockResolvedValue({
      saves: [{ sha: "s1", short_sha: "s1abc", message: "folded save", timestamp: T, files: [] }],
    })

    const first = render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone")).toHaveLength(2))

    // First expand fetches once.
    fireEvent.click(screen.getAllByTestId("git-panel-milestone")[0])
    await waitFor(() => expect(screen.getByTestId("git-panel-save")).toBeInTheDocument())
    expect(mockGetMilestoneSaves).toHaveBeenCalledTimes(1)
    expect(mockGetMilestoneSaves).toHaveBeenCalledWith("m1full")

    // Collapse + re-expand: served from the sha cache, no second call and no
    // "Loading saves…" placeholder.
    fireEvent.click(screen.getAllByTestId("git-panel-milestone")[0])
    await waitFor(() => expect(screen.queryByTestId("git-panel-save")).not.toBeInTheDocument())
    fireEvent.click(screen.getAllByTestId("git-panel-milestone")[0])
    expect(screen.getByTestId("git-panel-save")).toBeInTheDocument()
    expect(screen.queryByText("Loading saves…")).not.toBeInTheDocument()
    expect(mockGetMilestoneSaves).toHaveBeenCalledTimes(1)

    // The cache is module-level: a fresh mount's expansion also skips the fetch.
    first.unmount()
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone")).toHaveLength(2))
    fireEvent.click(screen.getAllByTestId("git-panel-milestone")[0])
    expect(screen.getByTestId("git-panel-save")).toBeInTheDocument()
    expect(mockGetMilestoneSaves).toHaveBeenCalledTimes(1)
  })

  it("(e) status landing after mount hydrates the warm cache the seed missed", async () => {
    // Warm the cache with a full mount + settled refresh, then unmount.
    useGitStore.setState({ status: readyStatus })
    const first = render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone")).toHaveLength(2))
    await waitForRefreshSettled()
    first.unmount()

    // Remount BEFORE the store knows the working branch (first status load
    // still in flight): the mount-time seed has no cache key. Hold both the
    // status fetch and the milestones revalidate open.
    useGitStore.setState({ status: null })
    mockGetWorkingBranch.mockImplementation(() => new Promise(() => {}))
    let resolveMilestones!: (value: unknown) => void
    mockGetMilestones.mockImplementation(
      () => new Promise((resolve) => { resolveMilestones = resolve }),
    )
    render(<GitPanel {...defaultProps} />)

    // Cold paint: the seed missed, so the loading state shows.
    expect(screen.queryAllByTestId("git-panel-milestone")).toHaveLength(0)
    expect(screen.getByTestId("git-panel-loading")).toBeInTheDocument()

    // Status resolves → the working branch is known → the cached rows paint
    // WITHOUT waiting for the still-held milestones fetch, and without
    // triggering any extra fetch.
    useGitStore.setState({ status: readyStatus })
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone")).toHaveLength(2))
    expect(screen.getByText("First milestone")).toBeInTheDocument()
    expect(screen.queryByTestId("git-panel-loading")).not.toBeInTheDocument()
    expect(mockGetMilestones).toHaveBeenCalledTimes(2)

    // The in-flight revalidate still lands and reconciles changed data.
    resolveMilestones({
      working_branch: "pricing-dev",
      entries: [
        { sha: "m9full", short_sha: "m9abc", message: "Rebuilt milestone", timestamp: T, version_label: null },
      ],
    })
    await waitFor(() => expect(screen.getByText("Rebuilt milestone")).toBeInTheDocument())
    expect(screen.queryByText("First milestone")).not.toBeInTheDocument()
    expect(screen.getAllByTestId("git-panel-milestone")).toHaveLength(1)
  })

  it("(e2) a late status load does not clobber rows a completed refresh already applied", async () => {
    // Warm the cache, then remount before status is known.
    useGitStore.setState({ status: readyStatus })
    const first = render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone")).toHaveLength(2))
    await waitForRefreshSettled()
    first.unmount()

    useGitStore.setState({ status: null })
    mockGetWorkingBranch.mockImplementation(() => new Promise(() => {}))
    mockGetMilestoneSaves.mockResolvedValue({
      saves: [{ sha: "s1", short_sha: "s1abc", message: "folded save", timestamp: T, files: [] }],
    })
    render(<GitPanel {...defaultProps} />)

    // The refresh wins the race: it resolves the branch server-side and lands
    // rows while the store still has no status. The user expands a milestone.
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone")).toHaveLength(2))
    await waitForRefreshSettled()
    fireEvent.click(screen.getAllByTestId("git-panel-milestone")[0])
    await waitFor(() => expect(screen.getByTestId("git-panel-save")).toBeInTheDocument())
    const rowBefore = screen.getAllByTestId("git-panel-milestone")[0]

    // Status lands late → hydration must do NOTHING: rows for this branch are
    // already applied, so the expansion and the row nodes survive untouched.
    useGitStore.setState({ status: readyStatus })
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone")).toHaveLength(2))
    expect(screen.getByTestId("git-panel-save")).toBeInTheDocument()
    expect(screen.getAllByTestId("git-panel-milestone")[0]).toBe(rowBefore)
  })

  it("peeking an uncached branch still clears the rows (no stale carry-over)", async () => {
    render(<GitPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getAllByTestId("git-panel-milestone")).toHaveLength(2))
    await waitForRefreshSettled()

    // Peek a branch never viewed this session, holding its fetch open: the
    // previous branch's rows must vanish (cold path unchanged by the cache).
    mockGetMilestones.mockImplementation(() => new Promise(() => {}))
    useGitStore.getState().setPeekBranch("pricing/nick/never-seen")
    await waitFor(() => expect(screen.queryAllByTestId("git-panel-milestone")).toHaveLength(0))
  })
})
