import { rowIdentity } from "../../utils/rowKeys"
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react"
import { makePreviewResponse, nodeTarget, rootAddresses } from "../../test-utils/factories"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import usePipelineAPI from "../usePipelineAPI"
import DataPreview from "../../panels/DataPreview"
import { loadPipeline, previewInputs, previewNode, previewRecoveryNode } from "../../api/client"
import { ensureInputSnapshots } from "../ensureInputSnapshots"
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
import { nodeData } from "../../types/node"
import {
  COPY,
  OWNER,
  childKey,
  loadTwoOccurrences,
  onCanvas,
  openOccurrence,
  type Occurrence,
} from "../../testSupport/twoOccurrencesFixture"

// Preparing the inputs a preview reads, observed rather than run.
vi.mock("../ensureInputSnapshots", async (original) => ({
  ...await original<typeof import("../ensureInputSnapshots")>(),
  ensureInputSnapshots: vi.fn(async () => undefined),
}))

vi.mock("../../api/client", async (original) => ({
  ...await original<typeof import("../../api/client")>(),
  loadPipeline: vi.fn(),
  previewInputs: vi.fn(async () => ({ input_node_ids: [], node_addresses: {} })),
  previewNode: vi.fn(),
  previewRecoveryNode: vi.fn(),
}))

