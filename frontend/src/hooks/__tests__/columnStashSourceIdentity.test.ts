/**
 * Key-contract pin — source identity of the graph-embedded column stashes.
 *
 * `node.data._columns` / `_availableColumns` are a cache: captured from a
 * preview run against ONE data source, then read by editors (NodePanel,
 * ColumnsTab, OutputEditor, edge-join validation) and by the lazy-refresh
 * machinery. The cache key must include every input that affects the cached
 * output — and the active source is such an input. These tests pin the
 * contract that the stash carries the source it was captured under
 * (`_columnsSource`) and is invalidated, not served stale, when the active
 * source changes.
 *
 *   1. Capture stamps the stash with the source of the preview run.
 *   2. Switching the active source invalidates a stash captured under a
 *      different source (the key-contract pin).
 *   3. A stash captured under the CURRENT source survives a re-set of the
 *      same source — no gratuitous invalidation (render-gate: persisted
 *      editor state must not disappear as a side effect).
 *   4. An untagged stash (unknown provenance — e.g. loaded from a save
 *      made before tagging existed) is treated as stale on source switch.
 *   5. refreshPreview treats an upstream stash captured under a different
 *      source as missing and re-previews that upstream node.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act, waitFor } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import usePipelineAPI from "../usePipelineAPI"
import useToastStore from "../../stores/useToastStore"
import useSettingsStore from "../../stores/useSettingsStore"
import useGraphStore from "../../stores/useGraphStore"
import useNodeResultsStore from "../../stores/useNodeResultsStore"

vi.mock("../../api/client", () => ({
  loadPipeline: vi.fn(),
  previewNode: vi.fn(),
  savePipeline: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number
    detail?: string

    constructor(msg: string, status?: number, detail?: string) {
      super(msg)
      this.name = "ApiError"
      this.status = status ?? 0
      this.detail = detail
    }
  },
}))

import { loadPipeline, previewNode } from "../../api/client"
import { makeNode, makeEdge } from "../../test-utils/factories"

const mockLoad = vi.mocked(loadPipeline)
const mockPreview = vi.mocked(previewNode)

type SetNodesRaw = ReturnType<typeof useGraphStore.getState>["setNodesRaw"]

function makeParams(overrides: Partial<Parameters<typeof usePipelineAPI>[0]> = {}) {
  const setNodesRaw = vi.fn<SetNodesRaw>(
    (nodes) => useGraphStore.getState().setNodesRaw(nodes),
  )
  return {
    selectedNode: null as Node | null,
    graphRef: { current: { nodes: [] as Node[], edges: [] as Edge[] } },
    parentGraphRef: { current: null },
    submodelsRef: { current: {} },
    setNodesRaw,
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

async function flushAsyncWork() {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

const LIVE_COLUMNS = [{ name: "premium", dtype: "f64" }]

function makeStashedNode(id: string, source?: string): Node {
  const node = makeNode(id)
  node.data = {
    ...node.data,
    _columns: LIVE_COLUMNS,
    _availableColumns: LIVE_COLUMNS,
    _schemaWarnings: [],
    ...(source !== undefined ? { _columnsSource: source } : {}),
  }
  return node
}

describe("column-stash source identity (cache-key completeness)", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
    useSettingsStore.setState({ rowLimit: 1000, activeSource: "live", sources: ["live", "staging"] })
    useGraphStore.setState({
      nodes: [],
      edges: [],
      preamble: "",
      lastSavedSnapshot: null,
      undoStack: [],
      redoStack: [],
    })
    useNodeResultsStore.setState({ previews: {}, columnCache: {} })
    mockLoad.mockReset()
    mockLoad.mockResolvedValue({ nodes: [], edges: [], preserved_blocks: [], source_revision: "revision-test" })
    mockPreview.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
    cleanup()
    vi.restoreAllMocks()
  })

  it("stamps the stash with the source the preview ran under", async () => {
    vi.useRealTimers()
    mockPreview.mockResolvedValue({
      node_id: "A",
      status: "ok",
      columns: LIVE_COLUMNS,
      available_columns: LIVE_COLUMNS,
      preview: [],
    })
    const A = makeNode("A")
    const params = makeParams()
    params.graphRef.current = { nodes: [A], edges: [] }
    mockLoad.mockResolvedValue({ nodes: [A], edges: [], preserved_blocks: [], source_revision: "revision-test" })
    useSettingsStore.setState({ activeSource: "staging" })

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => { result.current.refreshPreview(A) })
    await flushAsyncWork()
    await waitFor(() => expect(result.current.previewBusy).toBe(false))

    const nodes = useGraphStore.getState().nodes
    expect(nodes[0].data._columns).toEqual(LIVE_COLUMNS)
    expect(nodes[0].data._columnsSource).toBe("staging")
  })

  it("invalidates a stash captured under a different source when the active source changes", async () => {
    vi.useRealTimers()
    const A = makeStashedNode("A", "live")
    const params = makeParams()
    params.graphRef.current = { nodes: [A], edges: [] }
    mockLoad.mockResolvedValue({ nodes: [A], edges: [], preserved_blocks: [], source_revision: "revision-test" })

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => { useSettingsStore.getState().setActiveSource("staging") })

    const nodes = useGraphStore.getState().nodes
    expect(nodes[0].data._columns).toBeUndefined()
    expect(nodes[0].data._availableColumns).toBeUndefined()
    expect(nodes[0].data._schemaWarnings).toBeUndefined()
    expect(nodes[0].data._columnsSource).toBeUndefined()
  })

  it("keeps a stash captured under the current source (no gratuitous invalidation)", async () => {
    vi.useRealTimers()
    const A = makeStashedNode("A", "staging")
    const params = makeParams()
    params.graphRef.current = { nodes: [A], edges: [] }
    mockLoad.mockResolvedValue({ nodes: [A], edges: [], preserved_blocks: [], source_revision: "revision-test" })
    useSettingsStore.setState({ activeSource: "staging" })

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => { useSettingsStore.getState().setActiveSource("staging") })

    const nodes = useGraphStore.getState().nodes
    expect(nodes[0].data._columns).toEqual(LIVE_COLUMNS)
    expect(nodes[0].data._availableColumns).toEqual(LIVE_COLUMNS)
  })

  it("treats an untagged stash as stale on source switch", async () => {
    vi.useRealTimers()
    const A = makeStashedNode("A") // no _columnsSource — unknown provenance
    const params = makeParams()
    params.graphRef.current = { nodes: [A], edges: [] }
    mockLoad.mockResolvedValue({ nodes: [A], edges: [], preserved_blocks: [], source_revision: "revision-test" })

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => { useSettingsStore.getState().setActiveSource("staging") })

    const nodes = useGraphStore.getState().nodes
    expect(nodes[0].data._columns).toBeUndefined()
  })

  it("refreshPreview re-previews an upstream node whose stash was captured under another source", async () => {
    vi.useRealTimers()
    mockPreview.mockResolvedValue({
      node_id: "A",
      status: "ok",
      columns: LIVE_COLUMNS,
      available_columns: LIVE_COLUMNS,
      preview: [],
    })
    // Upstream A carries a live-source stash; active source is staging.
    // The lazy-refresh gap-fill must treat A as never previewed.
    const A = makeStashedNode("A", "live")
    const B = makeNode("B")
    const params = makeParams()
    params.graphRef.current = { nodes: [A, B], edges: [makeEdge("A", "B")] }
    useSettingsStore.setState({ activeSource: "staging" })

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => { result.current.refreshPreview(B) })
    await flushAsyncWork()
    await waitFor(() => expect(result.current.previewBusy).toBe(false))

    const previewedNodeIds = mockPreview.mock.calls.map((c) => c[0].nodeId)
    expect(previewedNodeIds).toContain("A")
    expect(previewedNodeIds).toContain("B")
  })
})
