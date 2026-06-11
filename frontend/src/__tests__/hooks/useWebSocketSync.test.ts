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

// Mock getLayoutedElements — called when graph update has no positions
vi.mock("../../utils/layout.ts", () => ({
  getLayoutedElements: vi.fn(async (nodes: unknown[]) => nodes),
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
  const store = { dirty: false, markSaved: vi.fn() }
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

function makeHookParams() {
  return {
    setNodesRaw: vi.fn(),
    setEdgesRaw: vi.fn(),
    setPreamble: vi.fn(),
    preambleRef: { current: "" },
    sourceFileRef: { current: "rating/main.py" },
    graphRefreshingRef: { current: 0 },
    nodeIdCounter: { current: 0 },
    fitView: vi.fn(),
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
    vi.mocked(useGraphStore.getState().markSaved).mockClear()
    useGraphStore.getState().dirty = false
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
      const params = makeHookParams()
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      expect(latestWS().send).toHaveBeenCalledWith(JSON.stringify({
        type: "resync",
        source_file: "rating/main.py",
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
      const params = makeHookParams()
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
  // Message handling — graph_update
  // ────────────────────────────────────────────────────────────────

  describe("graph update messages", () => {
    it("updates nodes and edges on graph_update with positions", async () => {
      const params = makeHookParams()
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      const graphMsg = {
        type: "graph_update",
        graph: {
          nodes: [
            { id: "transform_3", position: { x: 100, y: 200 }, data: { label: "test" } },
          ],
          edges: [
            { id: "e1", source: "transform_3", target: "transform_4" },
          ],
          preamble: "import numpy as np",
        },
      }

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(graphMsg),
        }))
      })

      // setNodesRaw called with the positioned nodes (no layout needed)
      expect(params.setNodesRaw).toHaveBeenCalledWith(graphMsg.graph.nodes)
      // setEdgesRaw called with edges that have type and animated set
      expect(params.setEdgesRaw).toHaveBeenCalledWith(
        expect.arrayContaining([
          expect.objectContaining({
            id: "e1",
            source: "transform_3",
            target: "transform_4",
            type: "default",
            animated: false,
          }),
        ]),
      )
      // Preamble updated
      expect(params.setPreamble).toHaveBeenCalledWith("import numpy as np")
      expect(params.preambleRef.current).toBe("import numpy as np")
      // nodeIdCounter updated — computed from max numeric suffix (3) + 1
      expect(params.nodeIdCounter.current).toBe(4)
      // Toast fired
      expect(useToastStore.getState().addToast).toHaveBeenCalledWith(
        "info",
        "Pipeline updated from file",
      )
      // Sync banner cleared
      expect(useUIStore.getState().setSyncBanner).toHaveBeenCalledWith(null)
      // Ensures subsequent file-watcher updates mark the new graph as
      // the saved state (item #99: dirty is derived from this snapshot).
      expect(useGraphStore.getState().markSaved).toHaveBeenCalled()
    })

    it("uses layout when nodes have no positions", async () => {
      const { getLayoutedElements } = await import("../../utils/layout.ts")
      const params = makeHookParams()
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      const graphMsg = {
        type: "graph_update",
        graph: {
          nodes: [
            { id: "n1", position: { x: 0, y: 0 }, data: { label: "test" } },
          ],
          edges: [],
        },
      }

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(graphMsg),
        }))
      })

      expect(getLayoutedElements).toHaveBeenCalled()
    })

    it("ignores graph_update messages for a different source_file", async () => {
      const params = makeHookParams()
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      const graphMsg = {
        type: "graph_update",
        source_file: "modules/foreign_submodel.py",
        graph: {
          nodes: [{ id: "n1", position: { x: 100, y: 200 }, data: {} }],
          edges: [],
        },
      }

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(graphMsg),
        }))
      })

      expect(params.setNodesRaw).not.toHaveBeenCalled()
      expect(params.setEdgesRaw).not.toHaveBeenCalled()
      expect(useGraphStore.getState().markSaved).not.toHaveBeenCalled()
    })

    it("accepts graph_update for the current source_file even when one side is absolute", async () => {
      const params = makeHookParams()
      params.sourceFileRef.current = "rating/main.py"
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify({
            type: "graph_update",
            source_file: "C:\\Users\\prici\\haute\\rating\\main.py",
            graph: {
              nodes: [{ id: "n1", position: { x: 100, y: 200 }, data: {} }],
              edges: [],
            },
          }),
        }))
      })

      expect(params.setNodesRaw).toHaveBeenCalledWith([
        expect.objectContaining({ id: "n1" }),
      ])
    })

    it("accepts graph_update when the current source_file is absolute and the message is relative", async () => {
      const params = makeHookParams()
      params.sourceFileRef.current = "C:\\Users\\prici\\haute\\rating\\main.py"
      renderHook(() => useWebSocketSync(params))

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify({
            type: "graph_update",
            source_file: "rating/main.py",
            graph: {
              nodes: [{ id: "n1", position: { x: 100, y: 200 }, data: {} }],
              edges: [],
            },
          }),
        }))
      })

      expect(params.setNodesRaw).toHaveBeenCalledWith([
        expect.objectContaining({ id: "n1" }),
      ])
    })

    it("rejects same-basename graph_update messages when the current source is ambiguous", async () => {
      const params = makeHookParams()
      params.sourceFileRef.current = "main.py"
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify({
            type: "graph_update",
            source_file: "C:\\Users\\prici\\haute\\rating\\main.py",
            graph: {
              nodes: [{ id: "n1", position: { x: 100, y: 200 }, data: {} }],
              edges: [],
            },
          }),
        }))
      })

      expect(params.setNodesRaw).not.toHaveBeenCalled()
      expect(params.setEdgesRaw).not.toHaveBeenCalled()
      expect(useGraphStore.getState().markSaved).not.toHaveBeenCalled()
    })

    it("does not overwrite unsaved local edits with an external graph_update", async () => {
      const params = makeHookParams()
      useGraphStore.getState().dirty = true
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify({
            type: "graph_update",
            source_file: "rating/main.py",
            graph: {
              nodes: [{ id: "disk", position: { x: 100, y: 200 }, data: {} }],
              edges: [],
            },
          }),
        }))
      })

      expect(params.setNodesRaw).not.toHaveBeenCalled()
      expect(params.setEdgesRaw).not.toHaveBeenCalled()
      expect(useGraphStore.getState().markSaved).not.toHaveBeenCalled()
      expect(useUIStore.getState().setSyncBanner).toHaveBeenCalledWith(
        expect.stringContaining("changed on disk"),
      )
      expect(useToastStore.getState().addToast).toHaveBeenCalledWith(
        "warning",
        expect.stringContaining("unsaved changes"),
      )
    })

    it("does not overwrite edits made while an external graph_update is laying out", async () => {
      const { getLayoutedElements } = await import("../../utils/layout.ts")
      let resolveLayout!: (nodes: Node[]) => void
      const layoutPromise = new Promise<Node[]>((resolve) => {
        resolveLayout = resolve
      })
      vi.mocked(getLayoutedElements).mockImplementationOnce(async () => layoutPromise)

      const params = makeHookParams()
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      let messagePromise!: Promise<void>
      act(() => {
        messagePromise = latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify({
            type: "graph_update",
            source_file: "rating/main.py",
            graph: {
              nodes: [{ id: "disk", position: { x: 0, y: 0 }, data: {} }],
              edges: [],
            },
          }),
        })) as unknown as Promise<void>
      })

      expect(getLayoutedElements).toHaveBeenCalled()

      useGraphStore.getState().dirty = true
      await act(async () => {
        resolveLayout([{ id: "disk", position: { x: 100, y: 200 }, data: {} } as Node])
        await messagePromise
      })

      expect(params.setNodesRaw).not.toHaveBeenCalled()
      expect(params.setEdgesRaw).not.toHaveBeenCalled()
      expect(useGraphStore.getState().markSaved).not.toHaveBeenCalled()
      expect(useUIStore.getState().setSyncBanner).toHaveBeenCalledWith(
        expect.stringContaining("changed on disk"),
      )
      expect(useToastStore.getState().addToast).toHaveBeenCalledWith(
        "warning",
        expect.stringContaining("unsaved changes"),
      )
    })

    it("sets graphRefreshingRef around node replacement and clears after 150ms", async () => {
      const params = makeHookParams()
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      const graphMsg = {
        type: "graph_update",
        graph: {
          nodes: [
            { id: "transform_1", position: { x: 100, y: 200 }, data: { label: "test" } },
          ],
          edges: [],
        },
      }

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(graphMsg),
        }))
      })

      // Guard active — onSelectionChange will skip spurious deselections
      expect(params.graphRefreshingRef.current).toBeGreaterThan(0)

      // Guard released — normal selection behaviour resumes
      act(() => {
        vi.advanceTimersByTime(150)
      })

      expect(params.graphRefreshingRef.current).toBe(0)
    })

    it("handles parse_error messages by setting sync banner", async () => {
      const params = makeHookParams()
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify({
            type: "parse_error",
            error: "SyntaxError on line 42",
          }),
        }))
      })

      expect(useUIStore.getState().setSyncBanner).toHaveBeenCalledWith(
        "SyntaxError on line 42",
      )
    })

    it("ignores parse_error messages for a different source_file", async () => {
      const params = makeHookParams()
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify({
            type: "parse_error",
            source_file: "modules/foreign.py",
            error: "SyntaxError in a foreign file",
          }),
        }))
      })

      expect(useUIStore.getState().setSyncBanner).not.toHaveBeenCalled()
    })

    it("uses default error message when parse_error has no error field", async () => {
      const params = makeHookParams()
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify({ type: "parse_error" }),
        }))
      })

      expect(useUIStore.getState().setSyncBanner).toHaveBeenCalledWith(
        "Parse error in pipeline file",
      )
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
