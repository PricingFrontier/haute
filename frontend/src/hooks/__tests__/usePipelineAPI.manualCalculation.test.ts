/**
 * Manual calculation: clicking a node shows its last result for the current
 * source and row limit and runs nothing; Refresh still calculates, and a
 * moved node-data epoch leaves the displayed preview alone.
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
import useUIStore from "../../stores/useUIStore"

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

function envelope(nodeId: string): PreviewEnvelope {
  return {
    node_id: nodeId,
    status: "ok",
    row_count: 1,
    column_count: 1,
    columns: [{ name: "premium", dtype: "f64" }],
    preview: [{ premium: 1 }],
    seed_plan: [],
  } as PreviewEnvelope
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

describe("usePipelineAPI - manual calculation", () => {
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
    useUIStore.setState({ calculationMode: "automatic" })
    mockLoad.mockReset().mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))
    mockPreview.mockReset()
  })

  afterEach(() => {
    useUIStore.setState({ calculationMode: "automatic" })
    vi.useRealTimers()
    cleanup()
    vi.restoreAllMocks()
  })

  it("starts every session in automatic calculation", () => {
    expect(useUIStore.getInitialState().calculationMode).toBe("automatic")
  })

  it("shows nothing and runs nothing for a node never calculated", async () => {
    const A = makeNode("A")
    const { result } = await renderLoaded([A])
    act(() => useUIStore.getState().setCalculationMode("manual"))

    act(() => result.current.fetchPreview(A, { debounceMs: 0 }))
    await settle(result)

    expect(result.current.previewData).toBeNull()
    expect(mockPreview).not.toHaveBeenCalled()
  })

  it("shows a node's last result without calculating it again", async () => {
    mockPreview.mockImplementation(async (req) => envelope(req.nodeId))
    const A = makeNode("A")
    const B = makeNode("B")
    const { result } = await renderLoaded([A, B])

    act(() => result.current.fetchPreview(A, { debounceMs: 0 }))
    await waitFor(() => expect(result.current.previewData?.status).toBe("ok"))
    await settle(result)
    const shown = result.current.previewData
    expect(mockPreview).toHaveBeenCalledTimes(1)

    act(() => useUIStore.getState().setCalculationMode("manual"))
    // Edit the pipeline: the stored result is now out of date, and still shown.
    act(() => useGraphStore.setState({ structuralVersion: 1 }))
    act(() => result.current.fetchPreview(B, { debounceMs: 0 }))
    expect(result.current.previewData).toBeNull()
    act(() => result.current.fetchPreview(A, { debounceMs: 0 }))
    await settle(result)

    expect(result.current.previewData).toBe(shown)
    expect(mockPreview).toHaveBeenCalledTimes(1)
  })

  it("does not show a result calculated for another row limit", async () => {
    mockPreview.mockResolvedValue(envelope("A"))
    const A = makeNode("A")
    const B = makeNode("B")
    const { result } = await renderLoaded([A, B])
    act(() => result.current.fetchPreview(A, { debounceMs: 0 }))
    await waitFor(() => expect(result.current.previewData?.status).toBe("ok"))
    await settle(result)

    act(() => useUIStore.getState().setCalculationMode("manual"))
    act(() => useSettingsStore.setState({ rowLimit: 50 }))
    act(() => result.current.fetchPreview(B, { debounceMs: 0 }))
    act(() => result.current.fetchPreview(A, { debounceMs: 0 }))
    await settle(result)

    expect(result.current.previewData).toBeNull()
    expect(mockPreview).toHaveBeenCalledTimes(1)
  })

  it("leaves the displayed preview alone when a snapshot is published", async () => {
    mockPreview.mockResolvedValue(envelope("A"))
    const A = makeNode("A")
    const { result } = await renderLoaded([A])
    act(() => result.current.fetchPreview(A, { debounceMs: 0 }))
    await waitFor(() => expect(result.current.previewData?.status).toBe("ok"))
    await settle(result)

    act(() => useUIStore.getState().setCalculationMode("manual"))
    act(() => useNodeDataStore.getState().bumpEpoch())
    await settle(result)

    expect(result.current.previewData?.status).toBe("ok")
    expect(mockPreview).toHaveBeenCalledTimes(1)
  })

  it("still calculates on Refresh", async () => {
    mockPreview.mockResolvedValue(envelope("A"))
    const A = makeNode("A")
    const { result } = await renderLoaded([A])
    act(() => useUIStore.getState().setCalculationMode("manual"))

    act(() => result.current.refreshPreview(A))

    await waitFor(() => expect(result.current.previewData?.status).toBe("ok"), { timeout: STEP_TIMEOUT_MS })
    expect(mockPreview).toHaveBeenCalledTimes(1)
  })
})
