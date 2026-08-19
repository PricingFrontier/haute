/**
 * Gap tests for useWebSocketSync — covers scenarios missing from the main test file:
 *
 * 1. fitView delayed call (setTimeout 100ms after pipeline_document_update)
 * 2. Binary/blob messages (non-string event.data)
 * 3. Preamble normalization
 * 4. Multiple rapid pipeline_document_update messages (only last one wins)
 * 5. WebSocket constructor throwing (e.g. invalid URL, blocked by CSP)
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
  const toasts: Array<{ id: string; type: string; text: string }> = []
  let counter = 0
  const store = {
    toasts,
    _toastCounter: counter,
    addToast: vi.fn((type: string, text: string) => {
      counter++
      toasts.push({ id: String(counter), type, text })
    }),
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
  let syncBanner: string | null = null
  const store: Record<string, unknown> = {
    syncBanner,
    setSyncBanner: vi.fn((banner: string | null) => {
      syncBanner = banner
      store.syncBanner = banner
    }),
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

// The hook reads dirty-state from useGraphStore and applies incoming
// documents through loadGraphSnapshot; the mock must provide both or every
// document update rolls back before reaching the behavior under test.
vi.mock("../../stores/useGraphStore.ts", () => {
  const store = {
    dirty: false,
    nodes: [] as unknown[],
    edges: [] as unknown[],
    submodels: {} as Record<string, unknown>,
    preamble: "",
    markSaved: vi.fn(),
    loadGraphSnapshot: vi.fn((snapshot: {
      nodes: unknown[]
      edges: unknown[]
      preamble: string
      submodels: Record<string, unknown>
    }) => {
      store.nodes = snapshot.nodes
      store.edges = snapshot.edges
      store.preamble = snapshot.preamble
      store.submodels = snapshot.submodels
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
import useGraphStore from "../../stores/useGraphStore.ts"
import useToastStore from "../../stores/useToastStore.ts"
import useUIStore from "../../stores/useUIStore.ts"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore.ts"
import { getLayoutedElements } from "../../utils/layout.ts"
import { makePipelineEditorDocument } from "../../testSupport/pipelineDocumentFixture.ts"

// ── WebSocket mock infrastructure ────────────────────────────────

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

function latestWS(): MockWSInstance {
  return mockWSInstances[mockWSInstances.length - 1]
}

function createMockWebSocket() {
  function MockWebSocket(this: MockWSInstance, url: string) {
    this.url = url
    this.onopen = null
    this.onmessage = null
    this.onclose = null
    this.onerror = null
    this.close = vi.fn()
    this.send = vi.fn()
    mockWSInstances.push(this)
  }
  return MockWebSocket
}

const SOURCE_FILE = "rating/main.py"

function makeHookParams(sourceFile = SOURCE_FILE) {
  return {
    submodelsRef: { current: {} },
    preambleRef: { current: "" },
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

function nodeAt(id: string, x: number, y: number, label?: string): Node {
  return {
    id,
    type: "polars",
    position: { x, y },
    data: { label: label ?? id, nodeType: "polars", config: {} },
  }
}

// ── Test suites ──────────────────────────────────────────────────

describe("useWebSocketSync — gap tests", () => {
  let originalWebSocket: typeof globalThis.WebSocket

  beforeEach(() => {
    vi.useFakeTimers()
    mockWSInstances = []
    originalWebSocket = globalThis.WebSocket
    globalThis.WebSocket = createMockWebSocket() as unknown as typeof WebSocket

    vi.mocked(getLayoutedElements).mockImplementation(async (nodes: Node[]) => nodes)
    vi.mocked(useToastStore.getState().addToast).mockClear()
    vi.mocked(useUIStore.getState().setSyncBanner).mockClear()
    vi.mocked(useGraphStore.getState().loadGraphSnapshot).mockClear()
    useDocumentStatusStore.getState().reset()
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    globalThis.WebSocket = originalWebSocket
  })

  // ────────────────────────────────────────────────────────────────
  // 1. fitView delayed call after pipeline_document_update
  // ────────────────────────────────────────────────────────────────

  describe("fitView delayed call", () => {
    it("calls fitView with padding 0.8 after a 100ms delay on document update", async () => {
      // Catches: if someone removes the setTimeout or changes the delay,
      // graph will not fit to view after receiving a file-watcher update,
      // leaving the user looking at an empty canvas.
      const params = makeHookParams()
      const document = makePipelineEditorDocument({
        source_file: SOURCE_FILE,
        source_revision: "revision-test",
        nodes: [nodeAt("n1", 10, 20, "A")],
      })
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(document)),
        }))
      })

      // fitView should NOT have been called yet (it's deferred by 100ms)
      expect(params.fitView).not.toHaveBeenCalled()

      // Advance past the 100ms setTimeout
      act(() => {
        vi.advanceTimersByTime(100)
      })

      expect(params.fitView).toHaveBeenCalledTimes(1)
      expect(params.fitView).toHaveBeenCalledWith({ padding: 0.8 })
    })

    it("does NOT call fitView before 100ms elapses", async () => {
      // Catches: premature fitView call before nodes are rendered by React,
      // which would compute the wrong viewport bounds.
      const params = makeHookParams()
      const document = makePipelineEditorDocument({
        source_file: SOURCE_FILE,
        source_revision: "revision-test",
        nodes: [nodeAt("n1", 10, 20)],
      })
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(document)),
        }))
      })

      act(() => {
        vi.advanceTimersByTime(99)
      })

      expect(params.fitView).not.toHaveBeenCalled()
    })
  })

  // ────────────────────────────────────────────────────────────────
  // 2. Binary/blob messages (non-JSON event.data)
  // ────────────────────────────────────────────────────────────────

  describe("binary / non-JSON messages", () => {
    it("shows error toast when event.data is a non-string (binary blob)", async () => {
      // Catches: if backend accidentally sends binary frames or the proxy
      // corrupts a frame, JSON.parse on a non-string throws. Without the
      // try/catch, this would be an uncaught exception.
      const params = makeHookParams()
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      // Simulate a binary message (ArrayBuffer-like object)
      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: new ArrayBuffer(8),
        }))
      })

      expect(useToastStore.getState().addToast).toHaveBeenCalledWith(
        "error",
        expect.stringContaining("WebSocket sync error:"),
      )
      // Should NOT crash — nodes remain unchanged
      expect(useGraphStore.getState().loadGraphSnapshot).not.toHaveBeenCalled()
    })
  })

  // ────────────────────────────────────────────────────────────────
  // 3. Preamble normalization
  // ────────────────────────────────────────────────────────────────

  describe("preamble handling", () => {
    it("normalizes empty string preamble (document.preamble = '')", async () => {
      // Catches: preamble should normalize to "" so the preamble editor
      // starts clean rather than showing `undefined`.
      const params = makeHookParams()
      const document = makePipelineEditorDocument({
        source_file: SOURCE_FILE,
        source_revision: "revision-test",
        nodes: [nodeAt("n1", 1, 1)],
        preamble: "",
      })
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(document)),
        }))
      })

      expect(params.preambleRef.current).toBe("")
      expect(useGraphStore.getState().loadGraphSnapshot).toHaveBeenLastCalledWith(
        expect.objectContaining({ preamble: "" }),
      )
    })
  })

  // ────────────────────────────────────────────────────────────────
  // 4. Multiple rapid pipeline_document_update messages
  // ────────────────────────────────────────────────────────────────

  describe("multiple rapid document update messages", () => {
    it("processes each document update — last one's nodes win", async () => {
      // Catches: if the hook accumulated state or debounced updates
      // incorrectly, intermediate updates might be dropped or merged
      // wrong, leaving the UI out of sync with the file on disk.
      const params = makeHookParams()
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      const doc1 = makePipelineEditorDocument({
        source_file: SOURCE_FILE,
        source_revision: "revision-test",
        nodes: [nodeAt("n1", 1, 1, "first")],
      })
      const doc2 = makePipelineEditorDocument({
        source_file: SOURCE_FILE,
        source_revision: "revision-test-2",
        nodes: [nodeAt("n1", 10, 10, "second"), nodeAt("n2", 20, 20, "new")],
        edges: [{ id: "e1", source: "n1", target: "n2" }],
      })

      // Fire both messages rapidly (no timer advancement between them)
      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(doc1)),
        }))
      })
      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(doc2)),
        }))
      })

      // Each update applies through the graph store snapshot loader.
      const loadSnapshot = vi.mocked(useGraphStore.getState().loadGraphSnapshot)
      expect(loadSnapshot).toHaveBeenCalledTimes(2)
      // The last call should have the second message's nodes
      const lastCallNodes = loadSnapshot.mock.calls[1][0].nodes as Node[]
      expect(lastCallNodes).toHaveLength(2)
      expect(lastCallNodes[0].data.label).toBe("second")
    })

    it("only runs delayed fitView for the latest rapid document update", async () => {
      // Catches: multiple pending fitView timers should not cause
      // excessive viewport jumps after rapid file-watcher updates.
      const params = makeHookParams()
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      const docFor = (id: string, revision: string) => makePipelineEditorDocument({
        source_file: SOURCE_FILE,
        source_revision: revision,
        nodes: [nodeAt(id, 1, 1)],
      })

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(docFor("a", "revision-a"))),
        }))
      })
      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(docFor("b", "revision-b"))),
        }))
      })

      act(() => {
        vi.advanceTimersByTime(100)
      })

      expect(params.fitView).toHaveBeenCalledTimes(1)
    })

    // NOTE: the legacy "ignores a stale graph_update whose async layout
    // finishes after a newer update" race test is deleted, not ported.
    // `document.nodes[].display_position` is a required, schema-validated
    // finite {x, y} pair (parsePosition in pipelineDocument.ts throws on
    // non-finite coordinates), so `nodeIdsNeedingLayout()` is always empty
    // for a successfully-parsed pipeline_document_update frame and
    // `getLayoutedElements` is never awaited on this path. With no await
    // point in the handler, two document updates can no longer interleave
    // mid-processing — there is nothing left to race. The updateSeq
    // staleness guard itself is still exercised indirectly by "processes
    // each document update — last one's nodes win" above.
  })

  // ────────────────────────────────────────────────────────────────
  // 5. WebSocket constructor throwing
  // ────────────────────────────────────────────────────────────────

  describe("WebSocket constructor throwing", () => {
    it("does not crash when WebSocket constructor throws (e.g. CSP block)", () => {
      // Catches: in restrictive environments (CSP, corporate proxies),
      // `new WebSocket(url)` may throw synchronously. Without a try/catch
      // in the hook, the entire React tree would unmount.
      globalThis.WebSocket = function () {
        throw new Error("CSP blocked WebSocket")
      } as unknown as typeof WebSocket

      const params = makeHookParams()
      const { result } = renderHook(() => useWebSocketSync(params))

      expect(result.current).toBe("disconnected")
      expect(useToastStore.getState().addToast).toHaveBeenCalledWith(
        "error",
        "WebSocket sync error: CSP blocked WebSocket",
      )
    })
  })

  describe("unmount cleanup", () => {
    // NOTE: the legacy "does not apply a graph_update whose async layout
    // resolves after unmount" test is deleted, not ported. It relied on
    // `getLayoutedElements` being invoked and awaited so the message could
    // still be mid-flight at unmount time. As established above,
    // `getLayoutedElements` is unreachable for a successfully-parsed
    // pipeline_document_update frame (display_position is always a
    // schema-validated finite pair), so the document-update handler never
    // suspends on an await — there is no in-flight async gap for unmount to
    // race against.

    it("clears delayed fitView and selection-guard timers on unmount", async () => {
      // Catches: delayed callbacks from a successful sync should not fire
      // after unmount, and the selection guard must not remain stuck on.
      const params = makeHookParams()
      const document = makePipelineEditorDocument({
        source_file: SOURCE_FILE,
        source_revision: "revision-test",
        nodes: [nodeAt("n1", 10, 10)],
      })
      const { unmount } = renderHook(() => useWebSocketSync(params))
      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(document)),
        }))
      })

      expect(params.graphRefreshingRef.current).toBe(1)

      unmount()
      expect(params.graphRefreshingRef.current).toBe(0)

      act(() => {
        vi.advanceTimersByTime(1_000)
      })

      expect(params.fitView).not.toHaveBeenCalled()
      expect(params.graphRefreshingRef.current).toBe(0)
    })
  })
})
