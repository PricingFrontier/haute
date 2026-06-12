/**
 * Phase 5 Wave 10D — Item #120: App integration test.
 *
 * Strategy
 * --------
 * The existing `App.test.tsx` mocks so much of the dependency tree (ReactFlow,
 * every hook, every panel) that the surviving assertions barely exercise the
 * real component — they check that stubs render their stubs.  This file
 * replaces that with an integration-style test that:
 *
 *   - Renders the real <App />, ReactFlowProvider, Toolbar, NodePalette,
 *     ReactFlow canvas, ErrorBoundary, ToastContainer, etc.
 *   - Mocks ONLY the network layer (`../api/client`) so no real HTTP
 *     traffic is attempted.
 *   - Uses the real zustand stores (useGraphStore, useUIStore, useToastStore,
 *     useSettingsStore, useNodeResultsStore) and resets them between tests.
 *   - Stubs browser primitives jsdom doesn't provide (ResizeObserver,
 *     getBoundingClientRect dimensions, WebSocket).  These are the bare
 *     minimum so ReactFlow's measure-pass doesn't crash — none of them
 *     replace application logic.
 *
 * What this does NOT mock
 * -----------------------
 *   - ReactFlow (the real `@xyflow/react` module renders the canvas).
 *   - Hooks (usePipelineAPI, useWebSocketSync, useGraphCanvasState, useTracing,
 *     useSubmodelNavigation, useKeyboardShortcuts, useBackgroundJobs,
 *     useNodeHandlers, useEdgeHandlers — all real).
 *   - Sub-components (Toolbar, NodePalette, NodePanel, DataPreview,
 *     TracePanel, UtilityPanel, ImportsPanel, GitPanel, Toast — all real).
 *
 * The tradeoff: these tests are slower than the stub-heavy unit tests they
 * replace, but they cover integration — hook wiring, store plumbing, prop
 * threading, conditional panel rendering — that the old file could not.
 * Target runtime is <5s for the whole file.
 */
import { describe, it, expect, vi, beforeAll, afterAll, beforeEach, afterEach } from "vitest"
import { render, screen, cleanup, fireEvent, waitFor, within } from "@testing-library/react"

