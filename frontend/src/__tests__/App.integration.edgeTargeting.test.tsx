/**
 * Edge-targeting integration — whole-node body drops, end to end through
 * the REAL App: real useEdgeHandlers → real commitConnection → real
 * useGraphCanvasState → real useGraphStore → real handleSave, asserting
 * the EXACT persisted edge shape in the `savePipeline` payload.
 *
 * Strategy mirrors `App.integration.test.tsx` (mock ONLY `../api/client`
 * + the jsdom polyfills ReactFlow needs), with two additions:
 *
 * 1. `useEdgeHandlers` is wrapped (NOT stubbed): the wrapper calls the
 *    real hook and captures its return so tests can invoke
 *    `onConnectEnd` with a synthetic FinalConnectionState — jsdom
 *    cannot run xyflow's pointer-drag choreography (real pointer
 *    gestures are covered in Playwright, e2e/canvas/edge-targeting).
 * 2. `document.elementsFromPoint` (absent in jsdom) is stubbed to report
 *    the RENDERED `.react-flow__node` wrapper of the drop-target node,
 *    so the real `topmostNodeAtPoint` DOM walk runs unmodified; and
 *    because jsdom performs no layout (xyflow never measures nodes or
 *    handle bounds), realistic measurements are installed onto the live
 *    internal-node records (see `installNodeGeometry`).
 *
 * Geometry edge cases (nearest / dead band / clamp boundaries) are
 * pinned in `dropResolver.test.ts` and the useEdgeHandlers unit suite;
 * THIS file pins the store/save/undo plumbing and the persisted shape.
 */
import { describe, it, expect, vi, beforeAll, afterAll, beforeEach, afterEach } from "vitest"
import { render, screen, cleanup, fireEvent, waitFor, act } from "@testing-library/react"

const { edgeHandlersCapture, edgeHandlerParamsCapture } = vi.hoisted(() => ({
  edgeHandlersCapture: {
    current: null as null | { onConnectEnd: (event: unknown, state: unknown) => void },
  },
  edgeHandlerParamsCapture: {
    current: null as null | { getInternalNode: (id: string) => unknown },
  },
}))

