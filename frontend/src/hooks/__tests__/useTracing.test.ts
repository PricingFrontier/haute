import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act, waitFor } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import useTracing, {
  TRACE_MOTION_GRAPH_SIZE_LIMIT,
  TRACE_PROGRESS_DELAY_MS,
  buildEdgeAdjacency,
} from "../useTracing"
import useToastStore from "../../stores/useToastStore"
import useSettingsStore from "../../stores/useSettingsStore"
import useGraphStore from "../../stores/useGraphStore"
import { makeNode, makeEdge } from "../../test-utils/factories"
import { NODE_TYPES } from "../../utils/nodeTypes"
import type { TraceResult } from "../../types/trace"

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

function mockReducedMotion(matches: boolean) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: query === "(prefers-reduced-motion: reduce)" ? matches : false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  })
}

function makeInstrumentedEdge(
  id: string,
  source: string,
  target: string,
  counters: { sourceReads: number; targetReads: number },
): Edge {
  const edge = { id, type: "default" } as Edge
  Object.defineProperties(edge, {
    source: {
      enumerable: true,
      get() {
        counters.sourceReads += 1
        return source
      },
    },
    target: {
      enumerable: true,
      get() {
        counters.targetReads += 1
        return target
      },
    },
  })
  return edge
}

function makeParams(overrides: Partial<Parameters<typeof useTracing>[0]> = {}) {
  return {
    nodes: [makeNode("n1"), makeNode("n2")] as Node[],
    edges: [makeEdge("n1", "n2")] as Edge[],
    selectedNode: makeNode("n2"),
    graphRef: { current: { nodes: [] as Node[], edges: [] as Edge[] } },
    parentGraphRef: { current: null },
    activeSubmodelIdentity: null,
    submodels: {},
    submodelsRef: { current: {} },
    preambleRef: { current: "" },
    nodeStatuses: {} as Record<string, "ok" | "error" | "running">,
    hoveredNodeId: null,
    ...overrides,
  }
}

type TraceFixture = Omit<
  TraceResult,
  "omissions" | "correlation_diagnostics" | "generated_at" | "pipeline_source" | "execution_origin"
>

function completeTrace(trace: TraceFixture): TraceResult {
  return {
    ...trace,
    omissions: [],
    correlation_diagnostics: [],
    generated_at: "2026-07-23T12:00:00+00:00",
    pipeline_source: null,
    execution_origin: "fresh_execution",
  }
}

function makeTrace(nodeIds: string[]): TraceResult {
  return completeTrace({
    steps: nodeIds.map((nodeId, topologicalRank) => ({
      node_id: nodeId,
      node_name: nodeId,
      node_type: "polars",
      schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: [] },
      input_values: {},
      output_values: {},
      topological_rank: topologicalRank,
      column_relevant: true,
    })),
    target_node_id: nodeIds.at(-1) ?? "",
    row_index: 0,
    column: "price",
    output_value: 1,
    total_nodes_in_pipeline: nodeIds.length,
    nodes_in_trace: nodeIds.length,
    execution_ms: 10,
    row_id_column: null,
    row_id_value: null,
  })
}