// ═══════════════════════════════════════════════════════════════════════════
// Mock the network layer — `../api/client`.  Every exported function is
// a `vi.fn()` so individual tests can tailor resolution.  Types come from
// the real module via `typeof import(...)` for fidelity.
// ═══════════════════════════════════════════════════════════════════════════

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client")
  return {
    // Preserve the real ApiError class so `instanceof` checks in production
    // code still work.  Only the network functions are stubbed.
    ApiError: actual.ApiError,
    // Pipeline endpoints
    loadPipeline: vi.fn(() => Promise.resolve({ nodes: [], edges: [], preamble: "" })),
    previewNode: vi.fn(() => Promise.resolve({ node_id: "", status: "ok", columns: [], preview: [], row_count: 0, column_count: 0 })),
    savePipeline: vi.fn(() => Promise.resolve({ file: "pipeline.py", pipeline_name: "main" })),
    traceCell: vi.fn(() => Promise.resolve({ status: "ok" })),
    executeSink: vi.fn(() => Promise.resolve({ status: "ok" })),
    // Submodel
    createSubmodel: vi.fn(() => Promise.resolve({})),
    loadSubmodel: vi.fn(() => Promise.resolve({})),
    dissolveSubmodel: vi.fn(() => Promise.resolve({})),
    // Schema
    fetchSchema: vi.fn(() => Promise.resolve({ columns: [] })),
    fetchDatabricksSchema: vi.fn(() => Promise.resolve({ columns: [] })),
    // MLflow — checkMlflow is invoked on startup by useSettingsStore.
    checkMlflow: vi.fn(() => Promise.resolve({ mlflow_installed: false })),
    getTrainStatus: vi.fn(() => Promise.resolve({})),
    trainModel: vi.fn(() => Promise.resolve({})),
    estimateTrainingRam: vi.fn(() => Promise.resolve({})),
    logToMlflow: vi.fn(() => Promise.resolve({})),
    // Optimiser
    solveOptimiser: vi.fn(() => Promise.resolve({})),
    estimateOptimiserSolve: vi.fn(() => Promise.resolve({})),
    getOptimiserStatus: vi.fn(() => Promise.resolve({})),
    applyOptimiser: vi.fn(() => Promise.resolve({})),
    saveOptimiser: vi.fn(() => Promise.resolve({})),
    logOptimiserToMlflow: vi.fn(() => Promise.resolve({})),
    runFrontier: vi.fn(() => Promise.resolve({})),
    selectFrontierPoint: vi.fn(() => Promise.resolve({})),
    // Explore
    runExplore: vi.fn(() => Promise.resolve({ status: "started", job_id: "explore-job-1", cached: false, message: "started" })),
    getExploreStatus: vi.fn(() => Promise.resolve({ status: "running", progress: 0, message: "running", result: null })),
    cancelExplore: vi.fn(() => Promise.resolve({ status: "cancelled", progress: 1, message: "cancelled", result: null })),
    // Databricks
    getWarehouses: vi.fn(() => Promise.resolve({ warehouses: [] })),
    getCatalogs: vi.fn(() => Promise.resolve({ catalogs: [] })),
    getSchemas: vi.fn(() => Promise.resolve({ schemas: [] })),
    getTables: vi.fn(() => Promise.resolve({ tables: [] })),
    getCacheStatus: vi.fn(() => Promise.resolve({})),
    getFetchProgress: vi.fn(() => Promise.resolve({})),
    fetchDatabricksData: vi.fn(() => Promise.resolve({})),
    deleteCache: vi.fn(() => Promise.resolve({})),
    // JSON cache
    buildJsonCache: vi.fn(() => Promise.resolve({})),
    cancelJsonCache: vi.fn(() => Promise.resolve({ cancelled: false, data_path: "" })),
    getJsonCacheProgress: vi.fn(() => Promise.resolve({})),
    getJsonCacheStatus: vi.fn(() => Promise.resolve({})),
    getJsonCacheStatusForSchema: vi.fn(() => Promise.resolve({})),
    deleteJsonCache: vi.fn(() => Promise.resolve({ cached: false, data_path: "" })),
    // MLflow browse
    getExperiments: vi.fn(() => Promise.resolve([])),
    getRuns: vi.fn(() => Promise.resolve([])),
    getModels: vi.fn(() => Promise.resolve([])),
    getModelVersions: vi.fn(() => Promise.resolve([])),
    // Utility
    listUtilityFiles: vi.fn(() => Promise.resolve({ files: [] })),
    readUtilityFile: vi.fn(() => Promise.resolve({ name: "", module: "", content: "" })),
    createUtilityFile: vi.fn(() => Promise.resolve({})),
    updateUtilityFile: vi.fn(() => Promise.resolve({})),
    deleteUtilityFile: vi.fn(() => Promise.resolve({ status: "ok", module: "" })),
    // File browsing
    listFiles: vi.fn(() => Promise.resolve({ items: [] })),
    readJson: vi.fn(() => Promise.resolve({})),
    // Git
    getGitStatus: vi.fn(() => Promise.resolve({ branch: "main", ahead: 0, behind: 0, dirty: false, files: [] })),
    listGitBranches: vi.fn(() => Promise.resolve({ current: "main", branches: [] })),
    createGitBranch: vi.fn(() => Promise.resolve({ branch: "" })),
    switchGitBranch: vi.fn(() => Promise.resolve({ status: "ok", branch: "" })),
    gitSave: vi.fn(() => Promise.resolve({ commit_sha: "", message: "", timestamp: "" })),
    gitSubmit: vi.fn(() => Promise.resolve({ compare_url: null, branch: "" })),
    getGitHistory: vi.fn(() => Promise.resolve({ entries: [] })),
    gitRevert: vi.fn(() => Promise.resolve({ backup_tag: "", reverted_to: "" })),
    gitPull: vi.fn(() => Promise.resolve({ success: true, conflict: false, conflict_message: null, commits_pulled: 0 })),
    gitArchiveBranch: vi.fn(() => Promise.resolve({ archived_as: "" })),
    gitDeleteBranch: vi.fn(() => Promise.resolve({ status: "ok", branch: "" })),
  }
})

// ═══════════════════════════════════════════════════════════════════════════
// jsdom polyfills — the minimum needed so ReactFlow measures without crashing.
// ═══════════════════════════════════════════════════════════════════════════

class MockResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

