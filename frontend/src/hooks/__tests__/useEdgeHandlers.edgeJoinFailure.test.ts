/**
 * Edge-join insertion FAILURE-path coverage for the onConnectEnd arbiter.
 *
 * The arbiter's `commitEdgeJoinResult` helper has two branches that the
 * happy-path suites never reach because the real `insertEdgeJoinNode*`
 * helpers succeed for those fixtures:
 *
 *   1. `{ ok: false, reason }` → addToast(mapped message) + early return,
 *      with the graph left completely untouched.
 *   2. `{ ok: true, ... }` whose `newNodeId` is NOT present in the
 *      returned nodes → the `result.nodes.find(...) ?? null` fallback
 *      selects null rather than crashing.
 *
 * Both require steering the pure graph helper to a chosen result, so this
 * file (unlike the main unit suite) module-mocks `../utils/edgeJoinGraph`.
 * Kept as a dedicated companion — mirroring `useEdgeHandlers.dragJson` —
 * so the module mock does not disturb the real-insertion assertions in
 * `useEdgeHandlers.test.ts`.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { renderHook, cleanup, act } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import useEdgeHandlers from "../useEdgeHandlers"
import { NODE_TYPES } from "../../utils/nodeTypes"
import useToastStore from "../../stores/useToastStore"
import {
  insertEdgeJoinNode,
  type EdgeJoinInsertResult,
} from "../../utils/edgeJoinGraph"

vi.mock("../../utils/edgeJoinGraph", () => ({
  insertEdgeJoinNode: vi.fn(),
}))

const mockedInsertEdgeJoinNode = vi.mocked(insertEdgeJoinNode)

function makeParams() {
  return {
    selectedNode: null as Node | null,
    graphRef: { current: { nodes: [] as Node[], edges: [] as Edge[] } },
    nodeIdCounter: { current: 0 },
    lastSelectedNodeRef: { current: null as Node | null },
    setNodes: vi.fn((updater: (nds: Node[]) => Node[]) => updater([])),
    setEdges: vi.fn((updater: (eds: Edge[]) => Edge[]) => updater([])),
    setNodesRaw: vi.fn(),
    setEdgesRaw: vi.fn(),
    pushSnapshot: vi.fn(),
    setSelectedNode: vi.fn(),
    setPreviewData: vi.fn(),
    setContextMenu: vi.fn(),
    fetchPreview: vi.fn(),
    cancelPreview: vi.fn(),
    shouldSkipAutomaticPreview: vi.fn(() => false),
    clearTrace: vi.fn(),
    screenToFlowPosition: vi.fn((pos: { x: number; y: number }) => pos),
    graphRefreshingRef: { current: 0 },
    findEdgeIdAtPoint: vi.fn(() => null as string | null),
    findNodeIdAtPoint: vi.fn(() => null as string | null),
    getInternalNode: vi.fn(() => undefined),
    getZoom: vi.fn(() => 1),
  }
}

describe("useEdgeHandlers — edgeJoin insertion failure handling", () => {
  const realAddToast = useToastStore.getState().addToast
  let toastSpy: ReturnType<typeof vi.fn<typeof realAddToast>>

  function installToastSpy() {
    toastSpy = vi.fn<typeof realAddToast>()
    useToastStore.setState({ addToast: toastSpy })
  }

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    useToastStore.setState({ addToast: realAddToast })
  })

  it("Arm 4 splice failure toasts the mapped reason and leaves the graph untouched", () => {
    // L238-240: an exposed-edge splice that the pure helper rejects (here:
    // a cycle) surfaces the mapped failure message and commits nothing.
    const failure: EdgeJoinInsertResult = { ok: false, reason: "cycle" }
    mockedInsertEdgeJoinNode.mockReturnValue(failure)

    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "a", position: { x: 0, y: 0 }, data: { label: "Base", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      { id: "b", position: { x: 300, y: 0 }, data: { label: "Downstream", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      { id: "c", position: { x: 0, y: 160 }, data: { label: "Lookup", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
    ]
    params.graphRef.current.edges = [
      { id: "e_ab", source: "a", target: "b", sourceHandle: null, targetHandle: null } as Edge,
    ]
    params.findEdgeIdAtPoint.mockReturnValue("e_ab")
    installToastSpy()
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        { clientX: 200, clientY: 150 } as MouseEvent,
        {
          isValid: null,
          fromNode: { id: "c" },
          fromHandle: { id: "lookup_out", type: "source" },
          toNode: null,
        } as never,
      )
    })

    expect(mockedInsertEdgeJoinNode).toHaveBeenCalledOnce()
    expect(toastSpy).toHaveBeenCalledWith(
      "error",
      "Edge join rejected: that connection would create a cycle",
    )
    // Graph untouched: no snapshot, no node/edge writes, no selection.
    expect(params.pushSnapshot).not.toHaveBeenCalled()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
    expect(params.setSelectedNode).not.toHaveBeenCalled()
  })

  it("selects null when a successful insert omits the new node from its result", () => {
    // L243: `result.nodes.find(...) ?? null` — a defensive fallback when
    // the returned node array does not contain newNodeId.
    const success: EdgeJoinInsertResult = {
      ok: true,
      nodes: [],
      edges: [{ id: "e_new", source: "c", target: "missing" } as Edge],
      newNodeId: "missing",
    }
    mockedInsertEdgeJoinNode.mockReturnValue(success)

    const params = makeParams()
    params.lastSelectedNodeRef.current = { id: "stale" } as Node
    params.graphRef.current.nodes = [
      { id: "a", position: { x: 0, y: 0 }, data: { label: "Base", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      { id: "b", position: { x: 300, y: 0 }, data: { label: "Downstream", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      { id: "c", position: { x: 0, y: 160 }, data: { label: "Lookup", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
    ]
    params.graphRef.current.edges = [
      { id: "e_ab", source: "a", target: "b", sourceHandle: null, targetHandle: null } as Edge,
    ]
    params.findEdgeIdAtPoint.mockReturnValue("e_ab")
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        { clientX: 200, clientY: 150 } as MouseEvent,
        {
          isValid: null,
          fromNode: { id: "c" },
          fromHandle: { id: "lookup_out", type: "source" },
          toNode: null,
        } as never,
      )
    })

    expect(params.pushSnapshot).toHaveBeenCalledOnce()
    expect(params.setNodesRaw).toHaveBeenCalledWith(success.nodes)
    expect(params.setEdgesRaw).toHaveBeenCalledWith(success.edges)
    // newNodeId absent from result.nodes → null selection (fallback).
    expect(params.setSelectedNode).toHaveBeenCalledWith(null)
    expect(params.lastSelectedNodeRef.current).toBeNull()
    expect(params.clearTrace).toHaveBeenCalledOnce()
    expect(params.cancelPreview).toHaveBeenCalledOnce()
  })
})
