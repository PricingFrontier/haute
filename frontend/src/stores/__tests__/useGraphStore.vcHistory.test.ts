/**
 * VC entries on the graph store's history stacks (feedback round 2).
 *
 * Branch switch / archive / restore / delete record `VcHistoryEntry` items
 * that ride the SAME undo/redo stacks as graph snapshots. This file pins the
 * store-side sequencing contract:
 *
 *   - pushVcEntry appends a kind:"vc" entry and clears redo (like any edit);
 *   - undo/redo of a vc entry runs its async leg with history LOCKED
 *     (vcBusy) — canUndo/canRedo report false until the leg settles;
 *   - a failed leg puts the entry back where it came from (retryable);
 *   - interleaving with graph snapshots replays inverses in stack order:
 *     a vc entry pushed after graph edits is undone FIRST.
 */
import { describe, it, expect, beforeEach, vi } from "vitest"
import type { Node } from "@xyflow/react"
import useGraphStore, { MAX_HISTORY, isVcEntry } from "../useGraphStore"

const makeNode = (id: string): Node => ({ id, position: { x: 0, y: 0 }, data: {} })

/** Let the entry's already-settled promise legs run their then/finally. */
const flush = () => new Promise((r) => setTimeout(r, 0))

/** A vc entry with controllable legs. */
const makeEntry = (over: Partial<{ undo: () => Promise<void>; redo: () => Promise<void> }> = {}) => ({
  label: "switch to demo",
  undo: vi.fn(() => Promise.resolve()),
  redo: vi.fn(() => Promise.resolve()),
  ...over,
})

beforeEach(() => {
  useGraphStore.setState({
    nodes: [],
    edges: [],
    preamble: "",
    lastSavedSnapshot: null,
    undoStack: [],
    redoStack: [],
    vcBusy: false,
  })
})

describe("useGraphStore — pushVcEntry", () => {
  it("appends a kind:'vc' entry and clears the redo stack", () => {
    useGraphStore.setState({
      redoStack: [{ nodes: [], edges: [], preamble: "stale", submodels: {} }],
    })
    const entry = makeEntry()
    useGraphStore.getState().pushVcEntry(entry)

    const { undoStack, redoStack } = useGraphStore.getState()
    expect(undoStack).toHaveLength(1)
    expect(undoStack[0]).toMatchObject({ kind: "vc", label: "switch to demo" })
    expect(isVcEntry(undoStack[0])).toBe(true)
    expect(redoStack).toEqual([])
    // Nothing ran yet — recording is not replaying.
    expect(entry.undo).not.toHaveBeenCalled()
    expect(entry.redo).not.toHaveBeenCalled()
  })

  it("caps the undo stack at MAX_HISTORY, dropping the oldest", () => {
    const filler = Array.from({ length: MAX_HISTORY }, (_, i) => ({
      nodes: [makeNode(`n${i}`)],
      edges: [],
      preamble: "",
      submodels: {},
    }))
    useGraphStore.setState({ undoStack: filler })
    useGraphStore.getState().pushVcEntry(makeEntry())

    const { undoStack } = useGraphStore.getState()
    expect(undoStack).toHaveLength(MAX_HISTORY)
    expect(isVcEntry(undoStack[undoStack.length - 1])).toBe(true)
    // The oldest snapshot fell off the bottom.
    expect(undoStack[0]).toMatchObject({ nodes: [expect.objectContaining({ id: "n1" })] })
  })
})

