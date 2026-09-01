/**
 * Phase 1 Package 1H — Item #32: deleting a node must NOT race cache cleanup
 * ahead of the React commit that removes the node from the DOM.
 *
 * Pre-fix: `clearNode(id)` is called synchronously in `handleDeleteNode`,
 * *before* React has committed the state update that removes the node.
 * Downstream components (like OptimiserPreview / ModellingPreview that read
 * from the store during render) then flip from "cached result" to "null" on
 * the same render cycle the node is still present — a flicker-crash.
 *
 * Fix: defer `clearNode` by one render cycle (queueMicrotask or
 * `setTimeout(..., 0)`) so downstream components see the node removed from
 * the graph BEFORE the store-backed cache is cleared.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import useNodeHandlers from "../useNodeHandlers"
import useNodeResultsStore from "../../stores/useNodeResultsStore"
import useUIStore from "../../stores/useUIStore"
import { makeNode } from "../../test-utils/factories"

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
    resolveNodeIdentities: vi.fn(async (nodes: readonly Node[]) => [...nodes]),
  }
}

describe("useNodeHandlers — cache cleanup deferred on delete (#32)", () => {
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
    // Catches: if clearNode fires before setNodes, any component reading
    // `useNodeResultsStore.getOptimiserPreview(nodeId)` during the same
    // render tick will see `null` while the graph still contains the node,
    // triggering a panel unmount/remount flicker.
    //
    // We verify by seeding an optimiser result, calling handleDeleteNode,
    // and confirming the store still has the result immediately after
    // setNodes is invoked.  Only after a tick should the cache be gone.
    const params = makeParams()
    const n1 = makeNode("n1")
    params.graphRef.current = { nodes: [n1], edges: [] }

    // Seed the store with an active solve job + result for n1
    useNodeResultsStore.setState({
      solveJobs: {},
      solveResults: {
        n1: {
          result: {
            total_objective: 100,
            baseline_objective: 80,
            constraints: {},
            baseline_constraints: {},
            lambdas: {},
            converged: true,
          },
          originalResult: {
            total_objective: 100,
            baseline_objective: 80,
            constraints: {},
            baseline_constraints: {},
            lambdas: {},
            converged: true,
          },
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

  it("clearNode is deferred past the current microtask (cache outlives the setNodes call)", async () => {
    // Catches: if `clearNode(id)` is still invoked synchronously after
    // `setNodes`, downstream selectors reading during the same render
    // cycle will observe a state where the node is gone from the cache
    // *before* React has committed the node removal.
    //
    // The fix is to schedule cleanup via queueMicrotask / setTimeout so
    // the cache entry survives at least the current render tick.
    const params = makeParams()
    const n1 = makeNode("n1")
    params.graphRef.current = { nodes: [n1], edges: [] }

    // Seed a preview entry
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

    vi.useFakeTimers()

    act(() => {
      result.current.handleDeleteNode("n1")
    })

    // Immediately after handleDeleteNode returns (still in the same
    // synchronous microtask window), the cache MUST still hold the
    // preview — otherwise a downstream component that re-reads the
    // store on its next render will see null while the graph still
    // displays the node.
    const cachedAfterDelete = useNodeResultsStore.getState().getPreview("n1")
    expect(cachedAfterDelete).not.toBeNull()

    // After the microtask queue drains, cleanup should have run.
    await act(async () => {
      vi.runOnlyPendingTimers()
      await Promise.resolve()
    })
    const cachedAfterTick = useNodeResultsStore.getState().getPreview("n1")
    expect(cachedAfterTick).toBeNull()
  })

  it("multiple rapid deletes each defer their own clearNode (no missed nodes)", async () => {
    // Catches: the deferral logic must handle N pending deletes without
    // dropping cleanup for any of them.
    const params = makeParams()
    const n1 = makeNode("n1")
    const n2 = makeNode("n2")
    const n3 = makeNode("n3")
    params.graphRef.current = { nodes: [n1, n2, n3], edges: [] }

    // Seed previews for all three nodes
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

    vi.useFakeTimers()

    act(() => {
      result.current.handleDeleteNode("n1")
      result.current.handleDeleteNode("n2")
      result.current.handleDeleteNode("n3")
    })

    // All three previews must still exist in the cache right now
    expect(useNodeResultsStore.getState().getPreview("n1")).not.toBeNull()
    expect(useNodeResultsStore.getState().getPreview("n2")).not.toBeNull()
    expect(useNodeResultsStore.getState().getPreview("n3")).not.toBeNull()

    // Drain microtasks — all should be cleaned up.
    await act(async () => {
      vi.runOnlyPendingTimers()
      await Promise.resolve()
    })

    expect(useNodeResultsStore.getState().getPreview("n1")).toBeNull()
    expect(useNodeResultsStore.getState().getPreview("n2")).toBeNull()
    expect(useNodeResultsStore.getState().getPreview("n3")).toBeNull()
  })
})
