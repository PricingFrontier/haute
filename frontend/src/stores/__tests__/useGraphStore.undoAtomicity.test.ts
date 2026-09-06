/**
 * Undo-atomicity regression suite (the "two-snapshot collapse").
 *
 * Guards the bug class where one user gesture that mutates BOTH nodes and
 * edges recorded TWO undo entries (setNodes then setEdges each snapshotting),
 * so a single ⌘/Ctrl-Z only rewound half the gesture. The fix routes such
 * gestures through the combined `setNodesAndEdges`, which snapshots exactly
 * once.
 *
 * Per Nick's 2026-06-19 testing note on BUGS.md §"Deleting a node is not one
 * atomic undo": the count was a CONSTANT two regardless of selection size, so
 * the fix must be regression-tested against multi-node deletes MIXING
 * connected and unconnected nodes, and pure-edge deletes — to prove the
 * collapse is genuinely gone, not papered over for the 1-node case. These
 * tests assert the actual undo-stack depth and a single-undo restoration,
 * which is the real contract (a hook-level "called once" assertion cannot see
 * the snapshot count).
 */
import { describe, it, expect, beforeEach } from "vitest"
import { act } from "@testing-library/react"
import useGraphStore, { resetGraphStoreForTests } from "../useGraphStore"
import { makeNode, makeEdge } from "../../test-utils/factories"
import type { Node, Edge } from "@xyflow/react"

/** Load a graph without history, then clear the stacks for a clean baseline. */
function seed(nodes: Node[], edges: Edge[]) {
  const store = useGraphStore.getState()
  act(() => {
    store.setNodesRaw(nodes)
    store.setEdgesRaw(edges)
  })
  useGraphStore.setState({ undoStack: [], redoStack: [] })
}

/** The exact node+edge filter the delete handlers apply for a selection. */
function deleteSelection(selectedIds: Set<string>) {
  act(() => {
    useGraphStore.getState().setNodesAndEdges(
      (nds) => nds.filter((n) => !selectedIds.has(n.id)),
      (eds) => eds.filter((e) => !selectedIds.has(e.source) && !selectedIds.has(e.target)),
    )
  })
}

const ids = (arr: { id: string }[]) => arr.map((x) => x.id).sort()