describe("useGraphStore — undo/redo of a vc entry", () => {
  it("runs the undo leg with history locked, then unlocks and enables redo", async () => {
    let resolveUndo!: () => void
    const entry = makeEntry({ undo: vi.fn(() => new Promise<void>((res) => { resolveUndo = res })) })
    useGraphStore.getState().pushVcEntry(entry)

    useGraphStore.getState().undo()

    // In flight: leg started, entry moved to redo, history locked.
    expect(entry.undo).toHaveBeenCalledTimes(1)
    expect(useGraphStore.getState().vcBusy).toBe(true)
    expect(useGraphStore.getState().undoStack).toEqual([])
    expect(useGraphStore.getState().redoStack).toHaveLength(1)
    expect(useGraphStore.getState().canUndo()).toBe(false)
    expect(useGraphStore.getState().canRedo()).toBe(false)

    // Further undo/redo are no-ops while the leg runs.
    useGraphStore.getState().undo()
    useGraphStore.getState().redo()
    expect(entry.undo).toHaveBeenCalledTimes(1)
    expect(entry.redo).not.toHaveBeenCalled()

    resolveUndo()
    await flush()
    expect(useGraphStore.getState().vcBusy).toBe(false)
    expect(useGraphStore.getState().canRedo()).toBe(true)
  })

  it("redo replays the redo leg and moves the entry back onto the undo stack", async () => {
    const entry = makeEntry()
    useGraphStore.getState().pushVcEntry(entry)
    useGraphStore.getState().undo()
    await flush()

    useGraphStore.getState().redo()
    expect(entry.redo).toHaveBeenCalledTimes(1)
    expect(useGraphStore.getState().vcBusy).toBe(true)
    await flush()
    expect(useGraphStore.getState().vcBusy).toBe(false)
    expect(useGraphStore.getState().undoStack).toHaveLength(1)
    expect(useGraphStore.getState().redoStack).toEqual([])
    expect(useGraphStore.getState().canUndo()).toBe(true)
  })

  it("a failed undo leg restores the entry to the undo stack for a retry", async () => {
    const entry = makeEntry({ undo: vi.fn(() => Promise.reject(new Error("offline"))) })
    useGraphStore.getState().pushVcEntry(entry)

    useGraphStore.getState().undo()
    await flush()

    const { undoStack, redoStack, vcBusy } = useGraphStore.getState()
    expect(vcBusy).toBe(false)
    expect(undoStack).toHaveLength(1)
    expect(undoStack[0]).toMatchObject({ kind: "vc", label: "switch to demo" })
    expect(redoStack).toEqual([])
    expect(useGraphStore.getState().canUndo()).toBe(true)

    // The retry runs the leg again.
    useGraphStore.getState().undo()
    expect(entry.undo).toHaveBeenCalledTimes(2)
    await flush()
  })

  it("a failed redo leg restores the entry to the redo stack", async () => {
    const entry = makeEntry({ redo: vi.fn(() => Promise.reject(new Error("offline"))) })
    useGraphStore.getState().pushVcEntry(entry)
    useGraphStore.getState().undo()
    await flush()

    useGraphStore.getState().redo()
    await flush()

    const { undoStack, redoStack, vcBusy } = useGraphStore.getState()
    expect(vcBusy).toBe(false)
    expect(redoStack).toHaveLength(1)
    expect(redoStack[0]).toMatchObject({ kind: "vc" })
    expect(undoStack).toEqual([])
    expect(useGraphStore.getState().canRedo()).toBe(true)
  })
})

describe("useGraphStore — vc entries interleaved with graph snapshots", () => {
  it("undoes the vc entry pushed after graph edits FIRST, then the edits; redo reverses", async () => {
    // 1. A graph edit (snapshots the pre-edit empty graph)…
    useGraphStore.getState().setNodes([makeNode("n1")])
    // 2. …then a recorded VC operation.
    const entry = makeEntry()
    useGraphStore.getState().pushVcEntry(entry)
    expect(useGraphStore.getState().undoStack).toHaveLength(2)

    // First undo: the VC inverse runs; the graph is untouched.
    useGraphStore.getState().undo()
    expect(entry.undo).toHaveBeenCalledTimes(1)
    expect(useGraphStore.getState().nodes.map((n) => n.id)).toEqual(["n1"])
    // The remaining graph snapshot cannot be undone until the leg settles.
    expect(useGraphStore.getState().canUndo()).toBe(false)
    await flush()
    expect(useGraphStore.getState().canUndo()).toBe(true)

    // Second undo: the graph edit reverses.
    useGraphStore.getState().undo()
    expect(useGraphStore.getState().nodes).toEqual([])
    expect(useGraphStore.getState().redoStack).toHaveLength(2)

    // Redo order mirrors back: graph first, then the VC leg.
    useGraphStore.getState().redo()
    expect(useGraphStore.getState().nodes.map((n) => n.id)).toEqual(["n1"])
    expect(entry.redo).not.toHaveBeenCalled()
    useGraphStore.getState().redo()
    expect(entry.redo).toHaveBeenCalledTimes(1)
    await flush()
    expect(useGraphStore.getState().undoStack).toHaveLength(2)
    expect(useGraphStore.getState().redoStack).toEqual([])
  })
})
