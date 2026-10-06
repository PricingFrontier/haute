/**
 * A preview is current only at the node-data epoch its request was sent
 * under: a snapshot published, refreshed, or cleared after that may change
 * its rows. The displayed preview is fetched again when the epoch moves; a
 * preview's own captures raise the epoch without fetching that preview again,
 * unless something else moved the epoch while it was in flight.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act, waitFor } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import type { MutableRefObject } from "react"
import usePipelineAPI from "../usePipelineAPI"
import useSettingsStore from "../../stores/useSettingsStore"
import useGraphStore from "../../stores/useGraphStore"
import useNodeResultsStore from "../../stores/useNodeResultsStore"
import useNodeDataStore from "../../stores/useNodeDataStore"
import type { PreviewData } from "../../panels/DataPreview"
import type { PreviewSeedPlanEntry } from "../../api/types"

// A step crosses a mocked request and several effects, which a whole parallel
// suite run makes slower without making it wrong.
const STEP_TIMEOUT_MS = 10_000

vi.mock("../../api/client", () => ({
  loadPipeline: vi.fn(),
  previewInputs: vi.fn(async () => ({ input_node_ids: [] as string[] })),
  previewNode: vi.fn(),
  previewRecoveryNode: vi.fn(),
  savePipeline: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number
    detail?: string

    constructor(message: string, status: number, detail?: string) {
      super(message)
      this.name = "ApiError"
      this.status = status
      this.detail = detail
    }
  },
}))

vi.mock("../../utils/buildGraph", () => ({
  resolveGraphFromRefs: vi.fn(
    (
      graphRef: MutableRefObject<{ nodes: Node[]; edges: Edge[] }>,
      _parentGraphRef: unknown,
      submodelsRef: MutableRefObject<Record<string, unknown>>,
      preambleRef: MutableRefObject<string>,
    ) => ({
      nodes: graphRef.current.nodes,
      edges: graphRef.current.edges,
      submodels: submodelsRef.current,
      preamble: preambleRef.current,
    }),
  ),
}))

vi.mock("../../utils/makePreviewData", () => ({
  makePreviewData: vi.fn((nodeId: string, label: string, opts: Record<string, unknown>) => ({
    nodeId,
    nodeLabel: label,
    status: opts.status || "ok",
    row_count: opts.row_count ?? 0,
    column_count: opts.column_count ?? 0,
    columns: opts.columns ?? [],
    preview: opts.preview ?? [],
    error: opts.error ?? null,
    seed_plan: opts.seed_plan ?? [],
  })),
}))

import { loadPipeline, previewNode } from "../../api/client"
import { makeNode } from "../../test-utils/factories"
import { makeLoadedPipeline } from "../../testSupport/pipelineDocumentFixture"
const mockLoad = vi.mocked(loadPipeline)
const mockPreview = vi.mocked(previewNode)

type PreviewEnvelope = Awaited<ReturnType<typeof previewNode>>

function makeParams(overrides: Partial<Parameters<typeof usePipelineAPI>[0]> = {}) {
  return {
    selectedNode: null as Node | null,
    graphRef: { current: { nodes: [] as Node[], edges: [] as Edge[] } },
    parentGraphRef: { current: null },
    activeSubmodelIdentity: null,
    submodelsRef: { current: {} },
    setNodes: vi.fn(),
    setNodesRaw: vi.fn(),
    setEdgesRaw: vi.fn(),
    setPreamble: vi.fn(),
    preambleRef: { current: "" },
    pipelineNameRef: { current: "test" },
    descriptionRef: { current: "" },
    sourceFileRef: { current: "test.py" },
    sourceRevisionRef: { current: "revision-test" },
    preservedBlocksRef: { current: [] as string[] },
    nodeIdCounter: { current: 0 },
    ...overrides,
  }
}

function captured(nodeId: string): PreviewSeedPlanEntry {
  return {
    node_id: nodeId,
    port_label: null,
    node_label: nodeId,
    identity_digest: "c".repeat(64),
    generation_id: `${nodeId}-generation`,
    columns: null,
    created_at: "2026-09-19T00:00:00+00:00",
    kind: "captured",
  }
}

function envelope(
  nodeId: string,
  seedPlan: PreviewSeedPlanEntry[] = [],
  column = "premium",
): PreviewEnvelope {
  return {
    node_id: nodeId,
    status: "ok",
    row_count: 1,
    column_count: 1,
    columns: [{ name: column, dtype: "f64" }],
    preview: [{ [column]: 1 }],
    seed_plan: seedPlan,
  } as PreviewEnvelope
}

function epoch(): number {
  return useNodeDataStore.getState().epoch
}

async function renderLoaded(nodes: Node[], edges: Edge[] = []) {
  const params = makeParams()
  params.graphRef.current = { nodes, edges }
  const rendered = renderHook(() => usePipelineAPI(params))
  await waitFor(() => expect(rendered.result.current.loading).toBe(false))
  return rendered
}

async function settle(result: { current: ReturnType<typeof usePipelineAPI> }) {
  await waitFor(() => expect(result.current.previewBusy).toBe(false), { timeout: STEP_TIMEOUT_MS })
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

describe("usePipelineAPI - previews and the node-data epoch", () => {
  beforeEach(() => {
    vi.useRealTimers()
    useSettingsStore.setState({ rowLimit: 1000, activeSource: "live", sources: ["live"] })
    useGraphStore.setState({
      nodes: [],
      edges: [],
      preamble: "",
      lastSavedSnapshot: null,
      undoStack: [],
      redoStack: [],
      structuralVersion: 0,
    })
    useNodeResultsStore.setState({ previews: {}, columnCache: {} })
    useNodeDataStore.getState().reset()
    mockLoad.mockReset().mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))
    mockPreview.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
    cleanup()
    vi.restoreAllMocks()
  })

  it("fetches the displayed preview again once a snapshot is published after its request", async () => {
    mockPreview.mockResolvedValue(envelope("A"))
    const A = makeNode("A")
    const { result } = await renderLoaded([A])

    act(() => result.current.fetchPreview(A, { debounceMs: 0 }))
    await waitFor(() => expect(result.current.previewData?.status).toBe("ok"))
    await settle(result)
    expect(mockPreview).toHaveBeenCalledTimes(1)

    act(() => useNodeDataStore.getState().bumpEpoch())

    await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(2), { timeout: STEP_TIMEOUT_MS })
    await settle(result)
    expect(useNodeResultsStore.getState().getPreview("A")?.nodeDataEpoch).toBe(epoch())
    expect(mockPreview).toHaveBeenCalledTimes(2)
  })

  it("raises the epoch for a preview's own capture without fetching it again", async () => {
    mockPreview.mockResolvedValue(envelope("A", [captured("join")]))
    const A = makeNode("A")
    const { result } = await renderLoaded([A])
    const before = epoch()

    act(() => result.current.fetchPreview(A, { debounceMs: 0 }))
    await waitFor(() => expect(result.current.previewData?.status).toBe("ok"))
    await settle(result)

    expect(epoch()).toBe(before + 1)
    expect(useNodeResultsStore.getState().getPreview("A")?.nodeDataEpoch).toBe(before + 1)
    expect(mockPreview).toHaveBeenCalledTimes(1)
  })

  it("fetches an own-capture preview again when the epoch moved while it was in flight", async () => {
    let resolveFirst!: (value: PreviewEnvelope) => void
    mockPreview
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve }))
      .mockResolvedValue(envelope("A", [captured("join")]))
    const A = makeNode("A")
    const { result } = await renderLoaded([A])

    act(() => result.current.fetchPreview(A, { debounceMs: 0 }))
    await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1))
    // Another reader publishes while this preview is in flight.
    act(() => useNodeDataStore.getState().bumpEpoch())
    await act(async () => { resolveFirst(envelope("A", [captured("join")])) })

    await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(2), { timeout: STEP_TIMEOUT_MS })
    await settle(result)
    expect(useNodeResultsStore.getState().getPreview("A")?.nodeDataEpoch).toBe(epoch())
  })

  it("shows a stored preview from an older epoch and fetches it again", async () => {
    mockPreview.mockResolvedValue(envelope("A"))
    const A = makeNode("A")
    const { result } = await renderLoaded([A])
    const stored = { ...envelope("A"), nodeId: "A", nodeLabel: "A", error: null } as unknown as PreviewData
    useNodeResultsStore.getState().setPreview("A", stored, useGraphStore.getState().structuralVersion, "live", 1000, epoch())

    act(() => useNodeDataStore.getState().bumpEpoch())
    act(() => result.current.fetchPreview(A, { debounceMs: 0 }))

    expect(result.current.previewData).toBe(stored)
    await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1), { timeout: STEP_TIMEOUT_MS })
    await settle(result)
  })

  it("answers a stored preview from the current epoch without a request", async () => {
    const A = makeNode("A")
    const { result } = await renderLoaded([A])
    const stored = { ...envelope("A"), nodeId: "A", nodeLabel: "A", error: null } as unknown as PreviewData
    useNodeResultsStore.getState().setPreview("A", stored, useGraphStore.getState().structuralVersion, "live", 1000, epoch())

    act(() => result.current.fetchPreview(A, { debounceMs: 0 }))
    await settle(result)

    expect(result.current.previewData).toBe(stored)
    expect(mockPreview).not.toHaveBeenCalled()
  })

  it("fetches a displayed frame preview again for its frame once a snapshot is published", async () => {
    mockPreview.mockResolvedValue(envelope("A"))
    const A = makeNode("A")
    const { result } = await renderLoaded([A])

    act(() => result.current.previewNodeFrame("A", "claims"))
    await waitFor(() => expect(result.current.previewData?.status).toBe("ok"))
    await settle(result)
    expect(mockPreview).toHaveBeenCalledTimes(1)

    act(() => useNodeDataStore.getState().bumpEpoch())

    await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(2), { timeout: STEP_TIMEOUT_MS })
    expect(mockPreview.mock.calls[1][0]).toMatchObject({ nodeId: "A", portLabel: "claims" })
    await settle(result)
    expect(mockPreview).toHaveBeenCalledTimes(2)
  })

  it("does not fetch a frame preview again for its own capture", async () => {
    mockPreview.mockResolvedValue(envelope("A", [captured("join")]))
    const A = makeNode("A")
    const { result } = await renderLoaded([A])
    const before = epoch()

    act(() => result.current.previewNodeFrame("A", "claims"))
    await waitFor(() => expect(result.current.previewData?.status).toBe("ok"))
    await settle(result)

    expect(epoch()).toBe(before + 1)
    expect(mockPreview).toHaveBeenCalledTimes(1)
  })

  it("leaves the epoch unchanged when a response's captured generation ids were already announced", async () => {
    mockPreview.mockResolvedValue(envelope("A", [captured("join")]))
    const A = makeNode("A")
    const B = makeNode("B")
    const { result } = await renderLoaded([A, B])
    const before = epoch()

    act(() => result.current.fetchPreview(A, { debounceMs: 0 }))
    await waitFor(() => expect(result.current.previewData?.status).toBe("ok"))
    await settle(result)
    expect(epoch()).toBe(before + 1)

    // B returns the same generation id that was already announced by A.
    mockPreview.mockResolvedValue(envelope("B", [captured("join")]))
    act(() => result.current.fetchPreview(B, { debounceMs: 0 }))
    await waitFor(() => expect(result.current.previewData?.nodeId).toBe("B"))
    await settle(result)

    expect(epoch()).toBe(before + 1)
  })

  it("raises the epoch once for a response naming a generation id not seen before", async () => {
    mockPreview.mockResolvedValue(envelope("A", [captured("join-1")]))
    const A = makeNode("A")
    const B = makeNode("B")
    const { result } = await renderLoaded([A, B])
    const before = epoch()

    act(() => result.current.fetchPreview(A, { debounceMs: 0 }))
    await waitFor(() => expect(result.current.previewData?.status).toBe("ok"))
    await settle(result)
    expect(epoch()).toBe(before + 1)

    // B returns a new generation id
    mockPreview.mockResolvedValue(envelope("B", [captured("join-2")]))
    act(() => result.current.fetchPreview(B, { debounceMs: 0 }))
    await waitFor(() => expect(result.current.previewData?.nodeId).toBe("B"))
    await settle(result)

    expect(epoch()).toBe(before + 2)
  })

  it("keeps request epoch and does not refetch a frame preview whose capture was already announced, while stamping one past it for a new generation", async () => {
    const A = makeNode("A")
    const { result } = await renderLoaded([A])

    // Pre-record the generation id so it has already been announced
    useNodeDataStore.getState().noteAnnouncedCaptures(["join-generation"])
    const before = epoch()

    mockPreview.mockResolvedValue(envelope("A", [captured("join")]))
    act(() => result.current.previewNodeFrame("A", "claims"))
    await waitFor(() => expect(result.current.previewData?.status).toBe("ok"))
    await settle(result)

    // Epoch was not bumped, and frame preview was not refetched
    expect(epoch()).toBe(before)
    expect(mockPreview).toHaveBeenCalledTimes(1)

    // Now a frame preview naming a new generation id
    mockPreview.mockResolvedValue(envelope("A", [captured("new-join")]))
    act(() => result.current.previewNodeFrame("A", "policies"))
    await waitFor(() => expect(result.current.previewData?.status).toBe("ok"))
    await settle(result)

    // Stamped one past it, epoch bumped by 1, not refetched
    expect(epoch()).toBe(before + 1)
    expect(mockPreview).toHaveBeenCalledTimes(2)
  })

  it("raises the epoch again for a duplicate announcement after a store reset", async () => {
    mockPreview.mockResolvedValue(envelope("A", [captured("join")]))
    const A = makeNode("A")
    const B = makeNode("B")
    const { result } = await renderLoaded([A, B])
    const before = epoch()

    act(() => result.current.fetchPreview(A, { debounceMs: 0 }))
    await waitFor(() => expect(result.current.previewData?.status).toBe("ok"))
    await settle(result)
    expect(epoch()).toBe(before + 1)

    // Reset clears announced captures while raising epoch
    act(() => useNodeDataStore.getState().reset())
    const afterReset = epoch()
    expect(afterReset).toBe(before + 2)

    // Second announcement of the same response raises the epoch again
    mockPreview.mockResolvedValue(envelope("B", [captured("join")]))
    act(() => result.current.fetchPreview(B, { debounceMs: 0 }))
    await waitFor(() => expect(result.current.previewData?.nodeId).toBe("B"))
    await settle(result)

    expect(epoch()).toBe(afterReset + 1)
  })
})
