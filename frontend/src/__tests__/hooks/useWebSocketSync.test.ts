/**
 * Tests for useWebSocketSync — WebSocket connection lifecycle, message handling,
 * reconnection with exponential backoff, error handling, and cleanup on unmount.
 *
 * Mocks the global WebSocket class and uses vi.useFakeTimers() to control
 * reconnection delays.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act, cleanup } from "@testing-library/react"
import { type Mock } from "vitest"
import type { Node } from "@xyflow/react"

// ── Mock dependencies BEFORE importing the hook ──────────────────

// Mock getLayoutedElements — called when graph updates have missing/non-finite positions
vi.mock("../../utils/layout.ts", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../utils/layout.ts")>(),
  getLayoutedElements: vi.fn(async (nodes: Node[]) =>
    nodes.map((node, index) => ({
      ...node,
      position: { x: index * 300, y: 0 },
    })),
  ),
}))

// Mock the stores — we need to inspect and control their state
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
    renameDialog: null,
    setRenameDialog: vi.fn((dialog: { nodeId: string; currentLabel: string } | null) => {
      store.renameDialog = dialog
    }),
    submodelDialog: null,
    setSubmodelDialog: vi.fn((dialog: { nodeIds: string[] } | null) => {
      store.submodelDialog = dialog
    }),
    // Other fields the hook destructures
    setPaletteOpen: vi.fn(),
    setShortcutsOpen: vi.fn(),
  }
  const useUIStore = Object.assign(() => store, {
    getState: () => store,
    setState: vi.fn(),
    subscribe: vi.fn(),
  })
  return { default: useUIStore }
})

// Wave 7E: dirty tracking moved from useUIStore to useGraphStore.
vi.mock("../../stores/useGraphStore.ts", () => {
  const store = {
    dirty: false,
    nodes: [] as Node[],
    edges: [] as import("@xyflow/react").Edge[],
    preamble: "",
    submodels: {} as Record<string, unknown>,
    loadGraphSnapshot: vi.fn((snapshot: {
      nodes: Node[]
      edges: import("@xyflow/react").Edge[]
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
import useToastStore from "../../stores/useToastStore.ts"
import useUIStore from "../../stores/useUIStore.ts"
import useGraphStore from "../../stores/useGraphStore.ts"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore.ts"
import { makePipelineEditorDocument } from "../../testSupport/pipelineDocumentFixture.ts"
import { HAUTE_SESSION_EXPIRED_EVENT } from "../../api/client.ts"

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

/**
 * Get the most recently created WebSocket mock instance.
 */
function latestWS(): MockWSInstance {
  return mockWSInstances[mockWSInstances.length - 1]
}

