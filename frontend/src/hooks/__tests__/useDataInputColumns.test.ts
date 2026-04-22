import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, waitFor, act } from "@testing-library/react"
import { useDataInputColumns } from "../useDataInputColumns"
import useGraphStore from "../../stores/useGraphStore"
import useNodeResultsStore from "../../stores/useNodeResultsStore"
import useSettingsStore from "../../stores/useSettingsStore"
import useToastStore from "../../stores/useToastStore"

vi.mock("../../api/client", () => ({
  previewNode: vi.fn(),
}))

vi.mock("../../utils/buildGraph", () => ({
  buildGraph: vi.fn(() => ({ nodes: [], edges: [], preamble: "" })),
}))

import { previewNode } from "../../api/client"
const mockPreview = vi.mocked(previewNode)

const sampleColumns = [
  { name: "age", dtype: "i64" },
  { name: "premium", dtype: "f64" },
]

const nodes = [
  { id: "ds1", data: { label: "DS1", description: "", nodeType: "dataSource" } },
  { id: "t1", data: { label: "T1", description: "", nodeType: "polars" } },
]

const edges = [{ id: "e1", source: "ds1", target: "t1" }]

describe("useDataInputColumns", () => {
  beforeEach(() => {
    useNodeResultsStore.setState({
      columnCache: {},
    })
    useGraphStore.setState({
      nodes: [],
      edges: [],
      preamble: "",
      lastSavedSnapshot: null,
      undoStack: [],
      redoStack: [],
      structuralVersion: 0,
      structuralFingerprint: "nodes:||edges:||preamble:\"\"",
    })
    useSettingsStore.setState({
      activeSource: "live",
      sources: ["live"],
    })
    mockPreview.mockReset()
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  // ── Basic behavior ────────────────────────────────────────────────

  it("returns empty array when dataInput is empty", () => {
    const { result } = renderHook(() => useDataInputColumns("", nodes, edges))
    expect(result.current).toEqual([])
  })

  it("fetches columns from API when no cache exists", async () => {
    mockPreview.mockResolvedValue({ node_id: "ds1", status: "ok", columns: sampleColumns })
    const { result } = renderHook(() => useDataInputColumns("ds1", nodes, edges))
    await waitFor(() => expect(result.current).toHaveLength(2))
    expect(result.current[0].name).toBe("age")
    expect(result.current[1].name).toBe("premium")
  })

  it("uses cached columns immediately when available", () => {
    // Cache key is now "nodeId:source"
    useNodeResultsStore.setState({
      columnCache: {
        "ds1:live": { columns: sampleColumns, structuralVersion: 0 },
      },
    })
    const { result } = renderHook(() => useDataInputColumns("ds1", nodes, edges))
    // Cached columns should be available synchronously
    expect(result.current).toHaveLength(2)
    expect(result.current[0].name).toBe("age")
  })

  it("skips API call when cache is fresh for the same structuralVersion", () => {
    useNodeResultsStore.setState({
      columnCache: {
        "ds1:live": { columns: sampleColumns, structuralVersion: 5 },
      },
    })
    useGraphStore.setState({ structuralVersion: 5 })
    renderHook(() => useDataInputColumns("ds1", nodes, edges))
    // Should NOT call previewNode since cache is fresh
    expect(mockPreview).not.toHaveBeenCalled()
  })

  it("uses graph structuralVersion for cache freshness", () => {
    useNodeResultsStore.setState({
      columnCache: {
        "ds1:live": { columns: sampleColumns, structuralVersion: 7 },
      },
    })
    useGraphStore.setState({
      structuralVersion: 7,
    })

    renderHook(() => useDataInputColumns("ds1", nodes, edges))
    expect(mockPreview).not.toHaveBeenCalled()
  })

  it("refetches when cache is stale for a different structuralVersion", async () => {
    useNodeResultsStore.setState({
      columnCache: {
        "ds1:live": { columns: [{ name: "old", dtype: "f64" }], structuralVersion: 1 },
      },
    })
    const freshCols = [{ name: "new_col", dtype: "str" }]
    mockPreview.mockResolvedValue({ node_id: "ds1", status: "ok", columns: freshCols })
    const { result } = renderHook(() => useDataInputColumns("ds1", nodes, edges))
    // Shows stale cache first
    expect(result.current[0].name).toBe("old")
    // Then updates with fresh data
    await waitFor(() => expect(result.current[0].name).toBe("new_col"))
  })

  it("refetches when structuralVersion changes", async () => {
    useNodeResultsStore.setState({
      columnCache: {
        "ds1:live": { columns: [{ name: "old", dtype: "f64" }], structuralVersion: 3 },
      },
    })
    useGraphStore.setState({ structuralVersion: 4 })
    const freshCols = [{ name: "new_col", dtype: "str" }]
    mockPreview.mockResolvedValue({ node_id: "ds1", status: "ok", columns: freshCols })

    const { result } = renderHook(() => useDataInputColumns("ds1", nodes, edges))
    expect(result.current[0].name).toBe("old")
    await waitFor(() => expect(result.current[0].name).toBe("new_col"))
  })

  it("handles API error gracefully with no cache", async () => {
    mockPreview.mockRejectedValue(new Error("Network error"))
    const { result } = renderHook(() => useDataInputColumns("ds1", nodes, edges))
    await waitFor(() => expect(mockPreview).toHaveBeenCalled())
    // Falls back to empty
    expect(result.current).toEqual([])
  })

  // ── Source propagation (the original bug) ───────────────────────

  it("passes active source from settings store to previewNode", async () => {
    useSettingsStore.setState({ activeSource: "nb_batch" })
    mockPreview.mockResolvedValue({ node_id: "ds1", status: "ok", columns: sampleColumns })

    renderHook(() => useDataInputColumns("ds1", nodes, edges))
    await waitFor(() => expect(mockPreview).toHaveBeenCalled())

    const callArgs = mockPreview.mock.calls[0]
    // previewNode(graph, nodeId, rowLimit, source, options)
    expect(callArgs[3]).toBe("nb_batch")
  })

  it("passes 'live' source when that is the active source", async () => {
    useSettingsStore.setState({ activeSource: "live" })
    mockPreview.mockResolvedValue({ node_id: "ds1", status: "ok", columns: sampleColumns })

    renderHook(() => useDataInputColumns("ds1", nodes, edges))
    await waitFor(() => expect(mockPreview).toHaveBeenCalled())

    const callArgs = mockPreview.mock.calls[0]
    expect(callArgs[3]).toBe("live")
  })

  it("uses the source that was active at mount time", async () => {
    // Source is nb_batch before the hook mounts
    useSettingsStore.setState({ activeSource: "nb_batch" })
    mockPreview.mockResolvedValue({ node_id: "ds1", status: "ok", columns: sampleColumns })

    renderHook(() => useDataInputColumns("ds1", nodes, edges))
    await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1))

    // Verify the call used the active source at mount time
    expect(mockPreview.mock.calls[0][3]).toBe("nb_batch")
  })

  it("refetches with new source when cache is invalidated", async () => {
    useSettingsStore.setState({ activeSource: "live" })
    mockPreview.mockResolvedValue({ node_id: "ds1", status: "ok", columns: sampleColumns })

    renderHook(() => useDataInputColumns("ds1", nodes, edges))
    await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1))
    expect(mockPreview.mock.calls[0][3]).toBe("live")

    // Change source and structuralVersion (simulating a graph change)
    act(() => {
      useSettingsStore.setState({ activeSource: "nb_batch" })
      useGraphStore.setState({ structuralVersion: 10 })
    })

    await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(2))
    expect(mockPreview.mock.calls[1][3]).toBe("nb_batch")
  })

  // ── Error handling edge cases ──────────────────────────────────────

  it("retains cached columns when API error occurs with stale cache", async () => {
    const cachedCols = [{ name: "cached_col", dtype: "f64" }]
    useNodeResultsStore.setState({
      columnCache: {
        "ds1:live": { columns: cachedCols, structuralVersion: 1 },
      },
    })
    mockPreview.mockRejectedValue(new Error("Server error"))

    const { result } = renderHook(() => useDataInputColumns("ds1", nodes, edges))
    // Should show cached columns immediately
    expect(result.current).toHaveLength(1)
    expect(result.current[0].name).toBe("cached_col")

    // After error, should still show cached columns (not wipe them)
    await waitFor(() => expect(mockPreview).toHaveBeenCalled())
    expect(result.current).toHaveLength(1)
    expect(result.current[0].name).toBe("cached_col")
  })

  it("handles result with undefined columns", async () => {
    // Backend returns ok but no columns field
    mockPreview.mockResolvedValue({ node_id: "ds1", status: "ok" })
    const { result } = renderHook(() => useDataInputColumns("ds1", nodes, edges))
    await waitFor(() => expect(mockPreview).toHaveBeenCalled())
    // Should remain empty (not crash)
    expect(result.current).toEqual([])
  })

  it("handles result with empty columns array", async () => {
    mockPreview.mockResolvedValue({ node_id: "ds1", status: "ok", columns: [] })
    const { result } = renderHook(() => useDataInputColumns("ds1", nodes, edges))
    await waitFor(() => expect(result.current).toEqual([]))
  })

  // ── Stale node ID handling ────────────────────────────────────────

  it("returns empty when dataInput references a non-existent node", async () => {
    // dataInput is "Polars_8" but no such node exists in the graph
    mockPreview.mockRejectedValue(new Error("Node not found"))
    const { result } = renderHook(() =>
      useDataInputColumns("Polars_8", nodes, edges),
    )
    await waitFor(() => expect(mockPreview).toHaveBeenCalled())
    expect(result.current).toEqual([])
  })

  // ── dataInput switching ───────────────────────────────────────────

  it("clears columns when dataInput switches to empty", async () => {
    mockPreview.mockResolvedValue({ node_id: "ds1", status: "ok", columns: sampleColumns })

    const { result, rerender } = renderHook(
      ({ input }: { input: string }) => useDataInputColumns(input, nodes, edges),
      { initialProps: { input: "ds1" } },
    )
    await waitFor(() => expect(result.current).toHaveLength(2))

    // Switch to empty
    rerender({ input: "" })
    expect(result.current).toEqual([])
  })

  it("fetches new columns when dataInput switches to different node", async () => {
    const cols1 = [{ name: "col_a", dtype: "i64" }]
    const cols2 = [{ name: "col_b", dtype: "f64" }]

    mockPreview
      .mockResolvedValueOnce({ node_id: "ds1", status: "ok", columns: cols1 })
      .mockResolvedValueOnce({ node_id: "t1", status: "ok", columns: cols2 })

    const { result, rerender } = renderHook(
      ({ input }: { input: string }) => useDataInputColumns(input, nodes, edges),
      { initialProps: { input: "ds1" } },
    )
    await waitFor(() => expect(result.current[0]?.name).toBe("col_a"))

    // Switch to different node
    rerender({ input: "t1" })
    await waitFor(() => expect(result.current[0]?.name).toBe("col_b"))
  })

  // ── Cache interaction ─────────────────────────────────────────────

  it("writes fetched columns to the cache store with source key", async () => {
    mockPreview.mockResolvedValue({ node_id: "ds1", status: "ok", columns: sampleColumns })

    renderHook(() => useDataInputColumns("ds1", nodes, edges))
    await waitFor(() => expect(mockPreview).toHaveBeenCalled())

    const cache = useNodeResultsStore.getState().columnCache
    // Cache key includes source: "ds1:live"
    expect(cache["ds1:live"]).toBeDefined()
    expect(cache["ds1:live"].columns).toEqual(sampleColumns)
  })

  it("does not write to cache when API returns no columns", async () => {
    mockPreview.mockResolvedValue({ node_id: "ds1", status: "ok" })

    renderHook(() => useDataInputColumns("ds1", nodes, edges))
    await waitFor(() => expect(mockPreview).toHaveBeenCalled())

    const cache = useNodeResultsStore.getState().columnCache
    expect(cache["ds1:live"]).toBeUndefined()
  })

  // ── Row limit ─────────────────────────────────────────────────────

  it("requests with row_limit=1 for efficiency", async () => {
    mockPreview.mockResolvedValue({ node_id: "ds1", status: "ok", columns: sampleColumns })

    renderHook(() => useDataInputColumns("ds1", nodes, edges))
    await waitFor(() => expect(mockPreview).toHaveBeenCalled())

    const callArgs = mockPreview.mock.calls[0]
    // previewNode(graph, nodeId, rowLimit, source, options)
    expect(callArgs[2]).toBe(1)
  })

  // ── AbortController (Issue 2) ──────────────────────────────────────

  it("passes AbortSignal to previewNode", async () => {
    mockPreview.mockResolvedValue({ node_id: "ds1", status: "ok", columns: sampleColumns })

    renderHook(() => useDataInputColumns("ds1", nodes, edges))
    await waitFor(() => expect(mockPreview).toHaveBeenCalled())

    // 5th argument is options with signal
    const options = mockPreview.mock.calls[0][4]
    expect(options).toBeDefined()
    expect(options?.signal).toBeInstanceOf(AbortSignal)
  })

  it("ignores AbortError when request is cancelled", async () => {
    const abortError = new DOMException("The operation was aborted.", "AbortError")
    mockPreview.mockRejectedValue(abortError)

    const { result } = renderHook(() => useDataInputColumns("ds1", nodes, edges))
    await waitFor(() => expect(mockPreview).toHaveBeenCalled())
    // Should NOT show a toast or crash — AbortErrors are expected during cleanup
    expect(result.current).toEqual([])
  })

  // ── Source-aware cache (Issue 6) ─────────────────────────────────

  it("uses separate cache entries per source", () => {
    useNodeResultsStore.setState({
      columnCache: {
        "ds1:live": { columns: [{ name: "live_col", dtype: "f64" }], structuralVersion: 0 },
        "ds1:nb_batch": { columns: [{ name: "batch_col", dtype: "i64" }], structuralVersion: 0 },
      },
    })

    // Live source → live columns
    useSettingsStore.setState({ activeSource: "live" })
    const { result: liveResult } = renderHook(() => useDataInputColumns("ds1", nodes, edges))
    expect(liveResult.current[0].name).toBe("live_col")

    cleanup()

    // Batch source → batch columns
    useSettingsStore.setState({ activeSource: "nb_batch" })
    const { result: batchResult } = renderHook(() => useDataInputColumns("ds1", nodes, edges))
    expect(batchResult.current[0].name).toBe("batch_col")
  })

  it("refetches when source changes even if structuralVersion is same", async () => {
    // Cache exists for "live" but not for "nb_batch"
    useNodeResultsStore.setState({
      columnCache: {
        "ds1:live": { columns: sampleColumns, structuralVersion: 0 },
      },
    })
    useSettingsStore.setState({ activeSource: "live" })

    const batchCols = [{ name: "batch_only", dtype: "str" }]
    mockPreview.mockResolvedValue({ node_id: "ds1", status: "ok", columns: batchCols })

    const { result } = renderHook(() => useDataInputColumns("ds1", nodes, edges))

    // Live cache hit — no API call
    expect(mockPreview).not.toHaveBeenCalled()
    expect(result.current[0].name).toBe("age")

    // Switch to nb_batch — no cache entry, should fetch
    act(() => {
      useSettingsStore.setState({ activeSource: "nb_batch" })
    })
    await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1))
    expect(result.current[0].name).toBe("batch_only")
  })

  // ── Toast on error (Issue 5) ───────────────────────────────────────

  it("shows toast when API call fails", async () => {
    mockPreview.mockRejectedValue(new Error("Server error"))
    renderHook(() => useDataInputColumns("ds1", nodes, edges))
    await waitFor(() => expect(mockPreview).toHaveBeenCalled())

    // Give the catch handler time to run
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      expect(toasts.some((t) => t.type === "warning" && t.text.includes("ds1"))).toBe(true)
    })
  })
})