// WebSocket is present in jsdom but trying to connect throws; replace with a
// no-op that stays in the "connecting" state and supports the handlers the
// hook assigns.  useWebSocketSync tolerates this by retrying — we just keep
// it quiet for the duration of the test.
class MockWebSocket {
  static instances: MockWebSocket[] = []
  url: string
  onopen: (() => void) | null = null
  onmessage: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 0 // CONNECTING
  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }
  send(): void {}
  close(): void {
    this.readyState = 3 // CLOSED
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// Imports (after mocks are declared)
// ═══════════════════════════════════════════════════════════════════════════

import App from "../App"
import useUIStore from "../stores/useUIStore"
import useGraphStore from "../stores/useGraphStore"
import useToastStore from "../stores/useToastStore"
import useSettingsStore from "../stores/useSettingsStore"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import * as api from "../api/client"

// ═══════════════════════════════════════════════════════════════════════════
// Test helpers
// ═══════════════════════════════════════════════════════════════════════════

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

/** Make a React Flow node with the minimum valid shape + a readable label. */
function makeNode(id: string, label: string, nodeType = "polars"): { id: string; type: string; position: { x: number; y: number }; data: Record<string, unknown> } {
  return {
    id,
    type: nodeType,
    position: { x: 0, y: 0 },
    data: { label, description: "", nodeType, config: {} },
  }
}

/**
 * Wait for the initial `loadPipeline()` promise to resolve so the app
 * transitions out of its "Loading pipeline..." state.  Returning control to
 * the tests only once the real layout is on screen keeps subsequent
 * queries honest.
 */
async function waitForAppReady(): Promise<void> {
  await waitFor(
    () => {
      expect(screen.queryByText("Loading pipeline...")).toBeNull()
    },
    { timeout: 3000 },
  )
}

// ═══════════════════════════════════════════════════════════════════════════
// Setup / teardown
// ═══════════════════════════════════════════════════════════════════════════

// Originals captured in ``beforeAll`` so ``afterAll`` can restore them.
// Without this round-trip the mutations below leak out of the file's
// vitest worker into the global prototype chain — vitest's worker
// isolation papers over this today, but the test file is no longer
// hermetic and a future move to shared-worker pools would silently
// break unrelated suites.
let _originalElementGetBCR: Element["getBoundingClientRect"] | undefined
let _originalRangeGetClientRects: Range["getClientRects"] | undefined
let _originalRangeGetBCR: Range["getBoundingClientRect"] | undefined
let _originalResizeObserver: typeof globalThis.ResizeObserver | undefined
let _originalWebSocket: typeof globalThis.WebSocket | undefined

beforeAll(() => {
  // Snapshot originals so afterAll can restore.  ``undefined`` is a
  // legitimate value (jsdom ships no Range.prototype.getClientRects at
  // all) so we distinguish via a sentinel property on the globalThis
  // caches.
  _originalElementGetBCR = Element.prototype.getBoundingClientRect
  _originalRangeGetClientRects = Range.prototype.getClientRects
  _originalRangeGetBCR = Range.prototype.getBoundingClientRect
  _originalResizeObserver = (globalThis as unknown as { ResizeObserver?: typeof globalThis.ResizeObserver }).ResizeObserver
  _originalWebSocket = (globalThis as unknown as { WebSocket?: typeof globalThis.WebSocket }).WebSocket

  // Install polyfills once for the whole file.

  ;(globalThis as unknown as { ResizeObserver: typeof MockResizeObserver }).ResizeObserver = MockResizeObserver
  ;(globalThis as unknown as { WebSocket: typeof MockWebSocket }).WebSocket = MockWebSocket as unknown as typeof MockWebSocket

  // Give elements measurable dimensions so ReactFlow's layout pass works.
  // Without this, internal `getBoundingClientRect()` returns zeros and
  // viewport calculations divide by zero.
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
  Object.defineProperty(bcrStub, "name", { value: "getBoundingClientRect [integration-stub]" })
  Element.prototype.getBoundingClientRect = bcrStub as Element["getBoundingClientRect"]

  // CodeMirror (used by UtilityPanel's CodeEditor) measures text by creating
  // a `Range` and calling `getClientRects()` on it.  jsdom does not
  // implement either `Range.prototype.getClientRects` or
  // `Range.prototype.getBoundingClientRect`, which crashes inside
  // CodeMirror's requestAnimationFrame callback AFTER the test has torn
  // down — surfacing as an "Unhandled Error" that taints the run summary.
  // Install minimal implementations matching CodeMirror's "no measurable
  // rects" code path so those rAF callbacks no-op cleanly.
  const getClientRectsStub = function getClientRects(this: Range): DOMRectList {
    const list: unknown = { length: 0, item: () => null, [Symbol.iterator]: function* () {} }
    return list as DOMRectList
  }
  Range.prototype.getClientRects = getClientRectsStub as Range["getClientRects"]

  const rangeGetBCRStub = function rangeGetBoundingClientRect(this: Range): DOMRect {
    return { x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({}) } as DOMRect
  }
  Range.prototype.getBoundingClientRect = rangeGetBCRStub as Range["getBoundingClientRect"]
})

afterAll(() => {
  // Restore every mutated global.  We use an ``as any`` write path for
  // Range.prototype because jsdom ships with ``undefined`` there and
  // TypeScript rejects assigning ``undefined`` to a non-optional
  // property; runtime behaviour matches the pre-test state.
  if (_originalElementGetBCR) {
    Element.prototype.getBoundingClientRect = _originalElementGetBCR
  }
  ;(Range.prototype as unknown as { getClientRects?: Range["getClientRects"] }).getClientRects = _originalRangeGetClientRects
  ;(Range.prototype as unknown as { getBoundingClientRect?: Range["getBoundingClientRect"] }).getBoundingClientRect = _originalRangeGetBCR
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

  // Reset all api mocks to their default resolution (empty graph, success).
  vi.mocked(api.loadPipeline).mockReset().mockResolvedValue({ nodes: [], edges: [], preamble: "" })
  vi.mocked(api.savePipeline).mockReset().mockResolvedValue({ file: "pipeline.py", pipeline_name: "main" })
  vi.mocked(api.previewNode).mockReset().mockResolvedValue({ node_id: "", status: "ok", columns: [], preview: [], row_count: 0, column_count: 0 })
  vi.mocked(api.runExplore).mockReset().mockResolvedValue({ status: "started", job_id: "explore-job-1", cached: false, message: "started" })
  vi.mocked(api.getExploreStatus).mockReset().mockResolvedValue({ status: "running", progress: 0, message: "running", result: null })
  vi.mocked(api.cancelExplore).mockReset().mockResolvedValue({ status: "cancelled", progress: 1, message: "cancelled", result: null })
  vi.mocked(api.checkMlflow).mockReset().mockResolvedValue({ mlflow_installed: false, backend: "", databricks_host: "" })
  vi.mocked(api.listUtilityFiles).mockReset().mockResolvedValue({ files: [] })
  vi.mocked(api.getGitStatus).mockReset().mockResolvedValue({ branch: "main", is_main: true, is_read_only: false, changed_files: [], main_ahead: false, main_ahead_by: 0, main_last_updated: null })
  vi.mocked(api.listGitBranches).mockReset().mockResolvedValue({ current: "main", branches: [] })
})

afterEach(() => {
  cleanup()
  vi.clearAllTimers()
})

// ═══════════════════════════════════════════════════════════════════════════
// Tests
// ═══════════════════════════════════════════════════════════════════════════

describe("App integration — mounts and renders main chrome", () => {
  it("does not open websocket sync while the initial pipeline load is pending", async () => {
    let resolveLoad!: (value: { nodes: []; edges: []; preamble: string }) => void
    vi.mocked(api.loadPipeline).mockImplementationOnce(
      () => new Promise((resolve) => {
        resolveLoad = resolve
      }),
    )

    render(<App />)

    expect(screen.getByText("Loading pipeline...")).toBeInTheDocument()
    expect(MockWebSocket.instances).toHaveLength(0)

    resolveLoad({ nodes: [], edges: [], preamble: "" })
    await waitForAppReady()

    expect(MockWebSocket.instances).toHaveLength(1)
    expect(MockWebSocket.instances[0].url).toBe("ws://localhost:3000/ws/sync")
  })

  it("mounts without error and shows the toolbar + canvas once loading completes", async () => {
    render(<App />)
    await waitForAppReady()
    // Toolbar
    expect(screen.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeInTheDocument()
    // Node palette (left sidebar)
    expect(screen.getByRole("navigation", { name: /node palette/i })).toBeInTheDocument()
    // Node-properties panel (right side)
    expect(screen.getByRole("complementary", { name: /node properties/i })).toBeInTheDocument()
    // Save button (toolbar's primary action)
    expect(screen.getByRole("button", { name: /^save$/i })).toBeInTheDocument()
  })

  it("calls loadPipeline on mount", async () => {
    render(<App />)
    await waitForAppReady()
    expect(vi.mocked(api.loadPipeline)).toHaveBeenCalledTimes(1)
  })
})

describe("App integration — empty pipeline state", () => {
  it("renders no application nodes when the pipeline is empty", async () => {
    render(<App />)
    await waitForAppReady()
    // With zero nodes, the store should be empty.
    expect(useGraphStore.getState().nodes).toEqual([])
  })

  it("exposes the toolbar's primary palette + utility affordances", async () => {
    render(<App />)
    await waitForAppReady()
    // Utility, Imports, Git buttons are clickable (not disabled).
    const utility = screen.getByRole("button", { name: /^utility$/i })
    const imports = screen.getByRole("button", { name: /^imports$/i })
    const git = screen.getByRole("button", { name: /^git$/i })
    expect(utility).toBeEnabled()
    expect(imports).toBeEnabled()
    expect(git).toBeEnabled()
  })

  it("disables Centre + Layout when there are zero nodes", async () => {
    render(<App />)
    await waitForAppReady()
    const centre = screen.getByRole("button", { name: /^centre$/i })
    const layout = screen.getByRole("button", { name: /^layout$/i })
    expect(centre).toBeDisabled()
    expect(layout).toBeDisabled()
  })
})

describe("App integration — load a pipeline with nodes", () => {
  it("renders node labels from a 3-node graph returned by loadPipeline", async () => {
    // Use labels that deliberately do NOT collide with palette names
    // (e.g. "Data Source", "Model Training") so getByText is unambiguous.
    vi.mocked(api.loadPipeline).mockResolvedValueOnce({
      nodes: [
        makeNode("ds_0", "CustomerDB Loader", "dataSource"),
        makeNode("polars_1", "Feature Cleanup", "polars"),
        makeNode("output_2", "Final Quote Payload", "output"),
      ],
      edges: [
        { id: "e1", source: "ds_0", target: "polars_1" },
        { id: "e2", source: "polars_1", target: "output_2" },
      ],
      preamble: "",
    })
    render(<App />)
    await waitForAppReady()

    // Labels come out of the real PipelineNode render — ReactFlow mounts
    // a node per graph entry, each using the registered PipelineNode which
    // renders `data.label`.
    await waitFor(() => {
      expect(screen.getByText("CustomerDB Loader")).toBeInTheDocument()
      expect(screen.getByText("Feature Cleanup")).toBeInTheDocument()
      expect(screen.getByText("Final Quote Payload")).toBeInTheDocument()
    })

    // The store should reflect the loaded graph too.
    expect(useGraphStore.getState().nodes).toHaveLength(3)
    expect(useGraphStore.getState().edges).toHaveLength(2)
  })

  it("enables Centre + Layout once nodes are loaded", async () => {
    vi.mocked(api.loadPipeline).mockResolvedValueOnce({
      nodes: [makeNode("polars_0", "Node A")],
      edges: [],
      preamble: "",
    })
    render(<App />)
    await waitForAppReady()
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^centre$/i })).toBeEnabled()
      expect(screen.getByRole("button", { name: /^layout$/i })).toBeEnabled()
    })
  })

  it("selecting an Explore node previews the post-code dataframe in the Explore lower panel", async () => {
    const sourceNode = makeNode("source_0", "Claims Source", "dataSource")
    sourceNode.data._columns = [{ name: "premium", dtype: "i64" }]
    sourceNode.data._availableColumns = [{ name: "premium", dtype: "i64" }]
    vi.mocked(api.loadPipeline).mockResolvedValueOnce({
      nodes: [
        sourceNode,
        makeNode("explore_1", "Claims Explore", "explore"),
      ],
      edges: [{ id: "e1", source: "source_0", target: "explore_1" }],
      preamble: "",
    })
    useSettingsStore.setState({ rowLimit: 2 })
    vi.mocked(api.previewNode).mockResolvedValueOnce({
      node_id: "explore_1",
      status: "ok",
      columns: [
        { name: "premium", dtype: "i64" },
        { name: "premium_plus_one", dtype: "i64" },
      ],
      preview_columns: ["premium", "premium_plus_one"],
      preview: [
        { premium: 10, premium_plus_one: 11 },
        { premium: 20, premium_plus_one: 21 },
      ],
      preview_row_count: 2,
      preview_row_limit: 2,
      preview_truncated: true,
      row_count: 3,
      column_count: 2,
    })
    vi.mocked(api.previewNode).mockResolvedValueOnce({
      node_id: "explore_1",
      status: "ok",
      columns: [
        { name: "premium", dtype: "i64" },
        { name: "premium_plus_one", dtype: "i64" },
      ],
      preview_columns: ["premium", "premium_plus_one"],
      preview: [
        { premium: 30, premium_plus_one: 31 },
        { premium: 40, premium_plus_one: 41 },
      ],
      preview_row_count: 2,
      preview_row_limit: 2,
      preview_truncated: true,
      row_count: 4,
      column_count: 2,
    })

    render(<App />)
    await waitForAppReady()
    const exploreNode = await screen.findByText("Claims Explore")
    fireEvent.click(exploreNode)

    expect(await screen.findByRole("button", { name: /process & cache full data/i })).toBeInTheDocument()
    await waitFor(() => expect(vi.mocked(api.previewNode)).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.previewNode)).toHaveBeenCalledWith(expect.objectContaining({
      nodeId: "explore_1",
      rowLimit: 2,
      source: "live",
    }))
    expect(await screen.findByTestId("data-preview-embedded")).toBeInTheDocument()
    expect(screen.getByText("premium_plus_one")).toBeInTheDocument()
    expect(screen.getByText("11")).toBeInTheDocument()
    expect(screen.getByText(/Showing 2 of 3 rows/)).toBeInTheDocument()

    fireEvent.click(screen.getByTitle("Refresh Explore outputs"))

    await waitFor(() => expect(vi.mocked(api.previewNode)).toHaveBeenCalledTimes(2))
    expect(vi.mocked(api.previewNode)).toHaveBeenLastCalledWith(expect.objectContaining({
      nodeId: "explore_1",
      rowLimit: 2,
      source: "live",
    }))
    expect(await screen.findByText("31")).toBeInTheDocument()
    expect(screen.getByText(/Showing 2 of 4 rows/)).toBeInTheDocument()
  })
})

