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
  const useToastStore = Object.assign((selector?: (state: typeof store) => unknown) => (selector ? selector(store) : store), {
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
    setChangeFocus: vi.fn(),
    renameDialog: null,
    setRenameDialog: vi.fn(),
    clearBandingRuleStash: vi.fn(),
  }
  const useUIStore = Object.assign((selector?: (state: typeof store) => unknown) => (selector ? selector(store) : store), {
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
    markSaved: vi.fn(() => { store.dirty = false }),
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
  const useGraphStore = Object.assign((selector?: (state: typeof store) => unknown) => (selector ? selector(store) : store), {
    getState: () => store,
    setState: vi.fn(),
    subscribe: vi.fn(),
  })
  return { default: useGraphStore }
})

import useWebSocketSync from "../../hooks/useWebSocketSync.ts"
import { discardCanvasEdits } from "../../hooks/useProjectDirty.ts"
import useToastStore from "../../stores/useToastStore.ts"
import useUIStore from "../../stores/useUIStore.ts"
import useGraphStore from "../../stores/useGraphStore.ts"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore.ts"
import { trackPendingSave } from "../../stores/pendingSaves.ts"
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

describe("useWebSocketSync - WS sync must not corrupt undo history (#8)", () => {
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
          // Each document is a different one, so each carries its own fingerprint.
          data: JSON.stringify(pipelineDocumentFrame(document, `document-fingerprint-${i}`)),
        }))
      })
    }

    expect(graphStore.loadGraphSnapshot).toHaveBeenCalledTimes(5)
    expect(graphStore.undoStack).toEqual([])
    expect(graphStore.redoStack).toEqual([])
  })

  it("drops a frame of the document the canvas already shows", async () => {
    const params = makeHookParams()
    const document = makePipelineEditorDocument({
      source_file: SOURCE_FILE,
      source_revision: "revision-test",
      nodes: [{ ...baseNode, id: "n1" }],
    })
    renderHook(() => useWebSocketSync(params))
    act(() => { latestWS().onopen?.(new Event("open")) })
    const frame = JSON.stringify(pipelineDocumentFrame(document, "fingerprint-a"))

    await act(async () => { latestWS().onmessage?.(new MessageEvent("message", { data: frame })) })
    vi.mocked(useToastStore.getState().addToast).mockClear()
    await act(async () => { latestWS().onmessage?.(new MessageEvent("message", { data: frame })) })

    expect(useGraphStore.getState().loadGraphSnapshot).toHaveBeenCalledTimes(1)
    expect(useToastStore.getState().addToast).not.toHaveBeenCalled()
  })

  it("applies a frame of the document first loaded once a save has replaced it", async () => {
    const params = makeHookParams()
    const document = makePipelineEditorDocument({
      source_file: SOURCE_FILE,
      source_revision: "revision-test",
      nodes: [{ ...baseNode, id: "n1" }],
    })
    renderHook(() => useWebSocketSync(params))
    act(() => { latestWS().onopen?.(new Event("open")) })
    const frame = JSON.stringify(pipelineDocumentFrame(document, "fingerprint-as-loaded"))
    await act(async () => { latestWS().onmessage?.(new MessageEvent("message", { data: frame })) })

    // The canvas is saved as something else, then a switch lands on a branch
    // holding the document first loaded: the canvas must show it.
    act(() => { useDocumentStatusStore.getState().acknowledgeSave("revision-saved", "fingerprint-as-saved") })
    await act(async () => { latestWS().onmessage?.(new MessageEvent("message", { data: frame })) })

    expect(useGraphStore.getState().loadGraphSnapshot).toHaveBeenCalledTimes(2)
  })

  describe("after this tab's save", () => {
    function loadSavedCanvas() {
      const params = makeHookParams()
      const document = makePipelineEditorDocument({
        source_file: SOURCE_FILE,
        source_revision: "revision-test",
        nodes: [{ ...baseNode, id: "n1" }],
      })
      renderHook(() => useWebSocketSync(params))
      act(() => { latestWS().onopen?.(new Event("open")) })
      const saved = makePipelineEditorDocument({
        source_file: SOURCE_FILE,
        source_revision: "revision-saved",
        nodes: [{ ...baseNode, id: "n1" }],
      })
      return { document, saved }
    }

    it("resyncs a reconnect with the saved document's fingerprint and keeps the canvas, its history and its jobs", async () => {
      const { document, saved } = loadSavedCanvas()
      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", { data: JSON.stringify(pipelineDocumentFrame(document, "fingerprint-as-loaded")) }))
      })
      vi.mocked(useGraphStore.getState().loadGraphSnapshot).mockClear()
      vi.mocked(useToastStore.getState().addToast).mockClear()
      act(() => { useDocumentStatusStore.getState().acknowledgeSave("revision-saved", "fingerprint-as-saved") })
      // A replaced document would install a fresh history (the mock's loadGraphSnapshot does).
      const history = useGraphStore.getState().undoStack
      const generation = useDocumentStatusStore.getState().executionGeneration

      // The socket drops (a sleep-wake) and reconnects.
      act(() => { latestWS().onclose?.(new CloseEvent("close", { code: 1006 })) })
      act(() => { vi.advanceTimersByTime(1_000) })
      act(() => { latestWS().onopen?.(new Event("open")) })
      const resync = JSON.parse(latestWS().send.mock.calls[0][0] as string)
      expect(resync).toMatchObject({ type: "resync", source_file: SOURCE_FILE, document_fingerprint: "fingerprint-as-saved" })

      // Any frame of the saved document (another tab's broadcast of it) is the canvas's own.
      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", { data: JSON.stringify(pipelineDocumentFrame(saved, "fingerprint-as-saved")) }))
      })
      expect(useGraphStore.getState().loadGraphSnapshot).not.toHaveBeenCalled()
      expect(useGraphStore.getState().undoStack).toBe(history)
      // The execution generation, which the background jobs are fenced on, is unchanged.
      expect(useDocumentStatusStore.getState().executionGeneration).toBe(generation)
      expect(useToastStore.getState().addToast).not.toHaveBeenCalled()
    })

    it("holds a frame that arrives before the save's own answer, and drops it once the save acknowledges it", async () => {
      const { document, saved } = loadSavedCanvas()
      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", { data: JSON.stringify(pipelineDocumentFrame(document, "fingerprint-as-loaded")) }))
      })
      vi.mocked(useGraphStore.getState().loadGraphSnapshot).mockClear()
      const generation = useDocumentStatusStore.getState().executionGeneration
      let answer!: () => void
      const saving = new Promise<void>((resolve) => { answer = resolve })
      trackPendingSave(SOURCE_FILE, saving)

      // A resync answered while the save is in flight carries what it wrote.
      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", { data: JSON.stringify(pipelineDocumentFrame(saved, "fingerprint-as-saved")) }))
      })
      expect(useDocumentStatusStore.getState().executionGeneration).toBe(generation)
      await act(async () => {
        useDocumentStatusStore.getState().acknowledgeSave("revision-saved", "fingerprint-as-saved")
        answer()
        await saving
      })

      expect(useGraphStore.getState().loadGraphSnapshot).not.toHaveBeenCalled()
      expect(useDocumentStatusStore.getState().executionGeneration).toBe(generation)
    })

    it("drops a frame of the document shown before the save instead of holding it", async () => {
      const { document } = loadSavedCanvas()
      const asLoaded = JSON.stringify(pipelineDocumentFrame(document, "fingerprint-as-loaded"))
      await act(async () => { latestWS().onmessage?.(new MessageEvent("message", { data: asLoaded })) })
      vi.mocked(useGraphStore.getState().loadGraphSnapshot).mockClear()
      let answer!: () => void
      const saving = new Promise<void>((resolve) => { answer = resolve })
      trackPendingSave(SOURCE_FILE, saving)

      // A git action broadcasts the unchanged document while the save is in flight.
      await act(async () => { latestWS().onmessage?.(new MessageEvent("message", { data: asLoaded })) })
      await act(async () => {
        useDocumentStatusStore.getState().acknowledgeSave("revision-saved", "fingerprint-as-saved")
        answer()
        await saving
      })

      expect(useGraphStore.getState().loadGraphSnapshot).not.toHaveBeenCalled()
      expect(useDocumentStatusStore.getState()).toMatchObject({
        sourceRevision: "revision-saved",
        documentFingerprint: "fingerprint-as-saved",
      })
    })

    it("asks for the document on disk instead of adopting a held frame the save's answer superseded", async () => {
      const { document, saved } = loadSavedCanvas()
      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", { data: JSON.stringify(pipelineDocumentFrame(document, "fingerprint-as-loaded")) }))
      })
      vi.mocked(useGraphStore.getState().loadGraphSnapshot).mockClear()
      latestWS().send.mockClear()
      let answer!: () => void
      const saving = new Promise<void>((resolve) => { answer = resolve })
      trackPendingSave(SOURCE_FILE, saving)

      // A resync read what the save wrote before a formatting hook rewrote it.
      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", { data: JSON.stringify(pipelineDocumentFrame(saved, "fingerprint-before-hook")) }))
      })
      // The save answers with the document the hook left.
      await act(async () => {
        useDocumentStatusStore.getState().acknowledgeSave("revision-after-hook", "fingerprint-after-hook")
        answer()
        await saving
      })

      expect(useGraphStore.getState().loadGraphSnapshot).not.toHaveBeenCalled()
      expect(useDocumentStatusStore.getState()).toMatchObject({
        sourceRevision: "revision-after-hook",
        documentFingerprint: "fingerprint-after-hook",
      })
      expect(latestWS().send).toHaveBeenCalledTimes(1)
      expect(JSON.parse(latestWS().send.mock.calls[0][0] as string)).toMatchObject({
        type: "resync",
        source_file: SOURCE_FILE,
        document_fingerprint: "fingerprint-after-hook",
      })
    })

    it("applies a held frame when the save fails", async () => {
      const { document, saved } = loadSavedCanvas()
      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", { data: JSON.stringify(pipelineDocumentFrame(document, "fingerprint-as-loaded")) }))
      })
      vi.mocked(useGraphStore.getState().loadGraphSnapshot).mockClear()
      let refuse!: (error: Error) => void
      const saving = new Promise<void>((_, reject) => { refuse = reject })
      trackPendingSave(SOURCE_FILE, saving)

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", { data: JSON.stringify(pipelineDocumentFrame(saved, "fingerprint-other-writer")) }))
      })
      expect(useGraphStore.getState().loadGraphSnapshot).not.toHaveBeenCalled()
      await act(async () => {
        refuse(new Error("Save rejected"))
        await saving.catch(() => undefined)
      })

      expect(useGraphStore.getState().loadGraphSnapshot).toHaveBeenCalledWith(
        expect.objectContaining({ nodes: [expect.objectContaining({ id: "n1" })] }),
      )
      expect(useDocumentStatusStore.getState().sourceRevision).toBe("revision-saved")
    })
  })

  it("asks for the document without its fingerprint on a resync request, and applies a frame a dirty canvas blocked", async () => {
    const params = makeHookParams()
    const graphStore = useGraphStore.getState()
    const document = makePipelineEditorDocument({
      source_file: SOURCE_FILE,
      source_revision: "revision-test",
      nodes: [{ ...baseNode, id: "destination" }],
    })
    renderHook(() => useWebSocketSync(params))
    act(() => { latestWS().onopen?.(new Event("open")) })
    const frame = JSON.stringify(pipelineDocumentFrame(document, "fingerprint-destination"))

    // A git action's broadcast reaches a canvas with unsaved edits: blocked.
    graphStore.dirty = true
    await act(async () => { latestWS().onmessage?.(new MessageEvent("message", { data: frame })) })
    expect(graphStore.loadGraphSnapshot).not.toHaveBeenCalled()

    // The switch dropped the edits and asked for the document again.
    graphStore.dirty = false
    latestWS().send.mockClear()
    act(() => { useDocumentStatusStore.getState().requestResync() })
    expect(latestWS().send).toHaveBeenCalledTimes(1)
    const request = JSON.parse(latestWS().send.mock.calls[0][0] as string)
    expect(request).toMatchObject({ type: "resync", source_file: SOURCE_FILE })
    expect(request).not.toHaveProperty("document_fingerprint")

    // The same document answers it, and this time it is applied.
    await act(async () => { latestWS().onmessage?.(new MessageEvent("message", { data: frame })) })
    expect(graphStore.loadGraphSnapshot).toHaveBeenCalledWith(
      expect.objectContaining({ nodes: [expect.objectContaining({ id: "destination" })] }),
    )
  })

  it("replaces discarded edits with the destination's document even when it is the one last accepted", async () => {
    const params = makeHookParams()
    const graphStore = useGraphStore.getState()
    const document = makePipelineEditorDocument({
      source_file: SOURCE_FILE,
      source_revision: "revision-test",
      nodes: [{ ...baseNode, id: "n1", data: { label: "As saved", nodeType: "polars", config: {} } }],
    })
    renderHook(() => useWebSocketSync(params))
    act(() => { latestWS().onopen?.(new Event("open")) })
    const frame = JSON.stringify(pipelineDocumentFrame(document, "fingerprint-both-branches"))
    await act(async () => { latestWS().onmessage?.(new MessageEvent("message", { data: frame })) })

    // An unsaved edit, then a switch to a branch with the same document, chosen
    // over the edit.
    graphStore.nodes = [{ ...baseNode, id: "n1", data: { label: "Edited", nodeType: "polars", config: {} } }]
    graphStore.dirty = true
    act(() => { discardCanvasEdits() })
    await act(async () => { latestWS().onmessage?.(new MessageEvent("message", { data: frame })) })

    expect(graphStore.dirty).toBe(false)
    expect(graphStore.nodes).toEqual([
      expect.objectContaining({ id: "n1", data: expect.objectContaining({ label: "As saved" }) }),
    ])
  })

  it("retains an exact dirty column configuration and its history when a watcher update arrives", async () => {
    const params = makeHookParams()
    const graphStore = useGraphStore.getState()
    const local = {
      ...baseNode,
      id: "local-columns",
      data: {
        label: "Local columns", nodeType: "polars",
        config: { selected_columns: ["city"], column_renames: { city: "Municipality" } },
      },
    }
    graphStore.dirty = true
    graphStore.nodes = [local]
    graphStore.undoStack = [{ nodes: [baseNode], edges: [], preamble: "", submodels: {} }]
    graphStore.redoStack = [{ nodes: [], edges: [], preamble: "", submodels: {} }]
    const originalNodes = structuredClone(graphStore.nodes)
    const originalUndo = structuredClone(graphStore.undoStack)
    const originalRedo = structuredClone(graphStore.redoStack)
    renderHook(() => useWebSocketSync(params))
    act(() => { latestWS().onopen?.(new Event("open")) })
    const incoming = makePipelineEditorDocument({
      source_file: SOURCE_FILE,
      source_revision: "revision-external",
      nodes: [{ ...baseNode, id: "disk-columns", data: { label: "Disk", nodeType: "polars", config: { selected_columns: ["disk"] } } }],
    })
    await act(async () => {
      latestWS().onmessage?.(new MessageEvent("message", { data: JSON.stringify(pipelineDocumentFrame(incoming, "external-columns")) }))
    })
    expect(graphStore.loadGraphSnapshot).not.toHaveBeenCalled()
    expect(graphStore.nodes).toEqual(originalNodes)
    expect(graphStore.undoStack).toEqual(originalUndo)
    expect(graphStore.redoStack).toEqual(originalRedo)
    expect(useDocumentStatusStore.getState().graphSynchronized).toBe(false)
    expect(useUIStore.getState().setSyncBanner).toHaveBeenCalledWith(expect.stringContaining("unsaved changes"))
  })
})