vi.mock("../hooks/useEdgeHandlers", async () => {
  const actual = await vi.importActual<typeof import("../hooks/useEdgeHandlers")>(
    "../hooks/useEdgeHandlers",
  )
  return {
    default: (...args: Parameters<typeof actual.default>) => {
      const handlers = actual.default(...args)
      edgeHandlersCapture.current = handlers as unknown as typeof edgeHandlersCapture.current
      edgeHandlerParamsCapture.current = args[0] as unknown as typeof edgeHandlerParamsCapture.current
      return handlers
    },
  }
})

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client")
  return {
    // Preserve the real session/auth machinery — this renders the REAL App
    // with real hooks (useWebSocketSync calls hauteSessionToken(); App.tsx
    // subscribes to HAUTE_SESSION_EXPIRED_EVENT). Only network fns are stubbed.
    ApiError: actual.ApiError,
    ApiTimeoutError: actual.ApiTimeoutError,
    HAUTE_SESSION_EXPIRED_EVENT: actual.HAUTE_SESSION_EXPIRED_EVENT,
    HAUTE_SESSION_EXPIRED_REASON: actual.HAUTE_SESSION_EXPIRED_REASON,
    isHauteSessionExpiredReason: actual.isHauteSessionExpiredReason,
    isHauteSessionExpiredError: actual.isHauteSessionExpiredError,
    notifyHauteSessionExpired: actual.notifyHauteSessionExpired,
    hauteSessionToken: actual.hauteSessionToken,
    checkHauteSession: vi.fn(() => Promise.resolve({ ok: true })),
    loadPipeline: vi.fn(() => Promise.resolve({ nodes: [], edges: [], preamble: "" })),
    previewNode: vi.fn(() => Promise.resolve({ node_id: "", status: "ok", columns: [], preview: [], row_count: 0, column_count: 0 })),
    savePipeline: vi.fn(() => Promise.resolve({ file: "pipeline.py", pipeline_name: "main" })),
    traceCell: vi.fn(() => Promise.resolve({ status: "ok" })),
    executeSink: vi.fn(() => Promise.resolve({ status: "ok" })),
    createSubmodel: vi.fn(() => Promise.resolve({})),
    loadSubmodel: vi.fn(() => Promise.resolve({})),
    dissolveSubmodel: vi.fn(() => Promise.resolve({})),
    fetchSchema: vi.fn(() => Promise.resolve({ columns: [] })),
    fetchDatabricksSchema: vi.fn(() => Promise.resolve({ columns: [] })),
    checkMlflow: vi.fn(() => Promise.resolve({ mlflow_installed: false })),
    getTrainStatus: vi.fn(() => Promise.resolve({})),
    trainModel: vi.fn(() => Promise.resolve({})),
    estimateTrainingRam: vi.fn(() => Promise.resolve({})),
    logToMlflow: vi.fn(() => Promise.resolve({})),
    solveOptimiser: vi.fn(() => Promise.resolve({})),
    estimateOptimiserSolve: vi.fn(() => Promise.resolve({})),
    getOptimiserStatus: vi.fn(() => Promise.resolve({})),
    applyOptimiser: vi.fn(() => Promise.resolve({})),
    saveOptimiser: vi.fn(() => Promise.resolve({})),
    logOptimiserToMlflow: vi.fn(() => Promise.resolve({})),
    runFrontier: vi.fn(() => Promise.resolve({})),
    selectFrontierPoint: vi.fn(() => Promise.resolve({})),
    runExplore: vi.fn(() => Promise.resolve({ status: "started", job_id: "explore-job-1", cached: false, message: "started" })),
    getExploreStatus: vi.fn(() => Promise.resolve({ status: "running", progress: 0, message: "running", result: null })),
    cancelExplore: vi.fn(() => Promise.resolve({ status: "cancelled", progress: 1, message: "cancelled", result: null })),
    getWarehouses: vi.fn(() => Promise.resolve({ warehouses: [] })),
    getCatalogs: vi.fn(() => Promise.resolve({ catalogs: [] })),
    getSchemas: vi.fn(() => Promise.resolve({ schemas: [] })),
    getTables: vi.fn(() => Promise.resolve({ tables: [] })),
    getCacheStatus: vi.fn(() => Promise.resolve({})),
    getFetchProgress: vi.fn(() => Promise.resolve({})),
    fetchDatabricksData: vi.fn(() => Promise.resolve({})),
    deleteCache: vi.fn(() => Promise.resolve({})),
    buildJsonCache: vi.fn(() => Promise.resolve({})),
    cancelJsonCache: vi.fn(() => Promise.resolve({ cancelled: false, data_path: "" })),
    getJsonCacheProgress: vi.fn(() => Promise.resolve({})),
    getJsonCacheStatus: vi.fn(() => Promise.resolve({})),
    getJsonCacheStatusForSchema: vi.fn(() => Promise.resolve({})),
    deleteJsonCache: vi.fn(() => Promise.resolve({ cached: false, data_path: "" })),
    getExperiments: vi.fn(() => Promise.resolve([])),
    getRuns: vi.fn(() => Promise.resolve([])),
    getModels: vi.fn(() => Promise.resolve([])),
    getModelVersions: vi.fn(() => Promise.resolve([])),
    listUtilityFiles: vi.fn(() => Promise.resolve({ files: [] })),
    readUtilityFile: vi.fn(() => Promise.resolve({ name: "", module: "", content: "" })),
    createUtilityFile: vi.fn(() => Promise.resolve({})),
    updateUtilityFile: vi.fn(() => Promise.resolve({})),
    deleteUtilityFile: vi.fn(() => Promise.resolve({ status: "ok", module: "" })),
    listFiles: vi.fn(() => Promise.resolve({ items: [] })),
    readJson: vi.fn(() => Promise.resolve({})),
    getGitStatus: vi.fn(() => Promise.resolve({ branch: "main", is_main: true, is_read_only: false, changed_files: [], main_ahead: false, main_ahead_by: 0, main_last_updated: null })),
    listGitBranches: vi.fn(() => Promise.resolve({ current: "main", branches: [] })),
    createGitBranch: vi.fn(() => Promise.resolve({ branch: "" })),
    switchGitBranch: vi.fn(() => Promise.resolve({ status: "ok", branch: "" })),
    gitSave: vi.fn(() => Promise.resolve({ commit_sha: "", message: "", timestamp: "" })),
    gitSubmit: vi.fn(() => Promise.resolve({ compare_url: null, branch: "" })),
    getGitHistory: vi.fn(() => Promise.resolve({ entries: [] })),
  }
})

class MockResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

class MockWebSocket {
  static instances: MockWebSocket[] = []
  url: string
  onopen: (() => void) | null = null
  onmessage: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 0
  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }
  send(): void {}
  close(): void {
    this.readyState = 3
  }
}

import App from "../App"
import useGraphStore from "../stores/useGraphStore"
import useUIStore from "../stores/useUIStore"
import useToastStore from "../stores/useToastStore"
import useSettingsStore from "../stores/useSettingsStore"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import * as api from "../api/client"
import { DEFAULT_TARGET_HANDLE } from "../utils/flowHandles"

type ElementsFromPoint = (x: number, y: number) => Element[]

function resetAllStores(): void {
  useGraphStore.setState({
    nodes: [],
    edges: [],
    preamble: "",
    lastSavedSnapshot: null,
    undoStack: [],
    redoStack: [],
  })
  useUIStore.setState({
    paletteOpen: true,
    utilityOpen: false,
    importsOpen: false,
    gitOpen: false,
    shortcutsOpen: false,
    submodelDialog: null,
    renameDialog: null,
    syncBanner: null,
    nodePanelWidth: 0,
    ratingStepEditorSections: {},
    explorePanes: {},
    explorePreviewPanes: {},
    previewColumnWidths: {},
    hoveredNodeId: null,
    nodeSearchOpen: false,
  })
  useToastStore.setState({ toasts: [], _toastCounter: 0 })
  useNodeResultsStore.setState({
    previews: {},
    pinnedPreviewNodeId: null,
    columnCache: {},
    solveResults: {},
    solveJobs: {},
    trainResults: {},
    trainJobs: {},
    exploreResults: {},
    exploreJobs: {},
  })
  useSettingsStore.setState({
    rowLimit: 100,
    mlflow: {
      status: "pending",
      backend: "",
      host: "",
      installed: null,
      importable: null,
      trackingConfigured: null,
      detail: "",
    },
    _mlflowFetching: false,
    _mlflowLastAttempt: 0,
    sources: ["live"],
    activeSource: "live",
    fileListCache: {},
  })
}

async function waitForAppReady(): Promise<void> {
  await waitFor(
    () => {
      expect(screen.queryByText("Loading pipeline...")).toBeNull()
    },
    { timeout: 3000 },
  )
}

/** The seeded 2-emit-table Quote Input + consumer graph (no edges). */
function seedPipeline(): void {
  vi.mocked(api.loadPipeline).mockResolvedValueOnce({
    nodes: [
      {
        id: "quote_input_1",
        type: "apiInput",
        position: { x: 0, y: 0 },
        data: {
          label: "Quote Input",
          description: "",
          nodeType: "apiInput",
          config: {
            tables: [
              { label: "quotes", emit: true },
              { label: "policies", emit: true },
            ],
          },
        },
      },
      {
        id: "polars_1",
        type: "polars",
        position: { x: 600, y: 0 },
        data: { label: "Rater", description: "", nodeType: "polars", config: {} },
      },
    ],
    edges: [],
    preamble: "",
  })
}

/** Stub jsdom's missing elementsFromPoint to report a rendered node wrapper. */
function stubPointerOverNode(nodeId: string): void {
  const wrapper = document.querySelector(`.react-flow__node[data-id="${nodeId}"]`)
  expect(wrapper).not.toBeNull()
  ;(document as { elementsFromPoint?: ElementsFromPoint }).elementsFromPoint =
    vi.fn(() => [wrapper as Element]) as unknown as ElementsFromPoint
}