describe("App integration — add a node via drag-and-drop from the palette", () => {
  // Note: there is no dedicated 'Add Node' button in the current Toolbar —
  // the canonical add-node flow is dragging a palette item onto the canvas.
  // This test exercises that flow end-to-end: simulate a drop on the
  // ReactFlow canvas with the palette's data-transfer payload and assert
  // that a new node enters the graph store.
  it("drop on the canvas appends a new node to the graph store", async () => {
    render(<App />)
    await waitForAppReady()

    // Locate the React Flow canvas container.  ReactFlow renders a
    // <div class="react-flow"> at the root of the canvas region.
    const canvas = document.querySelector(".react-flow") as HTMLElement | null
    expect(canvas, "ReactFlow canvas container rendered").not.toBeNull()

    // The before-count lets us assert the drop added exactly one node.
    const before = useGraphStore.getState().nodes.length

    // jsdom does not implement DataTransfer, so construct a minimal
    // in-memory shim compatible with the subset of the API that
    // useEdgeHandlers.onDrop reads: `getData(type)`, `setData(type, v)`,
    // and the `dropEffect` property written during onDragOver.
    const store = new Map<string, string>()
    const dataTransfer = {
      dropEffect: "none",
      effectAllowed: "move",
      getData: (type: string): string => store.get(type) ?? "",
      setData: (type: string, value: string): void => {
        store.set(type, value)
      },
      clearData: (type?: string): void => {
        if (type) store.delete(type)
        else store.clear()
      },
      types: [] as ReadonlyArray<string>,
      files: [] as unknown as FileList,
      items: [] as unknown as DataTransferItemList,
    }
    dataTransfer.setData("application/reactflow-type", "polars")
    dataTransfer.setData("application/reactflow-config", JSON.stringify({}))

    // ReactFlow's `onDrop` is bound to the container's onDrop prop — fire
    // a dragover (required by the handler) then a drop event on it.
    fireEvent.dragOver(canvas!, { dataTransfer })
    fireEvent.drop(canvas!, { dataTransfer })

    await waitFor(() => {
      expect(useGraphStore.getState().nodes.length).toBe(before + 1)
    })
  })
})

