import { makeTrainResult } from "../../test-utils/factories"
import { selectedCandidate, selectedPathPoint } from "../pathSelection"
import type { StagePathPayload } from "../../generated/api-contracts.generated"
import { act, cleanup, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { readStages, inspectStagePath, applyStagePath, restoreFinalStage, updateStageCheckpoint, updateStagePreview, type TrainModelArgs } from "../../api/client"
import type { StageState } from "../../types/trainGuards"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import { useStageTableResult } from "../useStageTableResult"
import { useStagedTraining, useStagedViews, useAppliedStageRecipe } from "../useStagedTraining"

vi.mock("../../api/client", () => ({ readStages: vi.fn(), inspectStagePath: vi.fn(), applyStagePath: vi.fn(), updateStageCheckpoint: vi.fn(), updateStagePreview: vi.fn(), restoreFinalStage: vi.fn() }))
const request = () => ({ node_id: "node", model: "model", graph: { nodes: [{ id: "node", data: { config: { models: [{ id: "model", name: "t-boost", algorithm: "tboost", params: {} }] } } }], edges: [] } } as unknown as TrainModelArgs)
const empty: StageState = { schema_version: 1, next_preview: null, checkpoints: [], current: null, preview: null, final: null, job: null, stale_reason: null }

describe("durable stage restoration", () => {
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.resetAllMocks(); useStagedViews.setState({ models: {}, preferences: {} }); useDocumentStatusStore.setState({ sourceFile: "" }) })
  it("reads reloaded legacy settings using the same staged draft as training without changing the source", async () => {
    vi.useFakeTimers()
    vi.mocked(readStages).mockResolvedValue(empty)
    const input = { ...request(), graph: { nodes: [{ id: "node", data: { config: { interactive_build: true, models: [{ id: "model", name: "t-boost", algorithm: "tboost", params: { n_trees: 4000, prune: false, band_tolerance: null, graduate: false } }] } } }], edges: [] } } as unknown as TrainModelArgs
    const before = JSON.stringify(input)
    renderHook(() => useStagedTraining({ enabled: true, resultKey: "node#model:model", request: () => input }))
    await act(() => vi.advanceTimersByTimeAsync(100))
    const sent = (vi.mocked(readStages).mock.calls[0][0].graph.nodes[0].data.config as Record<string, unknown>).models as Array<Record<string, unknown>>
    expect(sent[0].params).toEqual({ n_trees: 4000 })
    expect(sent[0].stages).toMatchObject({ schema_version: 2 })
    expect(JSON.stringify(input)).toBe(before)
  })
  it("loads dense tables on demand and never shows a result from a superseded revision", async () => {
    const first = "a".repeat(32), second = "b".repeat(32)
    const dense = makeTrainResult()
    let resolve!: (value: StageState) => void
    vi.mocked(readStages).mockReturnValueOnce(new Promise((done) => { resolve = done }))
    const { result, rerender } = renderHook(({ enabled, revision }) => useStageTableResult({
      request, viewKey: "model", currentRevision: revision, enabled,
    }), { initialProps: { enabled: false, revision: first } })
    expect(readStages).not.toHaveBeenCalled()
    rerender({ enabled: true, revision: first })
    expect(readStages).toHaveBeenCalledWith(expect.objectContaining({ diagnostics: "full" }))
    vi.mocked(readStages).mockResolvedValueOnce({ ...empty, current: { revision: second, result: { response: dense } } } as StageState)
    rerender({ enabled: true, revision: second })
    await act(async () => {})
    expect(result.current.response).toBe(dense)
    await act(async () => resolve({ ...empty, current: { revision: first, result: { response: makeTrainResult({ model_path: "old" }) } } } as StageState))
    expect(result.current.response).toBe(dense)
  })
  it("restores state, keeps the last good state on read failure, and fences a different document", async () => {
    vi.useFakeTimers()
    vi.mocked(readStages).mockResolvedValue(empty)
    const { result } = renderHook(() => useStagedTraining({ enabled: true, resultKey: "node#model:model", request }))
    await act(() => vi.advanceTimersByTimeAsync(100))
    expect(result.current.state).toEqual(empty)
    vi.mocked(readStages).mockRejectedValueOnce(new Error("Storage unavailable"))
    act(() => result.current.refresh())
    await act(() => vi.advanceTimersByTimeAsync(100))
    expect(result.current.state).toEqual(empty)
    expect(result.current.error).toContain("Storage unavailable")
    let resolve!: (value: StageState) => void
    vi.mocked(readStages).mockReturnValueOnce(new Promise((done) => { resolve = done }))
    act(() => result.current.refresh())
    await act(() => vi.advanceTimersByTimeAsync(100))
    act(() => useDocumentStatusStore.setState({ sourceFile: "other.py" }))
    await act(async () => resolve({ ...empty, stale_reason: "Old document" }))
    expect(result.current.state).toBeUndefined()
  })
  it("reports final export restoration errors without publishing an export handle", async () => {
    vi.useFakeTimers()
    // This seam consumes the already contract-validated API state.
    vi.mocked(readStages).mockResolvedValue({ ...empty, final: { revision: "a".repeat(32) } as NonNullable<StageState['final']> })
    vi.mocked(restoreFinalStage).mockRejectedValue(new Error("Final artifact checksum mismatch"))
    const { result } = renderHook(() => useStagedTraining({ enabled: true, resultKey: "node#model:model", request }))
    await act(() => vi.advanceTimersByTimeAsync(100))
    await act(() => result.current.prepareExport())
    expect(result.current.finalJobId).toBeUndefined()
    expect(result.current.error).toContain("checksum")
    expect(result.current.busy).toBe(false)
  })
  it("synchronises a completed background model's applied recipe while preserving drafts", () => {
    const update = vi.fn()
    const recipe = { schema_version: 1, operations: [{ stage: "banded", parent: "full", settings: { tolerance: .5 } }] }
    const state = { ...empty, current: { revision: "a".repeat(32), result: { response: {
      staged: { configuration: { applied_recipe: recipe } },
    } } } } as unknown as StageState
    const config = { stages: { applied_recipe: { schema_version: 1, operations: [] }, path: { min_rows: 50 }, banding: { tolerance: .7 } } }
    const { rerender } = renderHook(({ value }) => useAppliedStageRecipe(value, config, update),
      { initialProps: { value: empty } })
    expect(update).not.toHaveBeenCalled()
    rerender({ value: state })
    expect(update).toHaveBeenCalledWith({ stages: { ...config.stages, applied_recipe: recipe } })
    rerender({ value: state })
    expect(update).toHaveBeenCalledOnce()
  })
  it("drops a final export handle when another stage job may release its transient artifacts", async () => {
    vi.useFakeTimers()
    vi.mocked(readStages).mockResolvedValue(empty)
    const { result } = renderHook(() => useStagedTraining({ enabled: true, resultKey: "node#model:model", request }))
    await act(() => vi.advanceTimersByTimeAsync(100))
    const key = result.current.key
    act(() => useStagedViews.getState().update(key, { finalExport: { revision: "final", jobId: "expired-job" } }))
    act(() => useStagedViews.getState().update(key, { state: { ...empty, job: {
      id: "preview", operation: "pruned", automatic: false, status: "completed", owner_pid: 1, expected_revision: null, message: "Done",
    } } }))
    expect(useStagedViews.getState().models[key].finalExport).toBeUndefined()
  })

})


