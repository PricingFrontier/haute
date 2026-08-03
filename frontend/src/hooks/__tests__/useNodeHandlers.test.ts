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
    setNodesAndEdges: vi.fn(),
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

  it("handleDeleteNode removes node and connected edges in ONE atomic step", () => {
    const params = makeParams()
    const n1 = makeNode("n1")
    const n2 = makeNode("n2")
    const edges = [{ id: "e1", source: "n1", target: "n2" } as Edge]
    params.graphRef.current = { nodes: [n1, n2], edges }
    const { result } = renderHook(() => useNodeHandlers(params))
    act(() => {
      result.current.handleDeleteNode("n1")
    })
    // Undo-atomicity: exactly one combined setNodesAndEdges call — never a
    // separate setNodes then setEdges (that would be two undo snapshots).
    expect(params.setNodesAndEdges).toHaveBeenCalledOnce()
    expect(params.setNodes).not.toHaveBeenCalled()
    const [nodesUpdater, edgesUpdater] = params.setNodesAndEdges.mock.calls[0] as [
      (nds: Node[]) => Node[],
      (eds: Edge[]) => Edge[],
    ]
    expect(nodesUpdater([n1, n2])).toEqual([n2])
    expect(edgesUpdater(edges)).toEqual([])
  })

  it("uses a committed shared deletion without a raw graph setter while cleaning selection", () => {
    const n1 = makeNode("n1")
    const params = makeParams()
    params.graphRef.current = { nodes: [n1], edges: [] }
    const commitSharedNodeDeletion = vi.fn(() => "committed" as const)
    const { result } = renderHook(() => useNodeHandlers({
      ...params,
      commitSharedNodeDeletion,
    }))
    act(() => result.current.handleDeleteNode("n1"))
    expect(params.setNodesAndEdges).not.toHaveBeenCalled()
    expect(params.setSelectedNode).toHaveBeenCalledOnce()
    expect(params.setPreviewData).toHaveBeenCalledOnce()
  })

  it("leaves graph and cleanup untouched when shared deletion is blocked", () => {
    const params = makeParams()
    params.graphRef.current = { nodes: [makeNode("n1")], edges: [] }
    const commitSharedNodeDeletion = vi.fn(() => "blocked" as const)
    const { result } = renderHook(() => useNodeHandlers({
      ...params,
      commitSharedNodeDeletion,
    }))
    act(() => result.current.handleDeleteNode("n1"))
    expect(params.setNodesAndEdges).not.toHaveBeenCalled()
    expect(params.setSelectedNode).not.toHaveBeenCalled()
    expect(params.setPreviewData).not.toHaveBeenCalled()
  })

  it("refuses raw deletion of a submodel occurrence", () => {
    const params = makeParams()
    const submodel = makeNode("submodel_10", "submodel", {
      data: {
        label: "Scoring",
        nodeType: "submodel",
        config: { definitionId: "definition_scoring", alias: "scoring" },
      },
    })
    params.graphRef.current = { nodes: [submodel], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))

    act(() => {
      result.current.handleDeleteNode(submodel.id)
    })

    expect(params.setNodesAndEdges).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts.at(-1)?.text).toMatch(/Dissolve Submodel/)
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

  it("refuses generic duplication of a submodel occurrence", () => {
    const params = makeParams()
    const submodel = makeNode("instance_a", "submodel", {
      data: {
        label: "Scoring",
        nodeType: "submodel",
        config: { definitionId: "definition_scoring", alias: "scoring" },
      },
    })
    params.graphRef.current = { nodes: [submodel], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))

    act(() => {
      result.current.handleDuplicateNode(submodel.id)
    })

    expect(params.setNodes).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts.at(-1)?.text).toMatch(/Create Instance/)
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
  it("creates a SUBMODEL occurrence without copying its shared definition", () => {
    const params = makeParams()
    const source = makeNode("submodel_10", "submodel", {
      position: { x: 100, y: 200 },
      data: {
        label: "Scoring",
        nodeType: "submodel",
        config: {
          definitionId: "definition_scoring",
          alias: "scoring",
          file: "modules/scoring.py",
          childNodeIds: ["internal_input", "internal_output"],
          graph: { nodes: [{ id: "internal_input" }], edges: [] },
        },
      },
    })
    const existing = makeNode("submodel_11", "submodel", {
      data: {
        label: "Scoring 2",
        nodeType: "submodel",
        config: {
          definitionId: "definition_scoring",
          alias: "scoring_2",
        },
      },
    })
    params.graphRef.current = { nodes: [source, existing], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))

    act(() => {
      result.current.handleCreateInstance(source.id)
    })

    expect(params.setNodes).toHaveBeenCalledOnce()
    expect(params.setNodesAndEdges).not.toHaveBeenCalled()
    expect(params.setSelectedNode).toHaveBeenCalledOnce()
    const created = params.setSelectedNode.mock.calls[0][0] as Node
    expect([source.id, existing.id]).not.toContain(created.id)
    expect(created.type).toBe("submodel")
    expect(created.data.nodeType).toBe("submodel")
    expect(created.data.config).toEqual({
      definitionId: "definition_scoring",
      alias: "scoring_3",
      instanceOf: source.id,
    })
    expect(created.position).toEqual({ x: 160, y: 280 })
    expect(created.data.config).not.toHaveProperty("graph")
    expect(created.data.config).not.toHaveProperty("file")
    expect(created.data.config).not.toHaveProperty("childNodeIds")
  })

  it("allocates occurrence ids and aliases across the combined identity namespace", () => {
    const params = makeParams()
    const source = makeNode("instance_source", "submodel", {
      data: {
        label: "Scoring",
        nodeType: "submodel",
        config: { definitionId: "definition_scoring", alias: "scoring" },
      },
    })
    const aliasOccupier = makeNode("instance_existing", "submodel", {
      data: {
        label: "Existing",
        nodeType: "submodel",
        config: { definitionId: "definition_other", alias: "submodel_11" },
      },
    })
    const nodeIdOccupier = makeNode("scoring_2")
    params.graphRef.current = {
      nodes: [source, aliasOccupier, nodeIdOccupier],
      edges: [],
    }
    const { result } = renderHook(() => useNodeHandlers(params))

    act(() => {
      result.current.handleCreateInstance(source.id)
    })

    const created = params.setSelectedNode.mock.calls[0][0] as Node
    expect(created.id).toBe("submodel_12")
    expect(created.data.config).toEqual({
      definitionId: "definition_scoring",
      alias: "scoring_3",
      instanceOf: source.id,
    })
  })

  it("rejects a partial reusable-submodel identity", () => {
    const params = makeParams()
    const source = makeNode("instance_source", "submodel", {
      data: {
        label: "Broken scoring",
        nodeType: "submodel",
        config: { alias: "scoring" },
      },
    })
    params.graphRef.current = { nodes: [source], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))

    act(() => {
      result.current.handleCreateInstance(source.id)
    })

    expect(params.setNodes).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts.at(-1)?.text).toMatch(/identity/)
  })

  it("normalises a suffixed source alias before choosing the next occurrence alias", () => {
    const params = makeParams()
    const base = makeNode("submodel_10", "submodel", {
      data: {
        label: "Scoring",
        nodeType: "submodel",
        config: {
          definitionId: "definition_scoring",
          alias: "scoring",
        },
      },
    })
    const source = makeNode("submodel_11", "submodel", {
      data: {
        label: "Scoring 2",
        nodeType: "submodel",
        config: {
          definitionId: "definition_scoring",
          alias: "scoring_2",
          instanceOf: base.id,
        },
      },
    })
    params.graphRef.current = { nodes: [base, source], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))

    act(() => {
      result.current.handleCreateInstance(source.id)
    })

    const created = params.setSelectedNode.mock.calls[0][0] as Node
    expect(created.data.config).toEqual({
      definitionId: "definition_scoring",
      alias: "scoring_3",
      instanceOf: base.id,
    })
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