function fixture(instance = true, sameChild = false, outputCount = 2) {
  const names = outputCount === 1 ? [instance ? "again" : "premium"]
    : instance ? ["again_premium", "again_audit"] : ["premium", "audit"]
  const occurrence = makeNode(instance ? "again" : "owner", "submodel", { data: {
    label: instance ? "again" : "owner", nodeType: "submodel",
    config: { definition_id: "rating", alias: instance ? "again" : "owner", ...(instance ? { instance_of: "owner" } : {}) },
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
    selectedNode: occurrence,
    activeSubmodelIdentity: null,
    // The store's own raw setter, as the editor passes it.
    setNodesRaw: (update) => useGraphStore.getState().setNodesRaw(update), setEdgesRaw: vi.fn(), setPreamble: vi.fn(),
    pipelineNameRef: { current: "test" }, descriptionRef: { current: "" },
    sourceFileRef: { current: "main.py" }, sourceRevisionRef: { current: "revision-test" },
    preservedBlocksRef: { current: [] }, nodeIdCounter: { current: 0 },
  }
  vi.mocked(loadPipeline).mockResolvedValue(makeLoadedPipeline({ nodes, submodels }))
  return { occurrence, names, params }
}

/** An occurrence's output port as a preview from the main pipeline reads it, by address. */
function outputTarget(occurrence: string, port: string) {
  return { kind: "occurrence_output", occurrence, port }
}

function response(value: number) {
  return makePreviewResponse({ columns: [{ name: "value", dtype: "Int64" }],
    preview: [{ value }], row_count: 1, column_count: 1 })
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

describe("pipeline document load", () => {
  it("forgets the Banding rules a type switch set aside", async () => {
    useUIStore.getState().setBandingRuleStash("band", { identity: rowIdentity([{}]), rules: { 0: { breakpoints: [{ boundary: "20", label: "young" }] } } })

    const hook = await loaded()
    expect(useUIStore.getState().bandingRuleStash).toEqual({})

    useUIStore.getState().setBandingRuleStash("band", { identity: rowIdentity([{}]), rules: { 0: { categorical: [{ value: "A", assignment: "a" }] } } })
    act(() => hook.result.current.adoptPipelineDocument(makePipelineEditorDocument(), null))
    expect(useUIStore.getState().bandingRuleStash).toEqual({})
  })
})

describe("submodel occurrence previews", () => {
  it("keeps the chosen public output after renaming an instance and refreshing", async () => {
    const hook = await loaded(fixture(true, true))
    act(() => hook.result.current.fetchPreview(hook.occurrence, { debounceMs: 0 }))
    await settled(hook)
    act(() => hook.result.current.previewNodeFrame("again", "again_audit"))
    await settled(hook)
    const renamed = { ...hook.occurrence, data: { ...hook.occurrence.data,
      label: "renamed", config: { ...nodeData(hook.occurrence).config, alias: "renamed" },
      _sourceHandleInputNames: { out__premium: "renamed_premium", out__audit: "renamed_audit" },
    } }
    useGraphStore.setState({ nodes: [renamed], edges: [] })
    hook.params.selectedNode = renamed
    hook.rerender()
    vi.mocked(previewNode).mockResolvedValueOnce(response(33))
    act(() => hook.result.current.refreshPreview(renamed))
    await settled(hook)
    expect(previewNode).toHaveBeenCalledTimes(3)
    expect(previewNode).toHaveBeenLastCalledWith(expect.objectContaining({
      target: outputTarget("again", "audit"),
    }))
    expect(hook.result.current.previewData).toMatchObject({
      nodeLabel: "renamed_audit", selected_frame: "renamed_audit", preview: [{ value: 33 }],
    })
    expect(Object.keys(hook.result.current.previewData!.frame_columns!)).toEqual(["renamed_premium", "renamed_audit"])
  })

  it.each([false, true])("previews the first output and selects another by its output name (instance=%s)", async (instance) => {
    const hook = await loaded(fixture(instance))
    act(() => hook.result.current.fetchPreview(hook.occurrence, { debounceMs: 0 }))
    await settled(hook)
    expect(previewNode).toHaveBeenLastCalledWith(expect.objectContaining({
      target: outputTarget(hook.occurrence.id, "premium"),
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
    expect(previewNode).toHaveBeenLastCalledWith(expect.objectContaining({ target: outputTarget(hook.occurrence.id, "audit") }))
    expect(previewInputs).toHaveBeenLastCalledWith(expect.objectContaining({ target: outputTarget(hook.occurrence.id, "audit") }))
  })

  it("keeps two outputs of one child in separate caches and refreshes only the displayed frame", async () => {
    const hook = await loaded(fixture(true, true))
    act(() => hook.result.current.fetchPreview(hook.occurrence, { debounceMs: 0 }))
    await settled(hook)
    expect(previewNode).toHaveBeenLastCalledWith(expect.objectContaining({ target: outputTarget("again", "premium") }))
    vi.mocked(previewNode).mockResolvedValueOnce(response(22))
    act(() => hook.result.current.previewNodeFrame("again", "again_audit"))
    await settled(hook)
    expect(previewNode).toHaveBeenLastCalledWith(expect.objectContaining({ target: outputTarget("again", "audit") }))
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
    expect(previewNode).toHaveBeenLastCalledWith(expect.objectContaining({ target: outputTarget("again", "audit") }))
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
    expect(previewNode).toHaveBeenLastCalledWith(expect.objectContaining({ target: outputTarget("again", "audit") }))
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
    // The collapsed occurrence's entries are keyed by its instance id, addressed as the pipeline's node.
    vi.mocked(previewNode).mockResolvedValueOnce(makePreviewResponse({ ...response(11), frame_columns: { prices: columns },
      node_frame_columns: { again: { out__premium: columns } }, node_addresses: rootAddresses("again") }))
    act(() => hook.result.current.fetchPreview(hook.occurrence, { debounceMs: 0 }))
    await settled(hook)
    const data = useGraphStore.getState().nodes[0].data
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
    vi.mocked(previewNode).mockResolvedValueOnce(makePreviewResponse({ ...response(11), seed_plan: [{
      node_id: "source", port_label: null, node_label: "Source", identity_digest: "d".repeat(64),
      generation_id: "occurrence-capture", columns: null, created_at: "2026-10-29T00:00:00Z", kind: "captured",
    }] }))
    act(() => hook.result.current.fetchPreview(hook.occurrence, { debounceMs: 0 }))
    await settled(hook)
    expect(useNodeDataStore.getState().epoch).toBe(1)
    expect(previewNode).toHaveBeenCalledOnce()
    act(() => hook.result.current.fetchPreview(hook.occurrence, { debounceMs: 0 }))
    await settled(hook)
    expect(previewNode).toHaveBeenCalledOnce()
  })
})

describe("a child's preview in each occurrence of its definition", () => {
  /** The owner's child previewed, then the copy open, as leaving a view leaves no preview shown. */
  async function previewedInOwner() {
    // One set of refs: new ones would load the pipeline again.
    const shared: Parameters<typeof usePipelineAPI>[0] = {
      selectedNode: null,
      activeSubmodelIdentity: OWNER,
      setNodesRaw: (update) => useGraphStore.getState().setNodesRaw(update), setEdgesRaw: vi.fn(), setPreamble: vi.fn(),
      pipelineNameRef: { current: "test" }, descriptionRef: { current: "" },
      sourceFileRef: { current: "main.py" }, sourceRevisionRef: { current: "revision-test" },
      preservedBlocksRef: { current: [] }, nodeIdCounter: { current: 0 },
    }
    const params = (occurrence: Occurrence) => ({ ...shared, activeSubmodelIdentity: occurrence })
    vi.mocked(loadPipeline).mockResolvedValue(makeLoadedPipeline({ nodes: [] }))
    const hook = renderHook((props: Parameters<typeof usePipelineAPI>[0]) => usePipelineAPI(props), { initialProps: params(OWNER) })
    await waitFor(() => expect(hook.result.current.loading).toBe(false))
    act(() => {
      loadTwoOccurrences([makeNode("prepare")])
      openOccurrence(OWNER)
    })
    act(() => hook.result.current.fetchPreview(onCanvas("prepare"), { debounceMs: 0 }))
    await waitFor(() => expect(hook.result.current.previewBusy).toBe(false))
    expect(hook.result.current.previewData).toMatchObject({ nodeId: "prepare", preview: [{ value: 11 }] })
    act(() => {
      hook.result.current.cancelPreview()
      hook.result.current.setPreviewData(null)
      openOccurrence(COPY)
    })
    hook.rerender(params(COPY))
    return hook
  }

  it("shows the copy's child none of the owner's rows, and previews it for the copy", async () => {
    const hook = await previewedInOwner()
    let answer!: (value: ReturnType<typeof response>) => void
    vi.mocked(previewNode).mockImplementationOnce(() => new Promise((resolve) => { answer = resolve }))

    act(() => hook.result.current.fetchPreview(onCanvas("prepare"), { debounceMs: 0 }))

    // No flash of the owner's rows while the copy's are fetched.
    expect(hook.result.current.previewData).toMatchObject({ nodeId: "prepare", status: "loading", preview: [] })
    await waitFor(() => expect(previewNode).toHaveBeenCalledTimes(2))
    expect(previewNode).toHaveBeenLastCalledWith(expect.objectContaining({ target: nodeTarget("prepare", { occurrence: "pricing_2" }) }))
    await act(async () => answer(response(22)))
    await waitFor(() => expect(hook.result.current.previewData?.preview).toEqual([{ value: 22 }]))
    const previews = useNodeResultsStore.getState().previews
    expect(previews[childKey(OWNER, "prepare")]?.data.preview).toEqual([{ value: 11 }])
    expect(previews[childKey(COPY, "prepare")]?.data.preview).toEqual([{ value: 22 }])
    expect(previews).not.toHaveProperty("prepare")
  })

  it("in manual calculation shows the copy's child nothing until Refresh", async () => {
    const hook = await previewedInOwner()
    act(() => useUIStore.setState({ calculationMode: "manual" }))

    act(() => hook.result.current.fetchPreview(onCanvas("prepare"), { debounceMs: 0 }))

    expect(hook.result.current.previewData).toBeNull()
    expect(previewNode).toHaveBeenCalledOnce()
  })
})

describe("an answer's nodes placed by their addresses", () => {
  it("shows a collapsed occurrence's children's statuses as one on its card, the worst of them", async () => {
    const hook = await loaded()
    vi.mocked(previewNode).mockResolvedValueOnce(makePreviewResponse({
      columns: [{ name: "value", dtype: "Int64" }], preview: [{ value: 1 }], row_count: 1, column_count: 1,
      // The worse status first, so a card that took the last of them would show "ok".
      node_statuses: { "submodel_runtime/again/check": "error", "submodel_runtime/again/finish": "ok" },
      node_addresses: {
        "submodel_runtime/again/finish": { occurrence: "again", node_id: "finish", label: "finish" },
        "submodel_runtime/again/check": { occurrence: "again", node_id: "check", label: "check" },
      },
    }))

    act(() => hook.result.current.fetchPreview(hook.occurrence, { debounceMs: 0 }))
    await settled(hook)

    expect(hook.result.current.nodeStatuses).toEqual({ again: "error" })
  })

  it("prepares an input the preview reads inside a submodel, found by its address in the definition", async () => {
    vi.mocked(loadPipeline).mockResolvedValue(makeLoadedPipeline({ nodes: [] }))
    // One set of refs: new ones would load the pipeline again.
    const params: Parameters<typeof usePipelineAPI>[0] = {
      selectedNode: null,
      activeSubmodelIdentity: OWNER,
      setNodesRaw: (update) => useGraphStore.getState().setNodesRaw(update), setEdgesRaw: vi.fn(), setPreamble: vi.fn(),
      pipelineNameRef: { current: "test" }, descriptionRef: { current: "" },
      sourceFileRef: { current: "main.py" }, sourceRevisionRef: { current: "revision-test" },
      preservedBlocksRef: { current: [] }, nodeIdCounter: { current: 0 },
    }
    const hook = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(hook.result.current.loading).toBe(false))
    act(() => {
      loadTwoOccurrences([makeNode("quotes", "dataInput"), makeNode("prepare")])
      openOccurrence(OWNER)
    })
    vi.mocked(previewInputs).mockResolvedValueOnce({
      input_node_ids: ["submodel_runtime/pricing/quotes" as never],
      node_addresses: { "submodel_runtime/pricing/quotes": { occurrence: "pricing", node_id: "quotes", label: "quotes" } },
    })

    act(() => hook.result.current.fetchPreview(onCanvas("prepare"), { debounceMs: 0 }))
    await waitFor(() => expect(hook.result.current.previewBusy).toBe(false))

    expect(previewInputs).toHaveBeenLastCalledWith(expect.objectContaining({ target: nodeTarget("prepare", { occurrence: "pricing" }) }))
    expect(vi.mocked(ensureInputSnapshots).mock.calls.at(-1)?.[0].map((node) => (node as unknown as { id: string }).id)).toEqual(["quotes"])
  })

  it("prepares a pipeline input and a submodel's input of the same id, each from its own config", async () => {
    vi.mocked(loadPipeline).mockResolvedValue(makeLoadedPipeline({ nodes: [] }))
    // One set of refs: new ones would load the pipeline again.
    const params: Parameters<typeof usePipelineAPI>[0] = {
      selectedNode: null,
      activeSubmodelIdentity: OWNER,
      setNodesRaw: (update) => useGraphStore.getState().setNodesRaw(update), setEdgesRaw: vi.fn(), setPreamble: vi.fn(),
      pipelineNameRef: { current: "test" }, descriptionRef: { current: "" },
      sourceFileRef: { current: "main.py" }, sourceRevisionRef: { current: "revision-test" },
      preservedBlocksRef: { current: [] }, nodeIdCounter: { current: 0 },
    }
    const hook = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(hook.result.current.loading).toBe(false))
    act(() => {
      loadTwoOccurrences([
        makeNode("source", "dataInput", { data: { label: "source", nodeType: "dataInput", config: { path: "claims.csv" } } }),
        makeNode("prepare"),
      ])
      // The pipeline's own `source`, beside the occurrences.
      useGraphStore.getState().setNodesRaw((nodes) => [
        ...nodes,
        makeNode("source", "dataInput", { data: { label: "source", nodeType: "dataInput", config: { path: "policies.csv" } } }),
      ])
      openOccurrence(OWNER)
    })
    vi.mocked(previewInputs).mockResolvedValueOnce({
      input_node_ids: ["source" as never, "submodel_runtime/pricing/source" as never],
      node_addresses: {
        source: { occurrence: null, node_id: "source", label: "source" },
        "submodel_runtime/pricing/source": { occurrence: "pricing", node_id: "source", label: "source" },
      },
    })

    act(() => hook.result.current.fetchPreview(onCanvas("prepare"), { debounceMs: 0 }))
    await waitFor(() => expect(hook.result.current.previewBusy).toBe(false))

    const prepared = vi.mocked(ensureInputSnapshots).mock.calls.at(-1)?.[0]
      .map((node) => (node as unknown as { data: { config: { path: string } } }).data.config.path)
    expect(prepared?.sort()).toEqual(["claims.csv", "policies.csv"])
  })
})
