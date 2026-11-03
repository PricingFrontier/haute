/**
 * Deleting a node removes it from the graph first and then clears its cached
 * results in the same task, as the keyboard delete does: React renders both
 * store updates together, so no render shows the node without its results.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import useNodeHandlers from "../useNodeHandlers"
import useNodeResultsStore from "../../stores/useNodeResultsStore"
import useUIStore from "../../stores/useUIStore"
import { makeNode, makeSolveResult } from "../../test-utils/factories"

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
    submodels: {},
    resolveNodeIdentities: vi.fn(async (nodes: readonly Node[]) => [...nodes]),
  }
}

describe("useNodeHandlers - cache cleanup on delete", () => {
  beforeEach(() => {
    vi.useRealTimers()
    useNodeResultsStore.setState({
      previews: {},
      columnCache: {},
      solveResults: {},
      solveJobs: {},
      trainResults: {},
      trainJobs: {},
    })
    useUIStore.setState({ renameDialog: null, submodelDialog: null })
  })

  afterEach(() => {
    vi.useRealTimers()
    cleanup()
    vi.restoreAllMocks()
  })

  it("setNodes is called synchronously BEFORE clearNode commits", async () => {
    // The graph mutation runs while the node's result is still cached; the
    // cache is cleared only after it.
    const params = makeParams()
    const n1 = makeNode("n1")
    params.graphRef.current = { nodes: [n1], edges: [] }

    // Seed the store with an active solve job + result for n1
    useNodeResultsStore.setState({
      solveJobs: {},
      solveResults: {
        n1: {
          result: makeSolveResult({
            total_objective: 100,
            baseline_objective: 80,
            constraints: {},
            baseline_constraints: {},
            lambdas: {},
            converged: true,
          }),
          originalResult: makeSolveResult({
            total_objective: 100,
            baseline_objective: 80,
            constraints: {},
            baseline_constraints: {},
            lambdas: {},
            converged: true,
          }),
          jobId: "j1",
          configHash: "h1",
          source: "live",
          structuralVersion: 0,
          constraints: {},
          nodeLabel: "N1",
          frontier: null,
          selectedPointIndex: null,
        },
      },
    })

    const { result } = renderHook(() => useNodeHandlers(params))

    let snapshotDuringSetNodes: boolean | null = null
    // Arrange the graph setter to capture whether the cache is still intact
    // at the moment it is invoked.  If clearNode runs BEFORE the mutation,
    // snapshotDuringSetNodes will be false (bug). Delete now goes through the
    // atomic setNodesAndEdges, so hook that.
    params.setNodesAndEdges.mockImplementationOnce(() => {
      const res = useNodeResultsStore.getState().solveResults["n1"]
      snapshotDuringSetNodes = !!res
    })

    act(() => {
      result.current.handleDeleteNode("n1")
    })

    // Cache must still exist the instant the graph mutation was applied.
    expect(snapshotDuringSetNodes).toBe(true)
  })

  it("clears the deleted node's cache in the same task as its removal", () => {
    const params = makeParams()
    const n1 = makeNode("n1")
    params.graphRef.current = { nodes: [n1], edges: [] }
    useNodeResultsStore.getState().setPreview("n1", {
      nodeId: "n1",
      nodeLabel: "Node 1",
      status: "ok",
      row_count: 5,
      column_count: 1,
      columns: [{ name: "a", dtype: "f64" }],
      preview: [{ a: 1 }],
      error: null,
    }, 0)
    const { result } = renderHook(() => useNodeHandlers(params))

    act(() => {
      result.current.handleDeleteNode("n1")
    })

    expect(params.setNodesAndEdges).toHaveBeenCalledOnce()
    expect(useNodeResultsStore.getState().getPreview("n1")).toBeNull()
  })

  it("multiple rapid deletes each clear their own cache (no missed nodes)", () => {
    const params = makeParams()
    params.graphRef.current = { nodes: [makeNode("n1"), makeNode("n2"), makeNode("n3")], edges: [] }
    const { setPreview } = useNodeResultsStore.getState()
    const mkData = (id: string) => ({
      nodeId: id, nodeLabel: `Node ${id}`,
      status: "ok" as const, row_count: 1, column_count: 1,
      columns: [], preview: [], error: null,
    })
    setPreview("n1", mkData("n1"), 0)
    setPreview("n2", mkData("n2"), 0)
    setPreview("n3", mkData("n3"), 0)
    const { result } = renderHook(() => useNodeHandlers(params))

    act(() => {
      result.current.handleDeleteNode("n1")
      result.current.handleDeleteNode("n2")
      result.current.handleDeleteNode("n3")
    })

    expect(useNodeResultsStore.getState().getPreview("n1")).toBeNull()
    expect(useNodeResultsStore.getState().getPreview("n2")).toBeNull()
    expect(useNodeResultsStore.getState().getPreview("n3")).toBeNull()
  })
})