it("restores only per-model settings and the actual applied recipe", async () => {
  vi.useFakeTimers()
  const current = { revision: "a".repeat(32) } as NonNullable<StageState["current"]>
  vi.mocked(readStages).mockResolvedValue({ ...empty, current })
  const recipe = { schema_version: 1, operations: [{ stage: "pruned", parent: "full", settings: {} }] }
  const restored = { ...empty, current: { ...current, result: { graph_digest: "b".repeat(64),
    config: { params: { n_trees: 10 }, target: "do-not-restore", evaluation: { seed: 999 }, stages: { applied_recipe: { schema_version: 1, operations: [] } } },
    response: { staged: { configuration: { applied_recipe: recipe } } },
  } } } as unknown as StageState
  vi.mocked(updateStageCheckpoint).mockResolvedValue(restored)
  const { result, unmount } = renderHook(() => useStagedTraining({ enabled: true, resultKey: "node#model:model", request }))
  await act(() => vi.advanceTimersByTimeAsync(100))
  const onRestore = vi.fn()
  await act(() => result.current.checkpointAction("restore", "checkpoint", onRestore))
  expect(onRestore).toHaveBeenCalledWith(expect.objectContaining({ params: { n_trees: 10 }, stages: { applied_recipe: recipe } }))
  expect(onRestore.mock.calls[0][0]).not.toHaveProperty("target")
  expect(onRestore.mock.calls[0][0]).not.toHaveProperty("evaluation")
  unmount(); vi.useRealTimers(); useStagedViews.setState({ models: {}, preferences: {} })
})


