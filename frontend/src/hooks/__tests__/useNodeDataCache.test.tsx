import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, waitFor, act } from "@testing-library/react"

import type { NodeDataPointResponse } from "../../api/types"
import useNodeDataStore from "../../stores/useNodeDataStore"
import useSettingsStore from "../../stores/useSettingsStore"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import useToastStore from "../../stores/useToastStore"
import useNodeDataCache, { deriveAvailability, refreshNodeDataCache } from "../useNodeDataCache"

vi.mock("../../api/client", () => ({
  getNodeDataPoint: vi.fn(),
  runNodeData: vi.fn(),
  getNodeDataStatus: vi.fn(),
  cancelNodeData: vi.fn(),
  clearNodeData: vi.fn(),
  clearInputCache: vi.fn(),
}))

vi.mock("../../utils/buildGraph", () => ({
  buildGraph: vi.fn(() => ({ nodes: [], edges: [], preamble: "" })),
}))

const ensureInputSnapshots = vi.fn(async (_nodes: unknown[], _options?: unknown) => {})
const cancelInputSnapshotBuild = vi.fn(async (_jobId: string) => {})
vi.mock("../ensureInputSnapshots", () => ({
  ensureInputSnapshots: (nodes: unknown[], options?: unknown) => ensureInputSnapshots(nodes, options),
  cancelInputSnapshotBuild: (jobId: string) => cancelInputSnapshotBuild(jobId),
}))

import {
  cancelNodeData,
  clearInputCache,
  clearNodeData,
  getNodeDataPoint,
  runNodeData,
} from "../../api/client"

const mockGetPoint = vi.mocked(getNodeDataPoint)
const mockRun = vi.mocked(runNodeData)
const mockCancel = vi.mocked(cancelNodeData)
const mockClear = vi.mocked(clearNodeData)
const mockClearInputCache = vi.mocked(clearInputCache)

const nodes = [
  { id: "source", data: { label: "source", nodeType: "dataInput", config: { path: "quotes.parquet" } } },
  { id: "join", data: { label: "join", nodeType: "polars", config: { code: "df = source" } } },
  { id: "explore", data: { label: "Explore", nodeType: "explore", config: {} } },
  { id: "banding", data: { label: "Banding", nodeType: "banding", config: { factors: [] } } },
] as never as Parameters<typeof useNodeDataCache>[0]["allNodes"]

const edges = [
  { id: "e1", source: "source", target: "join" },
  { id: "e2", source: "join", target: "explore" },
  { id: "e3", source: "join", target: "banding" },
] as never as Parameters<typeof useNodeDataCache>[0]["edges"]

function nodeById(id: string) {
  return (nodes as unknown as { id: string }[]).find((node) => node.id === id) as never
}

function point(overrides: Partial<NodeDataPointResponse> = {}): NodeDataPointResponse {
  return {
    consumer_node_id: "explore",
    point: { producer_node_id: "join", port_label: null },
    slot_key: "join||live",
    kind: "node_output",
    state: "current",
    demand: "all",
    data_version: "gen-1",
    row_count: 1000,
    size_bytes: 4096,
    retention: "pinned",
    generation: {
      generation_id: "gen-1",
      columns: "all",
      row_count: 1000,
      column_count: 4,
      size_bytes: 4096,
      retention: "pinned",
      fresh: true,
      created_at: 1,
    },
    job: null,
    reads_directly: false,
    build_endpoint: null,
    clear_endpoint: null,
    ...overrides,
  }
}

function renderCache(nodeId: string) {
  return renderHook(() =>
    useNodeDataCache({ node: nodeById(nodeId), allNodes: nodes, edges, preamble: "" }),
  )
}