describe("useTracing", () => {
  beforeEach(() => {
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
    useSettingsStore.setState({ rowLimit: 1000, activeSource: "live" })
    mockTraceCell.mockReset()
    mockReducedMotion(false)
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
      trace: completeTrace({ steps: [], target_node_id: "n2", row_index: 0, column: "price", output_value: 1, total_nodes_in_pipeline: 2, nodes_in_trace: 1, execution_ms: 10, row_id_column: null, row_id_value: null }),
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
      steps: [{ node_id: "n1", node_name: "N1", node_type: "polars", schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: [] }, input_values: {}, output_values: {}, topological_rank: 0, column_relevant: true }],
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
    mockTraceCell.mockResolvedValue({ status: "ok", trace: completeTrace(trace) })
    const { result } = renderHook(() => useTracing(makeParams()))
    await act(async () => {
      result.current.handleCellClick(0, "price")
    })
    await waitFor(() => expect(result.current.traceResult).not.toBeNull())
    expect(result.current.tracedCell).toEqual({ rowIndex: 0, column: "price" })
  })

  it("ignores stale trace responses when a newer click resolves first", async () => {
    type TraceCellValue = Awaited<ReturnType<typeof traceCell>>
    let resolveFirst!: (value: TraceCellValue) => void
    let resolveSecond!: (value: TraceCellValue) => void

    mockTraceCell
      .mockImplementationOnce(() => new Promise<TraceCellValue>((resolve) => { resolveFirst = resolve }))
      .mockImplementationOnce(() => new Promise<TraceCellValue>((resolve) => { resolveSecond = resolve }))

    const { result } = renderHook(() => useTracing(makeParams()))

    act(() => {
      result.current.handleCellClick(0, "old_price")
    })
    act(() => {
      result.current.handleCellClick(1, "new_price")
    })

    await act(async () => {
      resolveSecond({ status: "ok", trace: makeTrace(["new_trace"]) })
      await Promise.resolve()
    })
    await waitFor(() => expect(result.current.traceResult?.target_node_id).toBe("new_trace"))
    expect(result.current.tracedCell).toEqual({ rowIndex: 1, column: "new_price" })

    await act(async () => {
      resolveFirst({ status: "ok", trace: makeTrace(["old_trace"]) })
      await Promise.resolve()
    })

    expect(result.current.traceResult?.target_node_id).toBe("new_trace")
    expect(result.current.tracedCell).toEqual({ rowIndex: 1, column: "new_price" })
  })

  it("keeps an in-band trace failure as persistent retryable state", async () => {
    // The backend always returns a `trace`; a non-"ok" status is the only
    // in-band failure signal (real failures arrive as rejected ApiErrors).
    mockTraceCell.mockResolvedValue({
      status: "error",
      trace: completeTrace({
        steps: [],
        target_node_id: "n2",
        row_index: 0,
        column: null,
        output_value: null,
        total_nodes_in_pipeline: 0,
        nodes_in_trace: 0,
        execution_ms: 0,
        row_id_column: null,
        row_id_value: null,
      }),
    })
    const { result } = renderHook(() => useTracing(makeParams()))
    await act(async () => {
      result.current.handleCellClick(0, "col")
    })
    await waitFor(() => expect(result.current.traceState.status).toBe("error"))
    expect(result.current.traceState).toMatchObject({ retryable: true })
    expect(result.current.traceResult).toBeNull()
  })

  it("keeps a network error as persistent state with raw detail", async () => {
    mockTraceCell.mockRejectedValue(new Error("Network error"))
    const { result } = renderHook(() => useTracing(makeParams()))
    await act(async () => {
      result.current.handleCellClick(0, "col")
    })
    await waitFor(() => expect(result.current.traceState).toMatchObject({ status: "error", detail: "Network error", retryable: true }))
  })

  it("handles a row-identity 409 by requiring a fresh row selection", async () => {
    const err = Object.assign(new Error("HTTP 409"), {
      status: 409,
      detail: "Trace data does not match the preview row.",
    })
    mockTraceCell.mockRejectedValue(err)
    const refreshPreview = vi.fn()
    const { result } = renderHook(() => useTracing(makeParams({ refreshPreview })))
    await act(async () => {
      result.current.handleCellClick(0, "col")
    })
    await waitFor(() => expect(result.current.traceState).toMatchObject({ status: "error", retryable: false }))
    expect(refreshPreview).toHaveBeenCalledWith(expect.objectContaining({ id: "n2" }))
    expect(result.current.tracedCell).toBeNull()
    expect(mockTraceCell).toHaveBeenCalledTimes(1)
  })

  it("does not expose progress for a trace that finishes before the delay", async () => {
    mockTraceCell.mockResolvedValue({ status: "ok", trace: makeTrace(["n1", "n2"]) })
    const { result } = renderHook(() => useTracing(makeParams()))
    await act(async () => { result.current.handleCellClick(0, "price") })
    await waitFor(() => expect(result.current.traceState.status).toBe("ready"))
    expect(result.current.traceState.status).toBe("ready")
  })

  it("shows delayed progress and cancellation returns to idle", () => {
    vi.useFakeTimers()
    mockTraceCell.mockReturnValue(new Promise(() => {}))
    const { result } = renderHook(() => useTracing(makeParams()))
    act(() => result.current.handleCellClick(0, "price"))
    expect(result.current.traceState).toEqual({ status: "loading", progressVisible: false })
    act(() => vi.advanceTimersByTime(TRACE_PROGRESS_DELAY_MS))
    expect(result.current.traceState).toEqual({ status: "loading", progressVisible: true })
    expect(result.current.tracedCell).toEqual({ rowIndex: 0, column: "price" })
    act(() => result.current.cancelTrace())
    expect(result.current.traceState).toEqual({ status: "idle" })
    expect(result.current.tracedCell).toBeNull()
    vi.useRealTimers()
  })

  it("clears and rejects a late result when structural context changes", async () => {
    let resolve!: (value: Awaited<ReturnType<typeof traceCell>>) => void
    mockTraceCell.mockReturnValue(new Promise((done) => { resolve = done }))
    const { result } = renderHook(() => useTracing(makeParams()))
    act(() => result.current.handleCellClick(0, "price"))
    act(() => useGraphStore.setState((state) => ({ structuralVersion: state.structuralVersion + 1 })))
    expect(result.current.traceState.status).toBe("idle")
    await act(async () => { resolve({ status: "ok", trace: makeTrace(["n1", "n2"]) }) })
    expect(result.current.traceResult).toBeNull()
  })

  it("invalidates a ready trace when its source or row limit changes", async () => {
    mockTraceCell.mockResolvedValue({ status: "ok", trace: makeTrace(["n1", "n2"]) })
    const { result } = renderHook(() => useTracing(makeParams()))
    await act(async () => { result.current.handleCellClick(0, "price") })
    await waitFor(() => expect(result.current.traceState.status).toBe("ready"))
    act(() => useSettingsStore.setState({ rowLimit: 99 }))
    expect(result.current.traceState.status).toBe("idle")
    expect(result.current.traceResult).toBeNull()
    useSettingsStore.setState({ rowLimit: 1000 })
  })

  it("does not resurrect a trace when semantic settings return to their old values", async () => {
    mockTraceCell.mockResolvedValue({ status: "ok", trace: makeTrace(["n1", "n2"]) })
    const { result } = renderHook(() => useTracing(makeParams()))
    await act(async () => { result.current.handleCellClick(0, "price") })
    await waitFor(() => expect(result.current.traceState.status).toBe("ready"))

    act(() => useSettingsStore.setState({ rowLimit: 99 }))
    expect(result.current.traceState.status).toBe("idle")
    act(() => useSettingsStore.setState({ rowLimit: 1000 }))

    expect(result.current.traceState.status).toBe("idle")
    expect(result.current.traceResult).toBeNull()
  })

  it("retries the captured row only while its semantic context remains current", async () => {
    mockTraceCell
      .mockRejectedValueOnce(new Error("temporary failure"))
      .mockResolvedValueOnce({ status: "ok", trace: makeTrace(["n1", "n2"]) })
    const rowValues = { quote_id: "Q-7", premium: 123 }
    const { result } = renderHook(() => useTracing(makeParams()))

    await act(async () => {
      result.current.handleCellClick(7, "premium", rowValues)
    })
    await waitFor(() => expect(result.current.traceState.status).toBe("error"))

    act(() => result.current.retryTrace())
    await waitFor(() => expect(result.current.traceState.status).toBe("ready"))
    expect(mockTraceCell).toHaveBeenCalledTimes(2)
    expect(mockTraceCell).toHaveBeenLastCalledWith(expect.objectContaining({
      row_index: 7,
      column: "premium",
      row_values: rowValues,
    }))

    act(() => useSettingsStore.setState({ activeSource: "snapshot" }))
    expect(result.current.traceState.status).toBe("idle")
    act(() => result.current.retryTrace())
    expect(mockTraceCell).toHaveBeenCalledTimes(2)
  })

  it("preserves a ready trace across position-only node changes", async () => {
    mockTraceCell.mockResolvedValue({ status: "ok", trace: makeTrace(["n1", "n2"]) })
    const initialNodes = [
      { ...makeNode("n1"), position: { x: 0, y: 0 } },
      { ...makeNode("n2"), position: { x: 100, y: 0 } },
    ] as Node[]
    const params = makeParams({
      nodes: initialNodes,
      selectedNode: initialNodes[1],
    })
    const { result, rerender } = renderHook((props) => useTracing(props), {
      initialProps: params,
    })

    await act(async () => {
      result.current.handleCellClick(0, "price")
    })
    await waitFor(() => expect(result.current.traceState.status).toBe("ready"))
    const readyTrace = result.current.traceResult

    const movedNodes = [
      { ...initialNodes[0], position: { x: 40, y: 20 } },
      { ...initialNodes[1], position: { x: 220, y: 90 } },
    ] as Node[]
    rerender({ ...params, nodes: movedNodes, selectedNode: movedNodes[1] })

    expect(result.current.traceState.status).toBe("ready")
    expect(result.current.traceResult).toBe(readyTrace)
    expect(mockTraceCell).toHaveBeenCalledTimes(1)
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

  it("maps flat external trace steps onto composite boundary cards", async () => {
    const inputBoundary = makeNode("boundary-input", NODE_TYPES.SUBMODEL_PORT, {
      data: {
        label: "INPUT",
        nodeType: NODE_TYPES.SUBMODEL_PORT,
        instanceId: "instance_primary",
        definitionId: "definition_pricing",
        portDirection: "input",
        ports: [],
        externalNodeIds: ["external-source-a", "external-source-b"],
      },
    })
    const child = makeNode("child")
    const outputBoundary = makeNode("boundary-output", NODE_TYPES.SUBMODEL_PORT, {
      data: {
        label: "OUTPUT",
        nodeType: NODE_TYPES.SUBMODEL_PORT,
        instanceId: "instance_primary",
        definitionId: "definition_pricing",
        portDirection: "output",
        ports: [],
        externalNodeIds: ["external-target"],
      },
    })
    const submodels = {
      definition_pricing: {
        definitionId: "definition_pricing",
        file: "modules/pricing.py",
        graph: { nodes: [child], edges: [] },
        inputPorts: [],
        outputPorts: [],
      },
    }
    const params = makeParams({
      activeSubmodelIdentity: { instanceId: "instance_primary", definitionId: "definition_pricing" },
      nodes: [inputBoundary, child, outputBoundary],
      submodels,
      submodelsRef: { current: submodels },
      edges: [
        makeEdge(inputBoundary.id, child.id),
        makeEdge(child.id, outputBoundary.id),
      ],
      selectedNode: child,
    })
    mockTraceCell.mockResolvedValue({
      status: "ok",
      trace: makeTrace(["external-source-b", "submodel_runtime/instance_primary/child", "external-target"]),
    })

    const { result } = renderHook(() => useTracing(params))
    await act(async () => {
      result.current.handleCellClick(0, "price")
    })
    await waitFor(() => expect(result.current.traceResult).not.toBeNull())
    expect(mockTraceCell).toHaveBeenCalledWith(expect.objectContaining({
      target_node_id: "submodel_runtime/instance_primary/child",
    }))

    const projectedData = Object.fromEntries(
      result.current.nodesWithStatus.map((node) => [node.id, node.data]),
    )
    expect(projectedData["boundary-input"]).toMatchObject({
      _traceActive: true,
      _traceDimmed: false,
    })
    expect(projectedData["boundary-output"]).toMatchObject({
      _traceActive: true,
      _traceDimmed: false,
    })
  })

  it("nodesWithStatus dims nodes not in trace via _traceDimmed data flag only", async () => {
    const trace = {
      steps: [{ node_id: "n1", node_name: "N1", node_type: "polars", schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: [] }, input_values: {}, output_values: {}, topological_rank: 0, column_relevant: true }],
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
    mockTraceCell.mockResolvedValue({ status: "ok", trace: completeTrace(trace) })
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
        { node_id: "n1", node_name: "N1", node_type: "polars", schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: [] }, input_values: {}, output_values: {}, topological_rank: 0, column_relevant: true },
        { node_id: "n2", node_name: "N2", node_type: "polars", schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: [] }, input_values: {}, output_values: {}, topological_rank: 1, column_relevant: true },
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
    mockTraceCell.mockResolvedValue({ status: "ok", trace: completeTrace(trace) })
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
      steps: [{ node_id: "n1", node_name: "N1", node_type: "polars", schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: [] }, input_values: {}, output_values: {}, topological_rank: 0, column_relevant: true }],
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
    mockTraceCell.mockResolvedValue({ status: "ok", trace: completeTrace(trace) })
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
        { node_id: "n1", node_name: "N1", node_type: "polars", schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: [] }, input_values: {}, output_values: {}, topological_rank: 0, column_relevant: true },
        { node_id: "n2", node_name: "N2", node_type: "polars", schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: [] }, input_values: {}, output_values: {}, topological_rank: 1, column_relevant: true },
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
    mockTraceCell.mockResolvedValue({ status: "ok", trace: completeTrace(trace) })
    const { result } = renderHook(() => useTracing(makeParams()))
    await act(async () => {
      result.current.handleCellClick(0, "price")
    })
    await waitFor(() => expect(result.current.traceResult).not.toBeNull())
    const edge = result.current.edgesWithTrace[0]
    expect(edge.animated).toBe(true)
    expect(edge.style?.strokeWidth).toBe(2.5)
  })

  it("small graph trace styling keeps motion affordances", async () => {
    mockTraceCell.mockResolvedValue({ status: "ok", trace: makeTrace(["n1", "n2"]) })
    const { result } = renderHook(() => useTracing(makeParams()))

    await act(async () => {
      result.current.handleCellClick(0, "price")
    })
    await waitFor(() => expect(result.current.traceResult).not.toBeNull())

    expect(result.current.nodesWithStatus[0].style?.transition).toBe("opacity 0.2s ease")
    expect(result.current.edgesWithTrace[0].animated).toBe(true)
    expect(result.current.edgesWithTrace[0].style?.filter).toBe("drop-shadow(0 0 4px var(--accent))")
    expect(result.current.edgesWithTrace[0].className).toBeUndefined()
  })

  it("reduced-motion users get non-animated trace styling", async () => {
    mockReducedMotion(true)
    mockTraceCell.mockResolvedValue({ status: "ok", trace: makeTrace(["n1", "n2"]) })
    const { result } = renderHook(() => useTracing(makeParams()))

    await act(async () => {
      result.current.handleCellClick(0, "price")
    })
    await waitFor(() => expect(result.current.traceResult).not.toBeNull())

    expect(result.current.nodesWithStatus[0].style?.transition).toBe("none")
    expect(result.current.nodesWithStatus[0].data._traceMotionDisabled).toBe(true)
    expect(result.current.nodesWithStatus[0].className).toContain("trace-motion-lite")
    expect(result.current.edgesWithTrace[0].animated).toBe(false)
    expect(result.current.edgesWithTrace[0].style?.filter).toBe("none")
    expect(result.current.edgesWithTrace[0].className).toContain("trace-motion-lite")
  })

  it("very large graphs disable expensive trace filter styles for active and dimmed traced edges", async () => {
    const nodes = Array.from({ length: TRACE_MOTION_GRAPH_SIZE_LIMIT }, (_, index) => makeNode(`n${index}`)) as Node[]
    const edges = [makeEdge("n0", "n1"), makeEdge("n1", "n2")] as Edge[]
    mockTraceCell.mockResolvedValue({ status: "ok", trace: makeTrace(["n0", "n1"]) })
    const { result } = renderHook(() => useTracing(makeParams({
      nodes,
      edges,
      selectedNode: nodes[1],
    })))

    await act(async () => {
      result.current.handleCellClick(0, "price")
    })
    await waitFor(() => expect(result.current.traceResult).not.toBeNull())

    expect(result.current.nodesWithStatus[0].style?.transition).toBe("none")
    expect(result.current.nodesWithStatus[0].data._traceMotionDisabled).toBe(true)
    expect(result.current.nodesWithStatus[0].className).toContain("trace-motion-lite")
    for (const edge of result.current.edgesWithTrace) {
      expect(edge.animated).toBe(false)
      expect(edge.style?.filter).toBe("none")
      expect(edge.className).toContain("trace-motion-lite")
    }
  })

  it("removes traced-edge drop shadows when a traced graph grows past the motion threshold", async () => {
    const stableNodes = [makeNode("n0"), makeNode("n1")] as Node[]
    const edges = [makeEdge("n0", "n1")] as Edge[]
    mockTraceCell.mockResolvedValue({ status: "ok", trace: makeTrace(["n0", "n1"]) })
    const params = makeParams({ nodes: stableNodes, edges, selectedNode: stableNodes[1] })
    const { result, rerender } = renderHook((p) => useTracing(p), { initialProps: params })

    await act(async () => {
      result.current.handleCellClick(0, "price")
    })
    await waitFor(() => expect(result.current.traceResult).not.toBeNull())
    expect(result.current.edgesWithTrace[0].style?.filter).toBe("drop-shadow(0 0 4px var(--accent))")

    rerender({
      ...params,
      nodes: [
        ...stableNodes,
        ...Array.from({ length: TRACE_MOTION_GRAPH_SIZE_LIMIT }, (_, index) => makeNode(`large-${index}`)),
      ],
    })

    expect(result.current.edgesWithTrace[0].style?.filter).toBe("none")
    expect(result.current.edgesWithTrace[0].animated).toBe(false)
    expect(result.current.edgesWithTrace[0].className).toContain("trace-motion-lite")
  })

  it("reprojects cached nodes when the graph crosses the trace-motion threshold", () => {
    const stableNodes = [makeNode("n0"), makeNode("n1")] as Node[]
    const params = makeParams({ nodes: stableNodes, edges: [makeEdge("n0", "n1")] as Edge[] })
    const { result, rerender } = renderHook((p) => useTracing(p), { initialProps: params })

    expect(result.current.nodesWithStatus[0].style?.transition).toBe("opacity 0.2s ease")
    expect(result.current.nodesWithStatus[0].data._traceMotionDisabled).toBe(false)

    const largeNodes = [
      ...stableNodes,
      ...Array.from({ length: TRACE_MOTION_GRAPH_SIZE_LIMIT }, (_, index) => makeNode(`large-${index}`)),
    ] as Node[]
    rerender({ ...params, nodes: largeNodes })

    expect(result.current.nodesWithStatus[0].style?.transition).toBe("none")
    expect(result.current.nodesWithStatus[0].data._traceMotionDisabled).toBe(true)
    expect(result.current.nodesWithStatus[0].className).toContain("trace-motion-lite")
  })

  it("edgesWithTrace returns original edges when no trace", () => {
    const params = makeParams()
    const { result } = renderHook(() => useTracing(params))
    expect(result.current.edgesWithTrace).toBe(params.edges)
  })

  describe("edge adjacency", () => {
    it("precomputes connected node and edge ids for each endpoint", () => {
      const adjacency = buildEdgeAdjacency([
        makeEdge("n1", "n2"),
        makeEdge("n1", "n3"),
        makeEdge("n3", "n4"),
      ] as Edge[])

      expect([...adjacency.nodesByNodeId.get("n1")!]).toEqual(["n1", "n2", "n3"])
      expect([...adjacency.edgeIdsByNodeId.get("n1")!]).toEqual(["e_n1_n2", "e_n1_n3"])
      expect([...adjacency.nodesByNodeId.get("n3")!]).toEqual(["n3", "n1", "n4"])
      expect(adjacency.endpointsByEdgeId.get("e_n1_n2")).toEqual({ source: "n1", target: "n2" })
    })

    it("reuses precomputed adjacency for same-edge hover changes without rereading edge endpoints", () => {
      const counters = { sourceReads: 0, targetReads: 0 }
      const n1 = makeNode("n1")
      const n2 = makeNode("n2")
      const n3 = makeNode("n3")
      const edges = [
        makeInstrumentedEdge("e1", "n1", "n2", counters),
        makeInstrumentedEdge("e2", "n2", "n3", counters),
      ]
      const params = makeParams({
        nodes: [n1, n2, n3] as Node[],
        edges,
        hoveredNodeId: "n1",
      })
      const { rerender } = renderHook((p) => useTracing(p), { initialProps: params })
      const sourceReadsAfterBuild = counters.sourceReads
      const targetReadsAfterBuild = counters.targetReads

      rerender({ ...params, hoveredNodeId: "n2" })
      rerender({ ...params, hoveredNodeId: "n3" })

      expect(counters.sourceReads).toBe(sourceReadsAfterBuild)
      expect(counters.targetReads).toBe(targetReadsAfterBuild)
    })
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
      steps: [{ node_id: "n1", node_name: "N1", node_type: "polars", schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: [] }, input_values: {}, output_values: {}, topological_rank: 0, column_relevant: true }],
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
    mockTraceCell.mockResolvedValue({ status: "ok", trace: completeTrace(trace) })
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

  it("does not add hover arrowheads when hovering an edgeJoin node", () => {
    const source = makeNode("source")
    const edgeJoin = makeNode("join", NODE_TYPES.EDGE_JOIN)
    const output = makeNode("output")
    const params = makeParams({
      nodes: [source, edgeJoin, output] as Node[],
      edges: [makeEdge("source", "join"), makeEdge("join", "output"), makeEdge("source", "output")] as Edge[],
      hoveredNodeId: "join",
    })

    const { result } = renderHook(() => useTracing(params))

    for (const edge of result.current.edgesWithTrace) {
      expect(edge.markerEnd).toBeUndefined()
    }
    expect(result.current.edgesWithTrace.find((edge) => edge.id === "e_source_join")?.style?.strokeWidth).toBe(2)
    expect(result.current.edgesWithTrace.find((edge) => edge.id === "e_source_output")?.style?.strokeWidth).toBe(1)
  })

  it("removes cached hover arrowheads when hover moves from a normal node to an edgeJoin", () => {
    const source = makeNode("source")
    const edgeJoin = makeNode("join", NODE_TYPES.EDGE_JOIN)
    const edge = makeEdge("source", "join")
    const params = makeParams({
      nodes: [source, edgeJoin] as Node[],
      edges: [edge] as Edge[],
      hoveredNodeId: "source",
    })
    const { result, rerender } = renderHook((p) => useTracing(p), { initialProps: params })

    expect(result.current.edgesWithTrace[0].markerEnd).toBeDefined()

    rerender({ ...params, hoveredNodeId: "join" })

    expect(result.current.edgesWithTrace[0].markerEnd).toBeUndefined()
  })

  it("preserves unchanged edge object references across hover-to-hover transitions", () => {
    const nodes = [makeNode("n1"), makeNode("n2"), makeNode("n3"), makeNode("n4")] as Node[]
    const edges = [
      makeEdge("n1", "n2"),
      makeEdge("n2", "n3"),
      makeEdge("n3", "n4"),
    ] as Edge[]
    const params = makeParams({ nodes, edges, hoveredNodeId: "n1" })
    const { result, rerender } = renderHook((p) => useTracing(p), { initialProps: params })
    const firstEdges = Object.fromEntries(result.current.edgesWithTrace.map((edge) => [edge.id, edge]))

    rerender({ ...params, hoveredNodeId: "n2" })

    const nextEdges = Object.fromEntries(result.current.edgesWithTrace.map((edge) => [edge.id, edge]))
    expect(nextEdges.e_n1_n2).toBe(firstEdges.e_n1_n2)
    expect(nextEdges.e_n2_n3).not.toBe(firstEdges.e_n2_n3)
    expect(nextEdges.e_n3_n4).toBe(firstEdges.e_n3_n4)
  })

  it("preserves unchanged edge object references across trace-to-trace transitions", async () => {
    const nodes = [makeNode("n1"), makeNode("n2"), makeNode("n3"), makeNode("n4")] as Node[]
    const edges = [
      makeEdge("n1", "n2"),
      makeEdge("n2", "n3"),
      makeEdge("n3", "n4"),
    ] as Edge[]
    mockTraceCell
      .mockResolvedValueOnce({ status: "ok", trace: makeTrace(["n1", "n2"]) })
      .mockResolvedValueOnce({ status: "ok", trace: makeTrace(["n2", "n3"]) })
    const { result } = renderHook(() => useTracing(makeParams({ nodes, edges, selectedNode: nodes[3] })))

    await act(async () => {
      result.current.handleCellClick(0, "price")
    })
    await waitFor(() => expect(result.current.traceResult?.target_node_id).toBe("n2"))
    const firstEdges = Object.fromEntries(result.current.edgesWithTrace.map((edge) => [edge.id, edge]))

    await act(async () => {
      result.current.handleCellClick(1, "price")
    })
    await waitFor(() => expect(result.current.traceResult?.target_node_id).toBe("n3"))

    const nextEdges = Object.fromEntries(result.current.edgesWithTrace.map((edge) => [edge.id, edge]))
    expect(nextEdges.e_n1_n2).not.toBe(firstEdges.e_n1_n2)
    expect(nextEdges.e_n2_n3).not.toBe(firstEdges.e_n2_n3)
    expect(nextEdges.e_n3_n4).toBe(firstEdges.e_n3_n4)
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