/**
 * jsdom performs no layout, so xyflow never measures nodes or handle
 * bounds (`measured: {}`, no `internals.handleBounds`). Install realistic
 * measurements onto the LIVE internal node record — the layout-engine
 * substitute this file already makes for getBoundingClientRect — so the
 * real `getInternalNode` → `resolveBodyDrop` path runs unmodified.
 */
function installNodeGeometry(
  nodeId: string,
  handleBounds: {
    source: Array<{ id: string | null; x: number; y: number; width: number; height: number }>
    target: Array<{ id: string | null; x: number; y: number; width: number; height: number }>
  },
): void {
  const internal = edgeHandlerParamsCapture.current!.getInternalNode(nodeId) as {
    measured: { width?: number; height?: number }
    internals: { handleBounds?: unknown }
  }
  expect(internal).toBeTruthy()
  internal.measured.width = 240
  internal.measured.height = 70
  internal.internals.handleBounds = handleBounds
}

const defaultInputBounds = { id: DEFAULT_TARGET_HANDLE, x: -4, y: 31, width: 8, height: 8 }

function dropForwardOnBody(fromNodeId: string, fromHandleId: string | null): void {
  act(() => {
    edgeHandlersCapture.current!.onConnectEnd(
      { clientX: 350, clientY: 300 } as MouseEvent,
      {
        isValid: null,
        fromNode: { id: fromNodeId },
        fromHandle: { id: fromHandleId, type: "source" },
        toNode: null,
        toHandle: null,
      },
    )
  })
}

let _originalElementGetBCR: Element["getBoundingClientRect"] | undefined
let _originalResizeObserver: typeof globalThis.ResizeObserver | undefined
let _originalWebSocket: typeof globalThis.WebSocket | undefined

beforeAll(() => {
  _originalElementGetBCR = Element.prototype.getBoundingClientRect
  _originalResizeObserver = (globalThis as unknown as { ResizeObserver?: typeof globalThis.ResizeObserver }).ResizeObserver
  _originalWebSocket = (globalThis as unknown as { WebSocket?: typeof globalThis.WebSocket }).WebSocket

  ;(globalThis as unknown as { ResizeObserver: typeof MockResizeObserver }).ResizeObserver = MockResizeObserver
  ;(globalThis as unknown as { WebSocket: typeof MockWebSocket }).WebSocket = MockWebSocket as unknown as typeof MockWebSocket

  const bcrStub = function getBoundingClientRect(this: Element): DOMRect {
    return {
      x: 0,
      y: 0,
      top: 0,
      left: 0,
      right: 800,
      bottom: 600,
      width: 800,
      height: 600,
      toJSON: () => ({}),
    } as DOMRect
  }
  Element.prototype.getBoundingClientRect = bcrStub as Element["getBoundingClientRect"]
})

afterAll(() => {
  if (_originalElementGetBCR) {
    Element.prototype.getBoundingClientRect = _originalElementGetBCR
  }
  if (_originalResizeObserver === undefined) {
    delete (globalThis as unknown as { ResizeObserver?: unknown }).ResizeObserver
  } else {
    ;(globalThis as unknown as { ResizeObserver: typeof globalThis.ResizeObserver }).ResizeObserver = _originalResizeObserver
  }
  if (_originalWebSocket === undefined) {
    delete (globalThis as unknown as { WebSocket?: unknown }).WebSocket
  } else {
    ;(globalThis as unknown as { WebSocket: typeof globalThis.WebSocket }).WebSocket = _originalWebSocket
  }
})

beforeEach(() => {
  resetAllStores()
  MockWebSocket.instances = []
  edgeHandlersCapture.current = null
  vi.mocked(api.loadPipeline).mockReset().mockResolvedValue({ nodes: [], edges: [], preamble: "" })
  vi.mocked(api.savePipeline).mockReset().mockResolvedValue({ file: "pipeline.py", pipeline_name: "main" })
  vi.mocked(api.checkMlflow).mockReset().mockResolvedValue({ mlflow_installed: false, backend: "", databricks_host: "" })
})

afterEach(() => {
  cleanup()
  delete (document as { elementsFromPoint?: ElementsFromPoint }).elementsFromPoint
  vi.clearAllTimers()
})

