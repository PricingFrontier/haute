import { describe, it, expect, vi, afterEach } from "vitest"
import { renderHook, cleanup, act } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import useEdgeHandlers from "../useEdgeHandlers"
import { NODE_TYPES } from "../../utils/nodeTypes"

vi.mock("@xyflow/react", async () => {
  const actual = await vi.importActual("@xyflow/react")
  return {
    ...actual,
    addEdge: (params: Record<string, unknown>, eds: Edge[]) => [...eds, { id: `e_${params.source}_${params.target}`, ...params }],
  }
})

function makeParams() {
  return {
    selectedNode: null as Node | null,
    graphRef: { current: { nodes: [] as Node[], edges: [] as Edge[] } },
    nodeIdCounter: { current: 0 },
    lastSelectedNodeRef: { current: null as Node | null },
    setNodes: vi.fn((updater: (nds: Node[]) => Node[]) => updater([])),
    setEdges: vi.fn((updater: (eds: Edge[]) => Edge[]) => updater([])),
    setSelectedNode: vi.fn(),
    setContextMenu: vi.fn(),
    fetchPreview: vi.fn(),
    cancelPreview: vi.fn(),
    shouldSkipAutomaticPreview: vi.fn(() => false),
    clearTrace: vi.fn(),
    screenToFlowPosition: vi.fn((pos: { x: number; y: number }) => pos),
    graphRefreshingRef: { current: 0 },
  }
}