function createMockWebSocket() {
  // Must use a real function (not arrow) so `new` works correctly
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

// ── Shared params for the hook ───────────────────────────────────

function makeHookParams(sourceFile = "") {
  return {
    setNodesRaw: vi.fn(),
    setEdgesRaw: vi.fn(),
    setSubmodelsRaw: vi.fn(),
    setPreamble: vi.fn(),
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

// ── Test suites ──────────────────────────────────────────────────

describe("useWebSocketSync", () => {
  let originalWebSocket: typeof globalThis.WebSocket

  beforeEach(() => {
    vi.useFakeTimers()
    mockWSInstances = []
    originalWebSocket = globalThis.WebSocket
    globalThis.WebSocket = createMockWebSocket() as unknown as typeof WebSocket

    // Reset mock state
    vi.mocked(useToastStore.getState().addToast).mockClear()
    vi.mocked(useUIStore.getState().setSyncBanner).mockClear()
    vi.mocked(useUIStore.getState().setRenameDialog).mockClear()
    vi.mocked(useUIStore.getState().setSubmodelDialog).mockClear()
    useUIStore.getState().renameDialog = null
    useUIStore.getState().submodelDialog = null
    vi.mocked(useGraphStore.getState().loadGraphSnapshot).mockClear()
    useGraphStore.getState().dirty = false
    useGraphStore.getState().nodes = []
    useGraphStore.getState().edges = []
    useGraphStore.getState().preamble = ""
    useGraphStore.getState().submodels = {}
    useDocumentStatusStore.getState().reset()
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    globalThis.WebSocket = originalWebSocket
  })

  // ────────────────────────────────────────────────────────────────
  // Connection establishment
  // ────────────────────────────────────────────────────────────────

  describe("connection establishment", () => {
    it("does not create a WebSocket connection when disabled", () => {
      const params = makeHookParams()
      const { result } = renderHook(() => useWebSocketSync({ ...params, enabled: false }))

      expect(mockWSInstances).toHaveLength(0)
      expect(result.current).toBe("disconnected")
    })

    it("creates a WebSocket connection when enabled after an initially disabled mount", () => {
      const params = makeHookParams()
      const { rerender } = renderHook(
        ({ enabled }) => useWebSocketSync({ ...params, enabled }),
        { initialProps: { enabled: false } },
      )

      expect(mockWSInstances).toHaveLength(0)

      rerender({ enabled: true })

      expect(mockWSInstances).toHaveLength(1)
      expect(latestWS().url).toBe("ws://localhost:3000/ws/sync")
    })

    it("closes the active WebSocket and cancels reconnects when disabled after connecting", () => {
      const params = makeHookParams()
      const { rerender } = renderHook(
        ({ enabled }) => useWebSocketSync({ ...params, enabled }),
        { initialProps: { enabled: true } },
      )
      const ws = latestWS()

      act(() => {
        ws.onclose?.({} as CloseEvent)
      })
      expect(mockWSInstances).toHaveLength(1)

      rerender({ enabled: false })

      expect(ws.close).toHaveBeenCalled()
      act(() => {
        vi.advanceTimersByTime(5_000)
      })
      expect(mockWSInstances).toHaveLength(1)
    })

    it("creates a WebSocket connection on mount", () => {
      const params = makeHookParams()
      renderHook(() => useWebSocketSync(params))

      expect(mockWSInstances).toHaveLength(1)
      expect(latestWS().url).toBe("ws://localhost:3000/ws/sync")
    })

    it("never places local-session credentials in the WebSocket URL", () => {
      const params = makeHookParams()

      renderHook(() => useWebSocketSync(params))

      expect(latestWS().url).toBe("ws://localhost:3000/ws/sync")
      expect(latestWS().url).not.toContain("?")
    })

    it("sets status to connected when onopen fires", () => {
      const params = makeHookParams()
      const { result } = renderHook(() => useWebSocketSync(params))

      // Initially should be "reconnecting" (the initial useState default)
      expect(result.current).toBe("reconnecting")

      // Simulate WebSocket opening
      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      expect(result.current).toBe("connected")
    })

    it("surfaces a WebSocket constructor failure and stops reconnecting", () => {
      function ThrowingWebSocket() {
        throw new Error("constructor boom")
      }
      globalThis.WebSocket = ThrowingWebSocket as unknown as typeof WebSocket

      const params = makeHookParams()
      const { result } = renderHook(() => useWebSocketSync(params))

      expect(result.current).toBe("disconnected")
      expect(useToastStore.getState().addToast).toHaveBeenCalledWith(
        "error",
        "WebSocket sync error: constructor boom",
      )
      expect(mockWSInstances).toHaveLength(0)
    })

    it("requests a current-source resync when the socket opens", () => {
      const params = makeHookParams("rating/main.py")
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      expect(latestWS().send).toHaveBeenCalledWith(JSON.stringify({
        type: "resync",
        source_file: "rating/main.py",
        document_schema_version: 1,
      }))
    })

    it("does not request reconnect resync when the current source file is blank", () => {
      const params = makeHookParams()
      params.sourceFileRef.current = "   "
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      expect(latestWS().send).not.toHaveBeenCalled()
    })

    it("keeps the socket connected and reports a failed reconnect resync send", () => {
      const params = makeHookParams("rating/main.py")
      renderHook(() => useWebSocketSync(params))
      latestWS().send.mockImplementation(() => {
        throw new Error("send boom")
      })

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      expect(useToastStore.getState().addToast).toHaveBeenCalledWith(
        "error",
        "WebSocket sync error: send boom",
      )
    })

    it("uses wss: protocol when page is served over https", () => {
      // Override window.location.protocol for this test
      const originalProtocol = window.location.protocol
      Object.defineProperty(window, "location", {
        value: { ...window.location, protocol: "https:", host: "example.com" },
        writable: true,
      })

      const params = makeHookParams()
      renderHook(() => useWebSocketSync(params))

      expect(latestWS().url).toBe("wss://example.com/ws/sync")

      // Restore
      Object.defineProperty(window, "location", {
        value: { ...window.location, protocol: originalProtocol, host: "localhost:3000" },
        writable: true,
      })
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Message handling — pipeline document update
  // ────────────────────────────────────────────────────────────────

  describe("pipeline document update messages", () => {
    const readyNode: Node = {
      id: "disk",
      type: "polars",
      position: { x: 100, y: 200 },
      data: { label: "Disk", nodeType: "polars", config: {} },
    }

    it("atomically applies a clean degraded document and its authoritative fence", async () => {
      const params = makeHookParams("rating/main.py")
      useDocumentStatusStore.getState().loadDocumentStatus(makePipelineEditorDocument({
        load_status: "ready",
        source_file: "rating/main.py",
        source_revision: "r1",
        nodes: [readyNode],
      }))
      const degraded = makePipelineEditorDocument({
        load_status: "degraded",
        source_file: "rating/main.py",
        source_revision: "r2",
        nodes: [readyNode],
        capabilities: { can_preview: true },
      })
      renderHook(() => useWebSocketSync(params))

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(degraded)),
        }))
      })

      expect(useDocumentStatusStore.getState()).toMatchObject({
        loadStatus: "degraded",
        sourceRevision: "r2",
        capabilities: { can_mutate: false, can_execute: false, can_preview: true },
        graphSynchronized: true,
      })
      expect(params.sourceRevisionRef.current).toBe("r2")
      expect(useGraphStore.getState().loadGraphSnapshot).toHaveBeenCalledOnce()
      expect(useGraphStore.getState().nodes).toHaveLength(1)
    })

    it("ignores a document update for another source", async () => {
      const params = makeHookParams("rating/main.py")
      const foreign = makePipelineEditorDocument({
        source_file: "rating/other.py",
        source_revision: "r2",
        nodes: [readyNode],
      })
      renderHook(() => useWebSocketSync(params))

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(foreign)),
        }))
      })

      expect(useGraphStore.getState().loadGraphSnapshot).not.toHaveBeenCalled()
      expect(params.sourceRevisionRef.current).toBe("revision-test")
    })

    it("retains dirty local work when the source falls back to source-only", async () => {
      const params = makeHookParams("rating/main.py")
      useDocumentStatusStore.getState().loadDocumentStatus(makePipelineEditorDocument({
        source_file: "rating/main.py",
        source_revision: "r1",
        nodes: [readyNode],
      }))
      useGraphStore.getState().nodes = [{ ...readyNode, id: "local-edit" }]
      useGraphStore.getState().dirty = true
      const sourceOnly = makePipelineEditorDocument({
        load_status: "source_only",
        source_file: "rating/main.py",
        source_revision: "r2",
        source_text: "unrecoverable source",
        nodes: [],
      })
      renderHook(() => useWebSocketSync(params))

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(sourceOnly)),
        }))
      })

      expect(useGraphStore.getState().nodes.map((node) => node.id)).toEqual(["local-edit"])
      expect(useDocumentStatusStore.getState().retainedCanvas?.kind).toBe("local_dirty")
      expect(useUIStore.getState().setSyncBanner).toHaveBeenCalledWith(
        expect.stringContaining("unsaved changes"),
      )
    })

    it("closes dialogs for removed nodes and warns while retaining an unresolved edge", async () => {
      const params = makeHookParams("rating/main.py")
      const targetNode: Node = {
        ...readyNode,
        id: "target",
        data: { label: "Target", nodeType: "polars", config: {} },
      }
      useUIStore.getState().renameDialog = { nodeId: "removed", currentLabel: "Removed" }
      useUIStore.getState().submodelDialog = { nodeIds: ["disk", "removed"] }
      const document = makePipelineEditorDocument({
        source_file: "rating/main.py",
        source_revision: "r2",
        nodes: [readyNode, targetNode],
        edges: [{
          id: "unresolved-handle",
          source: "disk",
          target: "target",
          sourceHandle: "missing-output",
        }],
      })
      renderHook(() => useWebSocketSync(params))

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(document)),
        }))
      })

      expect(useUIStore.getState().setRenameDialog).toHaveBeenCalledWith(null)
      expect(useUIStore.getState().setSubmodelDialog).toHaveBeenCalledWith(null)
      expect(useGraphStore.getState().edges).toHaveLength(1)
      expect(useToastStore.getState().addToast).toHaveBeenCalledWith(
        "warning",
        expect.stringContaining("unresolved synced edge"),
      )
    })

    it("accepts synced submodel output handles from the canonical definition registry", async () => {
      const params = makeHookParams("rating/main.py")
      const sourceNode: Node = {
        id: "submodel_instance_inputs",
        type: "submodel",
        position: { x: 0, y: 0 },
        data: {
          label: "Inputs",
          nodeType: "submodel",
          config: { definitionId: "Inputs", alias: "Inputs" },
        },
      }
      const targetNode: Node = {
        ...readyNode,
        id: "Polars_3",
        data: { label: "Polars 3", nodeType: "polars", config: {} },
      }
      const document = makePipelineEditorDocument({
        source_file: "rating/main.py",
        source_revision: "r2",
        nodes: [sourceNode, targetNode],
        edges: [{
          id: "submodel-output",
          source: sourceNode.id,
          target: targetNode.id,
          sourceHandle: "out__output_1",
        }],
        submodels: {
          Inputs: {
            definitionId: "Inputs",
            file: "modules/Inputs.py",
            graph: { nodes: [], edges: [] },
            inputPorts: [],
            outputPorts: [{
              name: "output_1",
              source: { nodeId: "live_switch", handleId: null },
            }],
          },
        },
      })
      renderHook(() => useWebSocketSync(params))

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(document)),
        }))
      })

      expect(useGraphStore.getState().edges).toHaveLength(1)
      expect(useToastStore.getState().addToast).not.toHaveBeenCalledWith(
        "warning",
        expect.stringContaining("unresolved synced edge"),
      )
    })

    it("rolls request mirrors back when the graph snapshot cannot be applied", async () => {
      const params = makeHookParams("rating/main.py")
      params.preservedBlocksRef.current = ["old block"]
      params.preambleRef.current = "old preamble"
      params.submodelsRef.current = { old: { definitionId: "old" } }
      vi.mocked(useGraphStore.getState().loadGraphSnapshot).mockImplementationOnce(() => {
        throw new Error("snapshot store failed")
      })
      const document = makePipelineEditorDocument({
        source_file: "rating/main.py",
        source_revision: "r2",
        preamble: "new preamble",
        preserved_blocks: ["new block"],
        nodes: [readyNode],
      })
      renderHook(() => useWebSocketSync(params))

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(document)),
        }))
      })

      expect(params.preservedBlocksRef.current).toEqual(["old block"])
      expect(params.preambleRef.current).toBe("old preamble")
      expect(params.submodelsRef.current).toEqual({ old: { definitionId: "old" } })
      expect(useToastStore.getState().addToast).toHaveBeenCalledWith(
        "error",
        expect.stringContaining("snapshot store failed"),
      )
    })

    it("applies a degraded status fence without replacing a dirty local graph", async () => {
      const params = makeHookParams("rating/main.py")
      useDocumentStatusStore.getState().loadDocumentStatus(makePipelineEditorDocument({
        load_status: "ready",
        source_file: "rating/main.py",
        source_revision: "r1",
        nodes: [readyNode],
      }))
      useGraphStore.getState().nodes = [{ ...readyNode, id: "local-edit" }]
      useGraphStore.getState().dirty = true
      const degraded = makePipelineEditorDocument({
        load_status: "degraded",
        source_file: "rating/main.py",
        source_revision: "r2",
        nodes: [readyNode],
      })
      renderHook(() => useWebSocketSync(params))

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(degraded)),
        }))
      })

      expect(useDocumentStatusStore.getState()).toMatchObject({
        loadStatus: "degraded",
        sourceRevision: "r2",
        capabilities: { can_save: false, can_execute: false },
        graphSynchronized: false,
      })
      expect(params.sourceRevisionRef.current).toBe("r2")
      expect(useGraphStore.getState().loadGraphSnapshot).not.toHaveBeenCalled()
      expect(useGraphStore.getState().nodes.map((node) => node.id)).toEqual(["local-edit"])
      expect(useUIStore.getState().setSyncBanner).toHaveBeenCalledWith(
        expect.stringContaining("unsaved changes"),
      )
    })

    it("keeps a dirty retained graph unsynchronized even when the new disk document is ready", async () => {
      const params = makeHookParams("rating/main.py")
      useDocumentStatusStore.getState().loadDocumentStatus(makePipelineEditorDocument({
        load_status: "ready",
        source_file: "rating/main.py",
        source_revision: "r1",
        nodes: [readyNode],
      }))
      useGraphStore.getState().nodes = [{ ...readyNode, id: "local-edit" }]
      useGraphStore.getState().dirty = true
      const ready = makePipelineEditorDocument({
        load_status: "ready",
        source_file: "rating/main.py",
        source_revision: "r2",
        nodes: [readyNode],
      })
      renderHook(() => useWebSocketSync(params))

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(ready)),
        }))
      })

      expect(useDocumentStatusStore.getState()).toMatchObject({
        loadStatus: "ready",
        sourceRevision: "r2",
        capabilities: { can_save: true, can_execute: true },
        graphSynchronized: false,
      })
      expect(useGraphStore.getState().nodes.map((node) => node.id)).toEqual(["local-edit"])
      expect(useGraphStore.getState().loadGraphSnapshot).not.toHaveBeenCalled()
    })

    it("retains a clean last-renderable canvas across repeated source-only updates", async () => {
      const params = makeHookParams("rating/main.py")
      useDocumentStatusStore.getState().loadDocumentStatus(makePipelineEditorDocument({
        load_status: "ready",
        source_file: "rating/main.py",
        source_revision: "r1",
        nodes: [readyNode],
      }))
      useGraphStore.getState().nodes = [readyNode]
      const sourceOnly = makePipelineEditorDocument({
        load_status: "source_only",
        source_file: "rating/main.py",
        source_revision: "r2",
        source_text: "this is not recoverable Python",
        nodes: [],
      })
      renderHook(() => useWebSocketSync(params))

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(sourceOnly)),
        }))
      })

      expect(useGraphStore.getState().loadGraphSnapshot).not.toHaveBeenCalled()
      expect(useGraphStore.getState().nodes.map((node) => node.id)).toEqual(["disk"])
      expect(useDocumentStatusStore.getState()).toMatchObject({
        loadStatus: "source_only",
        sourceRevision: "r2",
        sourceText: "this is not recoverable Python",
        retainedCanvas: {
          kind: "last_renderable",
          sourceRevision: "r1",
          loadStatus: "ready",
        },
        graphSynchronized: false,
      })

      const laterSourceOnly = {
        ...sourceOnly,
        source_revision: "r3",
        source_text: "this is still not recoverable Python",
      }
      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(
            laterSourceOnly,
            "document-fingerprint-r3",
          )),
        }))
      })

      expect(useDocumentStatusStore.getState()).toMatchObject({
        loadStatus: "source_only",
        sourceRevision: "r3",
        sourceText: "this is still not recoverable Python",
        retainedCanvas: {
          kind: "last_renderable",
          sourceRevision: "r1",
          loadStatus: "ready",
        },
        graphSynchronized: false,
      })
    })

    it("keeps a fresh source-only session on the source surface without a stale canvas", async () => {
      const params = makeHookParams("rating/main.py")
      const sourceOnly = makePipelineEditorDocument({
        load_status: "source_only",
        source_file: "rating/main.py",
        source_revision: "r2",
        source_text: "broken",
        nodes: [],
      })
      renderHook(() => useWebSocketSync(params))

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(sourceOnly)),
        }))
      })

      expect(useDocumentStatusStore.getState().retainedCanvas).toBeNull()
      expect(useGraphStore.getState().loadGraphSnapshot).not.toHaveBeenCalled()
    })

    it("surfaces a document system failure and clears it on repair", async () => {
      const params = makeHookParams("rating/main.py")
      const ready = makePipelineEditorDocument({
        load_status: "ready",
        source_file: "rating/main.py",
        source_revision: "r2",
        nodes: [readyNode],
      })
      renderHook(() => useWebSocketSync(params))

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(ready)),
        }))
      })
      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify({
            type: "parse_error",
            error: "Pipeline document could not be loaded. Check the server logs for details.",
            source_file: "rating/main.py",
          }),
        }))
      })

      expect(useDocumentStatusStore.getState()).toMatchObject({
        systemFailure: "Pipeline document could not be loaded. Check the server logs for details.",
        graphSynchronized: false,
      })

      const repaired = makePipelineEditorDocument({
        load_status: "ready",
        source_file: "rating/main.py",
        source_revision: "r3",
        nodes: [readyNode],
      })
      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(repaired, "repaired-fingerprint")),
        }))
      })

      expect(useDocumentStatusStore.getState()).toMatchObject({
        systemFailure: null,
        sourceRevision: "r3",
        graphSynchronized: true,
      })
    })

    it("rejects a malformed document frame before mutating status or graph state", async () => {
      const params = makeHookParams("rating/main.py")
      useDocumentStatusStore.getState().loadDocumentStatus(makePipelineEditorDocument({
        load_status: "ready",
        source_file: "rating/main.py",
        source_revision: "r1",
      }))
      renderHook(() => useWebSocketSync(params))
      const malformed = pipelineDocumentFrame(makePipelineEditorDocument({
        load_status: "degraded",
        source_file: "rating/main.py",
        source_revision: "r2",
      })) as Record<string, unknown>
      malformed.unexpected = true

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(malformed),
        }))
      })

      expect(useDocumentStatusStore.getState()).toMatchObject({
        loadStatus: "ready",
        sourceRevision: "r1",
      })
      expect(useGraphStore.getState().loadGraphSnapshot).not.toHaveBeenCalled()
      expect(useToastStore.getState().addToast).toHaveBeenCalledWith(
        "error",
        expect.stringContaining("unexpected frame fields"),
      )
    })

    it("rejects each invalid document-envelope identity field", async () => {
      const params = makeHookParams("rating/main.py")
      const document = makePipelineEditorDocument({
        source_file: "rating/main.py",
        source_revision: "r2",
        nodes: [readyNode],
      })
      const validFrame = pipelineDocumentFrame(document)
      const cases: Array<{ frame: Record<string, unknown>; error: string }> = [
        { frame: { ...validFrame, schema_version: 2 }, error: "unsupported schema_version" },
        { frame: { ...validFrame, document_fingerprint: " " }, error: "missing document_fingerprint" },
        { frame: { ...validFrame, source_file: " " }, error: "missing source_file" },
        {
          frame: {
            ...validFrame,
            document: { ...document, source_revision: null },
          },
          error: "document is missing source_revision",
        },
        {
          frame: { ...validFrame, source_file: "rating/other.py" },
          error: "envelope and document source_file differ",
        },
      ]
      renderHook(() => useWebSocketSync(params))

      for (const testCase of cases) {
        await act(async () => {
          latestWS().onmessage?.(new MessageEvent("message", {
            data: JSON.stringify(testCase.frame),
          }))
        })
        expect(useToastStore.getState().addToast).toHaveBeenLastCalledWith(
          "error",
          expect.stringContaining(testCase.error),
        )
      }

      expect(useGraphStore.getState().loadGraphSnapshot).not.toHaveBeenCalled()
    })

    it("silently ignores frames with an unknown or missing type", async () => {
      const params = makeHookParams("rating/main.py")
      useDocumentStatusStore.getState().loadDocumentStatus(makePipelineEditorDocument({
        load_status: "ready",
        source_file: "rating/main.py",
        source_revision: "r1",
      }))
      renderHook(() => useWebSocketSync(params))

      const unknownFrames = [
        { document: { nodes: [] }, source_file: "rating/main.py" },
        { type: "totally_unknown", payload: { nodes: [] }, source_file: "rating/main.py" },
      ]
      for (const frame of unknownFrames) {
        await act(async () => {
          latestWS().onmessage?.(new MessageEvent("message", {
            data: JSON.stringify(frame),
          }))
        })
      }

      expect(useToastStore.getState().addToast).not.toHaveBeenCalled()
      expect(useGraphStore.getState().loadGraphSnapshot).not.toHaveBeenCalled()
      expect(useDocumentStatusStore.getState()).toMatchObject({
        loadStatus: "ready",
        sourceRevision: "r1",
        systemFailure: null,
      })
    })

    it("ignores a parse error for another source", async () => {
      const params = makeHookParams("rating/main.py")
      renderHook(() => useWebSocketSync(params))

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify({
            type: "parse_error",
            error: "foreign failure",
            source_file: "rating/other.py",
          }),
        }))
      })

      expect(useDocumentStatusStore.getState().systemFailure).toBeNull()
    })

    it("resyncs by whole-document fingerprint after the new protocol is applied", async () => {
      const params = makeHookParams("rating/main.py")
      const document = makePipelineEditorDocument({
        source_file: "rating/main.py",
        source_revision: "r2",
        nodes: [readyNode],
      })
      renderHook(() => useWebSocketSync(params))
      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(document, "doc-fp")),
        }))
      })
      act(() => latestWS().onclose?.({} as CloseEvent))
      act(() => vi.advanceTimersByTime(1000))
      act(() => latestWS().onopen?.(new Event("open")))

      expect(latestWS().send).toHaveBeenCalledWith(JSON.stringify({
        type: "resync",
        source_file: "rating/main.py",
        document_schema_version: 1,
        document_fingerprint: "doc-fp",
      }))
    })

    it("does not match source_file paths by lowercasing case-twin names", async () => {
      const params = makeHookParams("modules/Main.py")
      useDocumentStatusStore.getState().loadDocumentStatus(makePipelineEditorDocument({
        source_file: "modules/Main.py",
        source_revision: "r1",
        nodes: [readyNode],
      }))
      const wrongCase = makePipelineEditorDocument({
        source_file: "modules/main.py",
        source_revision: "r2",
        nodes: [{ ...readyNode, id: "wrong-case" }],
      })
      renderHook(() => useWebSocketSync(params))

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(wrongCase)),
        }))
      })

      expect(useGraphStore.getState().loadGraphSnapshot).not.toHaveBeenCalled()
      expect(useDocumentStatusStore.getState().loadStatus).toBe("ready")
      expect(useDocumentStatusStore.getState().sourceRevision).toBe("r1")
    })

    it("accepts a document update for the current source_file even when one side is absolute", async () => {
      const params = makeHookParams("rating/main.py")
      const document = makePipelineEditorDocument({
        source_file: "C:\\Users\\prici\\haute\\rating\\main.py",
        source_revision: "r2",
        nodes: [readyNode],
      })
      renderHook(() => useWebSocketSync(params))

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(document)),
        }))
      })

      expect(useGraphStore.getState().loadGraphSnapshot).toHaveBeenCalledWith(
        expect.objectContaining({
          nodes: [expect.objectContaining({ id: "disk" })],
        }),
      )
    })

    it("accepts a document update when the current source_file is absolute and the frame is relative", async () => {
      const params = makeHookParams("C:\\Users\\prici\\haute\\rating\\main.py")
      const document = makePipelineEditorDocument({
        source_file: "rating/main.py",
        source_revision: "r2",
        nodes: [readyNode],
      })
      renderHook(() => useWebSocketSync(params))

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(document)),
        }))
      })

      expect(useGraphStore.getState().loadGraphSnapshot).toHaveBeenCalledWith(
        expect.objectContaining({
          nodes: [expect.objectContaining({ id: "disk" })],
        }),
      )
    })

    it("rejects same-basename document frames when the current source is ambiguous", async () => {
      const params = makeHookParams("main.py")
      useDocumentStatusStore.getState().loadDocumentStatus(makePipelineEditorDocument({
        source_file: "main.py",
        source_revision: "r1",
        nodes: [readyNode],
      }))
      const document = makePipelineEditorDocument({
        source_file: "C:\\Users\\prici\\haute\\rating\\main.py",
        source_revision: "r2",
        nodes: [readyNode],
      })
      renderHook(() => useWebSocketSync(params))

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(pipelineDocumentFrame(document)),
        }))
      })

      expect(useGraphStore.getState().loadGraphSnapshot).not.toHaveBeenCalled()
      expect(useDocumentStatusStore.getState().loadStatus).toBe("ready")
      expect(useDocumentStatusStore.getState().sourceRevision).toBe("r1")
    })

    it("uses default error message when parse_error has no error field", async () => {
      const params = makeHookParams("rating/main.py")
      renderHook(() => useWebSocketSync(params))

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify({
            type: "parse_error",
            source_file: "rating/main.py",
          }),
        }))
      })

      expect(useDocumentStatusStore.getState().systemFailure).toBe(
        "Pipeline document could not be loaded.",
      )
      expect(useUIStore.getState().setSyncBanner).toHaveBeenLastCalledWith(null)
    })

    it("routes parse_error text to the system failure state, not the banner", async () => {
      const params = makeHookParams("rating/main.py")
      renderHook(() => useWebSocketSync(params))

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify({
            type: "parse_error",
            source_file: "rating/main.py",
            error: "boom",
          }),
        }))
      })

      expect(useDocumentStatusStore.getState().systemFailure).toBe("boom")
      expect(useUIStore.getState().setSyncBanner).toHaveBeenLastCalledWith(null)
    })
  })


  // ────────────────────────────────────────────────────────────────
  // JSON parse error handling
  // ────────────────────────────────────────────────────────────────

  describe("JSON parse error handling", () => {
    it("shows toast on malformed JSON message", async () => {
      const params = makeHookParams()
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: "this is not valid JSON{{{",
        }))
      })

      expect(useToastStore.getState().addToast).toHaveBeenCalledWith(
        "error",
        expect.stringContaining("WebSocket sync error:"),
      )
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Reconnection on close
  // ────────────────────────────────────────────────────────────────

  describe("reconnection on close", () => {
    it("reconnects with exponential backoff on close", () => {
      const params = makeHookParams()
      const { result } = renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })
      expect(result.current).toBe("connected")

      // Simulate close
      act(() => {
        latestWS().onclose?.({} as CloseEvent)
      })
      expect(result.current).toBe("reconnecting")

      // Only the initial WS so far
      expect(mockWSInstances).toHaveLength(1)

      // After 1s (INITIAL_BACKOFF_MS * 2^0), reconnection should fire
      act(() => {
        vi.advanceTimersByTime(1000)
      })

      expect(mockWSInstances).toHaveLength(2)
    })

    it("increases backoff delay on consecutive closes", () => {
      const params = makeHookParams()
      renderHook(() => useWebSocketSync(params))

      // First close → reconnect after 1s (1000 * 2^0)
      act(() => {
        latestWS().onclose?.({} as CloseEvent)
      })
      act(() => {
        vi.advanceTimersByTime(1000)
      })
      expect(mockWSInstances).toHaveLength(2)

      // Second close → reconnect after 2s (1000 * 2^1)
      act(() => {
        latestWS().onclose?.({} as CloseEvent)
      })
      act(() => {
        vi.advanceTimersByTime(1500)
      })
      // Should NOT have reconnected yet at 1.5s
      expect(mockWSInstances).toHaveLength(2)
      act(() => {
        vi.advanceTimersByTime(500)
      })
      // Now at 2s total, should have reconnected
      expect(mockWSInstances).toHaveLength(3)
    })

    it("caps backoff at MAX_BACKOFF_MS (30s)", () => {
      const params = makeHookParams()
      renderHook(() => useWebSocketSync(params))

      // Simulate many closes to push backoff past cap
      // After 15 closes: 1000 * 2^14 = 16384000 ms → capped at 30000
      for (let i = 0; i < 16; i++) {
        act(() => {
          latestWS().onclose?.({} as CloseEvent)
        })
        act(() => {
          vi.advanceTimersByTime(30_000)
        })
      }

      const instancesBefore = mockWSInstances.length

      // Next close — backoff should be capped at 30s
      act(() => {
        latestWS().onclose?.({} as CloseEvent)
      })
      // Should NOT reconnect at 29s
      act(() => {
        vi.advanceTimersByTime(29_000)
      })
      expect(mockWSInstances.length).toBe(instancesBefore)
      // Should reconnect at 30s
      act(() => {
        vi.advanceTimersByTime(1_000)
      })
      expect(mockWSInstances.length).toBe(instancesBefore + 1)
    })

    it("resets retry counter on successful connection", () => {
      const params = makeHookParams()
      renderHook(() => useWebSocketSync(params))

      // First close → reconnect at 1s
      act(() => {
        latestWS().onclose?.({} as CloseEvent)
      })
      act(() => {
        vi.advanceTimersByTime(1000)
      })

      // Second close → reconnect at 2s
      act(() => {
        latestWS().onclose?.({} as CloseEvent)
      })
      act(() => {
        vi.advanceTimersByTime(2000)
      })

      // Now connect successfully
      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      const countBeforeClose = mockWSInstances.length

      // Close again — backoff should be reset to 1s (not 4s)
      act(() => {
        latestWS().onclose?.({} as CloseEvent)
      })
      act(() => {
        vi.advanceTimersByTime(1000)
      })
      expect(mockWSInstances.length).toBe(countBeforeClose + 1)
    })

    it("sets status to disconnected after MAX_RETRIES (50)", () => {
      const params = makeHookParams()
      const { result } = renderHook(() => useWebSocketSync(params))

      // Exhaust all 50 retries
      for (let i = 0; i < 50; i++) {
        act(() => {
          latestWS().onclose?.({} as CloseEvent)
        })
        act(() => {
          vi.advanceTimersByTime(30_000)
        })
      }

      // 51st close should set disconnected
      act(() => {
        latestWS().onclose?.({} as CloseEvent)
      })

      expect(result.current).toBe("disconnected")

      const countBefore = mockWSInstances.length
      // No further reconnection attempts
      act(() => {
        vi.advanceTimersByTime(60_000)
      })
      expect(mockWSInstances.length).toBe(countBefore)
    })

    it("refreshes the HttpOnly session and reconnects on an expired-session close", async () => {
      const originalFetch = globalThis.fetch
      const fetchMock = vi.fn(() => Promise.resolve({
        ok: true,
        status: 200,
        statusText: "OK",
        json: () => Promise.resolve({ ok: true }),
      } as Response))
      globalThis.fetch = fetchMock as unknown as typeof fetch
      const listener = vi.fn()
      window.addEventListener(HAUTE_SESSION_EXPIRED_EVENT, listener)
      const params = makeHookParams()
      const { result } = renderHook(() => useWebSocketSync(params))

      try {
        act(() => {
          latestWS().onclose?.({
            code: 1008,
            reason: "Missing or invalid Haute session token",
          } as CloseEvent)
        })

        await act(async () => {
          await Promise.resolve()
          await Promise.resolve()
        })

        expect(fetchMock).toHaveBeenCalledWith(
          "/api/session/bootstrap",
          expect.objectContaining({ method: "POST", credentials: "same-origin" }),
        )
        expect(result.current).toBe("reconnecting")
        expect(listener).not.toHaveBeenCalled()

        act(() => {
          vi.advanceTimersByTime(1_000)
        })
        expect(mockWSInstances).toHaveLength(2)
      } finally {
        window.removeEventListener(HAUTE_SESSION_EXPIRED_EVENT, listener)
        globalThis.fetch = originalFetch
      }
    })

    it("expires the session when refresh fails after an expired-session close", async () => {
      const originalFetch = globalThis.fetch
      const fetchMock = vi.fn(() => Promise.reject(new TypeError("Failed to fetch")))
      globalThis.fetch = fetchMock as unknown as typeof fetch
      const listener = vi.fn()
      window.addEventListener(HAUTE_SESSION_EXPIRED_EVENT, listener)
      const params = makeHookParams()
      const { result } = renderHook(() => useWebSocketSync(params))

      try {
        act(() => {
          latestWS().onclose?.({
            code: 1008,
            reason: "Missing or invalid Haute session token",
          } as CloseEvent)
        })

        await act(async () => {
          await Promise.resolve()
          await Promise.resolve()
          await Promise.resolve()
        })

        expect(fetchMock).toHaveBeenCalledWith(
          "/api/session/bootstrap",
          expect.objectContaining({ method: "POST", credentials: "same-origin" }),
        )
        expect(result.current).toBe("disconnected")
        expect(listener).toHaveBeenCalledTimes(1)
        expect(listener.mock.calls[0][0]).toMatchObject({
          detail: { reason: "Missing or invalid Haute session token" },
        })

        act(() => {
          vi.advanceTimersByTime(60_000)
        })
        expect(mockWSInstances).toHaveLength(1)
      } finally {
        window.removeEventListener(HAUTE_SESSION_EXPIRED_EVENT, listener)
        globalThis.fetch = originalFetch
      }
    })

    it("refreshes the session before reconnecting when a pre-open close hides the reason", async () => {
      const originalFetch = globalThis.fetch
      const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        void input
        void init
        return Promise.resolve({
          ok: true,
          status: 200,
          statusText: "OK",
          json: () => Promise.resolve({ ok: true }),
        } as Response)
      })
      globalThis.fetch = fetchMock as unknown as typeof fetch

      const listener = vi.fn()
      window.addEventListener(HAUTE_SESSION_EXPIRED_EVENT, listener)
      const params = makeHookParams()
      const { result } = renderHook(() => useWebSocketSync(params))

      try {
        act(() => {
          latestWS().onclose?.({
            code: 1006,
            reason: "",
          } as CloseEvent)
        })

        await act(async () => {
          await Promise.resolve()
          await Promise.resolve()
          await Promise.resolve()
        })

        expect(fetchMock).toHaveBeenCalledTimes(1)
        expect(fetchMock.mock.calls[0][0]).toBe("/api/session/bootstrap")
        expect(result.current).toBe("reconnecting")
        expect(listener).not.toHaveBeenCalled()

        act(() => {
          vi.advanceTimersByTime(1_000)
        })
        expect(mockWSInstances).toHaveLength(2)
      } finally {
        window.removeEventListener(HAUTE_SESSION_EXPIRED_EVENT, listener)
        globalThis.fetch = originalFetch
      }
    })

    it("continues reconnecting when a pre-open close session probe cannot reach the server", async () => {
      const originalFetch = globalThis.fetch
      const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        void input
        void init
        return Promise.reject(new TypeError("Failed to fetch"))
      })
      globalThis.fetch = fetchMock as unknown as typeof fetch

      const params = makeHookParams()
      const { result } = renderHook(() => useWebSocketSync(params))

      try {
        act(() => {
          latestWS().onclose?.({
            code: 1006,
            reason: "",
          } as CloseEvent)
        })

        await act(async () => {
          await Promise.resolve()
          await Promise.resolve()
          await Promise.resolve()
        })

        expect(fetchMock).toHaveBeenCalledTimes(1)
        expect(result.current).toBe("reconnecting")

        act(() => {
          vi.advanceTimersByTime(1_000)
        })
        expect(mockWSInstances).toHaveLength(2)
      } finally {
        globalThis.fetch = originalFetch
      }
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Reconnection on error
  // ────────────────────────────────────────────────────────────────

  describe("reconnection on error", () => {
    it("closes the WebSocket on error (which triggers reconnect via onclose)", () => {
      const params = makeHookParams()
      renderHook(() => useWebSocketSync(params))

      const ws = latestWS()

      act(() => {
        ws.onerror?.(new Event("error"))
      })

      // onerror calls ws.close()
      expect(ws.close).toHaveBeenCalled()
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Cleanup on unmount
  // ────────────────────────────────────────────────────────────────

  describe("cleanup on unmount", () => {
    it("closes WebSocket and clears reconnect timer on unmount", () => {
      const params = makeHookParams()
      const { unmount } = renderHook(() => useWebSocketSync(params))

      const ws = latestWS()

      unmount()

      expect(ws.close).toHaveBeenCalled()
    })

    it("does not reconnect after unmount", () => {
      const params = makeHookParams()
      const { unmount } = renderHook(() => useWebSocketSync(params))

      // Simulate close to schedule reconnect
      act(() => {
        latestWS().onclose?.({} as CloseEvent)
      })

      const countBefore = mockWSInstances.length

      // Unmount before timer fires
      unmount()

      // Advance past reconnect delay
      act(() => {
        vi.advanceTimersByTime(5000)
      })

      // No new WebSocket should have been created
      expect(mockWSInstances.length).toBe(countBefore)
    })

    it("does not update state after unmount (no React warnings)", () => {
      const params = makeHookParams()
      const { unmount } = renderHook(() => useWebSocketSync(params))

      const ws = latestWS()
      unmount()

      // Firing onopen after unmount should be safe (mounted = false guard)
      act(() => {
        ws.onopen?.(new Event("open"))
      })

      // Firing onclose after unmount should not attempt reconnect
      act(() => {
        ws.onclose?.({} as CloseEvent)
      })

      // Should still only have 1 instance
      expect(mockWSInstances).toHaveLength(1)
    })
  })
})
