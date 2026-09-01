import { act, renderHook } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type {
  ExplorePivotResult,
  ExplorePivotRunResponse,
  ExplorePivotStatusResponse,
} from "../../../api/types"
import useDocumentStatusStore from "../../../stores/useDocumentStatusStore"
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
    rows: [{ id: "region", field: "region", number_format: "general", decimal_places: null, use_grouping: true }],
    columns: [],
    values: [
      {
        id: "paid",
        field: "paid",
        aggregation: "sum",
        reference: "paid_sum",
        display_name: "Paid",
        color_scale_split_by: null, number_format: "general", decimal_places: null, use_grouping: true,
      },
    ],
    formulas: [],
    options: { row_grand_totals: true, column_grand_totals: true },
    ...overrides,
    value_order: overrides.value_order ?? (overrides.values ?? [{ id: "paid" }]).map(({ id }) => id),
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
    useNodeResultsStore.setState({
      pivotResults: {},
      pivotJobs: {},
      pivotStartClaims: {},
    })
    useSettingsStore.setState({
      activeSource: "pricing",
      streamingChunkSize: 250_000,
    })
    useGraphStore.setState({ structuralVersion: 0 })
    useDocumentStatusStore.getState().reset()
  })

  it("does nothing for a pivot without values", async () => {
    const { result: hook } = renderActions()
    await act(() => hook.current.updatePivot(pivot({ values: [] })))
    expect(mockRunExplorePivot).not.toHaveBeenCalled()
    expect(hook.current.submitting).toEqual({})
    expect(useNodeResultsStore.getState().pivotJobs).toEqual({})
  })

  it("does not submit while the current document cannot execute", async () => {
    useDocumentStatusStore.setState({
      loadStatus: "degraded",
      capabilities: {
        can_mutate: false,
        can_save: false,
        can_execute: false,
        can_preview: false,
      can_manage_submodels: false,
      can_repair: true,
      reserved_api_input_frame_labels: [],
      },
      sourceFile: "rating/main.py",
      sourceRevision: "degraded-revision",
      graphSynchronized: true,
    })
    const { result: hook } = renderActions()

    await act(() => hook.current.updatePivot(pivot()))

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

  describe("auto-update claim", () => {
    const claimKey = () => explorePivotResultKey(node.id, "claims")
    const identity = () => pivotCalculationIdentity(pivot())

    it("no-ops identical targets, replaces newer targets, and guards release by token", () => {
      const store = useNodeResultsStore.getState()
      const token1 = store.claimExplorePivotAuto(claimKey(), node.id, "df-a", identity())
      expect(token1).not.toBeNull()
      expect(
        store.claimExplorePivotAuto(claimKey(), node.id, "df-a", identity()),
      ).toBeNull()

      const token2 = store.claimExplorePivotAuto(claimKey(), node.id, "df-b", identity())
      expect(token2).not.toBeNull()
      expect(token2).not.toBe(token1)

      store.releaseExplorePivotStart(claimKey(), token1!)
      expect(
        useNodeResultsStore.getState().pivotStartClaims[claimKey()]?.token,
      ).toBe(token2)
      store.releaseExplorePivotStart(claimKey(), token2!)
      expect(
        useNodeResultsStore.getState().pivotStartClaims[claimKey()],
      ).toBeUndefined()
    })

    it("deduplicates an identical auto target but supersedes manual work for a newer dataframe", () => {
      const store = useNodeResultsStore.getState()
      const manualToken = store.claimExplorePivotManual(
        claimKey(),
        node.id,
        "df-current",
        identity(),
      )

      expect(
        store.claimExplorePivotAuto(claimKey(), node.id, "df-current", identity()),
      ).toBeNull()
      expect(
        useNodeResultsStore.getState().pivotStartClaims[claimKey()]?.token,
      ).toBe(manualToken)

      const newerToken = store.claimExplorePivotAuto(
        claimKey(),
        node.id,
        "df-next",
        identity(),
      )
      expect(newerToken).not.toBeNull()
      expect(
        useNodeResultsStore.getState().pivotStartClaims[claimKey()],
      ).toMatchObject({ dataframeCacheKey: "df-next", token: newerToken })
    })

    it("releases the claim through the real failure path, allowing a retry", async () => {
      const key = claimKey()
      const token = useNodeResultsStore
        .getState()
        .claimExplorePivotAuto(key, node.id, "df-a", identity())
      expect(token).not.toBeNull()
      mockRunExplorePivot.mockRejectedValueOnce(new Error("boom"))

      const { result: hook } = renderActions()
      await act(() => hook.current.updatePivot(pivot(), "df-a", token!))

      expect(
        useNodeResultsStore.getState().pivotStartClaims[key],
      ).toBeUndefined()
      expect(
        useNodeResultsStore
          .getState()
          .claimExplorePivotAuto(key, node.id, "df-a", identity()),
      ).not.toBeNull()
    })

    it("discards a superseded rejection without persisting its stale failure", async () => {
      const key = claimKey()
      const token1 = useNodeResultsStore
        .getState()
        .claimExplorePivotAuto(key, node.id, "df-old", identity())
      expect(token1).not.toBeNull()
      let rejectOld: (reason?: unknown) => void = () => {}
      mockRunExplorePivot.mockImplementationOnce(
        () =>
          new Promise<ExplorePivotRunResponse>((_resolve, reject) => {
            rejectOld = reject
          }),
      )
      const { result: hook } = renderActions()
      let oldInFlight: Promise<void> = Promise.resolve()
      act(() => {
        oldInFlight = hook.current.updatePivot(pivot(), "df-old", token1!)
      })

      const token2 = useNodeResultsStore
        .getState()
        .claimExplorePivotAuto(key, node.id, "df-new", identity())
      expect(token2).not.toBeNull()

      await act(async () => {
        rejectOld(new Error("obsolete failure"))
        await oldInFlight
      })

      expect(useNodeResultsStore.getState().pivotResults[key]).toBeUndefined()
      expect(useNodeResultsStore.getState().pivotJobs[key]).toBeUndefined()
      expect(hook.current.notices.claims).toBeUndefined()
      expect(
        useNodeResultsStore.getState().pivotStartClaims[key]?.token,
      ).toBe(token2)
    })

    it("stores nothing when a tokened response resolves after clearNode", async () => {
      const key = claimKey()
      const token = useNodeResultsStore
        .getState()
        .claimExplorePivotAuto(key, node.id, "df-a", identity())
      let resolveRun: (value: ExplorePivotRunResponse) => void = () => {}
      mockRunExplorePivot.mockImplementationOnce(
        () =>
          new Promise<ExplorePivotRunResponse>((resolve) => {
            resolveRun = resolve
          }),
      )
      const { result: hook } = renderActions()
      let inFlight: Promise<void> = Promise.resolve()
      act(() => {
        inFlight = hook.current.updatePivot(pivot(), "df-a", token!)
      })

      act(() => useNodeResultsStore.getState().clearNode(node.id))

      await act(async () => {
        resolveRun({
          status: "completed",
          job_id: "late-job",
          cached: true,
          message: "Completed",
          result,
          failure: null,
        })
        await inFlight
      })

      expect(useNodeResultsStore.getState().pivotResults[key]).toBeUndefined()
      expect(useNodeResultsStore.getState().pivotJobs[key]).toBeUndefined()
    })

    it("scopes the submitting flag to the latest generation so obsolete requests neither strand nor clear it", async () => {
      const key = claimKey()
      const token1 = useNodeResultsStore
        .getState()
        .claimExplorePivotAuto(key, node.id, "df-old", identity())
      let resolveOld: (value: ExplorePivotRunResponse) => void = () => {}
      mockRunExplorePivot.mockImplementationOnce(
        () =>
          new Promise<ExplorePivotRunResponse>((resolve) => {
            resolveOld = resolve
          }),
      )
      const { result: hook } = renderActions()
      let oldInFlight: Promise<void> = Promise.resolve()
      act(() => {
        oldInFlight = hook.current.updatePivot(pivot(), "df-old", token1!)
      })
      expect(hook.current.submitting.claims).toBe(true)

      const token2 = useNodeResultsStore
        .getState()
        .claimExplorePivotAuto(key, node.id, "df-new", identity())
      const newResult = { ...result, dataframe_cache_key: "df-new" }
      mockRunExplorePivot.mockResolvedValueOnce({
        status: "completed",
        job_id: "new-job",
        cached: true,
        message: "Completed",
        result: newResult,
        failure: null,
      })
      await act(() => hook.current.updatePivot(pivot(), "df-new", token2!))
      // The latest generation settled: the flag clears immediately even
      // though the obsolete first request is still pending, so fresh work
      // is not blocked behind it.
      expect(hook.current.submitting.claims).toBeUndefined()

      // A third target submits freely while the obsolete request hangs.
      const token3 = useNodeResultsStore
        .getState()
        .claimExplorePivotAuto(key, node.id, "df-next", identity())
      let resolveNext: (value: ExplorePivotRunResponse) => void = () => {}
      mockRunExplorePivot.mockImplementationOnce(
        () =>
          new Promise<ExplorePivotRunResponse>((resolve) => {
            resolveNext = resolve
          }),
      )
      let nextInFlight: Promise<void> = Promise.resolve()
      act(() => {
        nextInFlight = hook.current.updatePivot(pivot(), "df-next", token3!)
      })
      expect(mockRunExplorePivot).toHaveBeenCalledTimes(3)
      expect(hook.current.submitting.claims).toBe(true)

      // The obsolete first request settling never touches the flag.
      await act(async () => {
        resolveOld({
          status: "completed",
          job_id: "old-job",
          cached: true,
          message: "Completed",
          result,
          failure: null,
        })
        await oldInFlight
      })
      expect(hook.current.submitting.claims).toBe(true)
      expect(
        useNodeResultsStore.getState().pivotResults[key]?.result
          ?.dataframe_cache_key,
      ).toBe("df-new")

      const nextResult = { ...result, dataframe_cache_key: "df-next" }
      await act(async () => {
        resolveNext({
          status: "completed",
          job_id: "next-job",
          cached: true,
          message: "Completed",
          result: nextResult,
          failure: null,
        })
        await nextInFlight
      })
      expect(hook.current.submitting.claims).toBeUndefined()
      expect(
        useNodeResultsStore.getState().pivotResults[key]?.result
          ?.dataframe_cache_key,
      ).toBe("df-next")
    })

    it("discards a superseded submission's outcome so a late old response never overwrites newer work", async () => {
      const key = claimKey()
      const token1 = useNodeResultsStore
        .getState()
        .claimExplorePivotAuto(key, node.id, "df-old", identity())
      expect(token1).not.toBeNull()

      let resolveOld: (value: ExplorePivotRunResponse) => void = () => {}
      mockRunExplorePivot.mockImplementationOnce(
        () =>
          new Promise<ExplorePivotRunResponse>((resolve) => {
            resolveOld = resolve
          }),
      )
      const { result: hook } = renderActions()
      let oldInFlight: Promise<void> = Promise.resolve()
      act(() => {
        oldInFlight = hook.current.updatePivot(pivot(), "df-old", token1!)
      })

      // A newer target replaces the claim while the old request is in flight,
      // and its submission completes first.
      const token2 = useNodeResultsStore
        .getState()
        .claimExplorePivotAuto(key, node.id, "df-new", identity())
      expect(token2).not.toBeNull()
      const newResult = { ...result, dataframe_cache_key: "df-new" }
      mockRunExplorePivot.mockResolvedValueOnce({
        status: "completed",
        job_id: "new-job",
        cached: true,
        message: "Completed",
        result: newResult,
        failure: null,
      })
      await act(() => hook.current.updatePivot(pivot(), "df-new", token2!))
      expect(
        useNodeResultsStore.getState().pivotResults[key]?.result
          ?.dataframe_cache_key,
      ).toBe("df-new")

      // The old response completes last: neither promoted nor stored.
      await act(async () => {
        resolveOld({
          status: "completed",
          job_id: "old-job",
          cached: true,
          message: "Completed",
          result,
          failure: null,
        })
        await oldInFlight
      })
      expect(
        useNodeResultsStore.getState().pivotResults[key]?.result
          ?.dataframe_cache_key,
      ).toBe("df-new")
      expect(useNodeResultsStore.getState().pivotResults[key]?.jobId).toBe(
        "new-job",
      )
      // token2's finally released its own claim; token1's release no-oped.
      expect(
        useNodeResultsStore.getState().pivotStartClaims[key],
      ).toBeUndefined()
    })

    it("discards an automatic response superseded by Retry in another mounted action hook", async () => {
      const key = claimKey()
      const token = useNodeResultsStore
        .getState()
        .claimExplorePivotAuto(key, node.id, "df-auto", identity())
      expect(token).not.toBeNull()

      let resolveAutomatic: (value: ExplorePivotRunResponse) => void = () => {}
      mockRunExplorePivot.mockImplementationOnce(
        () => new Promise<ExplorePivotRunResponse>((resolve) => {
          resolveAutomatic = resolve
        }),
      )
      const { result: automaticHook } = renderActions()
      const { result: retryHook } = renderActions()
      let automaticStart: Promise<void> = Promise.resolve()
      act(() => {
        automaticStart = automaticHook.current.updatePivot(
          pivot(),
          "df-auto",
          token!,
        )
      })

      const retryResult = { ...result, dataframe_cache_key: "df-retry" }
      mockRunExplorePivot.mockResolvedValueOnce({
        status: "completed",
        job_id: "retry-job",
        cached: true,
        message: "Completed",
        result: retryResult,
        failure: null,
      })
      await act(() => retryHook.current.updatePivot(pivot(), "df-retry"))

      await act(async () => {
        resolveAutomatic({
          status: "completed",
          job_id: "automatic-job",
          cached: true,
          message: "Completed",
          result: { ...result, dataframe_cache_key: "df-auto" },
          failure: null,
        })
        await automaticStart
      })

      expect(useNodeResultsStore.getState().pivotResults[key]).toMatchObject({
        jobId: "retry-job",
        result: { dataframe_cache_key: "df-retry" },
      })
      expect(useNodeResultsStore.getState().pivotJobs[key]).toBeUndefined()
    })
  })
})
