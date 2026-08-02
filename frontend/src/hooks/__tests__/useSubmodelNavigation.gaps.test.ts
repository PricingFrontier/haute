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
    setPreamble: vi.fn(),
    preambleRef: { current: "" },
    descriptionRef: { current: "" },
    sourceFileRef: { current: "test.py" },
    sourceRevisionRef: { current: "revision-test" },
    preservedBlocksRef: { current: [] as string[] },
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

  it("projects parent boundaries into one composite Input and Output", async () => {
    vi.useFakeTimers()
    mockLoad.mockResolvedValue({
      status: "ok",
      submodel_name: "pricing",
      submodel_file: "modules/pricing.py",
      graph: {
        nodes: [
          makeNode("child1", "edgeJoin", { data: { label: "Claims frame" } }),
          makeNode("child2", "polars", { data: { label: "Quote frame" } }),
        ],
        edges: [],
      },
    })
    const params = makeParams()
    params.graphRef.current = {
      nodes: [
        makeNode("src1", "apiInput", { data: { label: "Quote In" } }),
        makeNode("tgt1", "polars", { data: { label: "Target One" } }),
        makeNode("submodel__pricing", "submodel", {
          data: {
            label: "pricing",
            config: {
              outputPorts: ["child1"],
              outputPortLabels: { child1: "Claims frame" },
            },
          },
        }),
      ],
      edges: [
        {
          ...makeEdge("src1", "submodel__pricing"),
          id: "input-claims",
          sourceHandle: "proposer_claims",
          targetHandle: "in__child1",
          targetPort: "join",
        } as Edge,
        {
          ...makeEdge("src1", "submodel__pricing"),
          id: "input-quote",
          sourceHandle: "quote_info",
          targetHandle: "in__child2",
        } as Edge,
        {
          ...makeEdge("submodel__pricing", "tgt1"),
          id: "output-claims",
          sourceHandle: "out__child1",
          sourcePort: "result",
        } as Edge,
      ],
    }
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleDrillIntoSubmodel("submodel__pricing")
    })

    const lastNodes: Node[] = (params.setNodesRaw as ReturnType<typeof vi.fn>)
      .mock.calls.at(-1)![0]
    const boundaries = lastNodes.filter((node) => node.type === "submodelPort")
    expect(boundaries).toHaveLength(2)
    const input = boundaries.find((node) => node.data.portDirection === "input")
    const output = boundaries.find((node) => node.data.portDirection === "output")
    expect(input?.data).toMatchObject({
      label: "INPUT",
      ports: [
        { label: "proposer_claims" },
        { label: "quote_info" },
      ],
    })
    expect(output?.data).toMatchObject({
      label: "OUTPUT",
      ports: [],
    })

    const lastEdges: Edge[] = (params.setEdgesRaw as ReturnType<typeof vi.fn>)
      .mock.calls.at(-1)![0]
    expect(lastEdges).toEqual(expect.arrayContaining([
      expect.objectContaining({
        source: input?.id,
        target: "child1",
        targetHandle: "join",
      }),
      expect.objectContaining({
        source: "child1",
        target: output?.id,
        sourceHandle: "result",
        targetHandle: null,
      }),
    ]))
    vi.useRealTimers()
  })

  it("keeps an unassigned input available without auto-connecting it", async () => {
    vi.useFakeTimers()
    mockLoad.mockResolvedValue({
      status: "ok",
      submodel_name: "pricing",
      submodel_file: "modules/pricing.py",
      graph: { nodes: [makeNode("child1")], edges: [] },
    })
    const params = makeParams()
    params.graphRef.current = {
      nodes: [makeNode("src1"), makeNode("tgt1")],
      edges: [
        makeEdge("src1", "submodel__pricing"),
        makeEdge("submodel__pricing", "tgt1", {
          sourceHandle: "out__missing",
        }),
      ],
    }
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleDrillIntoSubmodel("submodel__pricing")
    })

    const lastNodes: Node[] = (params.setNodesRaw as ReturnType<typeof vi.fn>)
      .mock.calls.at(-1)![0]
    const boundaries = lastNodes.filter((node) => node.type === "submodelPort")
    expect(boundaries).toHaveLength(2)
    const input = boundaries.find((node) => node.data.portDirection === "input")
    const output = boundaries.find((node) => node.data.portDirection === "output")
    expect(input?.data.ports).toEqual([
      expect.objectContaining({ label: "Node src1" }),
    ])
    expect(output?.data.ports).toEqual([])

    const lastEdges: Edge[] = (params.setEdgesRaw as ReturnType<typeof vi.fn>)
      .mock.calls.at(-1)![0]
    expect(lastEdges).toEqual([])
    vi.useRealTimers()
  })

  it("handleBreadcrumbNavigate clears parentGraphRef only when returning to depth 0", async () => {
    vi.useFakeTimers()
    mockLoad.mockResolvedValue({
      status: "ok",
      submodel_name: "pricing",
      submodel_file: "modules/pricing.py",
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
      submodel_file: "modules/pricing.py",
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