describe("App integration — whole-node drop targets", () => {
  it("persists a forward body drop as an exact-shape edge, undoable, never duplicated", async () => {
    seedPipeline()
    render(<App />)
    await waitForAppReady()
    await waitFor(() => {
      expect(screen.getByText("Rater")).toBeInTheDocument()
    })

    stubPointerOverNode("polars_1")
    installNodeGeometry("polars_1", {
      source: [{ id: null, x: 236, y: 31, width: 8, height: 8 }],
      target: [defaultInputBounds],
    })
    dropForwardOnBody("quote_input_1", "quotes")

    const expectedEdge = {
      id: "e_quote_input_1_polars_1_default_quotes",
      source: "quote_input_1",
      sourceHandle: "quotes",
      target: "polars_1",
      targetHandle: null,
    }
    await waitFor(() => {
      expect(useGraphStore.getState().edges).toEqual([expectedEdge])
    })

    // No-double-edge: repeating the gesture is a silent duplicate reject.
    dropForwardOnBody("quote_input_1", "quotes")
    expect(useGraphStore.getState().edges).toEqual([expectedEdge])

    // Save → the persisted payload carries the exact edge shape.
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }))
    await waitFor(() => {
      expect(vi.mocked(api.savePipeline)).toHaveBeenCalledTimes(1)
    })
    const [payload] = vi.mocked(api.savePipeline).mock.calls[0]
    expect(payload.graph.edges).toEqual([expectedEdge])
    // Named-absence pins: the sentinel never persists; no `""` handles;
    // no stray `data` payload leaks onto edges.
    expect(payload.graph.edges.every((e) => e.targetHandle !== DEFAULT_TARGET_HANDLE)).toBe(true)
    expect(payload.graph.edges.every((e) => e.sourceHandle !== "" && e.targetHandle !== "")).toBe(true)
    expect(payload.graph.edges[0]).not.toHaveProperty("data")

    // Undo removes the body-drop edge.
    fireEvent.click(screen.getByRole("button", { name: /^undo$/i }))
    await waitFor(() => {
      expect(useGraphStore.getState().edges).toEqual([])
    })
  })

  it("resolves a backward body drop onto the multi-port producer to an emit-table connector", async () => {
    seedPipeline()
    render(<App />)
    await waitForAppReady()
    await waitFor(() => {
      expect(screen.getByTestId("node-Quote Input")).toBeInTheDocument()
    })

    stubPointerOverNode("quote_input_1")
    installNodeGeometry("quote_input_1", {
      // Two stacked emit-table connectors down the right edge.
      source: [
        { id: "quotes", x: 236, y: 19, width: 8, height: 8 },
        { id: "policies", x: 236, y: 43, width: 8, height: 8 },
      ],
      target: [defaultInputBounds],
    })
    act(() => {
      edgeHandlersCapture.current!.onConnectEnd(
        // Screen y 300 maps well below both connector centres for any
        // sane viewport transform, so the LOWER port is nearest.
        { clientX: 350, clientY: 300 } as MouseEvent,
        {
          isValid: null,
          fromNode: { id: "polars_1" },
          fromHandle: { id: DEFAULT_TARGET_HANDLE, type: "target" },
          toNode: null,
          toHandle: null,
        },
      )
    })

    await waitFor(() => {
      expect(useGraphStore.getState().edges).toHaveLength(1)
    })
    // The backward body drop resolves to a REAL emit-table connector id
    // (the geometrically nearest, ruling 5), which flows verbatim into
    // the persisted sourceHandle.
    const expectedEdge = {
      id: "e_quote_input_1_polars_1_default_policies",
      source: "quote_input_1",
      sourceHandle: "policies",
      target: "polars_1",
      targetHandle: null,
    }
    expect(useGraphStore.getState().edges).toEqual([expectedEdge])

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }))
    await waitFor(() => {
      expect(vi.mocked(api.savePipeline)).toHaveBeenCalledTimes(1)
    })
    const [payload] = vi.mocked(api.savePipeline).mock.calls[0]
    expect(payload.graph.edges).toEqual([expectedEdge])
  })
})
