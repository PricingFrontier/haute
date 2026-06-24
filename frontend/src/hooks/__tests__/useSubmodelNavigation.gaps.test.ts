import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import useSubmodelNavigation from "../useSubmodelNavigation"
import useToastStore from "../../stores/useToastStore"
import useGraphStore from "../../stores/useGraphStore"
import { makeNode, makeEdge } from "../../test-utils/factories"

vi.mock("../../api/client", () => ({
  createSubmodel: vi.fn(),
  loadSubmodel: vi.fn(),
  dissolveSubmodel: vi.fn(),
}))

vi.mock("../../utils/layout", () => ({
  getLayoutedElements: vi.fn(async (nodes: Node[]) => nodes),
}))

import { loadSubmodel } from "../../api/client"
const mockLoad = vi.mocked(loadSubmodel)

function makeParams(overrides: Partial<Parameters<typeof useSubmodelNavigation>[0]> = {}) {
  return {
    graphRef: { current: { nodes: [makeNode("n1"), makeNode("n2")] as Node[], edges: [] as Edge[] } },
    parentGraphRef: { current: null as { nodes: Node[]; edges: Edge[]; submodels: Record<string, unknown> } | null },
    submodelsRef: { current: {} as Record<string, unknown> },
    setNodesRaw: vi.fn(),
    setEdgesRaw: vi.fn(),
    setSelectedNode: vi.fn(),
    setPreviewData: vi.fn(),
    preambleRef: { current: "" },
    descriptionRef: { current: "" },
    sourceFileRef: { current: "test.py" },
    pipelineNameRef: { current: "test" },
    fitView: vi.fn(),
    ...overrides,
  }
}

