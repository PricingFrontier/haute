import { act, renderHook } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type {
  ExplorePivotResult,
  ExplorePivotRunResponse,
  ExplorePivotStatusResponse,
} from "../../../api/types"
import useGraphStore from "../../../stores/useGraphStore"
import useNodeResultsStore, {
  explorePivotResultKey,
  resetNodeResultsDerivedCaches,
} from "../../../stores/useNodeResultsStore"
import useSettingsStore from "../../../stores/useSettingsStore"
import type { SimpleNode } from "../../editors"
import {
  pivotCalculationIdentity,
  type ExplorePivotConfig,
} from "../pivotConfig"
import useExplorePivotActions from "../useExplorePivotActions"

const mockRunExplorePivot = vi.fn()
const mockCancelExplorePivot = vi.fn()

vi.mock("../../../api/client", () => ({
  runExplorePivot: (...args: unknown[]) => mockRunExplorePivot(...args),
  cancelExplorePivot: (...args: unknown[]) => mockCancelExplorePivot(...args),
}))

const node: SimpleNode = {
  id: "explore_1",
  type: "explore",
  data: { label: "Explore Claims", description: "", nodeType: "explore" },
}

function pivot(overrides: Partial<ExplorePivotConfig> = {}): ExplorePivotConfig {
  return {
    version: 1,
    id: "claims",
    name: "Claims",
    enabled: true,
    filters: [],
    rows: [{ id: "region", field: "region" }],
    columns: [],
    values: [
      {
        id: "paid",
        field: "paid",
        aggregation: "sum",
        display_name: "Paid",
      },
    ],
    options: { row_grand_totals: true, column_grand_totals: true },
    ...overrides,
  }
}

const result: ExplorePivotResult = {
  version: 1,
  node_id: node.id,
  pivot_id: "claims",
  source: "pricing",
  dataframe_cache_key: "explore:current",
  calculation_key: "calculation",
  row_fields: ["region"],
  column_fields: [],
  values: [{ id: "paid", field: "paid", aggregation: "sum" }],
  row_paths: [],
  column_paths: [],
  cells: [],
  warnings: [],
  generated_at: 1,
  execution_metrics: null,
}

function renderActions(targetNode = node) {
  return renderHook(() =>
    useExplorePivotActions({
      node: targetNode,
      allNodes: [targetNode],
      edges: [],
    }),
  )
}

function startActiveJob(config: ExplorePivotConfig, jobId = "active-job") {
  const key = explorePivotResultKey(node.id, config.id)
  useNodeResultsStore.getState().startExplorePivotJob(
    key,
    jobId,
    node.id,
    config.id,
    "Explore Claims",
    config.name,
    "identity",
    "pricing",
    0,
  )
  return key
}