describe("useNodeDataCache", () => {
  beforeEach(() => {
    useNodeDataStore.getState().reset()
    useSettingsStore.setState({ activeSource: "live", sources: ["live"] })
    useDocumentStatusStore.setState({
      sourceFile: "main.py",
      executionGeneration: 1,
      loadStatus: "ready",
      capabilities: { can_execute: true } as never,
      graphSynchronized: true,
    })
    useToastStore.setState({ toasts: [] })
    vi.mocked(mockGetPoint).mockReset()
    mockRun.mockReset()
    mockCancel.mockReset()
    mockClear.mockReset()
    mockClearInputCache.mockReset()
    ensureInputSnapshots.mockReset()
    ensureInputSnapshots.mockResolvedValue(undefined)
  })

  afterEach(() => cleanup())

  it("reports the point the backend resolved for this consumer", async () => {
    mockGetPoint.mockResolvedValue(point())
    const { result } = renderCache("explore")

    await waitFor(() => expect(result.current.availability).toBe("current"))
    expect(result.current.rowCount).toBe(1000)
    expect(result.current.sizeBytes).toBe(4096)
    expect(result.current.retention).toBe("pinned")
    expect(result.current.dataVersion).toBe("gen-1")
    expect(result.current.canBuild).toBe(true)
  })

  it("gives two consumers of one point their own availability from one slot entry", async () => {
    const narrow = {
      generation_id: "gen-1",
      columns: ["premium"],
      row_count: 1000,
      column_count: 1,
      size_bytes: 512,
      retention: "automatic" as const,
      fresh: true,
      created_at: 1,
    }
    mockGetPoint.mockImplementation(async (args) =>
      args.node_id === "banding"
        ? point({ consumer_node_id: "banding", demand: ["premium"], generation: narrow, state: "current" })
        : point({ consumer_node_id: "explore", demand: "all", generation: narrow, state: "partial" }),
    )

    const explore = renderCache("explore")
    const banding = renderCache("banding")

    await waitFor(() => expect(banding.result.current.availability).toBe("current"))
    await waitFor(() => expect(explore.result.current.availability).toBe("partial"))
    expect(Object.keys(useNodeDataStore.getState().slots)).toEqual(["join||live"])
  })

  it("shows the build any consumer of the point started, with its progress", async () => {
    mockGetPoint.mockResolvedValue(point({ state: "missing", generation: null, data_version: null }))
    mockRun.mockResolvedValue({
      status: "started",
      job_id: "job-1",
      cached: false,
      message: "Caching started",
      point: point({ state: "building", generation: null, data_version: null }),
    })

    const explore = renderCache("explore")
    const banding = renderCache("banding")
    await waitFor(() => expect(explore.result.current.availability).toBe("missing"))

    await act(async () => {
      await explore.result.current.run()
    })

    expect(mockRun).toHaveBeenCalledWith(expect.objectContaining({ node_id: "explore", refresh: false }))
    await waitFor(() => expect(banding.result.current.jobId).toBe("job-1"))
    expect(banding.result.current.availability).toBe("building")

    act(() => {
      useNodeDataStore
        .getState()
        .updateJobProgress("join||live", { status: "running", progress: 0.4, message: "Caching data" })
    })
    await waitFor(() => expect(banding.result.current.progress).toBeCloseTo(0.4))
    expect(explore.result.current.progress).toBeCloseTo(0.4)
  })

  it("re-asks the backend when a completed build raises the node-data epoch", async () => {
    mockGetPoint.mockResolvedValue(point({ state: "missing", generation: null, data_version: null }))
    const { result } = renderCache("explore")
    await waitFor(() => expect(result.current.availability).toBe("missing"))
    expect(mockGetPoint).toHaveBeenCalledTimes(1)

    mockGetPoint.mockResolvedValue(point())
    act(() => {
      useNodeDataStore.getState().observePoint(point(), "live", "identity-1")
    })

    await waitFor(() => expect(result.current.availability).toBe("current"))
    expect(mockGetPoint.mock.calls.length).toBeGreaterThan(1)
  })

  // The node's Refresh button is the only way to cache data now, so what it
  // costs is decided here: data the consumer can already use is left alone,
  // because the same button is pressed to re-read a node's generated fields.
  it("caches on a refresh request when nothing is cached", async () => {
    mockGetPoint.mockResolvedValue(point({ state: "missing", generation: null, data_version: null }))
    mockRun.mockResolvedValue({
      status: "started",
      job_id: "job-refresh",
      cached: false,
      message: "Caching started",
      point: point({ state: "building", generation: null, data_version: null }),
    })
    const { result } = renderCache("explore")
    await waitFor(() => expect(result.current.availability).toBe("missing"))

    await act(async () => {
      refreshNodeDataCache("explore")
    })

    await waitFor(() => expect(mockRun).toHaveBeenCalledTimes(1))
    // Nothing is cached, so there is nothing to replace: it builds rather
    // than forcing a recompute.
    expect(mockRun.mock.calls[0][0]).toMatchObject({ node_id: "explore", refresh: false })
  })

  it("re-caches on a refresh request when what is cached is out of date", async () => {
    // A node_output's freshness is read off its generation, not the point's
    // own state word.
    mockGetPoint.mockResolvedValue(
      point({ state: "stale", generation: { ...point().generation!, fresh: false } }),
    )
    mockRun.mockResolvedValue({
      status: "started",
      job_id: "job-stale",
      cached: false,
      message: "Caching started",
      point: point({ state: "building" }),
    })
    const { result } = renderCache("explore")
    await waitFor(() => expect(result.current.availability).toBe("stale"))

    await act(async () => {
      refreshNodeDataCache("explore")
    })

    await waitFor(() => expect(mockRun).toHaveBeenCalledTimes(1))
    expect(mockRun.mock.calls[0][0]).toMatchObject({ node_id: "explore", refresh: true })
  })

  it("leaves current data alone on a refresh request", async () => {
    mockGetPoint.mockResolvedValue(point())
    const { result } = renderCache("explore")
    await waitFor(() => expect(result.current.availability).toBe("current"))

    await act(async () => {
      refreshNodeDataCache("explore")
    })

    // A Refresh pressed to re-read this node's generated fields must not
    // recompute a dataset that has not changed.
    expect(mockRun).not.toHaveBeenCalled()
  })

  it("ignores a refresh request aimed at a different node", async () => {
    mockGetPoint.mockResolvedValue(point({ state: "missing", generation: null, data_version: null }))
    const { result } = renderCache("explore")
    await waitFor(() => expect(result.current.availability).toBe("missing"))

    await act(async () => {
      refreshNodeDataCache("banding")
    })

    expect(mockRun).not.toHaveBeenCalled()
  })

  it("refreshes a cached point instead of completing as cached", async () => {
    mockGetPoint.mockResolvedValue(point())
    mockRun.mockResolvedValue({
      status: "started",
      job_id: "job-2",
      cached: false,
      message: "Caching started",
      point: point({ state: "building" }),
    })
    const { result } = renderCache("explore")
    await waitFor(() => expect(result.current.availability).toBe("current"))

    await act(async () => {
      await result.current.refresh()
    })

    expect(mockRun).toHaveBeenCalledWith(expect.objectContaining({ refresh: true }))
  })

  function delegatedPoint(state: NodeDataPointResponse["state"] = "missing"): NodeDataPointResponse {
    return point({
      consumer_node_id: "banding",
      point: { producer_node_id: "source", port_label: null },
      slot_key: "source||live",
      kind: "data_input",
      state,
      demand: ["premium"],
      generation: null,
      data_version: state === "missing" ? null : "input-gen-1",
      build_endpoint: "/api/input-cache/build",
      clear_endpoint: "/api/input-cache/clear",
    })
  }

  it("delegates a snapshot-backed Data Input build to the input-snapshot flow", async () => {
    const delegated = delegatedPoint()
    mockGetPoint.mockResolvedValue(delegated)
    mockRun.mockResolvedValue({
      status: "delegated",
      job_id: null,
      cached: false,
      message: "Build this Data Input's snapshot",
      point: delegated,
    })
    const { result } = renderCache("banding")
    await waitFor(() => expect(result.current.availability).toBe("missing"))

    await act(async () => {
      await result.current.run()
    })

    // Exactly the producer node of the point, so the ensure pass builds that
    // Data Input's snapshot rather than some other node's.
    expect(ensureInputSnapshots).toHaveBeenCalledTimes(1)
    expect(ensureInputSnapshots.mock.calls[0][0]).toEqual([nodeById("source")])
    const options = ensureInputSnapshots.mock.calls[0][1] as { signal?: AbortSignal }
    expect(options.signal).toBeInstanceOf(AbortSignal)
    // Nothing was cleared: there was nothing cached to replace.
    expect(mockClearInputCache).not.toHaveBeenCalled()
  })

  it.each([
    ["a refresh", "current" as const, true],
    ["a stale snapshot", "stale" as const, false],
  ])("replaces delegated data for %s instead of skipping a ready snapshot", async (
    _name,
    state,
    refresh,
  ) => {
    const delegated = delegatedPoint(state)
    mockGetPoint.mockResolvedValue(delegated)
    mockRun.mockResolvedValue({
      status: "delegated",
      job_id: null,
      cached: false,
      message: "Build this Data Input's snapshot",
      point: delegated,
    })
    const { result } = renderCache("banding")
    await waitFor(() => expect(result.current.point).not.toBeNull())

    await act(async () => {
      await (refresh ? result.current.refresh() : result.current.run())
    })

    // The ensure pass leaves a snapshot it considers ready alone, so a refresh
    // and an already-stale point both ask it to rebuild.
    expect(ensureInputSnapshots).toHaveBeenCalledTimes(1)
    expect(ensureInputSnapshots.mock.calls[0][1]).toMatchObject({ force: true })
    expect(mockClearInputCache).not.toHaveBeenCalled()
  })

  it("leaves a served delegated snapshot alone when it is only asked to cache it", async () => {
    const delegated = delegatedPoint("current")
    mockGetPoint.mockResolvedValue(delegated)
    mockRun.mockResolvedValue({
      status: "delegated",
      job_id: null,
      cached: false,
      message: "Build this Data Input's snapshot",
      point: delegated,
    })
    const { result } = renderCache("banding")
    await waitFor(() => expect(result.current.point).not.toBeNull())

    await act(async () => {
      await result.current.run()
    })

    expect(ensureInputSnapshots.mock.calls[0][1]).toMatchObject({ force: false })
  })

  it("shows a delegated build to every consumer of the point and cancels it", async () => {
    const delegated = delegatedPoint()
    mockGetPoint.mockResolvedValue(delegated)
    mockRun.mockResolvedValue({
      status: "delegated",
      job_id: null,
      cached: false,
      message: "Build this Data Input's snapshot",
      point: delegated,
    })
    const releaseEnsure: { current: (() => void) | null } = { current: null }
    const ensureSignal: { current: AbortSignal | null } = { current: null }
    ensureInputSnapshots.mockImplementation(async (_nodes: unknown[], options?: unknown) => {
      ensureSignal.current = (options as { signal?: AbortSignal } | undefined)?.signal ?? null
      await new Promise<void>((resolve) => {
        releaseEnsure.current = resolve
      })
    })
    const banding = renderCache("banding")
    await waitFor(() => expect(banding.result.current.point).not.toBeNull())

    const building = act(async () => {
      await banding.result.current.run()
    })
    await waitFor(() => expect(releaseEnsure.current).not.toBeNull())

    expect(useNodeDataStore.getState().slots["source||live"].delegatedBuild?.startedByLabel).toBe(
      "Banding",
    )
    await act(async () => {
      useNodeDataStore.getState().slots["source||live"].delegatedBuild?.cancel()
    })
    expect(ensureSignal.current?.aborted).toBe(true)

    releaseEnsure.current?.()
    await building
    expect(useNodeDataStore.getState().slots["source||live"].delegatedBuild).toBeNull()
  })

  it("offers no build for a point read straight from its file", async () => {
    mockGetPoint.mockResolvedValue(
      point({
        kind: "data_input",
        point: { producer_node_id: "source", port_label: null },
        slot_key: "source||live",
        reads_directly: true,
        generation: null,
      }),
    )
    const { result } = renderCache("banding")

    await waitFor(() => expect(result.current.readsDirectly).toBe(true))
    expect(result.current.canBuild).toBe(false)
  })

  it("clears a node output through the node-data route and stops claiming it is cached", async () => {
    const cleared = point({
      state: "missing",
      generation: null,
      data_version: null,
      row_count: null,
      size_bytes: null,
      retention: null,
    })
    mockGetPoint.mockResolvedValue(point())
    mockClear.mockResolvedValue({ status: "cleared", point: cleared })
    const { result } = renderCache("explore")
    await waitFor(() => expect(result.current.availability).toBe("current"))
    mockGetPoint.mockResolvedValue(cleared)

    await act(async () => {
      await result.current.clear()
    })

    expect(mockClear).toHaveBeenCalledWith(expect.objectContaining({ node_id: "explore" }))
    expect(mockClearInputCache).not.toHaveBeenCalled()
    await waitFor(() => expect(result.current.availability).toBe("missing"))
    expect(result.current.rowCount).toBeNull()
    expect(useNodeDataStore.getState().slots["join||live"].generation).toBeNull()
  })

  it("never lets an action's answer for an old identity replace the current one", async () => {
    mockGetPoint.mockImplementation(async () => point())
    let finishRun: ((value: Awaited<ReturnType<typeof runNodeData>>) => void) | null = null
    mockRun.mockImplementation(
      () =>
        new Promise<Awaited<ReturnType<typeof runNodeData>>>((resolve) => {
          finishRun = resolve
        }),
    )
    const { result, rerender } = renderHook(
      ({ code }: { code: string }) =>
        useNodeDataCache({
          node: { id: "join", data: { label: "join", nodeType: "polars", config: { code } } } as never,
          allNodes: nodes,
          edges,
          preamble: "",
        }),
      { initialProps: { code: "df = source" } },
    )
    await waitFor(() => expect(result.current.availability).toBe("current"))

    // Started outside act, so the edit below can still flush its own effects
    // while the run is on the wire.
    const running = result.current.refresh()
    await waitFor(() => expect(finishRun).not.toBeNull())
    // The consumer is edited while the run is on the wire, and the new identity
    // gets its own answer for the same underlying data.
    mockGetPoint.mockImplementation(async () => point({ data_version: "gen-2" }))
    await act(async () => {
      rerender({ code: "df = source.head(5)" })
    })
    await waitFor(() => expect(result.current.dataVersion).toBe("gen-2"))

    await act(async () => {
      // The old identity's run completes, reporting the data it cached. Its
      // point carries the same data version, so nothing in the shared slot
      // changes and no re-inspection would rescue a consumer that accepted it.
      finishRun?.({
        status: "completed",
        job_id: null,
        cached: true,
        message: "Data is cached",
        point: point({ data_version: "gen-2" }),
      })
      await running
    })

    // The answer belonged to the identity the consumer has left, so it is
    // dropped outright rather than stranding the consumer without an answer.
    expect(result.current.availability).toBe("current")
    expect(result.current.dataVersion).toBe("gen-2")
    expect(result.current.point?.consumer_node_id).toBe("explore")
  })

  it("never repopulates the shared store with a clear that landed after the document changed", async () => {
    mockGetPoint.mockResolvedValue(point())
    const { result } = renderCache("explore")
    await waitFor(() => expect(result.current.availability).toBe("current"))
    // No inspection may land while the clear is in flight, so the store can
    // only be repopulated by the clear's own answer.
    mockGetPoint.mockImplementation(() => new Promise<NodeDataPointResponse>(() => {}))
    mockClear.mockImplementation(async () => {
      // The document is replaced while the clear is on the wire.
      useDocumentStatusStore.setState({ executionGeneration: 42 })
      useNodeDataStore.getState().reset()
      return {
        status: "cleared" as const,
        point: point({ state: "missing", generation: null, data_version: null }),
      }
    })

    await act(async () => {
      await result.current.clear()
    })

    expect(useNodeDataStore.getState().slots).toEqual({})
    expect(result.current.point).toBeNull()
  })

  it("keeps the cached state and reports the failure when a clear is refused", async () => {
    mockGetPoint.mockResolvedValue(point())
    mockClear.mockRejectedValue(new Error("the analysis directory is in use"))
    const { result } = renderCache("explore")
    await waitFor(() => expect(result.current.availability).toBe("current"))

    await act(async () => {
      await result.current.clear()
    })

    expect(result.current.availability).toBe("current")
    expect(
      useToastStore.getState().toasts.some((toast) => toast.type === "error"),
    ).toBe(true)
  })

  it("stops trusting its answer once the shared store is reset for a new document", async () => {
    mockGetPoint.mockResolvedValue(point())
    const { result } = renderCache("explore")
    await waitFor(() => expect(result.current.availability).toBe("current"))
    const callsBefore = mockGetPoint.mock.calls.length
    let answerAgain: ((value: NodeDataPointResponse) => void) | null = null
    mockGetPoint.mockImplementation(
      () =>
        new Promise<NodeDataPointResponse>((resolve) => {
          answerAgain = resolve
        }),
    )

    await act(async () => {
      useDocumentStatusStore.setState({ executionGeneration: 7 })
      useNodeDataStore.getState().reset()
    })

    // The answer belonged to the previous document, so it is no longer shown,
    // and the consumer asks again for this one.
    await waitFor(() => expect(mockGetPoint.mock.calls.length).toBeGreaterThan(callsBefore))
    expect(result.current.point).toBeNull()
    expect(result.current.availability).toBe("checking")

    await act(async () => {
      answerAgain?.(point())
    })
    await waitFor(() => expect(result.current.availability).toBe("current"))
  })

  it("clears a delegated Data Input point through the input-cache route", async () => {
    const delegated = point({
      point: { producer_node_id: "source", port_label: null },
      slot_key: "source||live",
      kind: "data_input",
      clear_endpoint: "/api/input-cache/clear",
    })
    mockGetPoint.mockResolvedValue(delegated)
    mockClear.mockResolvedValue({ status: "delegated", point: delegated })
    mockClearInputCache.mockResolvedValue({} as never)
    const { result } = renderCache("banding")
    await waitFor(() => expect(result.current.point).not.toBeNull())

    await act(async () => {
      await result.current.clear()
    })

    expect(mockClearInputCache).toHaveBeenCalledWith({
      schema_version: 1,
      config: { path: "quotes.parquet" },
    })
  })

  it("clears an api_input_table point through the input-cache route with node_type apiInput", async () => {
    const quoteNodes = [
      ...nodes,
      {
        id: "quote-source",
        data: {
          label: "quote-source",
          nodeType: "apiInput",
          config: { path: "quotes.json", tables: [{ name: "orders" }] },
        },
      },
    ] as never as Parameters<typeof useNodeDataCache>[0]["allNodes"]
    const delegated = point({
      point: { producer_node_id: "quote-source", port_label: "orders" },
      slot_key: "quote-source|orders|live",
      kind: "api_input_table",
      clear_endpoint: "/api/input-cache/clear",
    })
    mockGetPoint.mockResolvedValue(delegated)
    mockClear.mockResolvedValue({ status: "delegated", point: delegated })
    mockClearInputCache.mockResolvedValue({} as never)
    const { result } = renderHook(() =>
      useNodeDataCache({ node: nodeById("banding"), allNodes: quoteNodes, edges, preamble: "" }),
    )
    await waitFor(() => expect(result.current.point).not.toBeNull())

    await act(async () => {
      await result.current.clear()
    })

    expect(mockClearInputCache).toHaveBeenCalledWith({
      schema_version: 1,
      node_type: "apiInput",
      config: { path: "quotes.json", tables: [{ name: "orders" }] },
    })
  })

  it("cancels the running build and re-asks for the point", async () => {
    mockGetPoint.mockResolvedValue(point({ state: "missing", generation: null, data_version: null }))
    mockRun.mockResolvedValue({
      status: "started",
      job_id: "job-3",
      cached: false,
      message: "Caching started",
      point: point({ state: "building", generation: null, data_version: null }),
    })
    mockCancel.mockResolvedValue({ status: "cancelled", progress: 1, message: "Cache build cancelled" })
    const { result } = renderCache("explore")
    await waitFor(() => expect(result.current.availability).toBe("missing"))
    await act(async () => {
      await result.current.run()
    })

    await act(async () => {
      await result.current.cancel()
    })

    expect(mockCancel).toHaveBeenCalledWith("job-3")
  })

  it("stops showing the previous identity's answer the moment the identity changes", async () => {
    let answerSecond: ((value: NodeDataPointResponse) => void) | null = null
    let identity: "first" | "second" = "first"
    mockGetPoint.mockImplementation(() => {
      if (identity === "first") return Promise.resolve(point())
      return new Promise<NodeDataPointResponse>((resolve) => {
        answerSecond = resolve
      })
    })

    const { result, rerender, unmount } = renderHook(
      ({ code }: { code: string }) =>
        useNodeDataCache({
          node: { id: "join", data: { label: "join", nodeType: "polars", config: { code } } } as never,
          allNodes: nodes,
          edges,
          preamble: "",
        }),
      { initialProps: { code: "df = source" } },
    )
    await waitFor(() => expect(result.current.availability).toBe("current"))

    identity = "second"
    rerender({ code: "df = source.head(5)" })

    expect(result.current.point).toBeNull()
    expect(result.current.availability).toBe("checking")
    await waitFor(() => expect(answerSecond).not.toBeNull())
    await act(async () => {
      answerSecond?.(point({ data_version: "gen-2" }))
    })
    await waitFor(() => expect(result.current.dataVersion).toBe("gen-2"))
    unmount()
  })

  it("aborts the in-flight request for an identity the consumer moved past", async () => {
    let resolveStale: ((value: NodeDataPointResponse) => void) | null = null
    const signals: AbortSignal[] = []
    mockGetPoint.mockImplementation(async (args) => {
      if (args.signal) signals.push(args.signal)
      if (!resolveStale) {
        return new Promise<NodeDataPointResponse>((resolve) => {
          resolveStale = resolve
        })
      }
      return point({ data_version: "gen-2" })
    })

    const { result, rerender, unmount } = renderHook(
      ({ code }: { code: string }) =>
        useNodeDataCache({
          node: { id: "join", data: { label: "join", nodeType: "polars", config: { code } } } as never,
          allNodes: nodes,
          edges,
          preamble: "",
        }),
      { initialProps: { code: "df = source" } },
    )
    await waitFor(() => expect(signals).toHaveLength(1))

    rerender({ code: "df = source.head(5)" })

    await waitFor(() => expect(signals[0].aborted).toBe(true))
    await waitFor(() => expect(result.current.dataVersion).toBe("gen-2"))

    await act(async () => {
      resolveStale?.(point({ data_version: "gen-stale" }))
    })

    expect(result.current.dataVersion).toBe("gen-2")
    unmount()
  })

  it("never writes state for a document that has moved on", async () => {
    mockGetPoint.mockImplementation(async () => {
      useDocumentStatusStore.setState({ executionGeneration: 99 })
      return point()
    })
    const { result } = renderCache("explore")

    await waitFor(() => expect(mockGetPoint).toHaveBeenCalled())
    // Let the answer arrive and every effect settle: the guard has to reject
    // it, rather than the assertion racing ahead of it.
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(result.current.point).toBeNull()
    expect(result.current.availability).toBe("checking")
  })
})

describe("deriveAvailability", () => {
  it("reports a point with no answer yet as checking", () => {
    expect(deriveAvailability(null, false)).toBe("checking")
  })

  it("prefers a running build over any cached state", () => {
    expect(deriveAvailability(point(), true)).toBe("building")
  })

  it("uses the backend state for kinds that are not node outputs", () => {
    expect(deriveAvailability(point({ kind: "data_input", state: "stale" }), false)).toBe("stale")
    expect(deriveAvailability(point({ kind: "api_input_table", state: "missing" }), false)).toBe("missing")
  })

  it("calls a superseded generation stale whatever columns it holds", () => {
    expect(
      deriveAvailability(point({ generation: { ...point().generation!, fresh: false } }), false),
    ).toBe("stale")
  })
})