describe("useSubmodelNavigation — port building & branch gaps", () => {
  beforeEach(() => {
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
    useGraphStore.setState({ lastSavedSnapshot: null })
    mockLoad.mockReset()
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it("builds an input port node + edge from a parent cross-boundary edge", async () => {
    vi.useFakeTimers()
    mockLoad.mockResolvedValue({
      status: "ok",
      submodel_name: "pricing",
      graph: { nodes: [makeNode("child1")], edges: [] },
    })
    // Parent feeds source node "src1" into submodel via in__child1 handle.
    const params = makeParams()
    params.graphRef.current = {
      nodes: [makeNode("src1", "polars", { data: { label: "Source One" } })],
      edges: [
        makeEdge("src1", "submodel__pricing", {
          targetHandle: "in__child1",
        }),
      ],
    }
    const { result } = renderHook(() => useSubmodelNavigation(params))
    await act(async () => {
      await result.current.handleDrillIntoSubmodel("submodel__pricing")
    })

    // The layouted nodes passed to setNodesRaw should include the input port.
    const setNodes = params.setNodesRaw as ReturnType<typeof vi.fn>
    const lastNodes: Node[] = setNodes.mock.calls.at(-1)![0]
    const inputPort = lastNodes.find((n) => n.id === "port_in__src1")
    expect(inputPort).toBeDefined()
    expect(inputPort!.data).toMatchObject({ portDirection: "input", label: "Source One" })

    // And an edge port -> child should be created.
    const setEdges = params.setEdgesRaw as ReturnType<typeof vi.fn>
    const lastEdges: Edge[] = setEdges.mock.calls.at(-1)![0]
    expect(lastEdges.some((e) => e.source === "port_in__src1" && e.target === "child1")).toBe(true)
    vi.useRealTimers()
  })

  it("falls back to source id label when targetHandle missing and child absent", async () => {
    vi.useFakeTimers()
    mockLoad.mockResolvedValue({
      status: "ok",
      submodel_name: "pricing",
      graph: { nodes: [makeNode("child1")], edges: [] },
    })
    const params = makeParams()
    // Edge with no targetHandle -> childId "__unconnected__" which is not in childIds,
    // so the port node is built but the inner edge is skipped (continue branch).
    // Also no parent node for "ghost" -> label falls back to the source id.
    params.graphRef.current = {
      nodes: [],
      edges: [makeEdge("ghost", "submodel__pricing")],
    }
    const { result } = renderHook(() => useSubmodelNavigation(params))
    await act(async () => {
      await result.current.handleDrillIntoSubmodel("submodel__pricing")
    })

    const setNodes = params.setNodesRaw as ReturnType<typeof vi.fn>
    const lastNodes: Node[] = setNodes.mock.calls.at(-1)![0]
    const inputPort = lastNodes.find((n) => n.id === "port_in__ghost")
    expect(inputPort).toBeDefined()
    expect(inputPort!.data).toMatchObject({ label: "ghost" })

    const setEdges = params.setEdgesRaw as ReturnType<typeof vi.fn>
    const lastEdges: Edge[] = setEdges.mock.calls.at(-1)![0]
    // No edge to "__unconnected__" since that child isn't in the submodel graph.
    expect(lastEdges.some((e) => e.source === "port_in__ghost")).toBe(false)
    vi.useRealTimers()
  })

  it("builds an output port node + edge from a parent cross-boundary edge", async () => {
    vi.useFakeTimers()
    mockLoad.mockResolvedValue({
      status: "ok",
      submodel_name: "pricing",
      graph: { nodes: [makeNode("child1")], edges: [] },
    })
    const params = makeParams()
    // Submodel outputs from child1 (out__child1 handle) into parent "tgt1".
    params.graphRef.current = {
      nodes: [makeNode("tgt1", "polars", { data: { label: "Target One" } })],
      edges: [
        makeEdge("submodel__pricing", "tgt1", {
          sourceHandle: "out__child1",
        }),
      ],
    }
    const { result } = renderHook(() => useSubmodelNavigation(params))
    await act(async () => {
      await result.current.handleDrillIntoSubmodel("submodel__pricing")
    })

    const setNodes = params.setNodesRaw as ReturnType<typeof vi.fn>
    const lastNodes: Node[] = setNodes.mock.calls.at(-1)![0]
    const outputPort = lastNodes.find((n) => n.id === "port_out__tgt1")
    expect(outputPort).toBeDefined()
    expect(outputPort!.data).toMatchObject({ portDirection: "output", label: "Target One" })

    const setEdges = params.setEdgesRaw as ReturnType<typeof vi.fn>
    const lastEdges: Edge[] = setEdges.mock.calls.at(-1)![0]
    expect(lastEdges.some((e) => e.source === "child1" && e.target === "port_out__tgt1")).toBe(true)
    vi.useRealTimers()
  })

  it("skips output edges whose child is not part of the submodel graph", async () => {
    vi.useFakeTimers()
    mockLoad.mockResolvedValue({
      status: "ok",
      submodel_name: "pricing",
      graph: { nodes: [makeNode("child1")], edges: [] },
    })
    const params = makeParams()
    // sourceHandle references a child ("missing") not in the submodel -> skipped.
    params.graphRef.current = {
      nodes: [makeNode("tgt1")],
      edges: [
        makeEdge("submodel__pricing", "tgt1", {
          sourceHandle: "out__missing",
        }),
      ],
    }
    const { result } = renderHook(() => useSubmodelNavigation(params))
    await act(async () => {
      await result.current.handleDrillIntoSubmodel("submodel__pricing")
    })

    const setNodes = params.setNodesRaw as ReturnType<typeof vi.fn>
    const lastNodes: Node[] = setNodes.mock.calls.at(-1)![0]
    // No output port built because the only candidate child was filtered out.
    expect(lastNodes.some((n) => n.id === "port_out__tgt1")).toBe(false)
    vi.useRealTimers()
  })

  it("ignores parent output edges that lack a sourceHandle", async () => {
    vi.useFakeTimers()
    mockLoad.mockResolvedValue({
      status: "ok",
      submodel_name: "pricing",
      graph: { nodes: [makeNode("child1")], edges: [] },
    })
    const params = makeParams()
    params.graphRef.current = {
      nodes: [makeNode("tgt1")],
      // Source is the submodel but no sourceHandle -> filtered out before the loop.
      edges: [makeEdge("submodel__pricing", "tgt1")],
    }
    const { result } = renderHook(() => useSubmodelNavigation(params))
    await act(async () => {
      await result.current.handleDrillIntoSubmodel("submodel__pricing")
    })

    const setNodes = params.setNodesRaw as ReturnType<typeof vi.fn>
    const lastNodes: Node[] = setNodes.mock.calls.at(-1)![0]
    expect(lastNodes.some((n) => n.id.startsWith("port_out__"))).toBe(false)
    vi.useRealTimers()
  })

  it("handleBreadcrumbNavigate clears parentGraphRef only when returning to depth 0", async () => {
    vi.useFakeTimers()
    mockLoad.mockResolvedValue({
      status: "ok",
      submodel_name: "pricing",
      graph: { nodes: [makeNode("child1")], edges: [] },
    })
    const params = makeParams()
    params.graphRef.current = { nodes: [makeNode("n1")], edges: [] }
    const { result } = renderHook(() => useSubmodelNavigation(params))
    await act(async () => {
      await result.current.handleDrillIntoSubmodel("submodel__pricing")
    })
    // parentGraphRef populated by the drill-in.
    expect(params.parentGraphRef.current).not.toBeNull()

    act(() => {
      result.current.handleBreadcrumbNavigate(0)
    })
    expect(params.parentGraphRef.current).toBeNull()
    expect(result.current.viewStack).toHaveLength(1)
    vi.useRealTimers()
  })

  it("handleDrillIntoSubmodel no-ops when API returns no graph", async () => {
    // Deliberately malformed (graph absent) to exercise the no-graph defensive branch.
    mockLoad.mockResolvedValue({
      status: "ok",
      submodel_name: "pricing",
      graph: undefined,
    } as unknown as Awaited<ReturnType<typeof loadSubmodel>>)
    const params = makeParams()
    const { result } = renderHook(() => useSubmodelNavigation(params))
    await act(async () => {
      await result.current.handleDrillIntoSubmodel("submodel__pricing")
    })
    // View stack stays at pipeline level; no node updates.
    expect(result.current.viewStack).toHaveLength(1)
    expect(params.setNodesRaw).not.toHaveBeenCalled()
  })
})