describe("useGraphStore — undo atomicity", () => {
  beforeEach(() => {
    resetGraphStoreForTests()
  })

  it("single-node delete (with edges) is ONE undo entry; one undo restores node AND edges", () => {
    // a -> b -> c ; delete the middle node b (edges on both sides).
    seed(
      [makeNode("a"), makeNode("b"), makeNode("c")],
      [makeEdge("a", "b"), makeEdge("b", "c")],
    )

    deleteSelection(new Set(["b"]))

    // Exactly one snapshot for the whole gesture — the crux of the bug class.
    expect(useGraphStore.getState().undoStack).toHaveLength(1)
    expect(ids(useGraphStore.getState().nodes)).toEqual(["a", "c"])
    expect(useGraphStore.getState().edges).toHaveLength(0)

    // A SINGLE undo brings back the node and BOTH its edges together.
    act(() => useGraphStore.getState().undo())
    expect(ids(useGraphStore.getState().nodes)).toEqual(["a", "b", "c"])
    expect(ids(useGraphStore.getState().edges)).toEqual(["e_a_b", "e_b_c"])

    // And there is no leftover second snapshot — the undo stack is empty and
    // the whole gesture sits as one entry on the redo stack.
    expect(useGraphStore.getState().undoStack).toHaveLength(0)
    expect(useGraphStore.getState().redoStack).toHaveLength(1)
  })

  it("multi-node delete mixing CONNECTED and UNCONNECTED nodes is ONE undo entry", () => {
    // a -> b -> c, a -> e, plus isolated node d.
    // Delete { b (connected, mid-chain), d (unconnected) } in one gesture.
    // Expect: b removes edges a->b and b->c; d removes nothing; a->e survives.
    seed(
      [makeNode("a"), makeNode("b"), makeNode("c"), makeNode("d"), makeNode("e")],
      [makeEdge("a", "b"), makeEdge("b", "c"), makeEdge("a", "e")],
    )

    deleteSelection(new Set(["b", "d"]))

    expect(useGraphStore.getState().undoStack).toHaveLength(1)
    expect(ids(useGraphStore.getState().nodes)).toEqual(["a", "c", "e"])
    expect(ids(useGraphStore.getState().edges)).toEqual(["e_a_e"])

    // One undo restores every deleted node (connected + unconnected) and every
    // deleted edge, atomically.
    act(() => useGraphStore.getState().undo())
    expect(ids(useGraphStore.getState().nodes)).toEqual(["a", "b", "c", "d", "e"])
    expect(ids(useGraphStore.getState().edges)).toEqual(["e_a_b", "e_a_e", "e_b_c"])
    expect(useGraphStore.getState().undoStack).toHaveLength(0)
  })

  it("selection size does not scale undo entries — 4 nodes deleted is still ONE undo", () => {
    // Nick's note: the count was a CONSTANT two before the fix, so it must be
    // a CONSTANT one after — independent of how many nodes/edges are removed.
    seed(
      [makeNode("a"), makeNode("b"), makeNode("c"), makeNode("d"), makeNode("e")],
      [makeEdge("a", "b"), makeEdge("b", "c"), makeEdge("c", "d"), makeEdge("d", "e")],
    )

    deleteSelection(new Set(["a", "b", "c", "d"]))

    expect(useGraphStore.getState().undoStack).toHaveLength(1)
    expect(ids(useGraphStore.getState().nodes)).toEqual(["e"])
    expect(useGraphStore.getState().edges).toHaveLength(0)

    act(() => useGraphStore.getState().undo())
    expect(useGraphStore.getState().nodes).toHaveLength(5)
    expect(useGraphStore.getState().edges).toHaveLength(4)
    expect(useGraphStore.getState().undoStack).toHaveLength(0)
  })

  it("pure-edge delete (no nodes) is ONE undo entry; one undo restores the edge", () => {
    // The pure-edge path keeps a single setEdges (already one snapshot). Nick
    // asked for it to be regression-covered alongside the node cases.
    seed([makeNode("a"), makeNode("b")], [makeEdge("a", "b")])

    act(() => {
      useGraphStore.getState().setEdges((eds) => eds.filter((e) => e.id !== "e_a_b"))
    })

    expect(useGraphStore.getState().undoStack).toHaveLength(1)
    expect(useGraphStore.getState().edges).toHaveLength(0)
    expect(ids(useGraphStore.getState().nodes)).toEqual(["a", "b"])

    act(() => useGraphStore.getState().undo())
    expect(ids(useGraphStore.getState().edges)).toEqual(["e_a_b"])
    expect(useGraphStore.getState().undoStack).toHaveLength(0)
  })

  it("paste (nodes + internal edges) is ONE undo entry; one undo removes the whole paste", () => {
    seed([makeNode("keep")], [])

    const pastedNodes = [makeNode("p1"), makeNode("p2")]
    const pastedEdge = makeEdge("p1", "p2")
    act(() => {
      useGraphStore.getState().setNodesAndEdges(
        (nds) => [...nds, ...pastedNodes],
        (eds) => [...eds, pastedEdge],
      )
    })

    expect(useGraphStore.getState().undoStack).toHaveLength(1)
    expect(ids(useGraphStore.getState().nodes)).toEqual(["keep", "p1", "p2"])
    expect(ids(useGraphStore.getState().edges)).toEqual(["e_p1_p2"])

    // One undo removes both pasted nodes AND the pasted edge together.
    act(() => useGraphStore.getState().undo())
    expect(ids(useGraphStore.getState().nodes)).toEqual(["keep"])
    expect(useGraphStore.getState().edges).toHaveLength(0)
  })

  it("redo re-applies the combined gesture in one step", () => {
    seed(
      [makeNode("a"), makeNode("b"), makeNode("c")],
      [makeEdge("a", "b"), makeEdge("b", "c")],
    )
    deleteSelection(new Set(["b"]))
    act(() => useGraphStore.getState().undo())
    // Back to full graph.
    expect(useGraphStore.getState().nodes).toHaveLength(3)

    act(() => useGraphStore.getState().redo())
    // One redo re-removes the node and both edges together.
    expect(ids(useGraphStore.getState().nodes)).toEqual(["a", "c"])
    expect(useGraphStore.getState().edges).toHaveLength(0)
    expect(useGraphStore.getState().redoStack).toHaveLength(0)
  })

  it("setNodesAndEdges pushes exactly one snapshot per call (store-level gesture contract)", () => {
    // DYLE §Undo atomicity candidate②: any public gesture helper that mutates
    // graph state pushes exactly one snapshot. Three successive combined
    // gestures => three undo entries (one each), never six.
    seed([makeNode("a"), makeNode("b"), makeNode("c")], [])
    const store = useGraphStore.getState()

    act(() => store.setNodesAndEdges((nds) => nds.filter((n) => n.id !== "a"), (eds) => eds))
    act(() => store.setNodesAndEdges((nds) => nds.filter((n) => n.id !== "b"), (eds) => eds))
    act(() => store.setNodesAndEdges((nds) => nds.filter((n) => n.id !== "c"), (eds) => eds))

    expect(useGraphStore.getState().undoStack).toHaveLength(3)
    expect(useGraphStore.getState().nodes).toHaveLength(0)
  })

  it("replaces nodes, edges, submodels, and preamble in one dirty undo step", () => {
    const originalNodes = [makeNode("root")]
    const originalEdges: Edge[] = []
    const originalSubmodels = {}
    act(() => {
      useGraphStore.getState().loadGraphSnapshot({
        nodes: originalNodes,
        edges: originalEdges,
        preamble: "PARENT = 1",
        submodels: originalSubmodels,
      })
    })

    const nextNodes = [makeNode("pricing")]
    const nextEdges = [makeEdge("upstream", "pricing")]
    const nextSubmodels = { pricing: { file: "modules/pricing.py" } }
    act(() => {
      useGraphStore.getState().setNodesAndEdgesAndSubmodels(
        nextNodes,
        nextEdges,
        nextSubmodels,
        "PARENT = 1\nCHILD_HELPER = 2",
      )
    })

    const changed = useGraphStore.getState()
    expect(changed.nodes).toEqual(nextNodes)
    expect(changed.edges).toEqual(nextEdges)
    expect(changed.submodels).toEqual(nextSubmodels)
    expect(changed.preamble).toBe("PARENT = 1\nCHILD_HELPER = 2")
    expect(changed.undoStack).toHaveLength(1)
    expect(changed.dirty).toBe(true)

    act(() => useGraphStore.getState().undo())

    const restored = useGraphStore.getState()
    expect(restored.nodes).toEqual(originalNodes)
    expect(restored.edges).toEqual(originalEdges)
    expect(restored.submodels).toEqual(originalSubmodels)
    expect(restored.preamble).toBe("PARENT = 1")
    expect(restored.dirty).toBe(false)
  })
})
