import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import useNodeHandlers from "../useNodeHandlers"
import useToastStore from "../../stores/useToastStore"
import useNodeResultsStore from "../../stores/useNodeResultsStore"
import { makeNode } from "../../test-utils/factories"
import { getLayoutedElements } from "../../utils/layout"

vi.mock("../../utils/layout", () => ({
  getLayoutedElements: vi.fn(async (nodes: Node[]) => nodes),
}))

function makeParams() {
  return {
    graphRef: { current: { nodes: [] as Node[], edges: [] as Edge[] } },
    nodeIdCounter: { current: 10 },
    lastSelectedNodeRef: { current: null as Node | null },
    setNodes: vi.fn(),
    setEdges: vi.fn(),
    setSelectedNode: vi.fn(),
    setPreviewData: vi.fn(),
    fitView: vi.fn(),
  }
}

describe("useNodeHandlers", () => {
  beforeEach(() => {
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
    useNodeResultsStore.setState({ previews: {}, columnCache: {} })
    vi.mocked(getLayoutedElements).mockReset()
    vi.mocked(getLayoutedElements).mockImplementation(async (nodes: Node[]) => nodes)
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it("handleDeleteNode removes node and connected edges", () => {
    const params = makeParams()
    const n1 = makeNode("n1")
    const n2 = makeNode("n2")
    params.graphRef.current = {
      nodes: [n1, n2],
      edges: [{ id: "e1", source: "n1", target: "n2" } as Edge],
    }
    const { result } = renderHook(() => useNodeHandlers(params))
    act(() => {
      result.current.handleDeleteNode("n1")
    })
    // setNodes/setEdges are called with updater functions
    const nodesUpdater = params.setNodes.mock.calls[0][0] as () => Node[]
    const edgesUpdater = params.setEdges.mock.calls[0][0] as () => Edge[]
    expect(nodesUpdater()).toEqual([n2])
    expect(edgesUpdater()).toEqual([])
  })

  it("handleDeleteNode clears selected node if it was selected", () => {
    const params = makeParams()
    const n1 = makeNode("n1")
    params.graphRef.current = { nodes: [n1], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))
    act(() => {
      result.current.handleDeleteNode("n1")
    })
    // setSelectedNode is called with a function that returns null when prev.id === id
    const updater = params.setSelectedNode.mock.calls[0][0] as (prev: Node | null) => Node | null
    expect(updater(n1)).toBeNull()
  })

  it("handleDeleteNode preserves selected node if different", () => {
    const params = makeParams()
    const n1 = makeNode("n1")
    const n2 = makeNode("n2")
    params.graphRef.current = { nodes: [n1, n2], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))
    act(() => {
      result.current.handleDeleteNode("n1")
    })
    const updater = params.setSelectedNode.mock.calls[0][0] as (prev: Node | null) => Node | null
    expect(updater(n2)).toBe(n2)
  })

  it("handleDuplicateNode creates a copy with offset position", () => {
    const params = makeParams()
    const n1 = makeNode("n1")
    n1.position = { x: 100, y: 200 }
    params.graphRef.current = { nodes: [n1], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))
    act(() => {
      result.current.handleDuplicateNode("n1")
    })
    expect(params.setNodes).toHaveBeenCalledOnce()
    expect(params.nodeIdCounter.current).toBe(11)
    expect(params.setSelectedNode).toHaveBeenCalledOnce()
    const newNode = params.setSelectedNode.mock.calls[0][0] as Node
    expect(newNode.position).toEqual({ x: 140, y: 240 })
    expect(newNode.data.label).toContain("copy")
  })

  it("handleDuplicateNode does nothing for singleton node types", () => {
    const params = makeParams()
    const apiNode = makeNode("api1")
    apiNode.data = { ...apiNode.data, nodeType: "apiInput" }
    params.graphRef.current = { nodes: [apiNode], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))
    act(() => {
      result.current.handleDuplicateNode("api1")
    })
    expect(params.setNodes).not.toHaveBeenCalled()
  })

  it("handleDuplicateNode does nothing for output node types", () => {
    const params = makeParams()
    const outputNode = makeNode("out1")
    outputNode.data = { ...outputNode.data, nodeType: "output" }
    params.graphRef.current = { nodes: [outputNode], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))
    act(() => {
      result.current.handleDuplicateNode("out1")
    })
    expect(params.setNodes).not.toHaveBeenCalled()
  })

  it("handleDuplicateNode does nothing if node not found", () => {
    const params = makeParams()
    params.graphRef.current = { nodes: [], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))
    act(() => {
      result.current.handleDuplicateNode("nonexistent")
    })
    expect(params.setNodes).not.toHaveBeenCalled()
  })

  it("handleCreateInstance creates an instance node with toast", () => {
    const params = makeParams()
    const n1 = makeNode("n1")
    params.graphRef.current = { nodes: [n1], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))
    act(() => {
      result.current.handleCreateInstance("n1")
    })
    expect(params.setNodes).toHaveBeenCalledOnce()
    expect(params.setSelectedNode).toHaveBeenCalledOnce()
    const newNode = params.setSelectedNode.mock.calls[0][0] as Node
    expect(newNode.data.config).toEqual({ instanceOf: "n1" })
    expect(newNode.data.label).toContain("instance")
    const toasts = useToastStore.getState().toasts
    expect(toasts[toasts.length - 1]).toMatchObject({ type: "info" })
  })

  it("handleAutoLayout applies layout and toasts", async () => {
    vi.useFakeTimers()
    const params = makeParams()
    const n1 = makeNode("n1")
    params.graphRef.current = { nodes: [n1], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))
    await act(async () => {
      await result.current.handleAutoLayout()
    })
    expect(params.setNodes).toHaveBeenCalledOnce()
    const toasts = useToastStore.getState().toasts
    expect(toasts[toasts.length - 1]).toMatchObject({ type: "info", text: "Auto-layout applied" })
    act(() => { vi.advanceTimersByTime(100) })
    expect(params.fitView).toHaveBeenCalledWith({ padding: 0.15 })
    vi.useRealTimers()
  })

  it("exposes pending auto-layout state while ELK is loading", async () => {
    let resolveLayout!: (nodes: Node[]) => void
    vi.mocked(getLayoutedElements).mockReturnValueOnce(new Promise((resolve) => {
      resolveLayout = resolve
    }))
    const params = makeParams()
    const n1 = makeNode("n1")
    params.graphRef.current = { nodes: [n1], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))

    expect(result.current.isAutoLayouting).toBe(false)

    await act(async () => {
      void result.current.handleAutoLayout()
    })

    expect(result.current.isAutoLayouting).toBe(true)
    expect(params.setNodes).not.toHaveBeenCalled()

    await act(async () => {
      resolveLayout([n1])
    })

    expect(result.current.isAutoLayouting).toBe(false)
    expect(params.setNodes).toHaveBeenCalledOnce()
  })

  it("does not queue overlapping auto-layout runs from repeated clicks", async () => {
    let resolveLayout!: (nodes: Node[]) => void
    vi.mocked(getLayoutedElements).mockReturnValueOnce(new Promise((resolve) => {
      resolveLayout = resolve
    }))
    const params = makeParams()
    const n1 = makeNode("n1")
    params.graphRef.current = { nodes: [n1], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))

    await act(async () => {
      void result.current.handleAutoLayout()
    })
    await act(async () => {
      void result.current.handleAutoLayout()
      void result.current.handleAutoLayout()
    })

    expect(getLayoutedElements).toHaveBeenCalledOnce()
    expect(params.setNodes).not.toHaveBeenCalled()

    await act(async () => {
      resolveLayout([n1])
    })

    expect(params.setNodes).toHaveBeenCalledOnce()
  })

  it("resets pending auto-layout state after a layout failure", async () => {
    const layoutError = new Error("ELK failed")
    vi.mocked(getLayoutedElements)
      .mockRejectedValueOnce(layoutError)
      .mockImplementationOnce(async (nodes: Node[]) => nodes)
    const params = makeParams()
    const n1 = makeNode("n1")
    params.graphRef.current = { nodes: [n1], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))
    let thrown: unknown

    await act(async () => {
      try {
        await result.current.handleAutoLayout()
      } catch (error) {
        thrown = error
      }
    })

    expect(thrown).toBe(layoutError)
    expect(result.current.isAutoLayouting).toBe(false)
    expect(params.setNodes).not.toHaveBeenCalled()

    await act(async () => {
      await result.current.handleAutoLayout()
    })

    expect(getLayoutedElements).toHaveBeenCalledTimes(2)
    expect(params.setNodes).toHaveBeenCalledOnce()
  })

  it("handleAutoLayout does nothing with empty graph", async () => {
    const params = makeParams()
    params.graphRef.current = { nodes: [], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))
    await act(async () => {
      await result.current.handleAutoLayout()
    })
    expect(params.setNodes).not.toHaveBeenCalled()
  })
})
