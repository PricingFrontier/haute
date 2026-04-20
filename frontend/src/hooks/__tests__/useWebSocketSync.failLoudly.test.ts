/**
 * Phase 1 Package 1H — Item #37: WebSocket message handler catch-all must
 * not leave partial state.
 *
 * Pre-fix: the onmessage handler wraps everything in a single try/catch and
 * forwards any error to a toast.  If `getLayoutedElements` throws midway
 * through graph update processing, the handler may have already called
 * `setNodesRaw` OR partially mutated `graphRefreshingRef`, leaving the UI
 * in an inconsistent state.
 *
 * Fix requirements:
 *   (a) If an error is thrown during the graph-update path, the final state
 *       visible to React should be either fully applied or untouched — not
 *       a mix.  Specifically: graphRefreshingRef must be decremented back to
 *       its pre-handler value.
 *   (b) The error is surfaced to the user via a toast.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act, cleanup } from "@testing-library/react"
import { type Mock } from "vitest"

// ── Mocks ────────────────────────────────────────────────────────

vi.mock("../../utils/layout.ts", () => ({
  getLayoutedElements: vi.fn(),
}))

vi.mock("../../stores/useToastStore.ts", () => {
  const store = {
    toasts: [] as Array<{ id: string; type: string; text: string }>,
    _toastCounter: 0,
    addToast: vi.fn((type: string, text: string) => {
      store._toastCounter++
      store.toasts.push({ id: String(store._toastCounter), type, text })
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
  const store: Record<string, unknown> = {
    lastSavedSnapshot: null,
    syncBanner: null,
    setSyncBanner: vi.fn((banner: string | null) => { store.syncBanner = banner }),
    markSaved: vi.fn((snap: string) => { store.lastSavedSnapshot = snap }),
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
  return {
    default: useUIStore,
    serializeSnapshot: (input: { nodes: unknown; edges: unknown; preamble: unknown }) =>
      JSON.stringify(input),
  }
})

import useWebSocketSync from "../../hooks/useWebSocketSync.ts"
import useToastStore from "../../stores/useToastStore.ts"
import { getLayoutedElements } from "../../utils/layout.ts"

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

describe("useWebSocketSync — partial failure rolls back consistently (#37)", () => {
  let originalWebSocket: typeof globalThis.WebSocket

  beforeEach(() => {
    vi.useFakeTimers()
    mockWSInstances = []
    originalWebSocket = globalThis.WebSocket
    globalThis.WebSocket = createMockWebSocket() as unknown as typeof WebSocket

    // Default: identity layout — individual tests override to throw.
    vi.mocked(getLayoutedElements).mockImplementation(async (n: unknown) => n as never)
    vi.mocked(useToastStore.getState().addToast).mockClear()
    useToastStore.getState().toasts.length = 0
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    globalThis.WebSocket = originalWebSocket
  })

  it("getLayoutedElements throws → graphRefreshingRef is restored to pre-handler value", async () => {
    // Catches: if graphRefreshingRef is incremented in a try block but
    // the finally doesn't run (e.g. a refactor uses async/await without
    // proper try/finally), the guard counter stays elevated forever,
    // silently suppressing future onSelectionChange events.
    const params = makeHookParams()
    params.graphRefreshingRef.current = 0

    vi.mocked(getLayoutedElements).mockImplementationOnce(async () => {
      throw new Error("ELK layout failed")
    })

    renderHook(() => useWebSocketSync(params))
    act(() => { latestWS().onopen?.(new Event("open")) })

    // Graph update with NO positions → layout will be invoked → throws
    await act(async () => {
      latestWS().onmessage?.(new MessageEvent("message", {
        data: JSON.stringify({
          type: "graph_update",
          graph: {
            nodes: [{ id: "n1", position: { x: 0, y: 0 }, data: {} }],
            edges: [],
          },
        }),
      }))
    })

    // After the guard-release timer (150ms), the ref must be back to 0.
    act(() => { vi.advanceTimersByTime(200) })

    expect(params.graphRefreshingRef.current).toBe(0)
  })

  it("getLayoutedElements throws → error toast is emitted to inform the user", async () => {
    const params = makeHookParams()
    vi.mocked(getLayoutedElements).mockImplementationOnce(async () => {
      throw new Error("ELK layout failed")
    })

    renderHook(() => useWebSocketSync(params))
    act(() => { latestWS().onopen?.(new Event("open")) })

    await act(async () => {
      latestWS().onmessage?.(new MessageEvent("message", {
        data: JSON.stringify({
          type: "graph_update",
          graph: {
            nodes: [{ id: "n1", position: { x: 0, y: 0 }, data: {} }],
            edges: [],
          },
        }),
      }))
    })

    const addToast = vi.mocked(useToastStore.getState().addToast)
    expect(addToast).toHaveBeenCalledWith(
      "error",
      expect.stringContaining("WebSocket sync error"),
    )
  })

  it("getLayoutedElements throws → setNodesRaw and setEdgesRaw are NOT both called with partial data", async () => {
    // Catches: a partially-applied graph (e.g. nodes set via fallback,
    // edges skipped due to the throw) would leave the canvas showing
    // disconnected nodes.  Either both setters are called consistently
    // or neither.
    const params = makeHookParams()
    vi.mocked(getLayoutedElements).mockImplementationOnce(async () => {
      throw new Error("ELK layout failed")
    })

    renderHook(() => useWebSocketSync(params))
    act(() => { latestWS().onopen?.(new Event("open")) })

    await act(async () => {
      latestWS().onmessage?.(new MessageEvent("message", {
        data: JSON.stringify({
          type: "graph_update",
          graph: {
            nodes: [{ id: "n1", position: { x: 0, y: 0 }, data: {} }],
            edges: [{ id: "e1", source: "n1", target: "n2" }],
          },
        }),
      }))
    })

    // Pre-fix: setNodesRaw may have been called before the throw.  Post-fix,
    // either BOTH setters ran with consistent data OR NEITHER did.  A
    // partial state (nodes set, edges unset) is the bug we're guarding
    // against.
    const nodesCount = params.setNodesRaw.mock.calls.length
    const edgesCount = params.setEdgesRaw.mock.calls.length
    expect(nodesCount).toBe(edgesCount)
  })

  it("subsequent graph_update after a failed one is handled cleanly", async () => {
    // Catches: a failed message should not poison the handler for
    // future messages.  The next valid message must process as usual.
    const params = makeHookParams()
    vi.mocked(getLayoutedElements)
      .mockImplementationOnce(async () => { throw new Error("layout failed") })
      .mockImplementationOnce(async (n: unknown) => n as never)

    renderHook(() => useWebSocketSync(params))
    act(() => { latestWS().onopen?.(new Event("open")) })

    await act(async () => {
      latestWS().onmessage?.(new MessageEvent("message", {
        data: JSON.stringify({
          type: "graph_update",
          graph: {
            nodes: [{ id: "bad", position: { x: 0, y: 0 }, data: {} }],
            edges: [],
          },
        }),
      }))
    })

    // Advance past the guard-release timer
    act(() => { vi.advanceTimersByTime(200) })

    // Now send a well-formed update with positions → no layout call → success
    await act(async () => {
      latestWS().onmessage?.(new MessageEvent("message", {
        data: JSON.stringify({
          type: "graph_update",
          graph: {
            nodes: [{ id: "good", position: { x: 10, y: 20 }, data: {} }],
            edges: [],
          },
        }),
      }))
    })

    // The good update should have applied cleanly
    expect(params.setNodesRaw).toHaveBeenCalledWith(
      expect.arrayContaining([
        expect.objectContaining({ id: "good" }),
      ]),
    )
    // And the ref must be at 0 after the second update's guard timer
    act(() => { vi.advanceTimersByTime(200) })
    expect(params.graphRefreshingRef.current).toBe(0)
  })
})
