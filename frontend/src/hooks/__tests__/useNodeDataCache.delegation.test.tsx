/**
 * Delegated builds through the real input-snapshot orchestration.
 *
 * These cases mount two consumers of one delegated point and drive the actual
 * `ensureInputSnapshots` helper, so the assertions land at the API seam: which
 * build the server is asked for, and that cancelling from either consumer
 * cancels that build rather than only abandoning the poll.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, waitFor, act } from "@testing-library/react"

import type { NodeDataPointResponse } from "../../api/types"
import useNodeDataStore from "../../stores/useNodeDataStore"
import useSettingsStore from "../../stores/useSettingsStore"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import useToastStore from "../../stores/useToastStore"
import useNodeDataCache from "../useNodeDataCache"

vi.mock("../../api/client", () => ({
  ApiError: class ApiError extends Error {
    status = 500
    detail = ""
  },
  getNodeDataPoint: vi.fn(),
  runNodeData: vi.fn(),
  getNodeDataStatus: vi.fn(),
  cancelNodeData: vi.fn(),
  clearNodeData: vi.fn(),
  clearInputCache: vi.fn(),
  deleteJsonCache: vi.fn(),
  buildInputCache: vi.fn(),
  cancelInputCacheJob: vi.fn(),
  getInputCacheJob: vi.fn(),
  getInputCacheStatus: vi.fn(),
  buildJsonCache: vi.fn(),
  getJsonCacheProgress: vi.fn(),
  getJsonCacheStatusForSchema: vi.fn(),
}))

vi.mock("../../utils/buildGraph", () => ({
  buildGraph: vi.fn(() => ({ nodes: [], edges: [], preamble: "" })),
}))

import {
  buildInputCache,
  buildJsonCache,
  cancelInputCacheJob,
  deleteJsonCache,
  getInputCacheJob,
  getInputCacheStatus,
  getNodeDataPoint,
  runNodeData,
} from "../../api/client"

const mockGetPoint = vi.mocked(getNodeDataPoint)
const mockRun = vi.mocked(runNodeData)
const mockBuildInputCache = vi.mocked(buildInputCache)
const mockCancelInputCacheJob = vi.mocked(cancelInputCacheJob)
const mockInputCacheJob = vi.mocked(getInputCacheJob)
const mockInputCacheStatus = vi.mocked(getInputCacheStatus)
const mockDeleteJsonCache = vi.mocked(deleteJsonCache)
const mockBuildJsonCache = vi.mocked(buildJsonCache)

const nodes = [
  {
    id: "source",
    data: {
      label: "source",
      nodeType: "dataInput",
      config: { inputType: "file", format: "csv", mode: "scan", path: "quotes.csv" },
    },
  },
  { id: "banding", data: { label: "Banding", nodeType: "banding", config: { factors: [] } } },
  { id: "rating", data: { label: "Rating", nodeType: "ratingStep", config: { tables: [] } } },
] as never as Parameters<typeof useNodeDataCache>[0]["allNodes"]

const edges = [
  { id: "e1", source: "source", target: "banding" },
  { id: "e2", source: "source", target: "rating" },
] as never as Parameters<typeof useNodeDataCache>[0]["edges"]

function nodeById(id: string) {
  return (nodes as unknown as { id: string }[]).find((node) => node.id === id) as never
}

function delegatedPoint(consumer: string, state: NodeDataPointResponse["state"]): NodeDataPointResponse {
  return {
    consumer_node_id: consumer,
    point: { producer_node_id: "source", port_label: null },
    slot_key: "source||live",
    kind: "data_input",
    state,
    demand: ["premium"],
    data_version: state === "missing" ? null : "input-gen-1",
    generation: null,
    job: null,
    reads_directly: false,
    build_endpoint: "/api/input-cache/build",
    clear_endpoint: "/api/input-cache/clear",
  }
}

function renderConsumer(nodeId: string) {
  return renderHook(() =>
    useNodeDataCache({ node: nodeById(nodeId), allNodes: nodes, edges, preamble: "" }),
  )
}

describe("delegated data-point builds", () => {
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
    for (const mock of [
      mockGetPoint,
      mockRun,
      mockBuildInputCache,
      mockCancelInputCacheJob,
      mockInputCacheJob,
      mockInputCacheStatus,
      mockDeleteJsonCache,
      mockBuildJsonCache,
    ]) {
      mock.mockReset()
    }
  })

  afterEach(() => cleanup())

  function answerPoint(state: NodeDataPointResponse["state"]) {
    mockGetPoint.mockImplementation(async (args) =>
      delegatedPoint(String(args.node_id), state),
    )
    mockRun.mockImplementation(async (args) => ({
      status: "delegated" as const,
      job_id: null,
      cached: false,
      message: "Build this Data Input's snapshot",
      point: delegatedPoint(String(args.node_id), state),
    }))
  }

  it("rebuilds a stale snapshot the ensure pass would otherwise leave alone", async () => {
    answerPoint("stale")
    mockBuildInputCache.mockResolvedValue({ job_id: "input-job-1" } as never)
    mockInputCacheJob.mockResolvedValue({ status: "completed", message: "" } as never)
    const banding = renderConsumer("banding")
    await waitFor(() => expect(banding.result.current.availability).toBe("stale"))

    await act(async () => {
      await banding.result.current.run()
    })

    // The status probe is skipped and the build is asked to refresh, because a
    // ready-but-stale snapshot would otherwise be served as it is.
    expect(mockInputCacheStatus).not.toHaveBeenCalled()
    expect(mockBuildInputCache).toHaveBeenCalledWith(
      expect.objectContaining({ refresh: true, config: expect.objectContaining({ path: "quotes.csv" }) }),
      expect.anything(),
    )
  })

  it("lets a second consumer cancel the build the first one started, and waits for it to stop", async () => {
    // The point reports itself as building until the server job is terminal,
    // exactly as the backend's build probe does.
    let serverJobStatus = "running"
    mockGetPoint.mockImplementation(async (args) =>
      delegatedPoint(String(args.node_id), serverJobStatus === "running" ? "building" : "missing"),
    )
    mockRun.mockImplementation(async (args) => ({
      status: "delegated" as const,
      job_id: null,
      cached: false,
      message: "Build this Data Input's snapshot",
      point: delegatedPoint(String(args.node_id), "building"),
    }))
    mockInputCacheStatus.mockResolvedValue({ state: "missing" } as never)
    mockBuildInputCache.mockResolvedValue({ job_id: "input-job-2" } as never)
    mockInputCacheJob.mockImplementation(async () => ({
      status: serverJobStatus,
      message: "Building",
    }) as never)
    // The endpoint only acknowledges the request: the job is still running when
    // it answers, and stops shortly afterwards.
    mockCancelInputCacheJob.mockImplementation(async () => {
      setTimeout(() => {
        serverJobStatus = "cancelled"
      }, 200)
      return { job_id: "input-job-2", cancellation_requested: true, status: "running" } as never
    })
    const banding = renderConsumer("banding")
    const rating = renderConsumer("rating")
    await waitFor(() => expect(banding.result.current.point).not.toBeNull())

    const building = act(async () => {
      await banding.result.current.run()
    })
    await waitFor(() => expect(mockBuildInputCache).toHaveBeenCalled())
    // Both consumers of the point show the one delegated build.
    await waitFor(() => expect(rating.result.current.availability).toBe("building"))
    expect(banding.result.current.availability).toBe("building")

    await act(async () => {
      await rating.result.current.cancel()
    })
    await building

    expect(mockCancelInputCacheJob).toHaveBeenCalledWith("input-job-2")
    // The delegated build only ends once the server job is terminal, so no
    // consumer is left offering a cancel control for a build nothing polls.
    expect(serverJobStatus).toBe("cancelled")
    await waitFor(() => expect(useNodeDataStore.getState().slots["source||live"].delegatedBuild).toBeNull())
    await waitFor(() => expect(rating.result.current.availability).toBe("missing"))
    expect(banding.result.current.availability).toBe("missing")
    expect(useToastStore.getState().toasts.some((toast) => toast.type === "error")).toBe(false)
  })

  it("reports a cancellation the server refused instead of claiming the build stopped", async () => {
    answerPoint("missing")
    mockInputCacheStatus.mockResolvedValue({ state: "missing" } as never)
    mockBuildInputCache.mockResolvedValue({ job_id: "input-job-3" } as never)
    mockInputCacheJob.mockResolvedValue({ status: "running", message: "Building" } as never)
    mockCancelInputCacheJob.mockRejectedValue(new Error("the server is unreachable"))
    const banding = renderConsumer("banding")
    await waitFor(() => expect(banding.result.current.availability).toBe("missing"))

    const building = act(async () => {
      await banding.result.current.run()
    })
    await waitFor(() => expect(mockBuildInputCache).toHaveBeenCalled())

    await act(async () => {
      await banding.result.current.cancel()
    })
    await building

    const toasts = useToastStore.getState().toasts
    expect(toasts.some((toast) => toast.type === "error")).toBe(true)
    expect(toasts.map((toast) => toast.text).join(" ")).toMatch(
      /cancelling the data cache failed.*could not be cancelled.*unreachable/i,
    )
    // The build may still be running, so the control stays live and cancels it
    // again rather than leaving the point building with nothing watching it.
    const retained = useNodeDataStore.getState().slots["source||live"].delegatedBuild
    expect(retained?.message).toMatch(/cancel it again/i)
    expect(banding.result.current.availability).toBe("building")

    mockCancelInputCacheJob.mockResolvedValue({
      job_id: "input-job-3",
      cancellation_requested: true,
      status: "cancelled",
    } as never)
    await act(async () => {
      retained?.cancel()
      await Promise.resolve()
    })

    expect(mockCancelInputCacheJob).toHaveBeenCalledTimes(2)
    await waitFor(() =>
      expect(useNodeDataStore.getState().slots["source||live"].delegatedBuild).toBeNull(),
    )
  })

  it("reports a build that never stops after it was cancelled, keeping its control", async () => {
    vi.useFakeTimers()
    try {
      answerPoint("missing")
      mockInputCacheStatus.mockResolvedValue({ state: "missing" } as never)
      mockBuildInputCache.mockResolvedValue({ job_id: "input-job-4" } as never)
      // The job ignores its cancellation and keeps running for good.
      mockInputCacheJob.mockResolvedValue({ status: "running", message: "Building" } as never)
      mockCancelInputCacheJob.mockResolvedValue({
        job_id: "input-job-4",
        cancellation_requested: true,
        status: "running",
      } as never)
      const banding = renderConsumer("banding")
      await vi.waitFor(() => expect(banding.result.current.availability).toBe("missing"))

      const building = act(async () => {
        await banding.result.current.run()
      })
      await vi.waitFor(() => expect(mockBuildInputCache).toHaveBeenCalled())

      const cancelling = act(async () => {
        await banding.result.current.cancel()
      })
      // Every cancellation poll elapses without the job stopping.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(60 * 800 + 1_000)
      })
      await cancelling
      await building

      const toasts = useToastStore.getState().toasts
      expect(toasts.map((toast) => toast.text).join(" ")).toMatch(
        /cancelling the data cache failed.*did not stop/i,
      )
      const retained = useNodeDataStore.getState().slots["source||live"].delegatedBuild
      expect(retained?.message).toMatch(/cancel it again/i)
      expect(banding.result.current.availability).toBe("building")
    } finally {
      vi.useRealTimers()
    }
  })

  it("keeps the control when a cancellation is accepted but its outcome cannot be read", async () => {
    answerPoint("missing")
    mockInputCacheStatus.mockResolvedValue({ state: "missing" } as never)
    mockBuildInputCache.mockResolvedValue({ job_id: "input-job-5" } as never)
    mockCancelInputCacheJob.mockResolvedValue({
      job_id: "input-job-5",
      cancellation_requested: true,
      status: "running",
    } as never)
    let polls = 0
    mockInputCacheJob.mockImplementation(async () => {
      polls += 1
      // The build poll succeeds; the poll that would confirm the cancellation
      // fails, so whether the build stopped is unknown.
      if (polls > 1) throw new Error("the server is unreachable")
      return { status: "running", message: "Building" } as never
    })
    const banding = renderConsumer("banding")
    await waitFor(() => expect(banding.result.current.availability).toBe("missing"))

    const building = act(async () => {
      await banding.result.current.run()
    })
    await waitFor(() => expect(mockBuildInputCache).toHaveBeenCalled())

    await act(async () => {
      await banding.result.current.cancel()
    })
    await building

    expect(useToastStore.getState().toasts.map((toast) => toast.text).join(" ")).toMatch(
      /cancelling the data cache failed.*could not be confirmed as stopped/i,
    )
    const retained = useNodeDataStore.getState().slots["source||live"].delegatedBuild
    expect(retained?.message).toMatch(/cancel it again/i)
    expect(banding.result.current.availability).toBe("building")
  })

  it("never lets a late cancellation failure take over the build that replaced it", async () => {
    answerPoint("missing")
    mockInputCacheStatus.mockResolvedValue({ state: "missing" } as never)
    mockBuildInputCache.mockResolvedValue({ job_id: "input-job-6" } as never)
    mockInputCacheJob.mockResolvedValue({ status: "running", message: "Building" } as never)
    let refuseCancellation: (() => void) | null = null
    mockCancelInputCacheJob.mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          refuseCancellation = () => reject(new Error("the server is unreachable"))
        }),
    )
    const banding = renderConsumer("banding")
    await waitFor(() => expect(banding.result.current.availability).toBe("missing"))
    const building = act(async () => {
      await banding.result.current.run()
    })
    await waitFor(() => expect(mockBuildInputCache).toHaveBeenCalled())
    const cancelling = act(async () => {
      await banding.result.current.cancel()
    })
    await waitFor(() => expect(refuseCancellation).not.toBeNull())

    // The document is replaced and another build claims the same slot while
    // that cancellation is still unresolved.
    const replacement = vi.fn()
    await act(async () => {
      useNodeDataStore.getState().reset()
      useNodeDataStore.getState().observePoint(delegatedPoint("banding", "missing"), "live")
      useNodeDataStore.getState().startDelegatedBuild("source||live", {
        token: "op-replacement",
        message: "Preparing this input",
        startedByLabel: "Rating",
        cancel: replacement,
      })
      refuseCancellation?.()
    })
    await cancelling
    await building

    const current = useNodeDataStore.getState().slots["source||live"].delegatedBuild
    expect(current?.token).toBe("op-replacement")
    expect(current?.message).toBe("Preparing this input")
    expect(current?.cancel).toBe(replacement)
  })

  it("replaces a Quote Input cache the build endpoint would otherwise answer with no work", async () => {
    const quoteNodes = [
      {
        id: "source",
        data: {
          label: "source",
          nodeType: "apiInput",
          config: { path: "quotes.json", tables: [{ name: "orders" }] },
        },
      },
      { id: "banding", data: { label: "Banding", nodeType: "banding", config: { factors: [] } } },
    ] as never as Parameters<typeof useNodeDataCache>[0]["allNodes"]
    const quoteEdges = [{ id: "e1", source: "source", target: "banding" }] as never as Parameters<
      typeof useNodeDataCache
    >[0]["edges"]
    const table = (state: NodeDataPointResponse["state"]) => ({
      ...delegatedPoint("banding", state),
      kind: "api_input_table" as const,
      point: { producer_node_id: "source", port_label: "orders" },
      slot_key: "source|orders|live",
      build_endpoint: "/api/json-cache/build",
      clear_endpoint: "/api/json-cache",
    })
    mockGetPoint.mockImplementation(async () => table("current"))
    mockRun.mockImplementation(async () => ({
      status: "delegated" as const,
      job_id: null,
      cached: false,
      message: "Build this table's cache",
      point: table("current"),
    }))
    let releaseDelete: (() => void) | null = null
    mockDeleteJsonCache.mockImplementation(
      () =>
        new Promise((resolve) => {
          releaseDelete = () => resolve({ cached: false, data_path: "" } as never)
        }),
    )
    mockBuildJsonCache.mockResolvedValue({ cached: true } as never)
    const { result } = renderHook(() =>
      useNodeDataCache({
        node: (quoteNodes as unknown as { id: string }[])[1] as never,
        allNodes: quoteNodes,
        edges: quoteEdges,
        preamble: "",
      }),
    )
    await waitFor(() => expect(result.current.point).not.toBeNull())

    const refreshing = result.current.refresh()
    await waitFor(() => expect(releaseDelete).not.toBeNull())

    // The build waits for that removal: building first and deleting afterwards
    // would throw away the cache the build had just produced.
    expect(mockDeleteJsonCache).toHaveBeenCalledWith("quotes.json", expect.anything())
    expect(mockBuildJsonCache).not.toHaveBeenCalled()

    await act(async () => {
      releaseDelete?.()
      await refreshing
    })

    expect(mockBuildJsonCache).toHaveBeenCalled()
    // The refreshed table is what the consumer ends up reading.
    await waitFor(() => expect(result.current.availability).toBe("current"))
  })
})
