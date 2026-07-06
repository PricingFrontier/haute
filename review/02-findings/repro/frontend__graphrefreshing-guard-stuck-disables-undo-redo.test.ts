/**
 * Adversarial repro for claim "graphrefreshing-guard-stuck-disables-undo-redo".
 *
 * CLAIM: across rapid reconnects where graphRefreshingRef was bumped by
 * MULTIPLE overlapping applies whose release timers are dropped, the shared
 * graphRefreshingRef can settle at a POSITIVE value while
 * activeSelectionGuardIncrements has been zeroed by cleanup — leaving
 * undo/redo permanently no-op.
 *
 * This test drives TWO overlapping graph_update applies so the guard counter
 * reaches 2 and activeSelectionGuardIncrements reaches 2, then UNMOUNTS before
 * the 150ms release timers fire. The claim predicts graphRefreshingRef.current
 * stays > 0. We ASSERT it must be 0 (the cleanup subtracts the FULL count, not
 * "only once").
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act, cleanup } from "@testing-library/react"
import { type Mock } from "vitest"
import type { Node } from "@xyflow/react"

vi.mock("../../../frontend/src/utils/layout.ts", () => ({
  getLayoutedElements: vi.fn(async (nodes: unknown[]) => nodes),
}))

vi.mock("../../../frontend/src/stores/useToastStore.ts", () => {
  const store = { toasts: [], _toastCounter: 0, addToast: vi.fn(), dismissToast: vi.fn() }
  const useToastStore = Object.assign(() => store, {
    getState: () => store, setState: vi.fn(), subscribe: vi.fn(),
  })
  return { default: useToastStore }
})

vi.mock("../../../frontend/src/stores/useUIStore.ts", () => {
  const store: Record<string, unknown> = {
    syncBanner: null, setSyncBanner: vi.fn(), setPaletteOpen: vi.fn(),
    setShortcutsOpen: vi.fn(), submodelDialog: null, setSubmodelDialog: vi.fn(),
    renameDialog: null, setRenameDialog: vi.fn(),
  }
  const useUIStore = Object.assign(() => store, {
    getState: () => store, setState: vi.fn(), subscribe: vi.fn(),
  })
  return { default: useUIStore }
})

vi.mock("../../../frontend/src/stores/useGraphStore.ts", () => {
  const store = { markSaved: vi.fn(), dirty: false, nodes: [], edges: [], preamble: "" }
  const useGraphStore = Object.assign(() => store, {
    getState: () => store, setState: vi.fn(), subscribe: vi.fn(),
  })
  return { default: useGraphStore }
})

import useWebSocketSync from "../../../frontend/src/hooks/useWebSocketSync.ts"
import { getLayoutedElements } from "../../../frontend/src/utils/layout.ts"

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

describe("REPRO: multi-apply overlap then unmount before guard release", () => {
  let originalWebSocket: typeof globalThis.WebSocket
  beforeEach(() => {
    vi.useFakeTimers()
    mockWSInstances = []
    originalWebSocket = globalThis.WebSocket
    globalThis.WebSocket = createMockWebSocket() as unknown as typeof WebSocket
    vi.mocked(getLayoutedElements).mockImplementation(async (n: Node[]) => n)
  })
  afterEach(() => {
    cleanup(); vi.useRealTimers(); globalThis.WebSocket = originalWebSocket
  })

  it("two overlapping applies bump guard to 2; unmount before 150ms leaves ref at 0 (claim predicts >0)", async () => {
    const params = makeHookParams()
    const { unmount } = renderHook(() => useWebSocketSync(params))
    act(() => { latestWS().onopen?.(new Event("open")) })

    // Apply #1 — nodes carry positions so no async layout; synchronous apply
    // increments graphRefreshingRef to 1 and schedules a 150ms release timer.
    await act(async () => {
      latestWS().onmessage?.(new MessageEvent("message", {
        data: JSON.stringify({
          type: "graph_update",
          graph: { nodes: [{ id: "a", position: { x: 1, y: 1 }, data: {} }], edges: [] },
        }),
      }))
    })
    expect(params.graphRefreshingRef.current).toBe(1)

    // Apply #2 — fires before the first release timer (still pending). The
    // handler bumps graphRefreshingRef to 2 and schedules a second release.
    await act(async () => {
      latestWS().onmessage?.(new MessageEvent("message", {
        data: JSON.stringify({
          type: "graph_update",
          graph: { nodes: [{ id: "b", position: { x: 2, y: 2 }, data: {} }], edges: [] },
        }),
      }))
    })
    // Guard counter is now 2 (two unreleased applies), matching the claim's
    // "graphRefreshingRef reaches 2 and activeSelectionGuardIncrements 2".
    expect(params.graphRefreshingRef.current).toBe(2)

    // UNMOUNT before either 150ms release timer fires. Cleanup clears both
    // timers, then subtracts activeSelectionGuardIncrements (=2) from the ref.
    unmount()

    // CLAIM predicts this stays > 0 (undo/redo silently disabled). Reality:
    // cleanup subtracts the FULL count, returning the ref to 0.
    expect(params.graphRefreshingRef.current).toBe(0)

    // Dropped timers must never fire after unmount.
    act(() => { vi.advanceTimersByTime(1000) })
    expect(params.graphRefreshingRef.current).toBe(0)
  })

  it("THREE overlapping applies then unmount also returns ref to 0", async () => {
    const params = makeHookParams()
    const { unmount } = renderHook(() => useWebSocketSync(params))
    act(() => { latestWS().onopen?.(new Event("open")) })

    for (const id of ["a", "b", "c"]) {
      await act(async () => {
        latestWS().onmessage?.(new MessageEvent("message", {
          data: JSON.stringify({
            type: "graph_update",
            graph: { nodes: [{ id, position: { x: 1, y: 1 }, data: {} }], edges: [] },
          }),
        }))
      })
    }
    expect(params.graphRefreshingRef.current).toBe(3)
    unmount()
    expect(params.graphRefreshingRef.current).toBe(0)
  })
})
