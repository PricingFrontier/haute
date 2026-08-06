/**
 * Phase 1 Package 1H — Item #39: a WebSocket graph sync that removes nodes
 * must also clear UI panel state that references those nodes.
 *
 * Pre-fix: `submodelDialog` and `renameDialog` in useUIStore may still
 * reference nodes that no longer exist after a file-watcher update.  The
 * dialogs then show obsolete labels or, worse, fire onConfirm with a nodeId
 * that maps to nothing.
 *
 * Fix: after applying a graph update, the hook should clear any
 * renameDialog / submodelDialog entries whose referenced nodeId is NOT in
 * the new nodes array.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act, cleanup } from "@testing-library/react"
import { type Mock } from "vitest"

vi.mock("../../utils/layout.ts", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../utils/layout.ts")>(),
  getLayoutedElements: vi.fn(async (n: unknown) => n),
}))

// Fresh mocks — track setRenameDialog / setSubmodelDialog calls
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
    submodelDialog: null as null | { nodeIds: string[] },
    setSubmodelDialog: vi.fn((d: null | { nodeIds: string[] }) => { store.submodelDialog = d }),
    renameDialog: null as null | { nodeId: string; currentLabel: string },
    setRenameDialog: vi.fn((d: null | { nodeId: string; currentLabel: string }) => { store.renameDialog = d }),
  }
  const useUIStore = Object.assign(() => store, {
    getState: () => store,
    setState: vi.fn((patch: Record<string, unknown>) => Object.assign(store, patch)),
    subscribe: vi.fn(),
  })
  return { default: useUIStore }
})

// The hook reads dirty-state from useGraphStore and applies incoming graphs
// through loadGraphSnapshot; the mock must provide both or every
// graph_update rolls back before reaching the dialog cleanup under test.
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
import useUIStore from "../../stores/useUIStore.ts"

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
    setSubmodelsRaw: vi.fn(),
    setPreamble: vi.fn(),
    preambleRef: { current: "" },
    submodelsRef: { current: {} as Record<string, unknown> },
    sourceRevisionRef: { current: "revision-test" },
    preservedBlocksRef: { current: [] as string[] },
    graphRefreshingRef: { current: 0 },
    nodeIdCounter: { current: 0 },
    fitView: vi.fn(),
  }
}

describe("useWebSocketSync — orphaned dialog state cleared on WS sync (#39)", () => {
  let originalWebSocket: typeof globalThis.WebSocket

  beforeEach(() => {
    vi.useFakeTimers()
    mockWSInstances = []
    originalWebSocket = globalThis.WebSocket
    globalThis.WebSocket = createMockWebSocket() as unknown as typeof WebSocket

    // Reset store to a clean baseline
    const store = useUIStore.getState() as unknown as Record<string, unknown>
    store.renameDialog = null
    store.submodelDialog = null
    vi.mocked(useUIStore.getState().setRenameDialog as Mock).mockClear()
    vi.mocked(useUIStore.getState().setSubmodelDialog as Mock).mockClear()
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    globalThis.WebSocket = originalWebSocket
  })

  it("renameDialog referring to a node removed by WS sync is cleared", async () => {
    // Simulate: user had opened the Rename dialog for node "doomed_42",
    // then the backend file watcher sends a graph_update where
    // "doomed_42" no longer exists.
    const store = useUIStore.getState() as unknown as Record<string, unknown>
    store.renameDialog = { nodeId: "doomed_42", currentLabel: "To Be Deleted" }

    const params = makeHookParams()
    renderHook(() => useWebSocketSync(params))
    act(() => { latestWS().onopen?.(new Event("open")) })

    await act(async () => {
      latestWS().onmessage?.(new MessageEvent("message", {
        data: JSON.stringify({
          type: "graph_update",
          graph: {
            submodels: {},
            source_revision: "revision-test",
            preserved_blocks: [],
            // Note: "doomed_42" is NOT in the new nodes list
            nodes: [{ id: "survivor_1", position: { x: 10, y: 10 }, data: {} }],
            edges: [],
          },
        }),
      }))
    })

    // After applying the graph update, the dialog state must be cleared.
    const setRename = useUIStore.getState().setRenameDialog as Mock
    expect(setRename).toHaveBeenCalledWith(null)
  })

  it("renameDialog referring to a node still present is NOT cleared", async () => {
    // Catches: over-eager clearing would dismiss a dialog that remains
    // valid. The dialog must survive a WS sync if its target is still
    // in the graph.
    const store = useUIStore.getState() as unknown as Record<string, unknown>
    store.renameDialog = { nodeId: "keeper_1", currentLabel: "Keep" }

    const params = makeHookParams()
    renderHook(() => useWebSocketSync(params))
    act(() => { latestWS().onopen?.(new Event("open")) })

    await act(async () => {
      latestWS().onmessage?.(new MessageEvent("message", {
        data: JSON.stringify({
          type: "graph_update",
          graph: {
            submodels: {},
            source_revision: "revision-test",
            preserved_blocks: [],
            nodes: [
              { id: "keeper_1", position: { x: 10, y: 10 }, data: {} },
              { id: "other", position: { x: 20, y: 20 }, data: {} },
            ],
            edges: [],
          },
        }),
      }))
    })

    const setRename = useUIStore.getState().setRenameDialog as Mock
    expect(setRename).not.toHaveBeenCalledWith(null)
  })

  it("submodelDialog with nodeIds referring to a removed node is cleared", async () => {
    // If any of the referenced node IDs are no longer in the graph,
    // the dialog is invalid — clear it.
    const store = useUIStore.getState() as unknown as Record<string, unknown>
    store.submodelDialog = { nodeIds: ["a", "gone_b", "c"] }

    const params = makeHookParams()
    renderHook(() => useWebSocketSync(params))
    act(() => { latestWS().onopen?.(new Event("open")) })

    await act(async () => {
      latestWS().onmessage?.(new MessageEvent("message", {
        data: JSON.stringify({
          type: "graph_update",
          graph: {
            submodels: {},
            source_revision: "revision-test",
            preserved_blocks: [],
            // Missing "gone_b"
            nodes: [
              { id: "a", position: { x: 10, y: 10 }, data: {} },
              { id: "c", position: { x: 20, y: 20 }, data: {} },
            ],
            edges: [],
          },
        }),
      }))
    })

    const setSub = useUIStore.getState().setSubmodelDialog as Mock
    expect(setSub).toHaveBeenCalledWith(null)
  })

  it("submodelDialog with all nodeIds present is NOT cleared", async () => {
    const store = useUIStore.getState() as unknown as Record<string, unknown>
    store.submodelDialog = { nodeIds: ["a", "b"] }

    const params = makeHookParams()
    renderHook(() => useWebSocketSync(params))
    act(() => { latestWS().onopen?.(new Event("open")) })

    await act(async () => {
      latestWS().onmessage?.(new MessageEvent("message", {
        data: JSON.stringify({
          type: "graph_update",
          graph: {
            submodels: {},
            source_revision: "revision-test",
            preserved_blocks: [],
            nodes: [
              { id: "a", position: { x: 10, y: 10 }, data: {} },
              { id: "b", position: { x: 20, y: 20 }, data: {} },
              { id: "c", position: { x: 30, y: 30 }, data: {} },
            ],
            edges: [],
          },
        }),
      }))
    })

    const setSub = useUIStore.getState().setSubmodelDialog as Mock
    expect(setSub).not.toHaveBeenCalledWith(null)
  })

  it("null dialogs stay null (handler does not spuriously set them)", async () => {
    // Make sure the cleanup logic handles the null case gracefully —
    // no spurious setRenameDialog(null) or setSubmodelDialog(null)
    // when the dialogs were already null.
    const store = useUIStore.getState() as unknown as Record<string, unknown>
    store.renameDialog = null
    store.submodelDialog = null

    const params = makeHookParams()
    renderHook(() => useWebSocketSync(params))
    act(() => { latestWS().onopen?.(new Event("open")) })

    await act(async () => {
      latestWS().onmessage?.(new MessageEvent("message", {
        data: JSON.stringify({
          type: "graph_update",
          graph: {
            submodels: {},
            source_revision: "revision-test",
            preserved_blocks: [],
            nodes: [{ id: "a", position: { x: 10, y: 10 }, data: {} }],
            edges: [],
          },
        }),
      }))
    })

    const setRename = useUIStore.getState().setRenameDialog as Mock
    const setSub = useUIStore.getState().setSubmodelDialog as Mock
    expect(setRename).not.toHaveBeenCalled()
    expect(setSub).not.toHaveBeenCalled()
  })
})
