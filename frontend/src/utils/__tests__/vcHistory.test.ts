/**
 * Undoable VC operations (utils/vcHistory): each record* pushes a
 * VcHistoryEntry onto the graph store whose async legs call the RIGHT client
 * function with the RIGHT branch name — archive/restore rename the branch
 * (archive/ prefix), so the two legs of one entry target different names.
 *
 * Legs also toast and resync git state (status reload + history nonce) on
 * success, and toast + RETHROW on failure so the store can put the entry
 * back for a retry. A switch's legs replace the canvas and the form, so they
 * ask about unsaved edits first.
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import { waitFor } from "@testing-library/react"
import { recordArchive, recordDelete, recordRestore, recordSwitch } from "../vcHistory"
import useGraphStore from "../../stores/useGraphStore"
import type { VcHistoryEntry } from "../../stores/useGraphStore"
import useGitStore from "../../stores/useGitStore"
import useToastStore from "../../stores/useToastStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchStore from "../../stores/useWorkbenchStore"
import { answerQuestion, asked } from "../../test-utils/question"

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
  useGraphStore.setState({ undoStack: [], redoStack: [], vcBusy: false, dirty: false })
  useWorkbenchStore.setState({ formDirty: false })
  useWorkbenchFormStore.setState({ reload: useWorkbenchFormStore.getInitialState().reload })
  useGitStore.setState({ status: null, historyNonce: 0 })
  mockSetWorkingBranch.mockResolvedValue({})
  mockArchive.mockResolvedValue({ archived_as: "archive/x" })
  mockDelete.mockResolvedValue({ status: "deleted", branch: "x", trash_id: "x@20261027T131400Z" })
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

describe("a switch's legs with unsaved edits", () => {
  beforeEach(() => {
    useGraphStore.setState({ dirty: true })
  })

  it("a clean project switches without asking", async () => {
    useGraphStore.setState({ dirty: false })
    recordSwitch("demo", "experiment", vi.fn())

    await lastEntry().undo()

    expect(asked()).toBeNull()
    expect(mockSetWorkingBranch).toHaveBeenCalledWith("demo", false)
  })

  it("an undo asks first; Cancel switches nothing, keeps the entry and reports nothing", async () => {
    recordSwitch("demo", "experiment", vi.fn())
    const entry = lastEntry()

    useGraphStore.getState().undo()
    await waitFor(() => expect(asked()).not.toBeNull())
    expect(asked()).toMatchObject({
      title: "Switch back to demo?",
      text: "You have unsaved changes. Save them before changing branches?",
      other: "Save first",
      confirm: "Discard",
      danger: true,
    })
    await answerQuestion(null)

    await waitFor(() => expect(useGraphStore.getState().vcBusy).toBe(false))
    expect(mockSetWorkingBranch).not.toHaveBeenCalled()
    expect(useGraphStore.getState().undoStack).toEqual([entry])
    expect(useGraphStore.getState().redoStack).toEqual([])
    expect(toastTexts()).toEqual([])
    expect(mockGetWorkingBranch).not.toHaveBeenCalled()
    expect(useGraphStore.getState().dirty).toBe(true)
  })

  it("Discard switches, then drops the canvas's and the form's edits", async () => {
    const reload = vi.fn(async () => {})
    useWorkbenchFormStore.setState({ reload })
    useWorkbenchStore.setState({ formDirty: true })
    const onSave = vi.fn()
    recordSwitch("demo", "experiment", onSave)

    const undone = lastEntry().undo()
    await waitFor(() => expect(asked()).not.toBeNull())
    await answerQuestion("confirm")
    await undone

    expect(mockSetWorkingBranch).toHaveBeenCalledWith("demo", false)
    expect(useGraphStore.getState().dirty).toBe(false)
    expect(reload).toHaveBeenCalledOnce()
    expect(onSave).not.toHaveBeenCalled()
    expect(toastTexts()).toEqual([["success", "Switched back to demo"]])
  })

  it("keeps the edits when the switch chosen over them fails", async () => {
    mockSetWorkingBranch.mockRejectedValueOnce(new Error("a file is held open"))
    recordSwitch("demo", "experiment", vi.fn())

    const failed = expect(lastEntry().undo()).rejects.toThrow("a file is held open")
    await waitFor(() => expect(asked()).not.toBeNull())
    await answerQuestion("confirm")

    await failed
    expect(useGraphStore.getState().dirty).toBe(true)
    expect(toastTexts()).toEqual([["error", "Undo/redo failed: a file is held open"]])
  })

  it("a redo's Save first saves and then switches, asking again after a save that fails", async () => {
    const onSave = vi.fn()
      .mockResolvedValueOnce(false)
      .mockImplementationOnce(async () => {
        useGraphStore.setState({ dirty: false })
        return true
      })
    recordSwitch("demo", "experiment", onSave)

    const redone = lastEntry().redo()
    await waitFor(() => expect(asked()?.title).toBe("Switch to experiment?"))
    await answerQuestion("other")
    await waitFor(() => expect(onSave).toHaveBeenCalledOnce())
    await waitFor(() => expect(asked()).not.toBeNull())
    expect(mockSetWorkingBranch).not.toHaveBeenCalled()

    await answerQuestion("other")
    await redone
    expect(onSave).toHaveBeenCalledTimes(2)
    expect(mockSetWorkingBranch).toHaveBeenCalledOnce()
    expect(mockSetWorkingBranch).toHaveBeenCalledWith("experiment", false)
  })

  it("offers no Save first when there is no save to run", async () => {
    recordSwitch("demo", "experiment")

    const cancelled = expect(lastEntry().undo()).rejects.toThrow("cancelled")
    await waitFor(() => expect(asked()).not.toBeNull())
    expect(asked()?.other).toBeUndefined()
    await answerQuestion(null)

    await cancelled
    expect(mockSetWorkingBranch).not.toHaveBeenCalled()
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
    recordDelete("experiment", "experiment@20261027T131400Z")
    const entry = lastEntry()
    expect(entry.label).toBe("delete experiment")

    await entry.undo()
    expect(mockUndelete).toHaveBeenCalledWith("experiment@20261027T131400Z")
    expect(mockDelete).not.toHaveBeenCalled()

    await entry.redo()
    expect(mockDelete).toHaveBeenCalledWith("experiment", true)
  })

  it("a redo's delete is a new trash entry, which the next undo restores", async () => {
    mockDelete.mockResolvedValue({ status: "deleted", branch: "experiment", trash_id: "experiment@20261027T140000Z" })
    recordDelete("experiment", "experiment@20261027T131400Z")
    const entry = lastEntry()

    await entry.undo()
    await entry.redo()
    await entry.undo()

    expect(mockUndelete.mock.calls.map((c) => c[0])).toEqual([
      "experiment@20261027T131400Z",
      "experiment@20261027T140000Z",
    ])
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

  it("a failing leg toasts the error, reads the status again, and rethrows for the store", async () => {
    mockUndelete.mockRejectedValue(new Error("tombstone missing"))
    recordDelete("experiment", "experiment@20261027T131400Z")

    await expect(lastEntry().undo()).rejects.toThrow("tombstone missing")

    expect(toastTexts()).toEqual([["error", "Undo/redo failed: tombstone missing"]])
    // A held file can leave the clone interrupted, which only the status shows…
    expect(mockGetWorkingBranch).toHaveBeenCalledTimes(1)
    // …but nothing changed for the Git panel to refetch.
    expect(useGitStore.getState().historyNonce).toBe(0)
  })

  it("each leg carries its own message (redo side)", async () => {
    recordArchive("experiment", "archive/experiment")

    await lastEntry().redo()

    expect(toastTexts()).toEqual([["success", "Archived experiment"]])
  })
})