describe("useExplorePivotActions", () => {
  beforeEach(() => {
    mockRunExplorePivot.mockReset()
    mockCancelExplorePivot.mockReset()
    resetNodeResultsDerivedCaches()
    useNodeResultsStore.setState({ pivotResults: {}, pivotJobs: {} })
    useSettingsStore.setState({
      activeSource: "pricing",
      streamingChunkSize: 250_000,
    })
    useGraphStore.setState({ structuralVersion: 0 })
  })

  it("does nothing for a pivot without values", async () => {
    const { result: hook } = renderActions()
    await act(() => hook.current.updatePivot(pivot({ values: [] })))
    expect(mockRunExplorePivot).not.toHaveBeenCalled()
    expect(hook.current.submitting).toEqual({})
    expect(useNodeResultsStore.getState().pivotJobs).toEqual({})
  })

  it("uses the node id as the stored label when the Explore label is empty", async () => {
    const unlabelledNode: SimpleNode = {
      ...node,
      data: { ...node.data, label: "" },
    }
    mockRunExplorePivot.mockResolvedValueOnce({
      status: "started",
      job_id: "unlabelled-job",
      cached: false,
      message: "Started",
      result: null,
      failure: null,
    })
    const { result: hook } = renderActions(unlabelledNode)

    await act(() => hook.current.updatePivot(pivot()))

    expect(
      useNodeResultsStore.getState().pivotJobs[`${node.id}:claims`]?.nodeLabel,
    ).toBe(node.id)
  })

  it("tracks submission while a started request is pending, then stores its job and progress", async () => {
    let resolveRequest: (response: ExplorePivotRunResponse) => void =
      () => undefined
    mockRunExplorePivot.mockImplementationOnce(
      () =>
        new Promise<ExplorePivotRunResponse>((resolve) => {
          resolveRequest = resolve
        }),
    )
    const { result: hook } = renderActions()
    const config = pivot()
    let update: Promise<void> = Promise.resolve()
    act(() => {
      update = hook.current.updatePivot(config, "dataframe-current")
    })
    expect(hook.current.submitting).toEqual({ [config.id]: true })
    await act(async () => {
      resolveRequest({
        status: "started",
        job_id: "pivot-job",
        cached: false,
        message: "",
        result: null,
        failure: null,
      })
      await update
    })
    const job =
      useNodeResultsStore.getState().pivotJobs[
        explorePivotResultKey(node.id, config.id)
      ]
    expect(hook.current.submitting).toEqual({})
    expect(job).toMatchObject({
      jobId: "pivot-job",
      requestedDataframeCacheKey: "dataframe-current",
      progress: { status: "running", message: "Starting" },
    })
  })

  it("persists cache-required fallback messages as contract errors", async () => {
    mockRunExplorePivot.mockResolvedValueOnce({
      status: "cache_required",
      job_id: null,
      cached: false,
      message: "Refresh the cache",
      result: null,
      failure: null,
    })
    const { result: hook } = renderActions()
    const config = pivot()
    await act(() => hook.current.updatePivot(config, "dataframe-current"))
    const cached =
      useNodeResultsStore.getState().pivotResults[
        explorePivotResultKey(node.id, config.id)
      ]
    expect(cached).toMatchObject({
      error: "Refresh the cache",
      lastAttemptedCalculationIdentity: pivotCalculationIdentity(config),
      lastAttemptedDataframeCacheKey: "dataframe-current",
      terminalStatus: { status: "contract_error" },
    })
  })

  it("stores completed cached results with a fallback job id and message", async () => {
    mockRunExplorePivot.mockResolvedValueOnce({
      status: "completed",
      job_id: null,
      cached: true,
      message: "",
      result,
      failure: null,
    })
    const { result: hook } = renderActions()
    await act(() => hook.current.updatePivot(pivot()))
    const key = explorePivotResultKey(node.id, "claims")
    expect(useNodeResultsStore.getState().pivotResults[key]).toMatchObject({
      result,
      jobId: `cached:${key}`,
      terminalStatus: { message: "Completed" },
    })
  })

  it("persists a terminal failure when completed responses omit their result", async () => {
    mockRunExplorePivot.mockResolvedValueOnce({
      status: "completed",
      job_id: "job",
      cached: false,
      message: "",
      result: null,
      failure: null,
    })
    const { result: hook } = renderActions()
    await act(() => hook.current.updatePivot(pivot()))
    expect(
      useNodeResultsStore.getState().pivotResults[
        explorePivotResultKey(node.id, "claims")
      ],
    ).toMatchObject({ error: "Pivot completed without a result" })
  })

  it("persists a terminal failure when started responses omit their job id", async () => {
    mockRunExplorePivot.mockResolvedValueOnce({
      status: "started",
      job_id: null,
      cached: false,
      message: "",
      result: null,
      failure: null,
    })
    const { result: hook } = renderActions()
    await act(() => hook.current.updatePivot(pivot()))
    expect(
      useNodeResultsStore.getState().pivotResults[
        explorePivotResultKey(node.id, "claims")
      ],
    ).toMatchObject({ error: "Pivot job did not return a job id" })
  })

  it("persists structured rejected-request detail and its terminal status", async () => {
    mockRunExplorePivot.mockRejectedValueOnce({
      detail: {
        message: "Request timed out",
        terminal_reason: "timed_out",
      },
    })
    const { result: hook } = renderActions()
    await act(() => hook.current.updatePivot(pivot()))
    expect(
      useNodeResultsStore.getState().pivotResults[
        explorePivotResultKey(node.id, "claims")
      ],
    ).toMatchObject({
      error: "Request timed out",
      terminalStatus: { status: "timed_out" },
    })
  })

  it("stringifies an unexpected non-Error rejection", async () => {
    mockRunExplorePivot.mockRejectedValueOnce("Connection unavailable")
    const { result: hook } = renderActions()

    await act(() => hook.current.updatePivot(pivot()))

    expect(
      useNodeResultsStore.getState().pivotResults[`${node.id}:claims`]?.error,
    ).toBe("Connection unavailable")
  })

  it("completes the active job when cancellation returns a completed result", async () => {
    const config = pivot()
    const key = startActiveJob(config)
    const response: ExplorePivotStatusResponse = {
      status: "completed",
      progress: 1,
      message: "Done",
      result,
      failure: null,
      terminal_reason: "completed",
      execution_metrics: null,
    }
    mockCancelExplorePivot.mockResolvedValueOnce(response)
    const { result: hook } = renderActions()
    await act(() => hook.current.cancelPivot(config, "active-job"))
    expect(useNodeResultsStore.getState().pivotResults[key]).toMatchObject({
      result,
      terminalStatus: response,
    })
    expect(useNodeResultsStore.getState().pivotJobs[key]).toBeUndefined()
  })

  it("fails an active job with Cancelled when cancellation has no message", async () => {
    const config = pivot()
    const key = startActiveJob(config)
    mockCancelExplorePivot.mockResolvedValueOnce({
      status: "cancelled",
      progress: 1,
      message: "",
      result: null,
      failure: null,
      terminal_reason: "cancelled",
      execution_metrics: null,
    })
    const { result: hook } = renderActions()
    await act(() => hook.current.cancelPivot(config, "active-job"))
    expect(useNodeResultsStore.getState().pivotResults[key]).toMatchObject({
      error: "Cancelled",
      terminalStatus: { status: "cancelled" },
    })
    expect(useNodeResultsStore.getState().pivotJobs[key]).toBeUndefined()
  })

  it("keeps an active job and exposes a notice when cancellation is rejected", async () => {
    const config = pivot()
    const key = startActiveJob(config)
    mockCancelExplorePivot.mockRejectedValueOnce(
      new Error("Cancellation unavailable"),
    )
    const { result: hook } = renderActions()
    await act(() => hook.current.cancelPivot(config, "active-job"))
    expect(useNodeResultsStore.getState().pivotJobs[key]).toBeDefined()
    expect(hook.current.notices[config.id]).toEqual({
      message: "Cancellation unavailable",
    })
  })
})
