/**
 * The one background poller also drives shared node-data builds, so a build
 * any consumer of a data point started finishes and raises the node-data epoch
 * whichever panel is open.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act, cleanup } from "@testing-library/react"

vi.mock("../../api/client.ts", () => ({
  getOptimiserStatus: vi.fn(),
  getTrainStatus: vi.fn(),
  getExploreStatus: vi.fn(),
  getExplorePivotStatus: vi.fn(),
  getNodeDataStatus: vi.fn(),
}))

import { getNodeDataStatus } from "../../api/client.ts"
import type { NodeDataPointResponse, NodeDataStatusResponse } from "../../api/types.ts"
import useNodeDataStore from "../../stores/useNodeDataStore.ts"
import useToastStore from "../../stores/useToastStore.ts"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore.ts"
import useBackgroundJobs from "../../hooks/useBackgroundJobs.ts"

const mockNodeDataStatus = vi.mocked(getNodeDataStatus)

function point(): NodeDataPointResponse {
  return {
    consumer_node_id: "explore",
    point: { producer_node_id: "join", port_label: null },
    slot_key: "join||live",
    kind: "node_output",
    state: "missing",
    demand: "all",
    data_version: null,
    generation: null,
    job: null,
    reads_directly: false,
  }
}

function status(overrides: Partial<NodeDataStatusResponse> = {}): NodeDataStatusResponse {
  return { status: "running", progress: 0.5, message: "Caching data", ...overrides }
}

describe("useBackgroundJobs — shared node-data builds", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    useNodeDataStore.getState().reset()
    useToastStore.setState({ toasts: [] })
    useDocumentStatusStore.setState({
      sourceFile: "main.py",
      executionGeneration: 1,
      loadStatus: "ready",
      capabilities: { can_execute: true } as never,
      graphSynchronized: true,
    })
    mockNodeDataStatus.mockReset()
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  function startBuild() {
    useNodeDataStore.getState().observePoint(point(), "live", "identity-1")
    useNodeDataStore
      .getState()
      .startJob("join||live", { jobId: "job-1", message: "Caching data", startedByLabel: "Explore" })
  }

  it("polls a build to completion, clears it, and raises the epoch", async () => {
    mockNodeDataStatus
      .mockResolvedValueOnce(status({ progress: 0.7 }))
      .mockResolvedValue(status({ status: "completed", progress: 1, message: "Data is cached" }))
    startBuild()
    const epochBefore = useNodeDataStore.getState().epoch
    renderHook(() => useBackgroundJobs())

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000)
    })

    expect(mockNodeDataStatus).toHaveBeenCalledWith("job-1", expect.objectContaining({ signal: expect.anything() }))
    expect(useNodeDataStore.getState().jobs).toEqual({})
    expect(useNodeDataStore.getState().slots["join||live"].job).toBeNull()
    expect(useNodeDataStore.getState().epoch).toBeGreaterThan(epochBefore)
    expect(useToastStore.getState().toasts.some((toast) => toast.type === "success")).toBe(true)
  })

  it("ends a failed build and still has its consumers ask what is there now", async () => {
    mockNodeDataStatus.mockResolvedValue(
      status({ status: "memory_limited", progress: 1, message: "Out of memory", error: "Out of memory" }),
    )
    startBuild()
    const epochBefore = useNodeDataStore.getState().epoch
    renderHook(() => useBackgroundJobs())

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000)
    })

    expect(useNodeDataStore.getState().jobs).toEqual({})
    expect(useNodeDataStore.getState().slots["join||live"].job).toBeNull()
    // The failure left the point as it was, so consumers must re-ask rather
    // than keep showing a build that is no longer running.
    expect(useNodeDataStore.getState().epoch).toBeGreaterThan(epochBefore)
    expect(useToastStore.getState().toasts.some((toast) => toast.type === "error")).toBe(true)
  })

  it("drops every slot when the document fence moves", async () => {
    startBuild()
    mockNodeDataStatus.mockResolvedValue(status())
    renderHook(() => useBackgroundJobs())

    await act(async () => {
      useDocumentStatusStore.setState({ executionGeneration: 2 })
      await vi.advanceTimersByTimeAsync(100)
    })

    expect(useNodeDataStore.getState().slots).toEqual({})
    expect(useNodeDataStore.getState().jobs).toEqual({})
  })
})