describe("App integration — save pipeline", () => {
  it("clicking Save calls savePipeline with the current graph serialized", async () => {
    vi.mocked(api.loadPipeline).mockResolvedValueOnce({
      nodes: [makeNode("polars_0", "Transform A")],
      edges: [],
      preamble: "import polars as pl",
      pipeline_name: "pricing",
      source_file: "pricing.py",
    })
    render(<App />)
    await waitForAppReady()

    // Wait for the loaded label to appear so we know graphRef has been
    // populated (handleSave reads graphRef, not a React-state snapshot).
    await waitFor(() => {
      expect(screen.getByText("Transform A")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }))

    await waitFor(() => {
      expect(vi.mocked(api.savePipeline)).toHaveBeenCalledTimes(1)
    })
    const [payload] = vi.mocked(api.savePipeline).mock.calls[0]
    expect(payload.name).toBe("pricing")
    expect(payload.source_file).toBe("pricing.py")
    expect(payload.preamble).toBe("import polars as pl")
    expect(payload.graph.nodes).toHaveLength(1)
    expect(payload.graph.nodes[0].id).toBe("polars_0")
    expect(payload.graph.edges).toEqual([])
  })

  it("shows a success toast when savePipeline resolves", async () => {
    vi.mocked(api.savePipeline).mockResolvedValueOnce({ file: "demo.py", pipeline_name: "demo" })
    render(<App />)
    await waitForAppReady()

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }))

    await waitFor(() => {
      // Toast container is role="alert" per Toast.tsx — the message text
      // includes the saved file path.
      expect(screen.getByRole("alert")).toHaveTextContent(/demo\.py/i)
    })
  })
})

