/**
 * Gap tests for useWebSocketSync — covers scenarios missing from the main test file:
 *
 * 1. fitView delayed call (setTimeout 100ms after graph_update)
 * 2. Binary/blob messages (non-string event.data)
 * 3. Preamble undefined vs empty string handling
 * 4. Multiple rapid graph_update messages (only last one wins)
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
  const store = { markSaved: vi.fn() }
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
import { getLayoutedElements } from "../../utils/layout.ts"

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

function makeHookParams() {
  return {
    setNodesRaw: vi.fn(),
    setEdgesRaw: vi.fn(),
    setPreamble: vi.fn(),
    preambleRef: { current: "" },
    graphRefreshingRef: { current: 0 },
    nodeIdCounter: { current: 0 },
    fitView: vi.fn(),
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
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    globalThis.WebSocket = originalWebSocket
  })

  // ────────────────────────────────────────────────────────────────
  // 1. fitView delayed call after graph_update
  // ────────────────────────────────────────────────────────────────

  describe("fitView delayed call", () => {
    it("calls fitView with padding 0.8 after a 100ms delay on graph_update", async () => {
      // Catches: if someone removes the setTimeout or changes the delay,
      // graph will not fit to view after receiving a file-watcher update,
      // leaving the user looking at an empty canvas.
      const params = makeHookParams()
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify({
            type: "graph_update",
            graph: {
              nodes: [{ id: "n1", position: { x: 10, y: 20 }, data: { label: "A" } }],
              edges: [],
            },
          }),
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
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify({
            type: "graph_update",
            graph: {
              nodes: [{ id: "n1", position: { x: 10, y: 20 }, data: {} }],
              edges: [],
            },
          }),
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
      expect(params.setNodesRaw).not.toHaveBeenCalled()
    })
  })

  // ────────────────────────────────────────────────────────────────
  // 3. Preamble undefined vs empty string
  // ────────────────────────────────────────────────────────────────

  describe("preamble handling", () => {
    it("does NOT call setPreamble when graph.preamble is undefined", async () => {
      // Catches: if the guard `g.preamble !== undefined` is removed,
      // every graph_update (even partial ones without preamble) would
      // overwrite the user's preamble with "".
      const params = makeHookParams()
      params.preambleRef.current = "import polars as pl"
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify({
            type: "graph_update",
            graph: {
              nodes: [{ id: "n1", position: { x: 1, y: 1 }, data: {} }],
              edges: [],
              // preamble key is intentionally absent
            },
          }),
        }))
      })

      expect(params.setPreamble).not.toHaveBeenCalled()
      // preambleRef should be untouched
      expect(params.preambleRef.current).toBe("import polars as pl")
    })

    it("normalizes empty string preamble (g.preamble = '')", async () => {
      // Catches: `g.preamble || ""` should normalize falsy preamble to ""
      // so the preamble editor starts clean rather than showing `undefined`.
      const params = makeHookParams()
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify({
            type: "graph_update",
            graph: {
              nodes: [{ id: "n1", position: { x: 1, y: 1 }, data: {} }],
              edges: [],
              preamble: "",
            },
          }),
        }))
      })

      expect(params.setPreamble).toHaveBeenCalledWith("")
      expect(params.preambleRef.current).toBe("")
    })
  })

  // ────────────────────────────────────────────────────────────────
  // 4. Multiple rapid graph_update messages
  // ────────────────────────────────────────────────────────────────

  describe("multiple rapid graph_update messages", () => {
    it("processes each graph_update — last one's nodes win", async () => {
      // Catches: if the hook accumulated state or debounced updates
      // incorrectly, intermediate updates might be dropped or merged
      // wrong, leaving the UI out of sync with the file on disk.
      const params = makeHookParams()
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      const msg1 = {
        type: "graph_update",
        graph: {
          nodes: [{ id: "n1", position: { x: 1, y: 1 }, data: { label: "first" } }],
          edges: [],
        },
      }
      const msg2 = {
        type: "graph_update",
        graph: {
          nodes: [
            { id: "n1", position: { x: 10, y: 10 }, data: { label: "second" } },
            { id: "n2", position: { x: 20, y: 20 }, data: { label: "new" } },
          ],
          edges: [{ id: "e1", source: "n1", target: "n2" }],
        },
      }

      // Fire both messages rapidly (no timer advancement between them)
      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(msg1),
        }))
      })
      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify(msg2),
        }))
      })

      // setNodesRaw should have been called twice
      expect(params.setNodesRaw).toHaveBeenCalledTimes(2)
      // The last call should have the second message's nodes
      const lastCallNodes = params.setNodesRaw.mock.calls[1][0]
      expect(lastCallNodes).toHaveLength(2)
      expect(lastCallNodes[0].data.label).toBe("second")
    })

    it("only runs delayed fitView for the latest rapid graph_update", async () => {
      // Catches: multiple pending fitView timers should not cause
      // excessive viewport jumps after rapid file-watcher updates.
      const params = makeHookParams()
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      const graphMsg = (id: string) => ({
        type: "graph_update",
        graph: {
          nodes: [{ id, position: { x: 1, y: 1 }, data: {} }],
          edges: [],
        },
      })

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", { data: JSON.stringify(graphMsg("a")) }))
      })
      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", { data: JSON.stringify(graphMsg("b")) }))
      })

      act(() => {
        vi.advanceTimersByTime(100)
      })

      expect(params.fitView).toHaveBeenCalledTimes(1)
    })

    it("ignores a stale graph_update whose async layout finishes after a newer update", async () => {
      // Catches: layout is async. If edit A starts layout, edit B arrives
      // and applies, then edit A finishes later, the UI must not roll back
      // to stale graph data.
      let resolveFirstLayout!: (nodes: Node[]) => void
      const firstLayout = new Promise<Node[]>((resolve) => {
        resolveFirstLayout = resolve
      })
      vi.mocked(getLayoutedElements)
        .mockImplementationOnce(async () => firstLayout)
        .mockImplementationOnce(async (nodes: Node[]) =>
          nodes.map(node => ({ ...node, position: { x: 20, y: 20 } })),
        )

      const params = makeHookParams()
      renderHook(() => useWebSocketSync(params))

      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      let firstMessage!: Promise<void>
      await act(async () => {
        firstMessage = latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify({
            type: "graph_update",
            graph: {
              nodes: [{
                id: "node_99",
                position: { x: Number.NaN, y: Number.NaN },
                data: { label: "old" },
              }],
              edges: [],
            },
          }),
        })) as unknown as Promise<void>
        await Promise.resolve()
      })

      await act(async () => {
        await (latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify({
            type: "graph_update",
            graph: {
              nodes: [{
                id: "node_2",
                position: { x: Number.NaN, y: Number.NaN },
                data: { label: "new" },
              }],
              edges: [],
            },
          }),
        })) as unknown as Promise<void>)
      })

      expect(params.setNodesRaw).toHaveBeenCalledTimes(1)
      expect(params.setNodesRaw).toHaveBeenLastCalledWith([
        expect.objectContaining({ id: "node_2" }),
      ])

      await act(async () => {
        resolveFirstLayout([{ id: "node_99", position: { x: 10, y: 10 }, data: { label: "old" } }])
        await firstMessage
      })

      expect(params.setNodesRaw).toHaveBeenCalledTimes(1)
      expect(params.setNodesRaw).toHaveBeenLastCalledWith([
        expect.objectContaining({ id: "node_2" }),
      ])
      expect(params.nodeIdCounter.current).toBe(3)
      expect(useToastStore.getState().addToast).toHaveBeenCalledTimes(1)
      expect(useToastStore.getState().addToast).toHaveBeenCalledWith(
        "info",
        "Pipeline updated from file",
      )
    })
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
    it("does not apply a graph_update whose async layout resolves after unmount", async () => {
      // Catches: a queued WebSocket message should not mutate React state
      // or stores after the hook has unmounted.
      let resolveLayout!: (nodes: Node[]) => void
      const layout = new Promise<Node[]>((resolve) => {
        resolveLayout = resolve
      })
      vi.mocked(getLayoutedElements).mockImplementationOnce(async () => layout)

      const params = makeHookParams()
      const { unmount } = renderHook(() => useWebSocketSync(params))
      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      let message!: Promise<void>
      await act(async () => {
        message = latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify({
            type: "graph_update",
            graph: {
              nodes: [{
                id: "after-unmount",
                position: { x: Number.NaN, y: Number.NaN },
                data: {},
              }],
              edges: [],
            },
          }),
        })) as unknown as Promise<void>
        await Promise.resolve()
      })

      unmount()

      await act(async () => {
        resolveLayout([{ id: "after-unmount", position: { x: 1, y: 1 }, data: {} }])
        await message
      })

      expect(params.setNodesRaw).not.toHaveBeenCalled()
      expect(params.setEdgesRaw).not.toHaveBeenCalled()
      expect(params.fitView).not.toHaveBeenCalled()
      expect(useToastStore.getState().addToast).not.toHaveBeenCalled()
    })

    it("clears delayed fitView and selection-guard timers on unmount", async () => {
      // Catches: delayed callbacks from a successful sync should not fire
      // after unmount, and the selection guard must not remain stuck on.
      const params = makeHookParams()
      const { unmount } = renderHook(() => useWebSocketSync(params))
      act(() => {
        latestWS().onopen?.(new Event("open"))
      })

      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify({
            type: "graph_update",
            graph: {
              nodes: [{ id: "n1", position: { x: 10, y: 10 }, data: {} }],
              edges: [],
            },
          }),
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