it("polls queued preparation even before a supervised job exists", async () => {
  vi.useFakeTimers()
  const queued: StageState = { ...empty, next_preview: { operation: "pruned", parent_revision: "a".repeat(32), owner_pid: 1, status: "queued", message: "Preparing" } }
  vi.mocked(readStages).mockReset().mockResolvedValueOnce(queued).mockResolvedValue(empty)
  const { result, unmount } = renderHook(() => useStagedTraining({ enabled: true, resultKey: "node#model:model", request }))
  await act(() => vi.advanceTimersByTimeAsync(100))
  await act(() => vi.advanceTimersByTimeAsync(1000))
  expect(readStages).toHaveBeenCalledTimes(2)
  expect(result.current.state?.next_preview).toBeNull()
  unmount(); vi.useRealTimers(); useStagedViews.setState({ models: {}, preferences: {} })
})

it.each(["apply", "discard"] as const)("only Apply requests preparation of the next stage (%s)", async (action) => {
  vi.useFakeTimers()
  const state = { ...empty, current: { revision: "a".repeat(32), result: { response: makeTrainResult() } }, preview: { revision: "b".repeat(32), result: { response: makeTrainResult() } } } as StageState
  vi.mocked(readStages).mockReset().mockResolvedValue(state)
  vi.mocked(updateStagePreview).mockReset().mockResolvedValue(empty)
  const { result, unmount } = renderHook(() => useStagedTraining({ enabled: true, resultKey: "node#model:model", request }))
  await act(() => vi.advanceTimersByTimeAsync(100))
  await act(() => result.current.previewAction(action))
  expect(updateStagePreview).toHaveBeenCalledWith(action, expect.objectContaining({ node_id: "node", model: "model", current_revision: "a".repeat(32), preview_revision: "b".repeat(32), ...(action === "apply" ? { prepare_next: true } : {}) }))
  unmount(); vi.useRealTimers(); useStagedViews.setState({ models: {}, preferences: {} })
})


it("publishes the queued-to-running transition so the automatic preview can be cancelled", async () => {
  vi.useFakeTimers()
  const queued: StageState = { ...empty, next_preview: { operation: "pruned", parent_revision: "a".repeat(32), owner_pid: 1, status: "queued", message: "Preparing" } }
  const running: StageState = { ...empty, job: { id: "automatic-preview", operation: "pruned", automatic: true, expected_revision: "a".repeat(32), owner_pid: 1, status: "running", message: "Pruning" } }
  vi.mocked(readStages).mockReset().mockResolvedValueOnce(queued).mockResolvedValueOnce(running).mockResolvedValue(empty)
  const { result, unmount } = renderHook(() => useStagedTraining({ enabled: true, resultKey: "node#model:model", request }))
  await act(() => vi.advanceTimersByTimeAsync(100))
  expect(result.current.state?.job?.id).toBe("automatic-preview")
  expect(result.current.state?.job?.status).toBe("running")
  expect(result.current.state?.next_preview).toBeNull()
  await act(() => vi.advanceTimersByTimeAsync(1000))
  expect(result.current.state?.job).toBeNull()
  unmount(); vi.useRealTimers(); useStagedViews.setState({ models: {}, preferences: {} })
})