describe("App integration — error handling", () => {
  it("shows an error toast when loadPipeline rejects, without crashing", async () => {
    vi.mocked(api.loadPipeline).mockRejectedValueOnce(new Error("Backend offline"))

    render(<App />)
    // The app should recover into the loaded (non-loading) state even
    // though loadPipeline failed, and surface the error via the toast
    // container.
    await waitForAppReady()
    await waitFor(() => {
      const alert = screen.getByRole("alert")
      expect(alert).toHaveTextContent(/failed to load pipeline/i)
      expect(alert).toHaveTextContent(/backend offline/i)
    })

    // And the toolbar still rendered — no crash.
    expect(screen.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeInTheDocument()
  })

  it("shows an error toast when savePipeline rejects", async () => {
    vi.mocked(api.savePipeline).mockRejectedValueOnce(new Error("disk full"))
    render(<App />)
    await waitForAppReady()

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }))

    await waitFor(() => {
      const alert = screen.getByRole("alert")
      expect(alert).toHaveTextContent(/failed to save pipeline/i)
      expect(alert).toHaveTextContent(/disk full/i)
    })
  })
})

describe("App integration — panel open/close", () => {
  it("clicking Utility opens the UtilityPanel; close button dismisses it", async () => {
    render(<App />)
    await waitForAppReady()

    // UtilityPanel is NOT in the DOM before opening.
    expect(useUIStore.getState().utilityOpen).toBe(false)

    fireEvent.click(screen.getByRole("button", { name: /^utility$/i }))

    // Store flipped — the real UIStore setter is invoked.
    await waitFor(() => {
      expect(useUIStore.getState().utilityOpen).toBe(true)
    })

    // The panel mounts inside the aside labelled "Node properties".
    const aside = screen.getByRole("complementary", { name: /node properties/i })
    // UtilityPanel renders a PanelShell with a "Close" button.
    const closeBtn = within(aside).queryByRole("button", { name: /close/i })
    expect(closeBtn, "Utility panel close button").not.toBeNull()

    fireEvent.click(closeBtn!)
    await waitFor(() => {
      expect(useUIStore.getState().utilityOpen).toBe(false)
    })
  })

  it("clicking Imports opens the ImportsPanel (mutually exclusive with Utility)", async () => {
    render(<App />)
    await waitForAppReady()

    // Open Utility first to prove mutual-exclusion in the store.
    fireEvent.click(screen.getByRole("button", { name: /^utility$/i }))
    await waitFor(() => expect(useUIStore.getState().utilityOpen).toBe(true))

    fireEvent.click(screen.getByRole("button", { name: /^imports$/i }))
    await waitFor(() => {
      expect(useUIStore.getState().importsOpen).toBe(true)
      // The UIStore setter resets the other panels' flags.
      expect(useUIStore.getState().utilityOpen).toBe(false)
    })
  })

  it("clicking Git opens the GitPanel (mutually exclusive with Utility/Imports)", async () => {
    render(<App />)
    await waitForAppReady()

    fireEvent.click(screen.getByRole("button", { name: /^git$/i }))
    await waitFor(() => {
      expect(useUIStore.getState().gitOpen).toBe(true)
      expect(useUIStore.getState().utilityOpen).toBe(false)
      expect(useUIStore.getState().importsOpen).toBe(false)
    })
  })
})

