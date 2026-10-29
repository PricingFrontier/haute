import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import usePipelineAPI from "../usePipelineAPI"
import DataPreview from "../../panels/DataPreview"
import { loadPipeline, previewInputs, previewNode, previewRecoveryNode } from "../../api/client"
import { makeLoadedPipeline } from "../../testSupport/pipelineDocumentFixture"
import { makeNode } from "../../test-utils/factories"
import useGraphStore from "../../stores/useGraphStore"
import useNodeResultsStore, { MAX_CACHED_PREVIEWS } from "../../stores/useNodeResultsStore"
import { makePreviewData } from "../../utils/makePreviewData"
import useNodeDataStore from "../../stores/useNodeDataStore"
import useSettingsStore from "../../stores/useSettingsStore"
import useUIStore from "../../stores/useUIStore"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import { makePipelineEditorDocument } from "../../testSupport/pipelineDocumentFixture"

vi.mock("../../api/client", async (original) => ({
  ...await original<typeof import("../../api/client")>(),
  loadPipeline: vi.fn(),
  previewInputs: vi.fn(async () => ({ input_node_ids: [] })),
  previewNode: vi.fn(),
  previewRecoveryNode: vi.fn(),
}))

function fixture(instance = true, sameChild = false, outputCount = 2) {
  const names = outputCount === 1 ? [instance ? "again" : "premium"]
    : instance ? ["again_premium", "again_audit"] : ["premium", "audit"]
  const occurrence = makeNode(instance ? "again" : "owner", "submodel", { data: {
    label: instance ? "again" : "owner", nodeType: "submodel",
    config: { definitionId: "rating", alias: instance ? "again" : "owner", ...(instance ? { instanceOf: "owner" } : {}) },
    _sourceHandleInputNames: Object.fromEntries(["premium", "audit"].slice(0, outputCount).map((port, i) => [`out__${port}`, names[i]])),
  } })
  const children = [makeNode("finish"), makeNode("check")]
  const submodels = { rating: {
    definitionId: "rating", file: "modules/rating.py", graph: { nodes: children, edges: [] }, inputPorts: [],
    outputPorts: [
      { name: "premium", source: { nodeId: "finish", handleId: sameChild ? "prices" : null } },
      { name: "audit", source: { nodeId: sameChild ? "finish" : "check", handleId: sameChild ? "checks" : null } },
    ].slice(0, outputCount),
  } }
  const nodes = [occurrence]
  const params: Parameters<typeof usePipelineAPI>[0] = {
    selectedNode: occurrence, graphRef: { current: { nodes, edges: [] } },
    parentGraphRef: { current: null }, activeSubmodelIdentity: null,
    submodelsRef: { current: submodels }, setNodesRaw: vi.fn(), setEdgesRaw: vi.fn(), setPreamble: vi.fn(),
    preambleRef: { current: "" }, pipelineNameRef: { current: "test" }, descriptionRef: { current: "" },
    sourceFileRef: { current: "main.py" }, sourceRevisionRef: { current: "revision-test" },
    preservedBlocksRef: { current: [] }, nodeIdCounter: { current: 0 },
  }
  params.setNodesRaw = (update) => {
    const graph = params.graphRef.current
    params.graphRef.current = { ...graph, nodes: typeof update === "function" ? update(graph.nodes) : update }
  }
  vi.mocked(loadPipeline).mockResolvedValue(makeLoadedPipeline({ nodes, submodels }))
  return { occurrence, names, params }
}

function response(value: number) {
  return { node_id: "runtime", status: "ok" as const, columns: [{ name: "value", dtype: "Int64" }],
    preview: [{ value }], row_count: 1, column_count: 1 }
}

async function loaded(f = fixture()) {
  const hook = renderHook(() => usePipelineAPI(f.params))
  await waitFor(() => expect(hook.result.current.loading).toBe(false))
  return { ...hook, ...f }
}