describe("useEdgeHandlers", () => {
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it("onConnect creates a new edge", () => {
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onConnect({
        source: "a",
        target: "b",
        sourceHandle: null,
        targetHandle: null,
      })
    })
    expect(params.setEdges).toHaveBeenCalledOnce()
  })

  it("onConnect prevents self-loop", () => {
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onConnect({
        source: "a",
        target: "a",
        sourceHandle: null,
        targetHandle: null,
      })
    })
    expect(params.setEdges).not.toHaveBeenCalled()
  })

  it("onConnect prevents duplicate edges", () => {
    const params = makeParams()
    params.graphRef.current.edges = [
      { id: "e1", source: "a", target: "b", sourceHandle: null, targetHandle: null } as Edge,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onConnect({
        source: "a",
        target: "b",
        sourceHandle: null,
        targetHandle: null,
      })
    })
    expect(params.setEdges).not.toHaveBeenCalled()
  })

  it("onConnect preserves targetHandle for submodel nodes", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "sm1", data: { label: "SM", nodeType: NODE_TYPES.SUBMODEL } } as unknown as Node,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onConnect({
        source: "a",
        target: "sm1",
        sourceHandle: null,
        targetHandle: "in__child1",
      })
    })
    expect(params.setEdges).toHaveBeenCalledOnce()
    // targetHandle is preserved for submodel navigation
    const updater = params.setEdges.mock.calls[0][0] as (eds: Edge[]) => Edge[]
    const newEdges = updater([])
    expect(newEdges[0]).toHaveProperty("targetHandle", "in__child1")
  })

  it("onConnect blocks when target node has reached maxInputs", () => {
    const params = makeParams()
    const expanderNode = {
      id: "exp1",
      data: { label: "Expander", nodeType: NODE_TYPES.SCENARIO_EXPANDER },
    } as unknown as Node
    params.graphRef.current.nodes = [expanderNode]
    params.graphRef.current.edges = [
      { id: "e1", source: "a", target: "exp1" } as Edge,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onConnect({
        source: "b",
        target: "exp1",
        sourceHandle: null,
        targetHandle: null,
      })
    })
    expect(params.setEdges).not.toHaveBeenCalled()
  })

  it("onConnect allows connection when target has not reached maxInputs", () => {
    const params = makeParams()
    const expanderNode = {
      id: "exp1",
      data: { label: "Expander", nodeType: NODE_TYPES.SCENARIO_EXPANDER },
    } as unknown as Node
    params.graphRef.current.nodes = [expanderNode]
    params.graphRef.current.edges = []
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onConnect({
        source: "a",
        target: "exp1",
        sourceHandle: null,
        targetHandle: null,
      })
    })
    expect(params.setEdges).toHaveBeenCalledOnce()
  })

  it("onConnect allows multiple inputs for nodes without maxInputs", () => {
    const params = makeParams()
    const transformNode = {
      id: "t1",
      data: { label: "Transform", nodeType: NODE_TYPES.POLARS },
    } as unknown as Node
    params.graphRef.current.nodes = [transformNode]
    params.graphRef.current.edges = [
      { id: "e1", source: "a", target: "t1" } as Edge,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onConnect({
        source: "b",
        target: "t1",
        sourceHandle: null,
        targetHandle: null,
      })
    })
    expect(params.setEdges).toHaveBeenCalledOnce()
  })

  it("onSelectionChange with single node does NOT open panel (drag-safe)", () => {
    const params = makeParams()
    const node = { id: "n1", position: { x: 0, y: 0 }, data: { label: "A" } } as Node
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onSelectionChange({ nodes: [node], edges: [] })
    })
    // Panel opening moved to onNodeClick — selection alone should not trigger it
    expect(params.fetchPreview).not.toHaveBeenCalled()
    expect(params.setSelectedNode).not.toHaveBeenCalled()
  })

  it("onSelectionChange with no nodes sets selectedNode to null", () => {
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onSelectionChange({ nodes: [], edges: [] })
    })
    expect(params.setSelectedNode).toHaveBeenCalledWith(null)
    expect(params.clearTrace).toHaveBeenCalled()
  })

  it("onSelectionChange skips deselection when graphRefreshingRef is true", () => {
    const params = makeParams()
    params.graphRefreshingRef.current = 1
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onSelectionChange({ nodes: [], edges: [] })
    })
    expect(params.setSelectedNode).not.toHaveBeenCalled()
    expect(params.clearTrace).not.toHaveBeenCalled()
  })

  it("onNodeClick opens panel and fetches preview", () => {
    const params = makeParams()
    const node = { id: "n1", position: { x: 0, y: 0 }, data: { label: "A" } } as Node
    const event = {} as React.MouseEvent
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onNodeClick(event, node)
    })
    expect(params.setSelectedNode).toHaveBeenCalledWith(node)
    expect(params.clearTrace).toHaveBeenCalled()
    expect(params.cancelPreview).toHaveBeenCalledOnce()
    expect(params.lastSelectedNodeRef.current).toBe(node)
    expect(params.fetchPreview).toHaveBeenCalledWith(node, {})
  })

  it("onNodeClick fetches optimiser preview with an idle delay", () => {
    const params = makeParams()
    const node = {
      id: "optimiser1",
      position: { x: 0, y: 0 },
      data: { label: "Optimiser", nodeType: NODE_TYPES.OPTIMISER },
    } as Node
    const event = {} as React.MouseEvent

    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onNodeClick(event, node)
    })

    expect(params.setSelectedNode).toHaveBeenCalledWith(node)
    expect(params.clearTrace).toHaveBeenCalled()
    expect(params.cancelPreview).toHaveBeenCalledOnce()
    expect(params.lastSelectedNodeRef.current).toBe(node)
    expect(params.fetchPreview).toHaveBeenCalledWith(node, {
      debounceMs: 800,
    })
  })

  it.each([
    NODE_TYPES.MODELLING,
    NODE_TYPES.DATA_SINK,
    NODE_TYPES.OUTPUT,
  ])("onNodeClick skips automatic preview for sink-only node type %s", (nodeType) => {
    const params = makeParams()
    const node = {
      id: "sink1",
      position: { x: 0, y: 0 },
      data: { label: "Sink", nodeType },
    } as Node
    const event = {} as React.MouseEvent

    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onNodeClick(event, node)
    })

    expect(params.setSelectedNode).toHaveBeenCalledWith(node)
    expect(params.clearTrace).toHaveBeenCalled()
    expect(params.cancelPreview).toHaveBeenCalledOnce()
    expect(params.lastSelectedNodeRef.current).toBe(node)
    expect(params.fetchPreview).not.toHaveBeenCalled()
  })

  it("onNodeClick skips automatic preview when a result panel will render", () => {
    const params = makeParams()
    params.shouldSkipAutomaticPreview.mockReturnValue(true)
    const node = {
      id: "optimiser1",
      position: { x: 0, y: 0 },
      data: { label: "Optimiser", nodeType: NODE_TYPES.OPTIMISER },
    } as Node
    const event = {} as React.MouseEvent

    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onNodeClick(event, node)
    })

    expect(params.setSelectedNode).toHaveBeenCalledWith(node)
    expect(params.clearTrace).toHaveBeenCalled()
    expect(params.cancelPreview).toHaveBeenCalledOnce()
    expect(params.fetchPreview).not.toHaveBeenCalled()
  })

  it("onNodeClick skips fetchPreview when re-clicking the same node", () => {
    const params = makeParams()
    const node = { id: "n1", position: { x: 0, y: 0 }, data: { label: "A" } } as Node
    params.selectedNode = node
    const event = {} as React.MouseEvent
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onNodeClick(event, node)
    })
    expect(params.setSelectedNode).toHaveBeenCalledWith(node)
    expect(params.fetchPreview).not.toHaveBeenCalled()
    expect(params.clearTrace).not.toHaveBeenCalled()
    expect(params.cancelPreview).not.toHaveBeenCalled()
  })

  it("onNodeClick skips duplicate fetch before React re-renders selection props", () => {
    const params = makeParams()
    const node = { id: "n1", position: { x: 0, y: 0 }, data: { label: "A" } } as Node
    const event = {} as React.MouseEvent

    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onNodeClick(event, node)
      result.current.onNodeClick(event, node)
    })

    expect(params.fetchPreview).toHaveBeenCalledOnce()
  })

  it("onNodeClick fetches rapid distinct selections immediately", () => {
    const params = makeParams()
    const first = { id: "n1", position: { x: 0, y: 0 }, data: { label: "A" } } as Node
    const second = { id: "n2", position: { x: 0, y: 0 }, data: { label: "B" } } as Node
    const event = {} as React.MouseEvent

    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onNodeClick(event, first)
      result.current.onNodeClick(event, second)
    })

    expect(params.fetchPreview).toHaveBeenNthCalledWith(1, first, {})
    expect(params.fetchPreview).toHaveBeenNthCalledWith(2, second, {})
    expect(params.lastSelectedNodeRef.current).toBe(second)
  })

  it("handleDeleteEdge removes edge by id", () => {
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.handleDeleteEdge("e1")
    })
    expect(params.setEdges).toHaveBeenCalledOnce()
    const updater = params.setEdges.mock.calls[0][0] as (eds: Edge[]) => Edge[]
    const remaining = updater([{ id: "e1" } as Edge, { id: "e2" } as Edge])
    expect(remaining).toHaveLength(1)
    expect(remaining[0].id).toBe("e2")
  })

  it("onNodeContextMenu sets context menu with correct data", () => {
    const params = makeParams()
    const node = { id: "n1", data: { label: "Test Node", nodeType: "polars" } } as unknown as Node
    const { result } = renderHook(() => useEdgeHandlers(params))
    const event = { preventDefault: vi.fn(), clientX: 100, clientY: 200 } as unknown as React.MouseEvent
    act(() => {
      result.current.onNodeContextMenu(event, node)
    })
    expect(event.preventDefault).toHaveBeenCalled()
    expect(params.setContextMenu).toHaveBeenCalledWith({
      x: 100,
      y: 200,
      nodeId: "n1",
      nodeLabel: "Test Node",
      isSubmodel: false,
      isSingleton: false,
    })
  })

  it("onNodeContextMenu marks apiInput nodes as singleton", () => {
    const params = makeParams()
    const node = { id: "n1", data: { label: "Quote Input", nodeType: "apiInput" } } as unknown as Node
    const { result } = renderHook(() => useEdgeHandlers(params))
    const event = { preventDefault: vi.fn(), clientX: 50, clientY: 60 } as unknown as React.MouseEvent
    act(() => {
      result.current.onNodeContextMenu(event, node)
    })
    expect(params.setContextMenu).toHaveBeenCalledWith(
      expect.objectContaining({ isSingleton: true }),
    )
  })

  it("onNodeContextMenu marks output nodes as singleton", () => {
    const params = makeParams()
    const node = { id: "out1", data: { label: "Quote Response", nodeType: "output" } } as unknown as Node
    const { result } = renderHook(() => useEdgeHandlers(params))
    const event = { preventDefault: vi.fn(), clientX: 50, clientY: 60 } as unknown as React.MouseEvent
    act(() => {
      result.current.onNodeContextMenu(event, node)
    })
    expect(params.setContextMenu).toHaveBeenCalledWith(
      expect.objectContaining({ isSingleton: true }),
    )
  })

  it("onDrop creates a new node from drag data", () => {
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))
    const event = {
      preventDefault: vi.fn(),
      clientX: 300,
      clientY: 400,
      dataTransfer: {
        getData: vi.fn((key: string) => {
          if (key === "application/reactflow-type") return NODE_TYPES.POLARS
          if (key === "application/reactflow-config") return "{}"
          return ""
        }),
      },
    } as unknown as React.DragEvent
    act(() => {
      result.current.onDrop(event)
    })
    expect(params.setNodes).toHaveBeenCalledOnce()
    expect(params.setSelectedNode).toHaveBeenCalledOnce()
    expect(params.nodeIdCounter.current).toBe(1)
  })

  it("onDrop with no type does nothing", () => {
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))
    const event = {
      preventDefault: vi.fn(),
      clientX: 0,
      clientY: 0,
      dataTransfer: {
        getData: vi.fn(() => ""),
      },
    } as unknown as React.DragEvent
    act(() => {
      result.current.onDrop(event)
    })
    expect(params.setNodes).not.toHaveBeenCalled()
  })
})
