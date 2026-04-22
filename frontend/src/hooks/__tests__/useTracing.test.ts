import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act, waitFor } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import useTracing from "../useTracing"
import useToastStore from "../../stores/useToastStore"
import useSettingsStore from "../../stores/useSettingsStore"
import { makeNode, makeEdge } from "../../test-utils/factories"

vi.mock("@xyflow/react", async () => {
  const actual = await vi.importActual("@xyflow/react")
  return { ...actual, useStore: (selector: (s: { transform: [number, number, number] }) => unknown) => selector({ transform: [0, 0, 1] }) }
})

vi.mock("../../api/client", () => ({
  traceCell: vi.fn(),
}))

vi.mock("../../utils/buildGraph", () => ({
  resolveGraphFromRefs: vi.fn(() => ({ nodes: [], edges: [], preamble: "" })),
}))

import { traceCell } from "../../api/client"
const mockTraceCell = vi.mocked(traceCell)

function makeParams(overrides: Partial<Parameters<typeof useTracing>[0]> = {}) {
  return {
    nodes: [makeNode("n1"), makeNode("n2")] as Node[],
    edges: [makeEdge("n1", "n2")] as Edge[],
    selectedNode: makeNode("n2"),
    graphRef: { current: { nodes: [] as Node[], edges: [] as Edge[] } },
    parentGraphRef: { current: null },
    submodelsRef: { current: {} },
    preambleRef: { current: "" },
    nodeStatuses: {} as Record<string, "ok" | "error" | "running">,
    hoveredNodeId: null,
    ...overrides,
  }
}