async function settled(hook: Awaited<ReturnType<typeof loaded>>) {
  await waitFor(() => expect(hook.result.current.previewBusy).toBe(false))
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} })
  useGraphStore.setState({ nodes: [], edges: [], submodels: {}, structuralVersion: 0 })
  useNodeResultsStore.setState({ previews: {}, pinnedPreviewNodeId: null })
  useNodeDataStore.setState({ epoch: 0, announcedCaptures: [] })
  useUIStore.setState({ calculationMode: "automatic" })
  useSettingsStore.setState({ rowLimit: 1000, activeSource: "live", sources: ["live"] })
  vi.mocked(previewNode).mockReset().mockResolvedValue(response(11))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe("submodel occurrence previews", () => {
  it.each([false, true])("previews the first output and selects another by its output name (instance=%s)", async (instance) => {
    const hook = await loaded(fixture(instance))
    act(() => hook.result.current.fetchPreview(hook.occurrence, { debounceMs: 0 }))
    await settled(hook)
    expect(previewNode).toHaveBeenLastCalledWith(expect.objectContaining({
      nodeId: `submodel_runtime/${hook.occurrence.id}/finish`, portLabel: undefined,
      graph: expect.objectContaining({ submodels: expect.objectContaining({ rating: expect.anything() }) }),
    }))
    expect(hook.result.current.previewData).toMatchObject({ nodeId: hook.occurrence.id, nodeLabel: hook.names[0], selected_frame: hook.names[0], preview: [{ value: 11 }] })
    const panel = render(<DataPreview data={hook.result.current.previewData} onSelectFrame={(name) => hook.result.current.previewNodeFrame(hook.occurrence.id, name)} />)
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual(hook.names)
    vi.mocked(previewNode).mockResolvedValueOnce(response(22))
    fireEvent.change(screen.getByLabelText("Select frame to preview"), { target: { value: hook.names[1] } })
    await settled(hook)
    panel.rerender(<DataPreview data={hook.result.current.previewData} />)
    expect(hook.result.current.previewData).toMatchObject({ nodeLabel: hook.names[1], selected_frame: hook.names[1], preview: [{ value: 22 }] })
    expect(previewNode).toHaveBeenLastCalledWith(expect.objectContaining({ nodeId: `submodel_runtime/${hook.occurrence.id}/check` }))
    expect(previewInputs).toHaveBeenLastCalledWith(expect.objectContaining({ nodeId: `submodel_runtime/${hook.occurrence.id}/check` }))
  })

  it("keeps two outputs of one child in separate caches and refreshes only the displayed frame", async () => {
    const hook = await loaded(fixture(true, true))
    act(() => hook.result.current.fetchPreview(hook.occurrence, { debounceMs: 0 }))
    await settled(hook)
    expect(previewNode).toHaveBeenLastCalledWith(expect.objectContaining({ nodeId: "submodel_runtime/again/finish", portLabel: "prices" }))
    vi.mocked(previewNode).mockResolvedValueOnce(response(22))
    act(() => hook.result.current.previewNodeFrame("again", "again_audit"))
    await settled(hook)
    expect(previewNode).toHaveBeenLastCalledWith(expect.objectContaining({ nodeId: "submodel_runtime/again/finish", portLabel: "checks" }))
    act(() => hook.result.current.previewNodeFrame("again", "again_premium"))
    await settled(hook)
    expect(previewNode).toHaveBeenCalledTimes(2)
    expect(hook.result.current.previewData?.preview).toEqual([{ value: 11 }])
    act(() => hook.result.current.previewNodeFrame("again", "again_audit"))
    await settled(hook)
    vi.mocked(previewNode).mockResolvedValueOnce(response(33))
    act(() => hook.result.current.refreshPreview(hook.occurrence))
    await settled(hook)
    expect(previewNode).toHaveBeenCalledTimes(3)
    expect(previewNode).toHaveBeenLastCalledWith(expect.objectContaining({ portLabel: "checks" }))
    expect(hook.result.current.previewData?.preview).toEqual([{ value: 33 }])
    act(() => hook.result.current.fetchPreview(hook.occurrence, { debounceMs: 0 }))
    await settled(hook)
    expect(hook.result.current.previewData?.selected_frame).toBe("again_premium")
    expect(hook.result.current.previewData?.preview).toEqual([{ value: 11 }])
    expect(previewNode).toHaveBeenCalledTimes(3)
    act(() => useNodeResultsStore.getState().clearNode("again"))
    expect(Object.values(useNodeResultsStore.getState().previews).filter((entry) => entry.data.nodeId === "again")).toEqual([])
  })

  it("supersedes an output request and renders failures under that output with the selector intact", async () => {
    const hook = await loaded()
    let resolveFirst!: (value: ReturnType<typeof response>) => void
    vi.mocked(previewNode).mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve }))
    act(() => hook.result.current.fetchPreview(hook.occurrence, { debounceMs: 0 }))
    await waitFor(() => expect(previewNode).toHaveBeenCalledOnce())
    const firstSignal = vi.mocked(previewNode).mock.calls[0][0].signal
    vi.mocked(previewNode).mockRejectedValueOnce(new Error("Audit failed: fix the expression"))
    act(() => hook.result.current.previewNodeFrame("again", "again_audit"))
    await settled(hook)
    expect(firstSignal?.aborted).toBe(true)
    expect(hook.result.current.previewData).toMatchObject({ nodeLabel: "again_audit", selected_frame: "again_audit", status: "error", error: "Audit failed: fix the expression" })
    render(<DataPreview data={hook.result.current.previewData} onSelectFrame={vi.fn()} />)
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual(hook.names)
    await act(async () => resolveFirst(response(99)))
    expect(hook.result.current.previewData?.status).toBe("error")
    expect(Object.values(useNodeResultsStore.getState().previews).some((entry) => entry.data.preview[0]?.value === 99)).toBe(false)
  })

  it("previews nothing when the occurrence declares no output", async () => {
    const hook = await loaded(fixture(true, false, 0))
    for (const action of [hook.result.current.fetchPreview, hook.result.current.refreshPreview]) {
      act(() => action(hook.occurrence))
      await settled(hook)
      expect(hook.result.current.previewData).toBeNull()
    }
    expect(previewInputs).not.toHaveBeenCalled()
    expect(previewNode).not.toHaveBeenCalled()
  })

  it("names a single instance output from its authoritative identity", async () => {
    const hook = await loaded(fixture(true, false, 1))
    act(() => hook.result.current.fetchPreview(hook.occurrence, { debounceMs: 0 }))
    await settled(hook)
    expect(hook.result.current.previewData).toMatchObject({ nodeLabel: "again", selected_frame: "again", preview: [{ value: 11 }] })
  })

  it("recalculates the displayed output at a new epoch and marks that output stale in manual mode", async () => {
    const hook = await loaded(fixture(true, true))
    act(() => hook.result.current.previewNodeFrame("again", "again_audit"))
    await settled(hook)
    vi.mocked(previewNode).mockResolvedValueOnce(response(44))
    act(() => useNodeDataStore.getState().bumpEpoch())
    await waitFor(() => expect(hook.result.current.previewData?.preview).toEqual([{ value: 44 }]))
    expect(previewNode).toHaveBeenLastCalledWith(expect.objectContaining({ portLabel: "checks" }))
    act(() => useUIStore.setState({ calculationMode: "manual" }))
    render(<DataPreview data={hook.result.current.previewData} />)
    expect(screen.queryByTestId("preview-out-of-date")).toBeNull()
    act(() => useNodeDataStore.getState().bumpEpoch())
    expect(screen.getByTestId("preview-out-of-date")).toHaveTextContent("Out of date")
    expect(previewNode).toHaveBeenCalledTimes(2)
  })

  it("never sends an occurrence as a recovery preview target", async () => {
    const hook = await loaded()
    act(() => useDocumentStatusStore.getState().loadDocumentStatus(makePipelineEditorDocument({
      load_status: "degraded", capabilities: { can_preview: true },
    })))
    act(() => hook.result.current.fetchPreview(hook.occurrence, { debounceMs: 0 }))
    await settled(hook)
    expect(hook.result.current.previewData).toMatchObject({ status: "error", error: expect.stringMatching(/recovery mode/) })
    expect(previewNode).not.toHaveBeenCalled()
    expect(previewInputs).not.toHaveBeenCalled()
    expect(previewRecoveryNode).not.toHaveBeenCalled()
  })

  it("pins the selected occurrence output while other previews fill the cache", async () => {
    const hook = await loaded()
    act(() => hook.result.current.fetchPreview(hook.occurrence, { debounceMs: 0 }))
    await settled(hook)
    act(() => {
      const store = useNodeResultsStore.getState()
      store.setPinnedPreviewNodeId("again")
      for (let i = 0; i < MAX_CACHED_PREVIEWS; i++) {
        store.setPreview(`other_${i}`, makePreviewData(`other_${i}`, "Other"), 0)
      }
    })
    act(() => hook.result.current.fetchPreview(hook.occurrence, { debounceMs: 0 }))
    await settled(hook)
    expect(hook.result.current.previewData?.preview).toEqual([{ value: 11 }])
    expect(previewNode).toHaveBeenCalledOnce()
  })

  it("keeps occurrence schemas keyed by public handles after previewing a child frame", async () => {
    const hook = await loaded(fixture(true, true))
    const columns = [{ name: "value", dtype: "Int64" }]
    vi.mocked(previewNode).mockResolvedValueOnce({ ...response(11), frame_columns: { prices: columns },
      node_frame_columns: { again: { out__premium: columns } } })
    act(() => hook.result.current.fetchPreview(hook.occurrence, { debounceMs: 0 }))
    await settled(hook)
    const data = hook.params.graphRef.current.nodes[0].data
    expect(data._frameColumns).toEqual({ out__premium: columns })
    expect(data._columns).toBeUndefined()
    expect(hook.result.current.previewData?.frame_columns).toEqual({ again_premium: columns, again_audit: [] })
  })

  it("Stop restores the displayed output's last result", async () => {
    const hook = await loaded(fixture(true, true))
    act(() => hook.result.current.previewNodeFrame("again", "again_audit"))
    await settled(hook)
    vi.mocked(previewNode).mockImplementationOnce(({ signal }) => new Promise((_resolve, reject) => {
      signal?.addEventListener("abort", () => reject(new DOMException("Stopped", "AbortError")))
    }))
    act(() => hook.result.current.refreshPreview(hook.occurrence))
    await waitFor(() => expect(previewNode).toHaveBeenCalledTimes(2))
    act(() => hook.result.current.stopPreview())
    await settled(hook)
    expect(hook.result.current.previewData).toMatchObject({ selected_frame: "again_audit", status: "ok", preview: [{ value: 11 }] })
    expect(previewNode).toHaveBeenCalledTimes(2)
  })

  it("an output's own captured snapshots keep its cache current", async () => {
    const hook = await loaded()
    vi.mocked(previewNode).mockResolvedValueOnce({ ...response(11), seed_plan: [{
      node_id: "source", port_label: null, node_label: "Source", identity_digest: "d".repeat(64),
      generation_id: "occurrence-capture", columns: null, created_at: "2026-10-29T00:00:00Z", kind: "captured",
    }] })
    act(() => hook.result.current.fetchPreview(hook.occurrence, { debounceMs: 0 }))
    await settled(hook)
    expect(useNodeDataStore.getState().epoch).toBe(1)
    expect(previewNode).toHaveBeenCalledOnce()
    act(() => hook.result.current.fetchPreview(hook.occurrence, { debounceMs: 0 }))
    await settled(hook)
    expect(previewNode).toHaveBeenCalledOnce()
  })
})
