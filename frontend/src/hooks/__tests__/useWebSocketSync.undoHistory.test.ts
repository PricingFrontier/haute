/**
 * Phase 1 Package 1H — Item #8: WebSocket sync must not corrupt undo history.
 *
 * A pipeline_document_update is an external source-of-truth snapshot. It must:
 *
 *   - install nodes, edges, preamble, and submodels atomically;
 *   - bypass local edit history; and
 *   - clear stale undo/redo entries so Ctrl+Z cannot resurrect pre-sync state.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act, cleanup } from "@testing-library/react"
import { type Mock } from "vitest"
import type { Node } from "@xyflow/react"

// ── Mock dependencies BEFORE importing the hook ──────────────────

vi.mock("../../utils/layout.ts", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../utils/layout.ts")>(),
  getLayoutedElements: vi.fn(async (nodes: unknown[]) => nodes),
}))

vi.mock("../../stores/useToastStore.ts", () => {
  const store = {
    toasts: [] as Array<{ id: string; type: string; text: string }>,
    _toastCounter: 0,
    addToast: vi.fn(),
    dismissToast: vi.fn(),
  }
  const useToastStore = Object.assign(() => store, {
    getState: () => store,
    setState: vi.fn(),
    subscribe: vi.fn(),
  })
  return { default: useToastStore }
})

vi.mock("../../stores/useUIStore.ts", () => {
  const store: Record<string, unknown> = {
    syncBanner: null,
    setSyncBanner: vi.fn(),
    setPaletteOpen: vi.fn(),
    setShortcutsOpen: vi.fn(),
    submodelDialog: null,
    setSubmodelDialog: vi.fn(),
    renameDialog: null,
    setRenameDialog: vi.fn(),
  }
  const useUIStore = Object.assign(() => store, {
    getState: () => store,
    setState: vi.fn(),
    subscribe: vi.fn(),
  })
  return { default: useUIStore }
})

vi.mock("../../stores/useGraphStore.ts", () => {
  const store = {
    dirty: false,
    nodes: [] as unknown[],
    edges: [] as unknown[],
    submodels: {} as Record<string, unknown>,
    preamble: "",
    undoStack: [] as unknown[],
    redoStack: [] as unknown[],
    loadGraphSnapshot: vi.fn((snapshot: {
      nodes: unknown[]
      edges: unknown[]
      submodels: Record<string, unknown>
      preamble: string
    }) => {
      store.nodes = snapshot.nodes
      store.edges = snapshot.edges
      store.submodels = snapshot.submodels
      store.preamble = snapshot.preamble
      store.undoStack = []
      store.redoStack = []
    }),
  }
  const useGraphStore = Object.assign(() => store, {
    getState: () => store,
    setState: vi.fn(),
    subscribe: vi.fn(),
  })
  return { default: useGraphStore }
})

import useWebSocketSync from "../../hooks/useWebSocketSync.ts"
import useToastStore from "../../stores/useToastStore.ts"
import useUIStore from "../../stores/useUIStore.ts"
import useGraphStore from "../../stores/useGraphStore.ts"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore.ts"
import { makePipelineEditorDocument } from "../../testSupport/pipelineDocumentFixture.ts"

// ── Mock WebSocket ───────────────────────────────────────────────

type MockWSInstance = {
  url: string
  onopen: ((ev: Event) => void) | null
  onmessage: ((ev: MessageEvent) => void) | null
  onclose: ((ev: CloseEvent) => void) | null
  onerror: ((ev: Event) => void) | null
  close: Mock
  send: Mock
}
let mockWSInstances: MockWSInstance[] = []
function latestWS(): MockWSInstance { return mockWSInstances[mockWSInstances.length - 1] }
function createMockWebSocket() {
  function MockWebSocket(this: MockWSInstance, url: string) {
    this.url = url
    this.onopen = null; this.onmessage = null
    this.onclose = null; this.onerror = null
    this.close = vi.fn(); this.send = vi.fn()
    mockWSInstances.push(this)
  }
  return MockWebSocket
}

const SOURCE_FILE = "rating/main.py"

function makeHookParams(sourceFile = SOURCE_FILE) {
  return {
    preambleRef: { current: "" },
    submodelsRef: { current: {} as Record<string, unknown> },
    sourceFileRef: { current: sourceFile },
    sourceRevisionRef: { current: "revision-test" },
    preservedBlocksRef: { current: [] as string[] },
    graphRefreshingRef: { current: 0 },
    nodeIdCounter: { current: 0 },
    fitView: vi.fn(),
  }
}

function pipelineDocumentFrame(
  document: ReturnType<typeof makePipelineEditorDocument>,
  documentFingerprint = "document-fingerprint",
) {
  return {
    type: "pipeline_document_update",
    schema_version: 1,
    document,
    document_fingerprint: documentFingerprint,
    source_file: document.source_file,
  }
}

const baseNode: Node = {
  id: "disk",
  type: "polars",
  position: { x: 100, y: 200 },
  data: { label: "Disk", nodeType: "polars", config: {} },
}

describe("useWebSocketSync — WS sync must not corrupt undo history (#8)", () => {
  let originalWebSocket: typeof globalThis.WebSocket

  beforeEach(() => {
    vi.useFakeTimers()
    mockWSInstances = []
    originalWebSocket = globalThis.WebSocket
    globalThis.WebSocket = createMockWebSocket() as unknown as typeof WebSocket

    const graphStore = useGraphStore.getState()
    vi.mocked(graphStore.loadGraphSnapshot).mockClear()
    graphStore.dirty = false
    graphStore.nodes = []
    graphStore.edges = []
    graphStore.submodels = {}
    graphStore.preamble = ""
    graphStore.undoStack = []
    graphStore.redoStack = []
    vi.mocked(useToastStore.getState().addToast).mockClear()
    vi.mocked(useToastStore.getState().dismissToast).mockClear()
    vi.mocked(useUIStore.getState().setSyncBanner).mockClear()
    useDocumentStatusStore.getState().reset()
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    globalThis.WebSocket = originalWebSocket
  })

  it("atomically loads a pipeline_document_update as a clean snapshot", async () => {
    const params = makeHookParams()
    const document = makePipelineEditorDocument({
      source_file: SOURCE_FILE,
      source_revision: "revision-test",
      nodes: [{ ...baseNode, id: "n1", position: { x: 5, y: 6 }, data: { label: "A", nodeType: "polars", config: {} } }],
    })
    renderHook(() => useWebSocketSync(params))
    act(() => { latestWS().onopen?.(new Event("open")) })

    await act(async () => {
      latestWS().onmessage?.(new MessageEvent("message", {
        data: JSON.stringify(pipelineDocumentFrame(document)),
      }))
    })

    const graphStore = useGraphStore.getState()
    expect(graphStore.loadGraphSnapshot).toHaveBeenCalledWith(expect.objectContaining({
      nodes: [expect.objectContaining({ id: "n1" })],
      edges: [],
      preamble: "",
      submodels: {},
    }))
    expect(graphStore.undoStack).toEqual([])
    expect(graphStore.redoStack).toEqual([])
  })

  it("loads the document node payload without creating local history", async () => {
    const params = makeHookParams()
    const document = makePipelineEditorDocument({
      source_file: SOURCE_FILE,
      source_revision: "revision-test",
      nodes: [{ ...baseNode, id: "transform_1", position: { x: 10, y: 20 }, data: { label: "Incoming", nodeType: "polars", config: {} } }],
    })
    renderHook(() => useWebSocketSync(params))
    act(() => { latestWS().onopen?.(new Event("open")) })

    await act(async () => {
      latestWS().onmessage?.(new MessageEvent("message", {
        data: JSON.stringify(pipelineDocumentFrame(document)),
      }))
    })

    expect(useGraphStore.getState().loadGraphSnapshot).toHaveBeenCalledWith(
      expect.objectContaining({ nodes: [expect.objectContaining({ id: "transform_1" })] }),
    )
  })

  it("repeated document updates replace the clean baseline without history", async () => {
    const params = makeHookParams()
    const graphStore = useGraphStore.getState()
    graphStore.undoStack = [{
      nodes: [],
      edges: [],
      preamble: "stale undo",
      submodels: {},
    }]
    graphStore.redoStack = [{
      nodes: [],
      edges: [],
      preamble: "stale redo",
      submodels: {},
    }]
    renderHook(() => useWebSocketSync(params))
    act(() => { latestWS().onopen?.(new Event("open")) })

    for (let i = 0; i < 5; i++) {
      const document = makePipelineEditorDocument({
        source_file: SOURCE_FILE,
        source_revision: "revision-test",
        nodes: [{ ...baseNode, id: `n${i}`, position: { x: i, y: i } }],
      })
      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(document)),
        }))
      })
    }

    expect(graphStore.loadGraphSnapshot).toHaveBeenCalledTimes(5)
    expect(graphStore.undoStack).toEqual([])
    expect(graphStore.redoStack).toEqual([])
  })
})
