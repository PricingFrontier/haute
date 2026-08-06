import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor, act } from "@testing-library/react"
import BranchManager from "../BranchManager"
import useGitStore from "../../stores/useGitStore"
import useGraphStore from "../../stores/useGraphStore"

const mockGetWorkingBranches = vi.fn()
const mockSetWorkingBranch = vi.fn()
const mockCreateWorkingBranch = vi.fn()
const mockArchive = vi.fn()
const mockDelete = vi.fn()
const mockRestore = vi.fn()
const mockUndelete = vi.fn()
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
  undeleteBranch: (...a: unknown[]) => mockUndelete(...a),
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
    useGitStore.setState({
      status: null,
      loading: false,
      statusError: null,
      branches: [],
      branchesLoaded: false,
      branchesLoading: false,
      branchesError: null,
      modal: null,
      pendingAction: null,
      peekBranch: null,
      historyNonce: 0,
      commitNonce: 0,
    })
    // In-app branch ops record undoable VC entries on the graph store.
    useGraphStore.setState({ undoStack: [], redoStack: [], vcBusy: false, dirty: false })
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

  it("guards a dirty switch after the ordinary confirmation until it is discarded", async () => {
    useGraphStore.setState({ dirty: true })
    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-switch")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("branch-manager-switch"))
    fireEvent.click(await screen.findByTestId("branch-manager-confirm-switch-go"))
    expect(await screen.findByTestId("git-navigation-confirm")).toBeInTheDocument()
    fireEvent.click(screen.getByTestId("git-navigation-cancel"))
    expect(mockSetWorkingBranch).not.toHaveBeenCalled()

    fireEvent.click(screen.getByTestId("branch-manager-switch"))
    fireEvent.click(await screen.findByTestId("branch-manager-confirm-switch-go"))
    fireEvent.click(await screen.findByTestId("git-navigation-discard"))
    await waitFor(() => expect(mockSetWorkingBranch).toHaveBeenCalledWith("experiment", false))
  })

  it("only continues a dirty switch after onSave succeeds", async () => {
    useGraphStore.setState({ dirty: true })
    const onSave = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true)
    render(<BranchManager onSave={onSave} />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-switch")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("branch-manager-switch"))
    fireEvent.click(await screen.findByTestId("branch-manager-confirm-switch-go"))
    fireEvent.click(await screen.findByTestId("git-navigation-save"))
    await waitFor(() => expect(onSave).toHaveBeenCalledOnce())
    expect(mockSetWorkingBranch).not.toHaveBeenCalled()
    fireEvent.click(screen.getByTestId("git-navigation-save"))
    await waitFor(() => expect(mockSetWorkingBranch).toHaveBeenCalledWith("experiment", false))
  })

  it("does not let the skip-switch-confirm preference bypass the dirty guard", async () => {
    mockGetPrefs.mockResolvedValue({ skip_switch_confirm: true })
    useGraphStore.setState({ dirty: true })
    render(<BranchManager />)
    await waitFor(() => expect(mockGetPrefs).toHaveBeenCalledOnce())
    fireEvent.click(await screen.findByTestId("branch-manager-switch"))
    expect(await screen.findByTestId("git-navigation-confirm")).toBeInTheDocument()
    expect(screen.queryByTestId("branch-manager-confirm-switch")).not.toBeInTheDocument()
    expect(mockSetWorkingBranch).not.toHaveBeenCalled()
  })

  it("guards dirty Create & Move before calling the create API", async () => {
    useGraphStore.setState({ dirty: true })
    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-create-input")).toBeInTheDocument())
    fireEvent.change(screen.getByTestId("branch-manager-create-input"), { target: { value: "moved" } })
    fireEvent.click(screen.getByTestId("branch-manager-create-menu"))
    fireEvent.click(screen.getByTestId("branch-manager-create-move"))
    fireEvent.click(await screen.findByTestId("branch-manager-confirm-move-go"))
    expect(await screen.findByTestId("git-navigation-confirm")).toBeInTheDocument()
    expect(mockCreateWorkingBranch).not.toHaveBeenCalled()
    fireEvent.click(screen.getByTestId("git-navigation-discard"))
    await waitFor(() => expect(mockCreateWorkingBranch).toHaveBeenCalledWith("moved", { move: true }))
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

  it("switches in-app: no page reload, and records an undoable VC entry", async () => {
    // window.location.reload is a no-op in jsdom; replace it so we can assert
    // the switch flow does NOT take the reload branch any more.
    const reloadSpy = vi.fn()
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, reload: reloadSpy },
    })
    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-switch")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("branch-manager-switch"))
    await waitFor(() => expect(screen.getByTestId("branch-manager-confirm-switch")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("branch-manager-confirm-switch-go"))
    await waitFor(() => expect(mockSetWorkingBranch).toHaveBeenCalledWith("experiment", false))

    // The completed switch is an undoable history entry (feedback round 2)…
    await waitFor(() => expect(useGraphStore.getState().undoStack).toHaveLength(1))
    expect(useGraphStore.getState().undoStack[0]).toMatchObject({
      kind: "vc",
      label: "switch to experiment",
    })
    // …and the branch list re-fetches in place instead of reloading the app.
    await waitFor(() => expect(mockGetWorkingBranches.mock.calls.length).toBeGreaterThan(1))
    expect(reloadSpy).not.toHaveBeenCalled()
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

  it("clears stale branch badges after a milestone commit", async () => {
    mockGetWorkingBranches
      .mockResolvedValueOnce({
        current: "demo",
        branches: [branch({
          name: "demo",
          is_current: true,
          has_uncommitted_changes: true,
          has_unmerged_saves: true,
        })],
      })
      .mockResolvedValue({
        current: "demo",
        branches: [branch({ name: "demo", is_current: true })],
      })

    render(<BranchManager />)
    await waitFor(() => expect(screen.getByTestId("branch-manager-unsaved")).toBeInTheDocument())

    useGitStore.getState().notifyMilestoneCommitted()

    await waitFor(() => {
      expect(screen.queryByTestId("branch-manager-uncommitted")).not.toBeInTheDocument()
      expect(screen.queryByTestId("branch-manager-unsaved")).not.toBeInTheDocument()
    })
  })

  it("does not let an older branch refresh restore stale badges", async () => {
    let resolveInitial!: (value: typeof listing) => void
    const initial = new Promise<typeof listing>((resolve) => { resolveInitial = resolve })
    mockGetWorkingBranches
      .mockReturnValueOnce(initial)
      .mockResolvedValue({
        current: "demo",
        branches: [branch({ name: "demo", is_current: true })],
      })

    render(<BranchManager />)
    await waitFor(() => expect(mockGetWorkingBranches).toHaveBeenCalledOnce())
    useGitStore.getState().notifyHistoryChanged()
    expect(mockGetWorkingBranches).toHaveBeenCalledOnce()

    await act(async () => {
      resolveInitial({
        current: "demo",
        branches: [branch({
          name: "demo",
          is_current: true,
          has_uncommitted_changes: true,
          has_unmerged_saves: true,
        })],
      })
      await initial
    })

    await waitFor(() => expect(mockGetWorkingBranches).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.getByTestId("branch-manager-current")).toBeInTheDocument())
    expect(screen.queryByTestId("branch-manager-uncommitted")).not.toBeInTheDocument()
    expect(screen.queryByTestId("branch-manager-unsaved")).not.toBeInTheDocument()
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

  // ── Row context menu ─────────────────────────────────────────────────────
  // Right-click on a branch row: the row's actions as a menu, routing through
  // the SAME handlers as the row buttons (dialogs included).
  describe("row context menu", () => {
    let reloadSpy: ReturnType<typeof vi.fn>

    beforeEach(() => {
      reloadSpy = vi.fn()
      Object.defineProperty(window, "location", {
        configurable: true,
        value: { ...window.location, reload: reloadSpy },
      })
    })

    // Row order: current (demo, boxed) first, then others, then archived.
    const openMenuOn = async (rowIndex: number) => {
      await waitFor(() => expect(screen.getAllByTestId("branch-manager-branch")).toHaveLength(3))
      fireEvent.contextMenu(screen.getAllByTestId("branch-manager-branch")[rowIndex])
      await waitFor(() => expect(screen.getByTestId("branch-manager-row-menu")).toBeInTheDocument())
    }

    it("opens on right-click with the live row's actions (no Restore)", async () => {
      render(<BranchManager />)
      await openMenuOn(1) // experiment
      expect(screen.getByTestId("branch-manager-row-menu")).toHaveTextContent("experiment")
      expect(screen.getByTestId("branch-manager-row-menu-select")).toBeInTheDocument()
      expect(screen.getByTestId("branch-manager-row-menu-switch")).toBeInTheDocument()
      expect(screen.getByTestId("branch-manager-row-menu-archive")).toBeInTheDocument()
      expect(screen.getByTestId("branch-manager-row-menu-delete")).toBeInTheDocument()
      expect(screen.queryByTestId("branch-manager-row-menu-restore")).not.toBeInTheDocument()
    })

    it("hides Switch on the current branch's own row", async () => {
      render(<BranchManager />)
      await openMenuOn(0) // demo (current)
      expect(screen.queryByTestId("branch-manager-row-menu-switch")).not.toBeInTheDocument()
      expect(screen.getByTestId("branch-manager-row-menu-archive")).toBeInTheDocument()
    })

    it("Select peeks the branch's history without switching", async () => {
      const onPeek = vi.fn()
      render(<BranchManager onPeek={onPeek} />)
      await openMenuOn(1)
      fireEvent.click(screen.getByTestId("branch-manager-row-menu-select"))
      expect(onPeek).toHaveBeenCalledWith("experiment")
      expect(mockSetWorkingBranch).not.toHaveBeenCalled()
      expect(screen.queryByTestId("branch-manager-row-menu")).not.toBeInTheDocument()
    })

    it("Switch routes through the confirm flow and never reloads the page", async () => {
      render(<BranchManager />)
      await openMenuOn(1)
      fireEvent.click(screen.getByTestId("branch-manager-row-menu-switch"))
      // Same gate as the row button: nothing happens until the confirm.
      await waitFor(() => expect(screen.getByTestId("branch-manager-confirm-switch")).toBeInTheDocument())
      expect(mockSetWorkingBranch).not.toHaveBeenCalled()
      fireEvent.click(screen.getByTestId("branch-manager-confirm-switch-go"))
      await waitFor(() => expect(mockSetWorkingBranch).toHaveBeenCalledWith("experiment", false))
      await waitFor(() => expect(useGraphStore.getState().undoStack).toHaveLength(1))
      expect(useGraphStore.getState().undoStack[0]).toMatchObject({
        kind: "vc",
        label: "switch to experiment",
      })
      expect(reloadSpy).not.toHaveBeenCalled()
    })

    it("Archive routes to the archive flow and records the undo entry", async () => {
      render(<BranchManager />)
      await openMenuOn(1)
      fireEvent.click(screen.getByTestId("branch-manager-row-menu-archive"))
      await waitFor(() => expect(mockArchive).toHaveBeenCalledWith("experiment"))
      await waitFor(() => expect(useGraphStore.getState().undoStack).toHaveLength(1))
      expect(useGraphStore.getState().undoStack[0]).toMatchObject({
        kind: "vc",
        label: "archive experiment",
      })
      expect(reloadSpy).not.toHaveBeenCalled()
    })

    it("Restore replaces Archive/Switch on an archived row and routes to restore", async () => {
      render(<BranchManager />)
      await openMenuOn(2) // archive/old
      expect(screen.queryByTestId("branch-manager-row-menu-switch")).not.toBeInTheDocument()
      expect(screen.queryByTestId("branch-manager-row-menu-archive")).not.toBeInTheDocument()
      fireEvent.click(screen.getByTestId("branch-manager-row-menu-restore"))
      await waitFor(() => expect(mockRestore).toHaveBeenCalledWith("archive/old"))
      await waitFor(() => expect(useGraphStore.getState().undoStack).toHaveLength(1))
      expect(useGraphStore.getState().undoStack[0]).toMatchObject({
        kind: "vc",
        label: "restore old",
      })
    })

    it("Delete routes through the same confirm dialog as the row button", async () => {
      render(<BranchManager />)
      await openMenuOn(1)
      fireEvent.click(screen.getByTestId("branch-manager-row-menu-delete"))
      await waitFor(() => expect(screen.getByTestId("branch-manager-confirm")).toBeInTheDocument())
      expect(mockDelete).not.toHaveBeenCalled()
      fireEvent.click(screen.getByTestId("branch-manager-confirm-delete"))
      await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("experiment", true))
      await waitFor(() => expect(useGraphStore.getState().undoStack).toHaveLength(1))
      expect(useGraphStore.getState().undoStack[0]).toMatchObject({
        kind: "vc",
        label: "delete experiment",
      })
      expect(reloadSpy).not.toHaveBeenCalled()
    })

    it("dismisses on backdrop click without acting", async () => {
      render(<BranchManager />)
      await openMenuOn(1)
      fireEvent.click(screen.getByTestId("branch-manager-row-menu").previousSibling as Element)
      await waitFor(() =>
        expect(screen.queryByTestId("branch-manager-row-menu")).not.toBeInTheDocument(),
      )
      expect(mockSetWorkingBranch).not.toHaveBeenCalled()
      expect(mockArchive).not.toHaveBeenCalled()
      expect(mockDelete).not.toHaveBeenCalled()
    })
  })
})
