/**
 * Phase 1 Package 1H — Item #37: WebSocket message handler catch-all must
 * not leave partial state.
 *
 * Pre-fix: the onmessage handler wraps everything in a single try/catch and
 * forwards any error to a toast.  If `getLayoutedElements` throws midway
 * through document-update processing, the handler may have already mutated
 * the graph store OR partially mutated `graphRefreshingRef`, leaving the UI
 * in an inconsistent state.
 *
 * Fix requirements:
 *   (a) If an error is thrown during the document-update path, the final
 *       state visible to React should be either fully applied or untouched
 *       — not a mix.  Specifically: graphRefreshingRef must be decremented
 *       back to its pre-handler value.
 *   (b) The error is surfaced to the user via a toast.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act, cleanup } from "@testing-library/react"
import { type Mock } from "vitest"
import type { Node } from "@xyflow/react"

// ── Mocks ────────────────────────────────────────────────────────

vi.mock("../../utils/layout.ts", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../utils/layout.ts")>(),
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
    syncBanner: null,
    setSyncBanner: vi.fn((banner: string | null) => { store.syncBanner = banner }),
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

// Wave 7E: dirty tracking moved from useUIStore to useGraphStore.
vi.mock("../../stores/useGraphStore.ts", () => {
  const store = {
    dirty: false,
    nodes: [{ id: "previous", position: { x: 1, y: 1 }, data: {} }] as unknown[],
    edges: [{ id: "old-edge", source: "previous", target: "previous" }] as unknown[],
    submodels: {} as Record<string, unknown>,
    preamble: "old preamble",
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
import useDocumentStatusStore from "../../stores/useDocumentStatusStore.ts"
import { getLayoutedElements } from "../../utils/layout.ts"
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
    sourceRevisionRef: { current: "revision-old" },
    preservedBlocksRef: { current: ["OLD_KEEP = 1"] },
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

const readyNode: Node = {
  id: "disk",
  type: "polars",
  position: { x: 100, y: 200 },
  data: { label: "Disk", nodeType: "polars", config: {} },
}

describe("useWebSocketSync — partial failure rolls back consistently (#37)", () => {
  let originalWebSocket: typeof globalThis.WebSocket

  beforeEach(() => {
    vi.useFakeTimers()
    mockWSInstances = []
    originalWebSocket = globalThis.WebSocket
    globalThis.WebSocket = createMockWebSocket() as unknown as typeof WebSocket

    // Default: identity layout — individual tests override to throw.
    // mockReset (not just mockImplementation) because an unconsumed
    // mockImplementationOnce queued by a previous test survives a plain
    // mockImplementation call and would be consumed first, making the
    // armed throw/success behaviour order-dependent under shuffle.
    vi.mocked(getLayoutedElements).mockReset().mockImplementation(async (n: unknown) => n as never)
    vi.mocked(useToastStore.getState().addToast).mockClear()
    useToastStore.getState().toasts.length = 0
    const graphStore = useGraphStore.getState()
    graphStore.nodes = [{ id: "previous", position: { x: 1, y: 1 }, data: {} }]
    graphStore.edges = [{ id: "old-edge", source: "previous", target: "previous" }]
    graphStore.submodels = {}
    graphStore.preamble = "old preamble"
    vi.mocked(graphStore.loadGraphSnapshot).mockReset().mockImplementation((snapshot) => {
      graphStore.nodes = snapshot.nodes
      graphStore.edges = snapshot.edges
      graphStore.submodels = snapshot.submodels
      graphStore.preamble = snapshot.preamble
    })
    useDocumentStatusStore.getState().reset()
    useDocumentStatusStore.getState().loadDocumentStatus(makePipelineEditorDocument({
      load_status: "ready",
      source_file: SOURCE_FILE,
      source_revision: "revision-old",
      nodes: [readyNode],
    }))
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    globalThis.WebSocket = originalWebSocket
  })

  it("installs source revision and preserved blocks with a successful document update", async () => {
    const params = makeHookParams()
    const document = makePipelineEditorDocument({
      source_file: SOURCE_FILE,
      source_revision: "revision-new",
      preserved_blocks: ["NEW_KEEP = 2"],
      nodes: [{ ...readyNode, id: "fresh", position: { x: 10, y: 10 } }],
    })
    renderHook(() => useWebSocketSync(params))
    act(() => { latestWS().onopen?.(new Event("open")) })

    await act(async () => {
      latestWS().onmessage?.(new MessageEvent("message", {
        data: JSON.stringify(pipelineDocumentFrame(document)),
      }))
    })

    expect(params.sourceRevisionRef.current).toBe("revision-new")
    expect(params.preservedBlocksRef.current).toEqual(["NEW_KEEP = 2"])
  })

  // NOTE: the two "getLayoutedElements throws" tests that existed in the
  // legacy graph_update suite are deleted, not ported. `document.nodes[].
  // display_position` is a required, schema-validated finite {x, y} pair
  // (see parsePosition in pipelineDocument.ts, which throws on non-finite
  // coordinates). Consequently `nodeIdsNeedingLayout()` can never return a
  // non-empty set for a successfully-parsed pipeline_document_update frame,
  // so `getLayoutedElements` is unreachable on this path — there is no way
  // to construct a wire frame that reaches it. Exception-rollback-and-toast
  // behaviour for the graph-apply step is still covered below via
  // `loadGraphSnapshot` throwing, which exercises the same try/catch/rollback
  // machinery through its actual reachable failure point.

  it("atomic snapshot failure leaves the previous graph untouched", async () => {
    const params = makeHookParams()
    vi.mocked(useGraphStore.getState().loadGraphSnapshot).mockImplementationOnce(() => {
      throw new Error("snapshot load failed")
    })

    const document = makePipelineEditorDocument({
      source_file: SOURCE_FILE,
      source_revision: "revision-new",
      nodes: [{ ...readyNode, id: "fresh", position: { x: 10, y: 10 } }],
    })
    renderHook(() => useWebSocketSync(params))
    act(() => { latestWS().onopen?.(new Event("open")) })

    await act(async () => {
      latestWS().onmessage?.(new MessageEvent("message", {
        data: JSON.stringify(pipelineDocumentFrame(document)),
      }))
    })

    const graphStore = useGraphStore.getState()
    expect(graphStore.loadGraphSnapshot).toHaveBeenCalledTimes(1)
    expect(graphStore.nodes).toEqual([expect.objectContaining({ id: "previous" })])
    expect(graphStore.edges).toEqual([expect.objectContaining({ id: "old-edge" })])
    // sourceRevisionRef mirrors the document fence unconditionally (before
    // the graph-apply try block), independent of whether the renderable
    // graph could be replaced — it is NOT rolled back on snapshot failure.
    expect(params.sourceRevisionRef.current).toBe("revision-new")
    expect(params.preservedBlocksRef.current).toEqual(["OLD_KEEP = 1"])
    expect(vi.mocked(useToastStore.getState().addToast)).toHaveBeenCalledWith(
      "error",
      expect.stringContaining("WebSocket sync error"),
    )
  })

  it("atomic snapshot failure rolls back request refs and preserves the store", async () => {
    const params = makeHookParams()
    const previousSubmodels = { old: { nodes: [], edges: [] } }
    params.preambleRef.current = "old preamble"
    params.submodelsRef.current = previousSubmodels
    const graphStore = useGraphStore.getState()
    graphStore.submodels = previousSubmodels
    vi.mocked(graphStore.loadGraphSnapshot).mockImplementationOnce(() => {
      throw new Error("snapshot load failed")
    })

    const document = makePipelineEditorDocument({
      source_file: SOURCE_FILE,
      source_revision: "revision-new",
      preamble: "new preamble",
      preserved_blocks: ["NEW_KEEP = 2"],
      nodes: [{ ...readyNode, id: "fresh", position: { x: 10, y: 10 } }],
    })
    renderHook(() => useWebSocketSync(params))
    act(() => { latestWS().onopen?.(new Event("open")) })

    await act(async () => {
      latestWS().onmessage?.(new MessageEvent("message", {
        data: JSON.stringify(pipelineDocumentFrame(document)),
      }))
    })

    expect(graphStore.loadGraphSnapshot).toHaveBeenCalledTimes(1)
    expect(graphStore.preamble).toBe("old preamble")
    expect(graphStore.submodels).toBe(previousSubmodels)
    expect(params.preambleRef.current).toBe("old preamble")
    // See the note above: the fence ref is set before the try block, so it
    // reflects the new document even though the graph apply rolled back.
    expect(params.sourceRevisionRef.current).toBe("revision-new")
    expect(params.preservedBlocksRef.current).toEqual(["OLD_KEEP = 1"])
    expect(params.submodelsRef.current).toBe(previousSubmodels)
    expect(vi.mocked(useToastStore.getState().addToast)).toHaveBeenCalledWith(
      "error",
      expect.stringContaining("WebSocket sync error"),
    )
  })

  it("subsequent document update after a failed one is handled cleanly", async () => {
    // Catches: a failed message should not poison the handler for
    // future messages.  The next valid message must process as usual.
    const params = makeHookParams()
    // Arm exactly one loadGraphSnapshot throw for the first message. A
    // second queued Once would go unconsumed and leak into whichever
    // test's snapshot call comes next under shuffled order.
    vi.mocked(useGraphStore.getState().loadGraphSnapshot).mockImplementationOnce(() => {
      throw new Error("snapshot load failed")
    })

    const failing = makePipelineEditorDocument({
      source_file: SOURCE_FILE,
      source_revision: "revision-failed",
      nodes: [{ ...readyNode, id: "bad", position: { x: 30, y: 30 } }],
    })
    renderHook(() => useWebSocketSync(params))
    act(() => { latestWS().onopen?.(new Event("open")) })

    await act(async () => {
      latestWS().onmessage?.(new MessageEvent("message", {
        data: JSON.stringify(pipelineDocumentFrame(failing)),
      }))
    })

    // Advance past the guard-release timer
    act(() => { vi.advanceTimersByTime(200) })

    // Now send a well-formed update with positions → no layout call → success
    const good = makePipelineEditorDocument({
      source_file: SOURCE_FILE,
      source_revision: "revision-good",
      nodes: [{ ...readyNode, id: "good", position: { x: 10, y: 20 } }],
    })
    await act(async () => {
      latestWS().onmessage?.(new MessageEvent("message", {
        data: JSON.stringify(pipelineDocumentFrame(good)),
      }))
    })

    // The good update should have applied cleanly
    expect(useGraphStore.getState().loadGraphSnapshot).toHaveBeenCalledWith(
      expect.objectContaining({
        nodes: expect.arrayContaining([
          expect.objectContaining({ id: "good" }),
        ]),
      }),
    )
    // And the ref must be at 0 after the second update's guard timer
    act(() => { vi.advanceTimersByTime(200) })
    expect(params.graphRefreshingRef.current).toBe(0)
  })
})