describe("App integration — node-type tooltips are persistence-inert (tooltips-descriptions §5.2-G)", () => {
  it("opening and closing palette + canvas tooltips leaves the graph clean and never saves", async () => {
    // Tooltips must never touch graph state: the hover gesture is pure
    // observation. This is rule 1/2 of AGENTS.md §UI Test Assertions as a
    // negative — the feature is proven persistence-inert.
    vi.mocked(api.loadPipeline).mockResolvedValueOnce({
      nodes: [makeNode("polars_0", "Feature Cleanup")],
      edges: [],
      preamble: "",
    })
    render(<App />)
    await waitForAppReady()
    await waitFor(() => {
      expect(screen.getByText("Feature Cleanup")).toBeInTheDocument()
    })

    // Palette tooltip: sustained hover through the real 300 ms delay.
    const paletteItem = screen.getByTestId("node-palette-item-dataSource")
    fireEvent.mouseEnter(paletteItem)
    await waitFor(
      () => {
        expect(screen.getByTestId("node-type-tooltip")).toBeInTheDocument()
      },
      { timeout: 2000 },
    )
    fireEvent.mouseLeave(paletteItem)
    expect(screen.queryByTestId("node-type-tooltip")).not.toBeInTheDocument()

    // Canvas tooltip: whole-node-body trigger through the real
    // CANVAS_TOOLTIP_DELAY_MS (700 ms) delay.
    const canvasNode = screen.getByTestId("node-Feature Cleanup")
    fireEvent.mouseEnter(canvasNode)
    await waitFor(
      () => {
        expect(screen.getByTestId("node-type-tooltip")).toBeInTheDocument()
      },
      { timeout: 3000 },
    )
    fireEvent.mouseLeave(canvasNode)
    expect(screen.queryByTestId("node-type-tooltip")).not.toBeInTheDocument()

    expect(useGraphStore.getState().dirty).toBe(false)
    expect(vi.mocked(api.savePipeline)).not.toHaveBeenCalled()
  })
})

