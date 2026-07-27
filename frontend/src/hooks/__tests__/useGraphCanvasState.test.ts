import { describe, it, expect, afterEach, vi } from "vitest"
import { renderHook, cleanup, act } from "@testing-library/react"
import useGraphCanvasState from "../useGraphCanvasState"
import useGraphStore from "../../stores/useGraphStore"
import { makeNode, makeEdge } from "../../test-utils/factories"

describe("useGraphCanvasState", () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it("initialises with provided nodes and edges", () => {
    const nodes = [makeNode("n1")]
    const edges = [makeEdge("n1", "n2", { id: "e1" })]
    const { result } = renderHook(() => useGraphCanvasState(nodes, edges))
    expect(result.current.nodes).toHaveLength(1)
    expect(result.current.edges).toHaveLength(1)
    expect(result.current.canUndo).toBe(false)
    expect(result.current.canRedo).toBe(false)
  })

  it("uses a full-snapshot load boundary on mount and remount", () => {
    useGraphStore.getState().loadGraphSnapshot({
      nodes: [makeNode("old")],
      edges: [],
      preamble: "import old",
      submodels: { old: { graph: { nodes: [makeNode("old-child")], edges: [] } } },
    })
    useGraphStore.getState().setNodes([makeNode("old"), makeNode("edited")])
    const first = renderHook(() => useGraphCanvasState([makeNode("first")], []))

    expect(useGraphStore.getState().preamble).toBe("")
    expect(useGraphStore.getState().submodels).toEqual({})
    expect(useGraphStore.getState().lastSavedSnapshot?.nodes.map((node) => node.id)).toEqual(["first"])
    expect(useGraphStore.getState().undoStack).toEqual([])
    expect(useGraphStore.getState().redoStack).toEqual([])
    expect(useGraphStore.getState().dirty).toBe(false)

    act(() => {
      first.result.current.setNodes([makeNode("first"), makeNode("first-edit")])
      first.result.current.undo()
    })
    expect(first.result.current.canRedo).toBe(true)
    first.unmount()

    const second = renderHook(() => useGraphCanvasState([makeNode("second")], []))
    expect(second.result.current.nodes.map((node) => node.id)).toEqual(["second"])
    expect(second.result.current.canUndo).toBe(false)
    expect(second.result.current.canRedo).toBe(false)
    act(() => {
      second.result.current.undo()
      second.result.current.redo()
    })
    expect(second.result.current.nodes.map((node) => node.id)).toEqual(["second"])
  })

  it("setNodes pushes snapshot and enables undo", () => {
    const { result } = renderHook(() => useGraphCanvasState([makeNode("n1")], []))
    act(() => {
      result.current.setNodes([makeNode("n1"), makeNode("n2")])
    })
    expect(result.current.canUndo).toBe(true)
    expect(result.current.canRedo).toBe(false)
  })

  it("setEdges pushes snapshot and enables undo", () => {
    const { result } = renderHook(() => useGraphCanvasState([], []))
    act(() => {
      result.current.setEdges([makeEdge("a", "b", { id: "e1" })])
    })
    expect(result.current.canUndo).toBe(true)
  })

  it("setNodesAndEdges applies both node and edge changes as one undo entry", () => {
    const initial = [makeNode("n1"), makeNode("n2")]
    const initialEdges = [makeEdge("n1", "n2", { id: "e1" })]
    const { result } = renderHook(() => useGraphCanvasState(initial, initialEdges))
    // Delete n1 and its edge in a single combined gesture.
    act(() => {
      result.current.setNodesAndEdges(
        (nds) => nds.filter((n) => n.id !== "n1"),
        (eds) => eds.filter((e) => e.source !== "n1" && e.target !== "n1"),
      )
    })
    expect(result.current.nodes.map((n) => n.id)).toEqual(["n2"])
    expect(result.current.edges).toHaveLength(0)
    // Exactly one undo entry — a single undo restores nodes AND edges together.
    expect(result.current.canUndo).toBe(true)
    act(() => {
      result.current.undo()
    })
    expect(result.current.nodes).toHaveLength(2)
    expect(result.current.edges).toHaveLength(1)
    expect(result.current.canUndo).toBe(false)
  })

  it("setNodesAndEdgesAndSubmodels snapshots the complete graph as one undo entry", () => {
    const initialNodes = [makeNode("n1"), makeNode("n2")]
    const initialEdges = [makeEdge("n1", "n2", { id: "e1" })]
    const submodels = {
      child: { graph: { nodes: [makeNode("child-node")], edges: [] } },
    }
    const { result } = renderHook(() => useGraphCanvasState(initialNodes, initialEdges))

    act(() => {
      result.current.setNodesAndEdgesAndSubmodels(
        (nodes) => nodes.filter((node) => node.id !== "n1"),
        [],
        submodels,
      )
    })

    expect(result.current.nodes.map((node) => node.id)).toEqual(["n2"])
    expect(result.current.edges).toEqual([])
    expect(useGraphStore.getState().submodels).toEqual(submodels)
    expect(useGraphStore.getState().undoStack).toHaveLength(1)

    act(() => {
      result.current.undo()
    })
    expect(result.current.nodes.map((node) => node.id)).toEqual(["n1", "n2"])
    expect(result.current.edges).toHaveLength(1)
    expect(useGraphStore.getState().submodels).toEqual({})
  })

  it("exposes raw submodel replacement and explicit snapshot actions", () => {
    const submodels = {
      child: { graph: { nodes: [makeNode("child-node")], edges: [] } },
    }
    const { result } = renderHook(() => useGraphCanvasState([makeNode("n1")], []))

    act(() => {
      result.current.setSubmodelsRaw(submodels)
    })
    expect(useGraphStore.getState().submodels).toEqual(submodels)
    expect(useGraphStore.getState().undoStack).toEqual([])

    act(() => {
      result.current.pushSnapshot()
    })
    expect(useGraphStore.getState().undoStack).toHaveLength(1)
    expect(useGraphStore.getState().undoStack[0]).toEqual(
      expect.objectContaining({ submodels }),
    )
  })

  it("undo restores previous state", () => {
    const initial = [makeNode("n1")]
    const { result } = renderHook(() => useGraphCanvasState(initial, []))
    act(() => {
      result.current.setNodes([makeNode("n1"), makeNode("n2")])
    })
    expect(result.current.nodes).toHaveLength(2)
    act(() => {
      result.current.undo()
    })
    expect(result.current.nodes).toHaveLength(1)
    expect(result.current.canRedo).toBe(true)
    expect(result.current.canUndo).toBe(false)
  })

  it("redo restores undone state", () => {
    const { result } = renderHook(() => useGraphCanvasState([makeNode("n1")], []))
    act(() => {
      result.current.setNodes([makeNode("n1"), makeNode("n2")])
    })
    act(() => {
      result.current.undo()
    })
    expect(result.current.nodes).toHaveLength(1)
    act(() => {
      result.current.redo()
    })
    expect(result.current.nodes).toHaveLength(2)
    expect(result.current.canUndo).toBe(true)
    expect(result.current.canRedo).toBe(false)
  })

  it("blocks undo and redo while a graph refresh is in flight", () => {
    const graphRefreshingRef = { current: 0 }
    const { result } = renderHook(() =>
      useGraphCanvasState([makeNode("n1")], [], graphRefreshingRef),
    )
    act(() => {
      result.current.setNodes([makeNode("n1"), makeNode("n2")])
    })
    expect(result.current.nodes).toHaveLength(2)

    graphRefreshingRef.current = 1
    act(() => {
      result.current.undo()
    })
    expect(result.current.nodes).toHaveLength(2)

    graphRefreshingRef.current = 0
    act(() => {
      result.current.undo()
    })
    expect(result.current.nodes).toHaveLength(1)

    graphRefreshingRef.current = 1
    act(() => {
      result.current.redo()
    })
    expect(result.current.nodes).toHaveLength(1)
  })

  it("new change after undo clears redo stack", () => {
    const { result } = renderHook(() => useGraphCanvasState([makeNode("n1")], []))
    act(() => {
      result.current.setNodes([makeNode("n1"), makeNode("n2")])
    })
    act(() => {
      result.current.undo()
    })
    expect(result.current.canRedo).toBe(true)
    act(() => {
      result.current.setNodes([makeNode("n3")])
    })
    expect(result.current.canRedo).toBe(false)
  })

  it("undo with empty history does nothing", () => {
    const { result } = renderHook(() => useGraphCanvasState([makeNode("n1")], []))
    act(() => {
      result.current.undo()
    })
    expect(result.current.nodes).toHaveLength(1)
  })

  it("redo with empty future does nothing", () => {
    const { result } = renderHook(() => useGraphCanvasState([makeNode("n1")], []))
    act(() => {
      result.current.redo()
    })
    expect(result.current.nodes).toHaveLength(1)
  })

  it("setNodesRaw bypasses history", () => {
    const { result } = renderHook(() => useGraphCanvasState([], []))
    act(() => {
      result.current.setNodesRaw([makeNode("n1"), makeNode("n2")])
    })
    expect(result.current.nodes).toHaveLength(2)
    // No snapshot was pushed — undo should remain unavailable
    expect(result.current.canUndo).toBe(false)
  })

  it("setEdgesRaw bypasses history", () => {
    const { result } = renderHook(() => useGraphCanvasState([], []))
    act(() => {
      result.current.setEdgesRaw([makeEdge("a", "b", { id: "e1" })])
    })
    expect(result.current.edges).toHaveLength(1)
    expect(result.current.canUndo).toBe(false)
  })

  it("onNodesChange with structural change pushes snapshot", () => {
    const { result } = renderHook(() => useGraphCanvasState([makeNode("n1")], []))
    act(() => {
      result.current.onNodesChange([{ type: "add", item: makeNode("n2") }])
    })
    expect(result.current.canUndo).toBe(true)
  })

  it("onNodesChange bumps structuralVersion only for structural graph changes", () => {
    const { result } = renderHook(() => useGraphCanvasState([makeNode("n1")], []))
    const startVersion = useGraphStore.getState().structuralVersion

    act(() => {
      result.current.onNodesChange([
        { type: "position", id: "n1", position: { x: 100, y: 100 } },
      ])
    })
    expect(useGraphStore.getState().structuralVersion).toBe(startVersion)

    act(() => {
      result.current.onNodesChange([{ type: "add", item: makeNode("n2") }])
    })
    expect(useGraphStore.getState().structuralVersion).toBeGreaterThan(startVersion)
  })

  it("onEdgesChange with structural change pushes snapshot", () => {
    const { result } = renderHook(() => useGraphCanvasState([], [makeEdge("a", "b", { id: "e1" })]))
    act(() => {
      result.current.onEdgesChange([{ type: "remove", id: "e1" }])
    })
    expect(result.current.canUndo).toBe(true)
  })

  it("onEdgesChange bumps structuralVersion for rewiring changes", () => {
    const { result } = renderHook(() => useGraphCanvasState([], [makeEdge("a", "b", { id: "e1" })]))
    const startVersion = useGraphStore.getState().structuralVersion

    act(() => {
      result.current.onEdgesChange([{ type: "remove", id: "e1" }])
    })
    expect(useGraphStore.getState().structuralVersion).toBeGreaterThan(startVersion)
  })

  // ─────────────────────────────────────────────────────────────────
  // MAX_HISTORY (100) cap — evicts oldest snapshot
  // Catches: if the cap is removed or miscalculated, undo history
  // would grow unbounded, causing OOM on long editing sessions.
  // ─────────────────────────────────────────────────────────────────

  it("101st snapshot evicts the oldest entry (MAX_HISTORY=100)", () => {
    const { result } = renderHook(() => useGraphCanvasState([makeNode("n0")], []))

    // Push 101 snapshots (each setNodes call pushes one)
    for (let i = 1; i <= 101; i++) {
      act(() => {
        result.current.setNodes([makeNode(`n${i}`)])
      })
    }

    // We should be able to undo exactly 100 times (MAX_HISTORY)
    let undoCount = 0
    while (result.current.canUndo) {
      act(() => {
        result.current.undo()
      })
      undoCount++
      // Safety guard to prevent infinite loop in case of bug
      if (undoCount > 150) break
    }

    expect(undoCount).toBe(100)
    expect(result.current.canUndo).toBe(false)
  })

  // ─────────────────────────────────────────────────────────────────
  // Drag snapshot behavior — snapshot on drag start, not during drag
  // Catches: if drag events pushed a snapshot on every position change
  // (mousemove), the undo history would fill with useless intermediate
  // positions and the user couldn't undo back to pre-drag position.
  // ─────────────────────────────────────────────────────────────────

  it("drag start pushes one snapshot; mid-drag position changes do not", () => {
    const node = makeNode("n1")
    const { result } = renderHook(() => useGraphCanvasState([node], []))

    // Simulate drag start
    act(() => {
      result.current.onNodesChange([
        { type: "position", id: "n1", dragging: true, position: { x: 10, y: 10 } },
      ])
    })
    expect(result.current.canUndo).toBe(true)

    // Record undo availability before mid-drag changes
    // Undo once to consume the drag-start snapshot
    act(() => {
      result.current.undo()
    })
    expect(result.current.canUndo).toBe(false)

    // Redo to get back, then simulate more mid-drag position changes
    act(() => {
      result.current.redo()
    })

    // Mid-drag: dragging is still true — should NOT push another snapshot
    act(() => {
      result.current.onNodesChange([
        { type: "position", id: "n1", dragging: true, position: { x: 50, y: 50 } },
      ])
    })
    act(() => {
      result.current.onNodesChange([
        { type: "position", id: "n1", dragging: true, position: { x: 100, y: 100 } },
      ])
    })

    // Drag end
    act(() => {
      result.current.onNodesChange([
        { type: "position", id: "n1", dragging: false, position: { x: 100, y: 100 } },
      ])
    })

    // Undo should go back to the state before the drag started (1 undo)
    // The mid-drag position changes should not have created additional snapshots
    act(() => {
      result.current.undo()
    })
    // After one undo we should be back at the original (pre-drag) state
    expect(result.current.canUndo).toBe(false)
  })

  // ─────────────────────────────────────────────────────────────────
  // Position-only changes (non-drag) should NOT push snapshots
  // Catches: if all position changes pushed snapshots, React Flow's
  // internal layout adjustments would pollute the undo history.
  // ─────────────────────────────────────────────────────────────────

  it("multiple structural changes in a single onNodesChange call push only one snapshot", () => {
    const { result } = renderHook(() =>
      useGraphCanvasState([makeNode("n1"), makeNode("n2")], []),
    )
    act(() => {
      result.current.onNodesChange([
        { type: "add", item: makeNode("n3") },
        { type: "remove", id: "n2" },
      ])
    })
    expect(result.current.canUndo).toBe(true)
    act(() => {
      result.current.undo()
    })
    expect(result.current.canUndo).toBe(false)
  })

  it("onEdgesChange with add type pushes snapshot", () => {
    const { result } = renderHook(() => useGraphCanvasState([], []))
    act(() => {
      result.current.onEdgesChange([
        { type: "add", item: makeEdge("a", "b", { id: "e1" }) },
      ])
    })
    expect(result.current.canUndo).toBe(true)
  })

  it("position-only change without dragging does not push a snapshot", () => {
    const node = makeNode("n1")
    const { result } = renderHook(() => useGraphCanvasState([node], []))

    // A position change with no dragging flag (e.g. from fitView or layout)
    act(() => {
      result.current.onNodesChange([
        { type: "position", id: "n1", position: { x: 200, y: 200 } },
      ])
    })

    // No snapshot should have been pushed
    expect(result.current.canUndo).toBe(false)
  })
})
