/**
 * Undoable VC operations (utils/vcHistory): each record* pushes a
 * VcHistoryEntry onto the graph store whose async legs call the RIGHT client
 * function with the RIGHT branch name — archive/restore rename the branch
 * (archive/ prefix), so the two legs of one entry target different names.
 *
 * Legs also toast and resync git state (status reload + history nonce) on
 * success, and toast + RETHROW on failure so the store can put the entry
 * back for a retry.
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import { recordArchive, recordDelete, recordRestore, recordSwitch } from "../vcHistory"
import useGraphStore from "../../stores/useGraphStore"
import type { VcHistoryEntry } from "../../stores/useGraphStore"
import useGitStore from "../../stores/useGitStore"
import useToastStore from "../../stores/useToastStore"

const mockSetWorkingBranch = vi.fn()
const mockArchive = vi.fn()
const mockDelete = vi.fn()
const mockRestore = vi.fn()
const mockUndelete = vi.fn()
const mockGetWorkingBranch = vi.fn()

vi.mock("../../api/client", () => ({
  setWorkingBranch: (...a: unknown[]) => mockSetWorkingBranch(...a),
  gitArchiveBranch: (...a: unknown[]) => mockArchive(...a),
  gitDeleteBranch: (...a: unknown[]) => mockDelete(...a),
  restoreBranch: (...a: unknown[]) => mockRestore(...a),
  undeleteBranch: (...a: unknown[]) => mockUndelete(...a),
  // useGitStore.loadStatus (the resync leg) reads the working branch.
  getWorkingBranch: (...a: unknown[]) => mockGetWorkingBranch(...a),
}))

const lastEntry = (): VcHistoryEntry => {
  const { undoStack } = useGraphStore.getState()
  const entry = undoStack[undoStack.length - 1]
  if (!entry || !("kind" in entry)) throw new Error("no vc entry recorded")
  return entry
}

const toastTexts = () => useToastStore.getState().toasts.map((t) => [t.type, t.text])

beforeEach(() => {
  vi.clearAllMocks()
  useGraphStore.setState({ undoStack: [], redoStack: [], vcBusy: false })
  useGitStore.setState({ status: null, historyNonce: 0 })
  useToastStore.setState({ toasts: [] })
  mockSetWorkingBranch.mockResolvedValue({})
  mockArchive.mockResolvedValue({ archived_as: "archive/x" })
  mockDelete.mockResolvedValue({ status: "deleted", branch: "x" })
  mockRestore.mockResolvedValue({ restored_as: "x" })
  mockUndelete.mockResolvedValue({ status: "restored", branch: "x" })
  mockGetWorkingBranch.mockResolvedValue({ working_branch: "demo", state: "ready" })
})

describe("recordSwitch", () => {
  it("pushes an entry whose legs switch to the right side's branch", async () => {
    recordSwitch("demo", "experiment")
    const entry = lastEntry()
    expect(entry.label).toBe("switch to experiment")
    expect(useGraphStore.getState().undoStack).toHaveLength(1)
    // Recording never calls the API.
    expect(mockSetWorkingBranch).not.toHaveBeenCalled()

    await entry.undo()
    expect(mockSetWorkingBranch).toHaveBeenCalledWith("demo", false)

    await entry.redo()
    expect(mockSetWorkingBranch).toHaveBeenLastCalledWith("experiment", false)
  })
})

describe("recordArchive", () => {
  it("undoes via restore of the ARCHIVED name and redoes via archive of the live name", async () => {
    recordArchive("experiment", "archive/experiment")
    const entry = lastEntry()
    expect(entry.label).toBe("archive experiment")

    await entry.undo()
    expect(mockRestore).toHaveBeenCalledWith("archive/experiment")
    expect(mockArchive).not.toHaveBeenCalled()

    await entry.redo()
    expect(mockArchive).toHaveBeenCalledWith("experiment")
  })
})

describe("recordRestore", () => {
  it("undoes via archive of the RESTORED name and redoes via restore of the archived name", async () => {
    recordRestore("archive/old", "old")
    const entry = lastEntry()
    expect(entry.label).toBe("restore old")

    await entry.undo()
    expect(mockArchive).toHaveBeenCalledWith("old")
    expect(mockRestore).not.toHaveBeenCalled()

    await entry.redo()
    expect(mockRestore).toHaveBeenCalledWith("archive/old")
  })
})

describe("recordDelete", () => {
  it("undoes via undelete (trash restore) and redoes via a confirmed delete", async () => {
    recordDelete("experiment")
    const entry = lastEntry()
    expect(entry.label).toBe("delete experiment")

    await entry.undo()
    expect(mockUndelete).toHaveBeenCalledWith("experiment")
    expect(mockDelete).not.toHaveBeenCalled()

    await entry.redo()
    expect(mockDelete).toHaveBeenCalledWith("experiment", true)
  })
})

describe("leg side effects", () => {
  it("a successful leg toasts and resyncs git state (status reload + history nonce)", async () => {
    recordSwitch("demo", "experiment")

    await lastEntry().undo()

    expect(toastTexts()).toEqual([["success", "Switched back to demo"]])
    // Resync: the toolbar/branch-manager status reloaded…
    expect(mockGetWorkingBranch).toHaveBeenCalledTimes(1)
    // …and the Git panel was told to refetch its history.
    expect(useGitStore.getState().historyNonce).toBe(1)
  })

  it("a failing leg toasts the error, skips the resync and rethrows for the store", async () => {
    mockUndelete.mockRejectedValue(new Error("tombstone missing"))
    recordDelete("experiment")

    await expect(lastEntry().undo()).rejects.toThrow("tombstone missing")

    expect(toastTexts()).toEqual([["error", "Undo/redo failed: tombstone missing"]])
    expect(mockGetWorkingBranch).not.toHaveBeenCalled()
    expect(useGitStore.getState().historyNonce).toBe(0)
  })

  it("each leg carries its own message (redo side)", async () => {
    recordArchive("experiment", "archive/experiment")

    await lastEntry().redo()

    expect(toastTexts()).toEqual([["success", "Archived experiment"]])
  })
})