describe("path inspection", () => {
  beforeEach(() => { vi.resetAllMocks(); useStagedViews.setState({ models: {}, preferences: {} }) })
  afterEach(() => { cleanup(); vi.resetAllMocks(); useStagedViews.setState({ models: {}, preferences: {} }) })
  it("fences out-of-order points without changing saved state, then applies the inspected point", async () => {
    const path = { id: "d".repeat(64), stage: "banded", parent: "full", selected_point: "point-1", points: [] } as unknown as StagePathPayload
    const saved = makeTrainResult()
    saved.staged = { schema_version: 2, selected_stage: "full", diagnostics_basis: "partition", reference_fit: "validation-0", pruning_path: null, recommendation: null, path_unavailable_reason: null, configuration: {}, evaluation_identity: "e".repeat(64), fit_config_digest: "f".repeat(64), stages: { full: {} }, paths: { banded: path }, prepared_path: path.id }
    const state = { ...empty, current: { revision: "a".repeat(32), result: { response: makeTrainResult() } }, preview: { revision: "b".repeat(32), result: { response: saved } } } as StageState
    const { result } = renderHook(() => useStagedTraining({ enabled: false, resultKey: "node#model:model", request }))
    act(() => useStagedViews.getState().update(result.current.key, { state }))
    const before = JSON.stringify(result.current.state)
    const available = { path, artifactRevision: "b".repeat(32) }
    const responses: ((value: Awaited<ReturnType<typeof inspectStagePath>>) => void)[] = []
    vi.mocked(inspectStagePath).mockImplementation(() => new Promise((resolve) => responses.push(resolve)))
    let first!: Promise<void>, second!: Promise<void>
    act(() => { first = result.current.inspect(available, "point-1") })
    act(() => { second = result.current.inspect(available, "point-2") })
    const response = makeTrainResult({ diagnostic_metrics: { rmse: .12 } })
    await act(async () => { responses[1]({ current_revision: "a".repeat(32), artifact_revision: available.artifactRevision, path_id: path.id, point_id: "point-2", response }); await second })
    await act(async () => { responses[0]({ current_revision: "a".repeat(32), artifact_revision: available.artifactRevision, path_id: path.id, point_id: "point-1", response: saved }); await first })
    expect(selectedCandidate(result.current)).toBe(response)
    expect(result.current.inspection?.pointId).toBe("point-2")
    expect(JSON.stringify(result.current.state)).toBe(before)
    expect(updateStagePreview).not.toHaveBeenCalled()
    vi.mocked(applyStagePath).mockResolvedValue({ ...state, preview: null, current: { ...state.current!, revision: "c".repeat(32) } })
    await act(() => result.current.applyPoint())
    expect(applyStagePath).toHaveBeenCalledWith(expect.objectContaining({ point_id: "point-2", path_id: path.id, prepare_next: true }))
    expect(result.current.inspection).toBeUndefined()
    expect(result.current.adoption).toEqual({ revision: "c".repeat(32), stage: "banded" })
    expect(result.current.pathStage).toBeUndefined()
    const nextPath = { ...path, id: "e".repeat(64), stage: "smoothed", parent: "banded" } as StagePathPayload
    const nextResult = makeTrainResult()
    nextResult.staged = { ...saved.staged!, paths: { smoothed: nextPath }, prepared_path: nextPath.id }
    act(() => useStagedViews.getState().update(result.current.key, { state: { ...result.current.state!,
      preview: { ...state.preview!, revision: "f".repeat(32), result: { graph_digest: "b".repeat(64), response: nextResult } } } }))
    expect(selectedPathPoint(result.current)?.available.path.stage).toBe("smoothed")
    expect(selectedCandidate(result.current)).toBe(nextResult)
    act(() => useStagedViews.getState().update(result.current.key, { pathStage: "pruned" }))
    expect(selectedPathPoint(result.current)).toBeUndefined()
    expect(selectedCandidate(result.current)).toBeUndefined()
  })
})


it("adopts only the applied path phase and its enable flag, preserving other drafts", () => {
  const key = JSON.stringify([JSON.stringify(["", "document"]), "node#model:phase-adoption"])
  const recipe = { schema_version: 1, operations: [{ stage: "banded", parent: "full", settings: { tolerance: .25, deviance_cap: .0001 } }] }
  const actual = { enabled: { pruned: false, banded: true, smoothed: true },
    banding: { parent: "full", tolerance: .25, deviance_cap: .0001 }, applied_recipe: recipe }
  const state = { ...empty, current: { revision: "selected", result: { response: {
    staged: { configuration: actual },
  } } } } as unknown as StageState
  const config = { stages: { enabled: { pruned: false, banded: false, smoothed: false },
    banding: { tolerance: .75 }, smoothing: { alpha: 12 }, path: { min_rows: 70 }, applied_recipe: { schema_version: 1, operations: [] } } }
  useStagedViews.getState().update(key, { adoption: { revision: "selected", stage: "banded" } })
  const update = vi.fn()
  const { unmount } = renderHook(() => useAppliedStageRecipe(state, config, update, key))
  expect(update).toHaveBeenCalledOnce()
  expect(update).toHaveBeenCalledWith({ stages: { ...config.stages, banding: actual.banding,
    enabled: { pruned: false, banded: true, smoothed: false }, applied_recipe: recipe } })
  expect(useStagedViews.getState().models[key].adoption).toBeUndefined()
  unmount(); useStagedViews.setState({ models: {}, preferences: {} })
})