describe("useTracing", () => {
  beforeEach(() => {
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
    useSettingsStore.setState({ rowLimit: 1000, activeSource: "live" })
    mockTraceCell.mockReset()
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it("returns null traceResult initially", () => {
    const { result } = renderHook(() => useTracing(makeParams()))
    expect(result.current.traceResult).toBeNull()
    expect(result.current.tracedCell).toBeNull()
  })

  it("clearTrace resets traceResult and tracedCell", async () => {
    mockTraceCell.mockResolvedValue({
      status: "ok",
      trace: { steps: [], target_node_id: "n2", row_index: 0, column: "price", output_value: 1, total_nodes_in_pipeline: 2, nodes_in_trace: 1, execution_ms: 10, row_id_column: null, row_id_value: null },
    })
    const { result } = renderHook(() => useTracing(makeParams()))
    await act(async () => {
      result.current.handleCellClick(0, "price")
    })
    await waitFor(() => expect(result.current.traceResult).not.toBeNull())
    act(() => {
      result.current.clearTrace()
    })
    expect(result.current.traceResult).toBeNull()
    expect(result.current.tracedCell).toBeNull()
  })

  it("handleCellClick does nothing without selectedNode", () => {
    const params = makeParams({ selectedNode: null })
    const { result } = renderHook(() => useTracing(params))
    act(() => {
      result.current.handleCellClick(0, "price")
    })
    expect(mockTraceCell).not.toHaveBeenCalled()
  })

  it("handleCellClick calls traceCell and sets result on success", async () => {
    const trace = {
      steps: [{ node_id: "n1", node_name: "N1", node_type: "polars", schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: [] }, input_values: {}, output_values: {}, column_relevant: true, execution_ms: 5 }],
      target_node_id: "n2",
      row_index: 0,
      column: "price",
      output_value: 42,
      total_nodes_in_pipeline: 2,
      nodes_in_trace: 1,
      execution_ms: 10,
      row_id_column: null,
      row_id_value: null,
    }
    mockTraceCell.mockResolvedValue({ status: "ok", trace })
    const { result } = renderHook(() => useTracing(makeParams()))
    await act(async () => {
      result.current.handleCellClick(0, "price")
    })
    await waitFor(() => expect(result.current.traceResult).not.toBeNull())
    expect(result.current.tracedCell).toEqual({ rowIndex: 0, column: "price" })
  })

  it("handleCellClick shows toast on trace failure", async () => {
    mockTraceCell.mockResolvedValue({ status: "error", error: "Something went wrong" })
    const { result } = renderHook(() => useTracing(makeParams()))
    await act(async () => {
      result.current.handleCellClick(0, "col")
    })
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      expect(toasts.some((t) => t.type === "error")).toBe(true)
    })
    expect(result.current.traceResult).toBeNull()
  })

  it("handleCellClick shows toast on network error", async () => {
    mockTraceCell.mockRejectedValue(new Error("Network error"))
    const { result } = renderHook(() => useTracing(makeParams()))
    await act(async () => {
      result.current.handleCellClick(0, "col")
    })
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      expect(toasts.some((t) => t.text.includes("Network error"))).toBe(true)
    })
  })

  it("handleCellClick shows API error detail when present", async () => {
    const err = Object.assign(new Error("HTTP 409"), {
      detail: "Trace data does not match the preview row.",
    })
    mockTraceCell.mockRejectedValue(err)
    const { result } = renderHook(() => useTracing(makeParams()))
    await act(async () => {
      result.current.handleCellClick(0, "col")
    })
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      expect(
        toasts.some((t) => t.text.includes("Trace data does not match the preview row.")),
      ).toBe(true)
    })
  })

  it("nodesWithStatus applies status from nodeStatuses", () => {
    const params = makeParams({ nodeStatuses: { n1: "ok", n2: "error" } })
    const { result } = renderHook(() => useTracing(params))
    const statusMap = Object.fromEntries(
      result.current.nodesWithStatus.map((n) => [n.id, n.data._status]),
    )
    expect(statusMap.n1).toBe("ok")
    expect(statusMap.n2).toBe("error")
  })

  it("nodesWithStatus dims nodes not in trace via _traceDimmed data flag only", async () => {
    const trace = {
      steps: [{ node_id: "n1", node_name: "N1", node_type: "polars", schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: [] }, input_values: {}, output_values: {}, column_relevant: true, execution_ms: 5 }],
      target_node_id: "n2",
      row_index: 0,
      column: "price",
      output_value: 1,
      total_nodes_in_pipeline: 2,
      nodes_in_trace: 1,
      execution_ms: 10,
      row_id_column: null,
      row_id_value: null,
    }
    mockTraceCell.mockResolvedValue({ status: "ok", trace })
    const { result } = renderHook(() => useTracing(makeParams()))
    await act(async () => {
      result.current.handleCellClick(0, "price")
    })
    await waitFor(() => expect(result.current.traceResult).not.toBeNull())
    // Dimmed node should have _traceDimmed in data, NOT style.opacity
    // (PipelineNode handles opacity via _traceDimmed to avoid double-opacity)
    const dimmedNode = result.current.nodesWithStatus.find((n) => n.id === "n2")!
    expect(dimmedNode.data._traceDimmed).toBe(true)
    expect(dimmedNode.style?.opacity).toBeUndefined()
  })

  it("nodesWithStatus does not set style.opacity on traced nodes either", async () => {
    const trace = {
      steps: [
        { node_id: "n1", node_name: "N1", node_type: "polars", schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: [] }, input_values: {}, output_values: {}, column_relevant: true, execution_ms: 5 },
        { node_id: "n2", node_name: "N2", node_type: "polars", schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: [] }, input_values: {}, output_values: {}, column_relevant: true, execution_ms: 5 },
      ],
      target_node_id: "n2",
      row_index: 0,
      column: "price",
      output_value: 1,
      total_nodes_in_pipeline: 2,
      nodes_in_trace: 2,
      execution_ms: 10,
      row_id_column: null,
      row_id_value: null,
    }
    mockTraceCell.mockResolvedValue({ status: "ok", trace })
    const { result } = renderHook(() => useTracing(makeParams()))
    await act(async () => {
      result.current.handleCellClick(0, "price")
    })
    await waitFor(() => expect(result.current.traceResult).not.toBeNull())
    // Traced (non-dimmed) nodes should also have no style.opacity
    const tracedNode = result.current.nodesWithStatus.find((n) => n.id === "n1")!
    expect(tracedNode.data._traceDimmed).toBe(false)
    expect(tracedNode.style?.opacity).toBeUndefined()
  })

  it("nodesWithStatus preserves transition on style", async () => {
    const trace = {
      steps: [{ node_id: "n1", node_name: "N1", node_type: "polars", schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: [] }, input_values: {}, output_values: {}, column_relevant: true, execution_ms: 5 }],
      target_node_id: "n2",
      row_index: 0,
      column: "price",
      output_value: 1,
      total_nodes_in_pipeline: 2,
      nodes_in_trace: 1,
      execution_ms: 10,
      row_id_column: null,
      row_id_value: null,
    }
    mockTraceCell.mockResolvedValue({ status: "ok", trace })
    const { result } = renderHook(() => useTracing(makeParams()))
    await act(async () => {
      result.current.handleCellClick(0, "price")
    })
    await waitFor(() => expect(result.current.traceResult).not.toBeNull())
    // Both dimmed and non-dimmed nodes should have the transition style
    for (const n of result.current.nodesWithStatus) {
      expect(n.style?.transition).toBe("opacity 0.2s ease")
    }
  })

  it("edgesWithTrace highlights edges between traced nodes", async () => {
    const trace = {
      steps: [
        { node_id: "n1", node_name: "N1", node_type: "polars", schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: [] }, input_values: {}, output_values: {}, column_relevant: true, execution_ms: 5 },
        { node_id: "n2", node_name: "N2", node_type: "polars", schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: [] }, input_values: {}, output_values: {}, column_relevant: true, execution_ms: 5 },
      ],
      target_node_id: "n2",
      row_index: 0,
      column: "price",
      output_value: 1,
      total_nodes_in_pipeline: 2,
      nodes_in_trace: 2,
      execution_ms: 10,
      row_id_column: null,
      row_id_value: null,
    }
    mockTraceCell.mockResolvedValue({ status: "ok", trace })
    const { result } = renderHook(() => useTracing(makeParams()))
    await act(async () => {
      result.current.handleCellClick(0, "price")
    })
    await waitFor(() => expect(result.current.traceResult).not.toBeNull())
    const edge = result.current.edgesWithTrace[0]
    expect(edge.animated).toBe(true)
    expect(edge.style?.strokeWidth).toBe(2.5)
  })

  it("edgesWithTrace returns original edges when no trace", () => {
    const params = makeParams()
    const { result } = renderHook(() => useTracing(params))
    expect(result.current.edgesWithTrace).toBe(params.edges)
  })

  // ── Hover dimming ─────────────────────────────────────────────────

  it("hoverConnectedIds includes hovered node and its direct neighbors", () => {
    const n1 = makeNode("n1")
    const n2 = makeNode("n2")
    const n3 = makeNode("n3")
    const params = makeParams({
      nodes: [n1, n2, n3] as Node[],
      edges: [makeEdge("n1", "n2")] as Edge[],
      hoveredNodeId: "n1",
    })
    const { result } = renderHook(() => useTracing(params))
    // n1 (hovered) and n2 (connected) should NOT be dimmed
    const dimMap = Object.fromEntries(
      result.current.nodesWithStatus.map((n) => [n.id, n.data._hoverDimmed]),
    )
    expect(dimMap.n1).toBe(false)
    expect(dimMap.n2).toBe(false)
    // n3 (disconnected) should be dimmed
    expect(dimMap.n3).toBe(true)
  })

  it("_hoverDimmed is false for all nodes when nothing is hovered", () => {
    const params = makeParams({ hoveredNodeId: null })
    const { result } = renderHook(() => useTracing(params))
    for (const n of result.current.nodesWithStatus) {
      expect(n.data._hoverDimmed).toBe(false)
    }
  })

  it("_hoverDimmed is false when trace is active (trace takes priority)", async () => {
    const trace = {
      steps: [{ node_id: "n1", node_name: "N1", node_type: "polars", schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: [] }, input_values: {}, output_values: {}, column_relevant: true, execution_ms: 5 }],
      target_node_id: "n2",
      row_index: 0,
      column: "price",
      output_value: 1,
      total_nodes_in_pipeline: 2,
      nodes_in_trace: 1,
      execution_ms: 10,
      row_id_column: null,
      row_id_value: null,
    }
    mockTraceCell.mockResolvedValue({ status: "ok", trace })
    const params = makeParams({ hoveredNodeId: "n1" })
    const { result } = renderHook(() => useTracing(params))
    await act(async () => {
      result.current.handleCellClick(0, "price")
    })
    await waitFor(() => expect(result.current.traceResult).not.toBeNull())
    // With trace active, hover dimming should be disabled
    for (const n of result.current.nodesWithStatus) {
      expect(n.data._hoverDimmed).toBe(false)
    }
  })

  it("edgesWithTrace brightens connected edges and dims others when hovering", () => {
    const n1 = makeNode("n1")
    const n2 = makeNode("n2")
    const n3 = makeNode("n3")
    const params = makeParams({
      nodes: [n1, n2, n3] as Node[],
      edges: [makeEdge("n1", "n2"), makeEdge("n2", "n3")] as Edge[],
      hoveredNodeId: "n1",
    })
    const { result } = renderHook(() => useTracing(params))
    const edgeStyles = Object.fromEntries(
      result.current.edgesWithTrace.map((e) => [`${e.source}-${e.target}`, e.style]),
    )
    // n1→n2 is connected to hovered node → bright
    expect(edgeStyles["n1-n2"]?.strokeWidth).toBe(2)
    expect(edgeStyles["n1-n2"]?.stroke).toBe("rgba(255,255,255,.55)")
    // n2→n3 is NOT connected to hovered node → dim
    expect(edgeStyles["n2-n3"]?.strokeWidth).toBe(1)
    expect(edgeStyles["n2-n3"]?.stroke).toBe("rgba(255,255,255,.06)")
  })

  it("_hoverDimmed reverts when hoveredNodeId becomes null", () => {
    const n1 = makeNode("n1")
    const n2 = makeNode("n2")
    const n3 = makeNode("n3")
    const params = makeParams({
      nodes: [n1, n2, n3] as Node[],
      edges: [makeEdge("n1", "n2")] as Edge[],
      hoveredNodeId: "n1",
    })
    const { result, rerender } = renderHook(
      (p) => useTracing(p),
      { initialProps: params },
    )
    // n3 should be dimmed while hovering n1
    expect(result.current.nodesWithStatus.find((n) => n.id === "n3")!.data._hoverDimmed).toBe(true)
    // Clear hover
    rerender({ ...params, hoveredNodeId: null })
    // All nodes should be un-dimmed
    for (const n of result.current.nodesWithStatus) {
      expect(n.data._hoverDimmed).toBe(false)
    }
  })
})