describe("App integration — preview column resize is persistence-inert (datapreview-column-resize §5.4)", () => {
  // Fixture note: no edge-join nodes here — handleSave runs a save-blocking
  // edge-join validation (findFirstInvalidEdgeJoin) before calling
  // savePipeline; an invalid edge-join fixture would abort the save and make
  // `mock.calls[0]` undefined for reasons unrelated to column resize.
  const PREVIEW_COLUMNS = [
    { name: "premium", dtype: "f64" },
    { name: "age", dtype: "i64" },
  ]

  async function loadSelectAndResize(): Promise<void> {
    // The loaded node already carries the schema stamp (`_columns` et al.)
    // the preview will write back, so the preview's setNodesRaw is
    // fingerprint-neutral and `dirty === false` below isolates the RESIZE
    // gesture rather than re-testing the preview stamping path.
    const node = makeNode("polars_0", "Wide Transform")
    node.data._columns = PREVIEW_COLUMNS
    node.data._availableColumns = PREVIEW_COLUMNS
    node.data._schemaWarnings = []
    vi.mocked(api.loadPipeline).mockResolvedValueOnce({
      nodes: [node],
      edges: [],
      preamble: "",
    })
    vi.mocked(api.previewNode).mockResolvedValue({
      node_id: "polars_0",
      status: "ok",
      columns: PREVIEW_COLUMNS,
      available_columns: PREVIEW_COLUMNS,
      schema_warnings: [],
      preview: [{ premium: 100.5, age: 25 }],
      row_count: 1,
      column_count: 2,
    })

    render(<App />)
    await waitForAppReady()
    const nodeEl = await screen.findByText("Wide Transform")
    fireEvent.click(nodeEl)

    // Selecting the node fetches and renders the bottom-panel DataPreview.
    const handle = await screen.findByTestId("data-preview-col-resize-premium")

    // Pre-drag sanity: the load + select + preview round-trip left the
    // graph clean — anything dirty after the drag is resize-caused.
    expect(useGraphStore.getState().dirty).toBe(false)

    fireEvent.mouseDown(handle, { clientX: 300 })
    fireEvent.mouseMove(document, { clientX: 460 })
    fireEvent.mouseUp(document, { clientX: 460 })

    // The gesture landed in the UI store (sanity, not the contract itself).
    expect(useUIStore.getState().previewColumnWidths.polars_0).toEqual({ premium: 320 })
  }

  it("preview column resize must not mark the pipeline dirty — view state must never enter the graph fingerprint", async () => {
    await loadSelectAndResize()

    const nodesAfter = useGraphStore.getState().nodes
    expect(useGraphStore.getState().dirty).toBe(false)
    expect(nodesAfter).toHaveLength(1)
    expect(nodesAfter[0].data.config).toEqual({})
  })

  it("save payload carries no column-width keys after a resize (named absence)", async () => {
    await loadSelectAndResize()
    const configBefore = JSON.parse(JSON.stringify(useGraphStore.getState().nodes[0].data.config))

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }))
    await waitFor(() => {
      expect(vi.mocked(api.savePipeline)).toHaveBeenCalledTimes(1)
    })

    const [payload] = vi.mocked(api.savePipeline).mock.calls[0]
    expect(payload.graph.nodes).toHaveLength(1)
    for (const node of payload.graph.nodes) {
      const cfg = node.data.config as Record<string, unknown>
      expect(cfg).toEqual(configBefore)
      expect(cfg).not.toHaveProperty("columnWidths")
      expect(cfg).not.toHaveProperty("previewColumnWidths")
      expect(node.data).not.toHaveProperty("columnWidths")
      expect(node.data).not.toHaveProperty("previewColumnWidths")
    }
  })
})
