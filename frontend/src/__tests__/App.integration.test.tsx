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
import { render, screen, cleanup, fireEvent, waitFor, within, act } from "@testing-library/react"

// ═══════════════════════════════════════════════════════════════════════════
// Mock the network layer — `../api/client`.  Every exported function is
// a `vi.fn()` so individual tests can tailor resolution.  Types come from
// the real module via `typeof import(...)` for fidelity.
// ═══════════════════════════════════════════════════════════════════════════

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client")
  return {
    // Preserve real non-network exports so production `instanceof` checks and
    // local-session event wiring keep their normal behaviour. Only network
    // functions are stubbed below.
    ApiError: actual.ApiError,
    ApiTimeoutError: actual.ApiTimeoutError,
    HAUTE_SESSION_EXPIRED_EVENT: actual.HAUTE_SESSION_EXPIRED_EVENT,
    HAUTE_SESSION_EXPIRED_REASON: actual.HAUTE_SESSION_EXPIRED_REASON,
    isHauteSessionExpiredReason: actual.isHauteSessionExpiredReason,
    isHauteSessionExpiredError: actual.isHauteSessionExpiredError,
    notifyHauteSessionExpired: actual.notifyHauteSessionExpired,
    bootstrapHauteSession: vi.fn(() => Promise.resolve()),
    checkHauteSession: vi.fn(() => Promise.resolve({ ok: true })),
    // Pipeline endpoints
    loadPipeline: vi.fn(() => Promise.resolve({ nodes: [], edges: [], preamble: "", preserved_blocks: [], source_revision: "revision-test" })),
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
    // JSON cache
    buildJsonCache: vi.fn(() => Promise.resolve({})),
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
    getWorkingBranch: vi.fn(() =>
      Promise.resolve({
        working_branch: "dev",
        state: "ready",
        errors: [],
        current_branch: "dev-save",
        last_save_sha: "abc1234def",
        eligible_branches: ["dev"],
        identity_set: true,
        user_name: "Test User",
        user_email: "test@example.com",
      }),
    ),
    setWorkingBranch: vi.fn(() =>
      Promise.resolve({ working_branch: "dev", state: "ready", last_save_sha: null }),
    ),
    setGitIdentity: vi.fn(() =>
      Promise.resolve({ user_name: "", user_email: "", scope: "local" }),
    ),
    commitMilestone: vi.fn(() =>
      Promise.resolve({
        sha: "deadbeef0000",
        short_sha: "deadbee",
        working_branch: "dev",
        version_label: null,
      }),
    ),
    getMilestones: vi.fn(() => Promise.resolve({ working_branch: "dev", entries: [] })),
    moveToVersion: vi.fn(() =>
      Promise.resolve({
        sha: "abc1234def",
        short_sha: "abc1234",
        prior_branch: "dev-save",
        is_detached: true,
      }),
    ),
    gitArchiveBranch: vi.fn(() => Promise.resolve({ archived_as: "" })),
    gitDeleteBranch: vi.fn(() => Promise.resolve({ status: "ok", branch: "" })),
    getGitRemotes: vi.fn(() => Promise.resolve({ remotes: [], working_branch: "dev" })),
    gitPush: vi.fn(() =>
      Promise.resolve({
        remote: "origin",
        working_branch: "dev",
        ledger_branch: "dev-save",
        pushed_refs: ["dev", "dev-save"],
        default_branch: "main",
        bootstrapped_default: false,
      }),
    ),
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
import useGitStore from "../stores/useGitStore"
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
    hoveredNodeId: null,
    nodeSearchOpen: false,
  })
  useToastStore.setState({ toasts: [], _toastCounter: 0 })
  useGitStore.setState({
    status: null,
    loading: false,
    modal: null,
    pendingAction: null,
    moveTarget: null,
  })
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

/**
 * First lookup after opening a node editor. Editor components are
 * lazy-loaded chunks, so the initial mount pays a dynamic import; under
 * full-suite worker contention that can exceed findBy's 1s default, so the
 * first query after opening an editor waits with an explicit timeout.
 */
async function findEditorTestId(id: string): Promise<HTMLElement> {
  return await screen.findByTestId(id, {}, { timeout: 10000 })
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
  vi.mocked(api.loadPipeline).mockReset().mockResolvedValue({ nodes: [], edges: [], preamble: "", preserved_blocks: [], source_revision: "revision-test" })
  vi.mocked(api.savePipeline).mockReset().mockResolvedValue({ file: "pipeline.py", pipeline_name: "main", source_revision: "revision-test" })
  vi.mocked(api.previewNode).mockReset().mockResolvedValue({ node_id: "", status: "ok", columns: [], preview: [], row_count: 0, column_count: 0 })
  vi.mocked(api.runExplore).mockReset().mockResolvedValue({ status: "started", job_id: "explore-job-1", cached: false, message: "started" })
  vi.mocked(api.getExploreStatus).mockReset().mockResolvedValue({ status: "running", progress: 0, message: "running", result: null })
  vi.mocked(api.cancelExplore).mockReset().mockResolvedValue({ status: "cancelled", progress: 1, message: "cancelled", result: null })
  vi.mocked(api.checkMlflow).mockReset().mockResolvedValue({
    mlflow_installed: false,
    mlflow_importable: false,
    tracking_configured: false,
    backend: "",
    databricks_host: "",
  })
  vi.mocked(api.listUtilityFiles).mockReset().mockResolvedValue({ files: [] })
  // Default to a healthy clone so the startup modal stays closed; tests that
  // need unset/divergent override with mockResolvedValue inside the test.
  vi.mocked(api.getWorkingBranch).mockReset().mockResolvedValue({ working_branch: "dev", state: "ready", errors: [], current_branch: "dev-save", last_save_sha: "abc1234def", eligible_branches: ["dev"], identity_set: true, user_name: "Test User", user_email: "test@example.com" })
  vi.mocked(api.setWorkingBranch).mockReset().mockResolvedValue({ working_branch: "dev", state: "ready", last_save_sha: null })
  vi.mocked(api.setGitIdentity).mockReset().mockResolvedValue({ user_name: "", user_email: "", scope: "local" })
  vi.mocked(api.commitMilestone).mockReset().mockResolvedValue({ sha: "deadbeef0000", short_sha: "deadbee", working_branch: "dev", version_label: null })
  vi.mocked(api.getMilestones).mockReset().mockResolvedValue({ working_branch: "dev", entries: [] })
  vi.mocked(api.moveToVersion).mockReset().mockResolvedValue({ sha: "abc1234def", short_sha: "abc1234", prior_branch: "dev-save", is_detached: true })
  useGitStore.setState({ status: null, loading: false, modal: null, pendingAction: null, moveTarget: null })
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
    let resolveLoad!: (value: {
      nodes: []
      edges: []
      preamble: string
      preserved_blocks: string[]
      source_revision: string
    }) => void
    vi.mocked(api.loadPipeline).mockImplementationOnce(
      () => new Promise((resolve) => {
        resolveLoad = resolve
      }),
    )

    render(<App />)

    expect(screen.getByText("Loading pipeline...")).toBeInTheDocument()
    expect(MockWebSocket.instances).toHaveLength(0)

    resolveLoad({ nodes: [], edges: [], preamble: "", preserved_blocks: [], source_revision: "revision-test" })
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

  it("shows a reload affordance when the local session expires", async () => {
    const originalLocation = window.location
    const reload = vi.fn()
    Object.defineProperty(window, "location", {
      value: { ...originalLocation, reload },
      configurable: true,
    })

    render(<App />)
    await waitForAppReady()

    window.dispatchEvent(new CustomEvent(api.HAUTE_SESSION_EXPIRED_EVENT, {
      detail: { reason: "Missing or invalid Haute session token" },
    }))

    const alert = await screen.findByRole("alert")
    expect(alert).toHaveTextContent("Session expired")
    fireEvent.click(within(alert).getByRole("button", { name: /^reload$/i }))
    expect(reload).toHaveBeenCalledTimes(1)

    Object.defineProperty(window, "location", {
      value: originalLocation,
      configurable: true,
    })
  })
})

describe("App integration — move failure recovery", () => {
  it("closes the busy move modal when the checkout request rejects", async () => {
    vi.mocked(api.moveToVersion).mockRejectedValueOnce(new Error("checkout failed"))
    render(<App />)
    await waitForAppReady()

    act(() => {
      useGitStore.getState().requestMove({ sha: "target-sha", label: "v2.0" })
    })
    fireEvent.click(await screen.findByTestId("move-confirm"))

    await waitFor(() => {
      expect(vi.mocked(api.moveToVersion)).toHaveBeenCalledWith("target-sha")
      expect(useGitStore.getState().moveTarget).toBeNull()
    })
    expect(screen.queryByTestId("move-confirm-modal")).not.toBeInTheDocument()
    expect(useToastStore.getState().toasts).toContainEqual(
      expect.objectContaining({
        type: "error",
        text: "Could not move to this version: checkout failed",
      }),
    )
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
    // Utility + Imports buttons are clickable (not disabled). The standalone
    // Git button was removed in favour of VC's branch indicator, which is the
    // single entry point into the version-control pane.
    const utility = screen.getByRole("button", { name: /^utility$/i })
    const imports = screen.getByRole("button", { name: /^imports$/i })
    expect(utility).toBeEnabled()
    expect(imports).toBeEnabled()
    expect(screen.queryByRole("button", { name: /^git$/i })).not.toBeInTheDocument()
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
        makeNode("ds_0", "CustomerDB Loader", "dataInput"),
        makeNode("polars_1", "Feature Cleanup", "polars"),
        makeNode("output_2", "Final Quote Payload", "output"),
      ],
      edges: [
        { id: "e1", source: "ds_0", target: "polars_1" },
        { id: "e2", source: "polars_1", target: "output_2" },
      ],
      preamble: "",
      preserved_blocks: [],
      source_revision: "revision-test",
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
      preserved_blocks: [],
      source_revision: "revision-test",
    })
    render(<App />)
    await waitForAppReady()
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^centre$/i })).toBeEnabled()
      expect(screen.getByRole("button", { name: /^layout$/i })).toBeEnabled()
    })
  })

  it("selecting an Explore node previews the post-code dataframe in the Explore lower panel", async () => {
    const sourceNode = makeNode("source_0", "Claims Source", "dataInput")
    // Direct Parquet by derivation, so the pre-preview snapshot-ensure stage
    // skips this source instead of querying the unmocked input-cache API.
    sourceNode.data.config = {
      inputType: "file",
      format: "parquet",
      mode: "scan",
      path: "data/claims.parquet",
      arguments: {},
    }
    sourceNode.data._columns = [{ name: "premium", dtype: "i64" }]
    sourceNode.data._availableColumns = [{ name: "premium", dtype: "i64" }]
    // Tag the stash with its capture source — untagged stashes are treated
    // as unknown provenance and invalidated on mount (cache-key completeness).
    sourceNode.data._columnsSource = "live"
    vi.mocked(api.loadPipeline).mockResolvedValueOnce({
      nodes: [
        sourceNode,
        makeNode("explore_1", "Claims Explore", "explore"),
      ],
      edges: [{ id: "e1", source: "source_0", target: "explore_1" }],
      preamble: "",
      preserved_blocks: [],
      source_revision: "revision-test",
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
      preserved_blocks: [],
      source_revision: "revision-test",
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
    vi.mocked(api.savePipeline).mockResolvedValueOnce({ file: "demo.py", pipeline_name: "demo", source_revision: "revision-test" })
    render(<App />)
    await waitForAppReady()

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }))

    await waitFor(() => {
      // Toast container is role="alert" per Toast.tsx — the message text
      // includes the saved file path.
      expect(screen.getByRole("alert")).toHaveTextContent(/demo\.py/i)
    })
  })

  it("save-gate: with no working branch, Save opens the modal first, then runs on confirm", async () => {
    // No working branch configured for this clone.
    vi.mocked(api.getWorkingBranch).mockResolvedValue({
      working_branch: null,
      state: "unset",
      errors: [],
      current_branch: "main",
      last_save_sha: null,
      eligible_branches: ["dev"],
      identity_set: true,
      user_name: "U",
      user_email: "u@x.y",
    })
    vi.mocked(api.setWorkingBranch).mockResolvedValue({
      working_branch: "dev",
      state: "ready",
      last_save_sha: "sha123",
    })
    render(<App />)
    await waitForAppReady()

    // The startup check itself surfaces the selection modal (state unset).
    await waitFor(() => {
      expect(screen.getByTestId("working-branch-modal")).toBeInTheDocument()
    })
    // Dismiss the startup modal to isolate the save-gate path.
    fireEvent.keyDown(document, { key: "Escape" })
    await waitFor(() => {
      expect(screen.queryByTestId("working-branch-modal")).toBeNull()
    })

    // Clicking Save must NOT save directly — it re-opens the gate modal.
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }))
    await waitFor(() => {
      expect(screen.getByTestId("working-branch-modal")).toBeInTheDocument()
    })
    expect(vi.mocked(api.savePipeline)).not.toHaveBeenCalled()

    // Confirming the branch sets it and lets the queued save proceed.
    fireEvent.click(screen.getByTestId("working-branch-confirm"))
    await waitFor(() => {
      expect(vi.mocked(api.setWorkingBranch)).toHaveBeenCalledWith("dev", false)
    })
    await waitFor(() => {
      expect(vi.mocked(api.savePipeline)).toHaveBeenCalledTimes(1)
    })
  })

  it("save & commit: Commit flushes a save, opens the milestone modal, then commits", async () => {
    // Healthy clone (default mock state is "ready").
    render(<App />)
    await waitForAppReady()

    fireEvent.click(screen.getByTestId("toolbar-save-menu")) // open the split-button menu
    fireEvent.click(screen.getByTestId("toolbar-save-commit"))

    // The milestone modal appears; nothing has been committed yet.
    await waitFor(() => {
      expect(screen.getByTestId("milestone-commit-modal")).toBeInTheDocument()
    })
    expect(vi.mocked(api.commitMilestone)).not.toHaveBeenCalled()
    // A flush-save was issued so the ledger has the latest editor state.
    await waitFor(() => {
      expect(vi.mocked(api.savePipeline)).toHaveBeenCalled()
    })

    fireEvent.change(screen.getByTestId("milestone-message"), {
      target: { value: "First milestone" },
    })
    fireEvent.click(screen.getByTestId("milestone-confirm"))
    await waitFor(() => {
      expect(vi.mocked(api.commitMilestone)).toHaveBeenCalledWith("First milestone", null, {
        allowFork: false,
      })
    })
  })

  it("commit-gate: with no working branch, Commit chooses a branch first, then commits", async () => {
    // First call (startup) is unset → chooser; after the branch is set, the
    // beforeEach default (ready) takes over so the milestone modal is enabled.
    vi.mocked(api.getWorkingBranch).mockResolvedValueOnce({
      working_branch: null,
      state: "unset",
      errors: [],
      current_branch: "main",
      last_save_sha: null,
      eligible_branches: ["dev"],
      identity_set: true,
      user_name: "U",
      user_email: "u@x.y",
    })
    vi.mocked(api.setWorkingBranch).mockResolvedValue({
      working_branch: "dev",
      state: "ready",
      last_save_sha: "sha123",
    })
    render(<App />)
    await waitForAppReady()

    // Dismiss the startup chooser to isolate the commit-gate path.
    await waitFor(() => expect(screen.getByTestId("working-branch-modal")).toBeInTheDocument())
    fireEvent.keyDown(document, { key: "Escape" })
    await waitFor(() => expect(screen.queryByTestId("working-branch-modal")).toBeNull())

    // Commit with no working branch → re-opens the chooser (queued action).
    fireEvent.click(screen.getByTestId("toolbar-save-menu")) // open the split-button menu
    fireEvent.click(screen.getByTestId("toolbar-save-commit"))
    await waitFor(() => expect(screen.getByTestId("working-branch-modal")).toBeInTheDocument())
    expect(vi.mocked(api.commitMilestone)).not.toHaveBeenCalled()

    // Confirm a branch → save flushes, then the milestone modal opens.
    fireEvent.click(screen.getByTestId("working-branch-confirm"))
    await waitFor(() => expect(vi.mocked(api.setWorkingBranch)).toHaveBeenCalledWith("dev", false))
    await waitFor(() => expect(screen.getByTestId("milestone-commit-modal")).toBeInTheDocument())
    await waitFor(() => expect(vi.mocked(api.savePipeline)).toHaveBeenCalled())

    fireEvent.change(screen.getByTestId("milestone-message"), { target: { value: "M1" } })
    fireEvent.click(screen.getByTestId("milestone-confirm"))
    await waitFor(() =>
      expect(vi.mocked(api.commitMilestone)).toHaveBeenCalledWith("M1", null, {
        allowFork: false,
      }),
    )
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

describe("App integration — apiInput emit-port edge reconciliation (Defect 1)", () => {
  // A multi-port apiInput (2 emit tables) with a downstream edge bound
  // to the 'drivers' port. Toggling that table's emit off in the editor
  // must prune the now-orphaned edge from the store AND surface a
  // visible warning toast — never leave the edge silently broken.
  function makeApiInputGraph() {
    const apiNode = makeNode("api_0", "Quote Source", "apiInput")
    apiNode.data.config = {
      path: "data/quotes.json",
      tables: [
        {
          path: "$[:]",
          label: "policies",
          emit: true,
          columns: [
            {
              name: "policy_id",
              path: "$[:].policy_id",
              type: "str",
              status: "Confirmed",
              selected: true,
              levels: null,
            },
          ],
        },
        {
          path: "$[:].drivers[:]",
          label: "drivers",
          emit: true,
          columns: [
            {
              name: "age",
              path: "$[:].drivers[:].age",
              type: "int",
              status: "Confirmed",
              selected: true,
              levels: null,
            },
          ],
        },
      ],
    }
    return {
      nodes: [apiNode, makeNode("polars_1", "Driver Cleanup", "polars")],
      edges: [
        {
          id: "e_drivers",
          source: "api_0",
          target: "polars_1",
          sourceHandle: "drivers",
          targetHandle: null,
        },
      ],
      preamble: "",
      preserved_blocks: [],
      source_revision: "revision-test",
    }
  }

  function makeRenameMigrationGraph(options: { collision?: boolean } = {}) {
    const base = makeApiInputGraph()
    const liveSwitch = makeNode("live_1", "Scenario Router", "liveSwitch")
    liveSwitch.data.config = {
      input_scenario_map: { drivers: "batch", Other_Source: "live" },
      untouched: "live-switch-config",
    }
    const original = makeNode("original_1", "Original Transform", "polars")
    const otherOriginal = makeNode("original_2", "Other Original", "polars")
    const downstreamInstance = makeNode("instance_value", "Downstream Instance", "polars")
    downstreamInstance.data.config = {
      instanceOf: "original_2",
      inputMapping: { original_input: "drivers", stable_value: "Other_Source" },
      untouched: "downstream-instance-config",
    }
    const firstOriginalInstance = makeNode("instance_key_1", "First Original Instance", "polars")
    firstOriginalInstance.data.config = {
      instanceOf: "original_1",
      inputMapping: { drivers: "Mapped_First", stable_key: "Stable_First" },
      untouched: "first-instance-config",
    }
    const secondOriginalInstance = makeNode("instance_key_2", "Second Original Instance", "polars")
    secondOriginalInstance.data.config = {
      instanceOf: "original_1",
      inputMapping: { drivers: "Mapped_Second", stable_key: "Stable_Second" },
      untouched: "second-instance-config",
    }
    const liveSwitchInstance = makeNode("live_instance", "Scenario Router Instance", "polars")
    liveSwitchInstance.data.config = {
      instanceOf: "live_1",
      inputMapping: { Other_Source: "Mapped_Ordinary", stable_key: "Stable_Value" },
      untouched: "live-instance-config",
    }
    const ordinarySource = makeNode(
      "ordinary_source",
      options.collision ? "collision name" : "Other Source",
      "polars",
    )

    return {
      nodes: [
        base.nodes[0],
        ordinarySource,
        liveSwitch,
        original,
        otherOriginal,
        downstreamInstance,
        firstOriginalInstance,
        secondOriginalInstance,
        liveSwitchInstance,
      ],
      edges: [
        {
          id: "e_api_live",
          source: "api_0",
          target: "live_1",
          sourceHandle: "drivers",
          targetHandle: null,
        },
        {
          id: "e_ordinary_live",
          source: "ordinary_source",
          target: "live_1",
          sourceHandle: null,
          targetHandle: null,
        },
        {
          id: "e_api_original",
          source: "api_0",
          target: "original_1",
          sourceHandle: "drivers",
          targetHandle: null,
        },
        {
          id: "e_api_instance",
          source: "api_0",
          target: "instance_value",
          sourceHandle: "drivers",
          targetHandle: null,
        },
        {
          id: "e_ordinary_instance",
          source: "ordinary_source",
          target: "instance_value",
          sourceHandle: null,
          targetHandle: null,
        },
      ],
      preamble: "",
      preserved_blocks: [],
      source_revision: "revision-test",
    }
  }

  function makeSubmodelRenameGraph(
    options: { collision?: boolean; internalCollision?: boolean } = {},
  ) {
    const base = makeApiInputGraph()
    const boundary = makeNode("instance_pricing", "Pricing", "submodel")
    boundary.data.config = { definitionId: "definition_pricing", alias: "pricing" }
    const childRouter = makeNode("child_router", "Child Router", "liveSwitch")
    childRouter.data.config = {
      input_scenario_map: { router_input: "batch", stable_input: "live" },
      untouched: "child-router-config",
    }
    const childValueInstance = makeNode("child_value_instance", "Child Value Instance", "polars")
    childValueInstance.data.config = {
      instanceOf: "unrelated_original",
      inputMapping: { original_input: "value_input", stable_value: "stable_input" },
      untouched: "child-value-config",
    }
    const childKeyInstance = makeNode("child_key_instance", "Child Key Instance", "polars")
    childKeyInstance.data.config = {
      instanceOf: "child_router",
      inputMapping: { router_input: "Mapped_Driver", stable_key: "Stable_Value" },
      untouched: "child-key-config",
    }
    const ordinary = makeNode(
      "ordinary_source",
      options.collision ? "collision name" : "Stable Input",
      "polars",
    )
    const internalCollisionSource = makeNode(
      "internal_collision_source",
      "collision name",
      "polars",
    )

    return {
      nodes: [base.nodes[0], ordinary, boundary],
      edges: [
        {
          id: "e_api_router",
          source: "api_0",
          target: boundary.id,
          sourceHandle: "drivers",
          targetHandle: "in__router_input",
        },
        {
          id: "e_api_value_instance",
          source: "api_0",
          target: boundary.id,
          sourceHandle: "drivers",
          targetHandle: "in__value_input",
        },
        ...(options.collision
          ? [
              {
                id: "e_ordinary_router",
                source: ordinary.id,
                target: boundary.id,
                sourceHandle: null,
                targetHandle: "in__router_input",
              },
            ]
          : []),
      ],
      submodels: {
        definition_pricing: {
          definitionId: "definition_pricing",
          file: "modules/pricing.py",
          graph: {
            nodes: [
              childRouter,
              childValueInstance,
              childKeyInstance,
              ...(options.internalCollision ? [internalCollisionSource] : []),
            ],
            edges: options.internalCollision
              ? [
                  {
                    id: "e_internal_collision_router",
                    source: internalCollisionSource.id,
                    target: childRouter.id,
                    sourceHandle: null,
                    targetHandle: null,
                  },
                ]
              : [],
          },
          inputPorts: [
            {
              portId: "router_input",
              label: "Router input",
              targets: [{ nodeId: childRouter.id, handleId: null }],
            },
            {
              portId: "value_input",
              label: "Value input",
              targets: [{ nodeId: childValueInstance.id, handleId: null }],
            },
            ...(options.collision
              ? [{
                  portId: "ordinary_router",
                  label: "Ordinary router input",
                  targets: [{ nodeId: childRouter.id, handleId: null }],
                }]
              : []),
          ],
          outputPorts: [],
        },
      },
      preamble: "",
      preserved_blocks: [],
      source_revision: "revision-test",
    }
  }

  function configFor(nodeId: string): Record<string, unknown> {
    const node = useGraphStore.getState().nodes.find((candidate) => candidate.id === nodeId)
    expect(node, `node ${nodeId}`).toBeDefined()
    return node!.data.config as Record<string, unknown>
  }

  function graphCommitStateBytes(): string {
    const { nodes, edges, submodels, undoStack, redoStack } = useGraphStore.getState()
    return JSON.stringify({ nodes, edges, submodels, undoStack, redoStack })
  }

  it("toggling a bound table's emit off prunes the orphaned edge and warns", async () => {
    vi.mocked(api.loadPipeline).mockResolvedValueOnce(makeApiInputGraph())
    render(<App />)
    await waitForAppReady()

    expect(useGraphStore.getState().edges).toHaveLength(1)

    // Open the apiInput editor panel.
    fireEvent.click(await screen.findByTestId("node-Quote Source"))

    // Untick the 'drivers' table emit (table index 1).
    const driversEmit = await findEditorTestId("api-input-table-1-emit")
    fireEvent.click(driversEmit)

    // The orphaned edge is pruned from the graph store...
    await waitFor(() => {
      expect(useGraphStore.getState().edges).toHaveLength(0)
    })
    // ...and a visible warning toast reports the disconnection.
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      expect(toasts.some((t) => t.type === "warning" && /source frame no longer exists/.test(t.text))).toBe(true)
    })
  })

  it("W1.3: renaming a CONNECTED port keeps its edge — rebound to the new handle in ONE undo entry", async () => {
    vi.mocked(api.loadPipeline).mockResolvedValueOnce(makeApiInputGraph())
    // Determinism: the default previewNode mock resolves `columns: []`
    // (truthy), and usePipelineAPI stashes `_columns` via history-aware
    // setNodes whenever a preview lands — an asynchronous undo-stack
    // push that can otherwise land inside this test's measurement
    // window (observed under coverage instrumentation). Keep every
    // preview PENDING so the only undo entries this test can observe
    // are the ones it creates. (beforeEach restores the default mock.)
    vi.mocked(api.previewNode).mockImplementation(() => new Promise<never>(() => {}))
    render(<App />)
    await waitForAppReady()

    fireEvent.click(await screen.findByTestId("node-Quote Source"))

    // Rename the bound 'drivers' port (table index 1) by typing several
    // characters. The label is the live handle id, so under the old
    // per-keystroke commit scheme the FIRST keystroke ("driversX" ≠
    // "drivers") destroyed the edge.
    const label = (await findEditorTestId("api-input-table-1-label")) as HTMLInputElement
    const undoDepthBefore = useGraphStore.getState().undoStack.length
    label.focus()
    for (const v of ["driver", "driver_", "driver_risk"]) {
      fireEvent.change(label, { target: { value: v } })
      // The edge survives EVERY intermediate keystroke, still bound to
      // the committed handle — nothing reached the graph yet.
      expect(useGraphStore.getState().edges).toHaveLength(1)
      expect(useGraphStore.getState().edges[0].sourceHandle).toBe("drivers")
    }
    expect(useGraphStore.getState().undoStack.length).toBe(undoDepthBefore)

    // Blur commits the rename atomically: config + edge rebind together.
    fireEvent.blur(label)
    await waitFor(() => {
      expect(useGraphStore.getState().edges[0].sourceHandle).toBe("driver_risk")
    })
    expect(useGraphStore.getState().edges).toHaveLength(1)
    // Exactly one undo-meaningful commit for the whole rename…
    expect(useGraphStore.getState().undoStack.length).toBe(undoDepthBefore + 1)
    // …and no disconnection warning, because nothing was disconnected.
    expect(useToastStore.getState().toasts.some((t) => t.type === "warning")).toBe(false)

    // Undo restores the old label AND the old binding in one step.
    useGraphStore.getState().undo()
    const node = useGraphStore.getState().nodes.find((n) => n.id === "api_0")
    const tables = (node?.data.config as { tables: { label: string }[] }).tables
    expect(tables[1].label).toBe("drivers")
    expect(useGraphStore.getState().edges[0].sourceHandle).toBe("drivers")
  })

  it("renames a frame, all persisted input identities, and every instance key in one undoable commit", async () => {
    vi.mocked(api.loadPipeline).mockResolvedValueOnce(makeRenameMigrationGraph())
    vi.mocked(api.previewNode).mockImplementation(() => new Promise<never>(() => {}))
    render(<App />)
    await waitForAppReady()

    const nodesBefore = JSON.stringify(useGraphStore.getState().nodes)
    const edgesBefore = JSON.stringify(useGraphStore.getState().edges)
    const undoDepthBefore = useGraphStore.getState().undoStack.length
    fireEvent.click(await screen.findByTestId("node-Quote Source"))

    const label = (await findEditorTestId("api-input-table-1-label")) as HTMLInputElement
    fireEvent.change(label, { target: { value: "driver_risk" } })
    fireEvent.blur(label)

    await waitFor(() => {
      expect(useGraphStore.getState().edges.filter((edge) => edge.source === "api_0")).toEqual([
        expect.objectContaining({ id: "e_api_live", sourceHandle: "driver_risk" }),
        expect.objectContaining({ id: "e_api_original", sourceHandle: "driver_risk" }),
        expect.objectContaining({ id: "e_api_instance", sourceHandle: "driver_risk" }),
      ])
    })
    expect(configFor("api_0")).toEqual(
      expect.objectContaining({
        tables: [
          expect.objectContaining({ label: "policies" }),
          expect.objectContaining({ label: "driver_risk" }),
        ],
      }),
    )
    expect(configFor("live_1")).toEqual({
      input_scenario_map: { driver_risk: "batch", Other_Source: "live" },
      untouched: "live-switch-config",
    })
    expect(configFor("instance_value")).toEqual({
      instanceOf: "original_2",
      inputMapping: { original_input: "driver_risk", stable_value: "Other_Source" },
      untouched: "downstream-instance-config",
    })
    expect(configFor("instance_key_1")).toEqual({
      instanceOf: "original_1",
      inputMapping: { driver_risk: "Mapped_First", stable_key: "Stable_First" },
      untouched: "first-instance-config",
    })
    expect(configFor("instance_key_2")).toEqual({
      instanceOf: "original_1",
      inputMapping: { driver_risk: "Mapped_Second", stable_key: "Stable_Second" },
      untouched: "second-instance-config",
    })
    expect(useGraphStore.getState().undoStack.length).toBe(undoDepthBefore + 1)

    await useGraphStore.getState().undo()
    expect(JSON.stringify(useGraphStore.getState().nodes)).toBe(nodesBefore)
    expect(JSON.stringify(useGraphStore.getState().edges)).toBe(edgesBefore)
    expect(useGraphStore.getState().undoStack.length).toBe(undoDepthBefore)
  })

  it("renames an ordinary source and migrates every downstream input identity atomically", async () => {
    vi.mocked(api.loadPipeline).mockResolvedValueOnce(makeRenameMigrationGraph())
    vi.mocked(api.previewNode).mockImplementation(() => new Promise<never>(() => {}))
    render(<App />)
    await waitForAppReady()

    const nodesBefore = JSON.stringify(useGraphStore.getState().nodes)
    const undoDepthBefore = useGraphStore.getState().undoStack.length
    fireEvent.click(await screen.findByTestId("node-Other Source"))

    const label = await screen.findByTestId("node-panel-label-input")
    fireEvent.change(label, { target: { value: "Renamed Source" } })
    fireEvent.blur(label)

    await waitFor(() => {
      expect(useGraphStore.getState().nodes.find((node) => node.id === "ordinary_source")?.data.label)
        .toBe("Renamed Source")
    })
    expect(configFor("live_1")).toEqual({
      input_scenario_map: { drivers: "batch", Renamed_Source: "live" },
      untouched: "live-switch-config",
    })
    expect(configFor("instance_value")).toEqual({
      instanceOf: "original_2",
      inputMapping: { original_input: "drivers", stable_value: "Renamed_Source" },
      untouched: "downstream-instance-config",
    })
    expect(configFor("live_instance")).toEqual({
      instanceOf: "live_1",
      inputMapping: { Renamed_Source: "Mapped_Ordinary", stable_key: "Stable_Value" },
      untouched: "live-instance-config",
    })
    expect(useGraphStore.getState().undoStack.length).toBe(undoDepthBefore + 1)

    await useGraphStore.getState().undo()
    expect(JSON.stringify(useGraphStore.getState().nodes)).toBe(nodesBefore)
    expect(useGraphStore.getState().undoStack.length).toBe(undoDepthBefore)
  })

  it("rejects an ordinary source rename that collides at a downstream target", async () => {
    vi.mocked(api.loadPipeline).mockResolvedValueOnce(makeRenameMigrationGraph())
    vi.mocked(api.previewNode).mockImplementation(() => new Promise<never>(() => {}))
    render(<App />)
    await waitForAppReady()

    fireEvent.click(await screen.findByTestId("node-Other Source"))
    const stateBefore = graphCommitStateBytes()
    const label = await screen.findByTestId("node-panel-label-input")
    fireEvent.change(label, { target: { value: "drivers" } })
    fireEvent.blur(label)

    const error = await screen.findByTestId("node-panel-label-error")
    expect(error).toHaveTextContent(/Scenario Router/)
    expect(error).toHaveTextContent(/drivers/)
    expect(label).toHaveValue("Other Source")
    expect(graphCommitStateBytes()).toBe(stateBefore)
  })

  it("rejects a colliding frame rename before any config, edge, mapping, or history mutation", async () => {
    vi.mocked(api.loadPipeline).mockResolvedValueOnce(makeRenameMigrationGraph({ collision: true }))
    vi.mocked(api.previewNode).mockImplementation(() => new Promise<never>(() => {}))
    render(<App />)
    await waitForAppReady()

    fireEvent.click(await screen.findByTestId("node-Quote Source"))
    const stateBefore = graphCommitStateBytes()
    const label = (await findEditorTestId("api-input-table-1-label")) as HTMLInputElement
    fireEvent.change(label, { target: { value: "collision_name" } })
    fireEvent.blur(label)

    const error = await screen.findByTestId("api-input-table-1-label-error")
    expect(error).toHaveTextContent(/Scenario Router/)
    expect(error).toHaveTextContent(/collision_name/)
    expect(graphCommitStateBytes()).toBe(stateBefore)
  })

  it("updates parent bindings atomically without mutating shared definition identities", async () => {
    const graph = makeSubmodelRenameGraph()
    vi.mocked(api.loadPipeline).mockResolvedValueOnce(graph)
    vi.mocked(api.previewNode).mockImplementation(() => new Promise<never>(() => {}))
    render(<App />)
    await waitForAppReady()

    const assertIdentityPayload = (graphValue: unknown, expectedName: "drivers" | "driver_risk") => {
      const graphState = graphValue as {
        nodes: Array<{ id: string; data: { config: Record<string, unknown> } }>
        edges: Array<{ id: string; sourceHandle?: string | null }>
        submodels: {
          definition_pricing: {
            graph: { nodes: Array<{ id: string; data: { config: Record<string, unknown> } }> }
          }
        }
      }
      const apiConfig = graphState.nodes.find((node) => node.id === "api_0")?.data.config as {
        tables: Array<{ label: string }>
      }
      expect(apiConfig.tables[1].label).toBe(expectedName)
      expect(
        graphState.edges
          .filter((edge) => edge.id === "e_api_router" || edge.id === "e_api_value_instance")
          .map((edge) => edge.sourceHandle),
      ).toEqual([expectedName, expectedName])

      const nestedConfig = (id: string) =>
        graphState.submodels.definition_pricing.graph.nodes.find((node) => node.id === id)?.data.config
      expect(nestedConfig("child_router")).toEqual({
        input_scenario_map: { router_input: "batch", stable_input: "live" },
        untouched: "child-router-config",
      })
      expect(nestedConfig("child_value_instance")).toEqual({
        instanceOf: "unrelated_original",
        inputMapping: { original_input: "value_input", stable_value: "stable_input" },
        untouched: "child-value-config",
      })
      expect(nestedConfig("child_key_instance")).toEqual({
        instanceOf: "child_router",
        inputMapping: { router_input: "Mapped_Driver", stable_key: "Stable_Value" },
        untouched: "child-key-config",
      })
    }
    const storePayload = () => {
      const { nodes, edges, submodels } = useGraphStore.getState()
      return { nodes, edges, submodels }
    }

    fireEvent.click(await screen.findByTestId("node-Quote Source"))
    const label = (await findEditorTestId("api-input-table-1-label")) as HTMLInputElement
    fireEvent.change(label, { target: { value: "driver_risk" } })
    fireEvent.blur(label)

    await waitFor(() => {
      expect(useGraphStore.getState().edges.filter((edge) => edge.source === "api_0")).toEqual([
        expect.objectContaining({ id: "e_api_router", sourceHandle: "driver_risk" }),
        expect.objectContaining({ id: "e_api_value_instance", sourceHandle: "driver_risk" }),
      ])
    })
    assertIdentityPayload(storePayload(), "driver_risk")

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }))
    await waitFor(() => expect(vi.mocked(api.savePipeline)).toHaveBeenCalledOnce())
    assertIdentityPayload(vi.mocked(api.savePipeline).mock.calls[0][0].graph, "driver_risk")

    fireEvent.click(screen.getByTestId("toolbar-undo"))
    await waitFor(() => assertIdentityPayload(storePayload(), "drivers"))
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }))
    await waitFor(() => expect(vi.mocked(api.savePipeline)).toHaveBeenCalledTimes(2))
    assertIdentityPayload(vi.mocked(api.savePipeline).mock.calls[1][0].graph, "drivers")

    fireEvent.click(screen.getByTestId("toolbar-redo"))
    await waitFor(() => assertIdentityPayload(storePayload(), "driver_risk"))
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }))
    await waitFor(() => expect(vi.mocked(api.savePipeline)).toHaveBeenCalledTimes(3))
    assertIdentityPayload(vi.mocked(api.savePipeline).mock.calls[2][0].graph, "driver_risk")
  })

  it("keeps another occurrence binding isolated from an upstream frame rename", async () => {
    const graph = makeSubmodelRenameGraph({ collision: true })
    vi.mocked(api.loadPipeline).mockResolvedValueOnce(graph)
    vi.mocked(api.previewNode).mockImplementation(() => new Promise<never>(() => {}))
    render(<App />)
    await waitForAppReady()

    fireEvent.click(await screen.findByTestId("node-Quote Source"))
    const label = (await findEditorTestId("api-input-table-1-label")) as HTMLInputElement
    fireEvent.change(label, { target: { value: "collision_name" } })
    fireEvent.blur(label)

    await waitFor(() => {
      expect(useGraphStore.getState().edges.filter((edge) => edge.source === "api_0")).toEqual([
        expect.objectContaining({ id: "e_api_router", sourceHandle: "collision_name" }),
        expect.objectContaining({ id: "e_api_value_instance", sourceHandle: "collision_name" }),
      ])
    })
    expect(screen.queryByTestId("api-input-table-1-label-error")).not.toBeInTheDocument()
    expect(useGraphStore.getState().submodels).toEqual(graph.submodels)
  })

  it("keeps internal child edge names isolated from an upstream frame rename", async () => {
    const graph = makeSubmodelRenameGraph({ internalCollision: true })
    vi.mocked(api.loadPipeline).mockResolvedValueOnce(graph)
    vi.mocked(api.previewNode).mockImplementation(() => new Promise<never>(() => {}))
    render(<App />)
    await waitForAppReady()

    fireEvent.click(await screen.findByTestId("node-Quote Source"))
    const label = (await findEditorTestId("api-input-table-1-label")) as HTMLInputElement
    fireEvent.change(label, { target: { value: "collision_name" } })
    fireEvent.blur(label)

    await waitFor(() => {
      expect(useGraphStore.getState().edges.filter((edge) => edge.source === "api_0")).toEqual([
        expect.objectContaining({ id: "e_api_router", sourceHandle: "collision_name" }),
        expect.objectContaining({ id: "e_api_value_instance", sourceHandle: "collision_name" }),
      ])
    })
    expect(screen.queryByTestId("api-input-table-1-label-error")).not.toBeInTheDocument()
    expect(useGraphStore.getState().submodels).toEqual(graph.submodels)
  })

  it("W1.4: blanking a port label in the editor never reaches the graph — no synthesized port, edge intact", async () => {
    vi.mocked(api.loadPipeline).mockResolvedValueOnce(makeApiInputGraph())
    // Same determinism guard as the W1.3 test above: keep previews
    // pending so no async `_columns` stash mutates nodes mid-test.
    vi.mocked(api.previewNode).mockImplementation(() => new Promise<never>(() => {}))
    render(<App />)
    await waitForAppReady()

    fireEvent.click(await screen.findByTestId("node-Quote Source"))

    const label = (await findEditorTestId("api-input-table-1-label")) as HTMLInputElement
    label.focus()
    fireEvent.change(label, { target: { value: "" } })
    fireEvent.blur(label)

    // The commit was refused with visible validation…
    expect(await screen.findByTestId("api-input-table-1-label-error")).toBeTruthy()
    // …config still carries the real label, the edge is untouched, and
    // no `port_<idx>` identity exists anywhere in the graph.
    const node = useGraphStore.getState().nodes.find((n) => n.id === "api_0")
    const tables = (node?.data.config as { tables: { label: string }[] }).tables
    expect(tables[1].label).toBe("drivers")
    expect(useGraphStore.getState().edges).toHaveLength(1)
    expect(useGraphStore.getState().edges[0].sourceHandle).toBe("drivers")
  })

  it("editing a non-port field (column) does NOT prune the still-valid edge", async () => {
    vi.mocked(api.loadPipeline).mockResolvedValueOnce(makeApiInputGraph())
    render(<App />)
    await waitForAppReady()

    fireEvent.click(await screen.findByTestId("node-Quote Source"))

    // Add a column to the drivers table (index 1) — the emit-port set is
    // unchanged (the table is already an emit+selected port), so the
    // 'drivers' edge must remain.
    fireEvent.click(await findEditorTestId("api-input-table-1-add-col"))

    await waitFor(() => {
      // The config write went through (a column was added: 1 seed + 1 new)...
      const node = useGraphStore.getState().nodes.find((n) => n.id === "api_0")
      const tables = (node?.data.config as { tables: { columns: unknown[] }[] }).tables
      expect(tables[1].columns).toHaveLength(2)
    })
    // ...but the still-valid edge is untouched.
    expect(useGraphStore.getState().edges).toHaveLength(1)
  })
  it("migrates a frame rename through an arbitrary canonical occurrence and every fan-out target", async () => {
    const base = makeApiInputGraph()
    const occurrence = makeNode(
      "instance_pricing_secondary",
      "Pricing secondary",
      "submodel",
    )
    occurrence.data.config = {
      definitionId: "definition_pricing",
      alias: "pricing_secondary",
    }
    const childRouter = makeNode("child_router", "Child Router", "liveSwitch")
    childRouter.data.config = {
      input_scenario_map: { drivers: "batch", stable_input: "live" },
      untouched: "child-router-config",
    }
    const childInstance = makeNode(
      "child_value_instance",
      "Child Value Instance",
      "polars",
    )
    childInstance.data.config = {
      instanceOf: "unrelated_original",
      inputMapping: { original_input: "drivers", stable_value: "stable_input" },
      untouched: "child-value-config",
    }
    const graph = {
      nodes: [base.nodes[0], occurrence],
      edges: [
        {
          id: "e_api_policy_data",
          source: "api_0",
          target: occurrence.id,
          sourceHandle: "drivers",
          targetHandle: "in__policy_data",
        },
      ],
      submodels: {
        definition_pricing: {
          definitionId: "definition_pricing",
          file: "modules/pricing.py",
          graph: {
            nodes: [childRouter, childInstance],
            edges: [],
          },
          inputPorts: [
            {
              portId: "policy_data",
              label: "Policy data",
              targets: [
                { nodeId: childRouter.id, handleId: null },
                { nodeId: childInstance.id, handleId: null },
              ],
            },
          ],
          outputPorts: [],
        },
      },
      preamble: "",
      preserved_blocks: [],
      source_revision: "revision-test",
    }
    vi.mocked(api.loadPipeline).mockResolvedValueOnce(graph)
    vi.mocked(api.previewNode).mockImplementation(() => new Promise<never>(() => {}))
    render(<App />)
    await waitForAppReady()

    fireEvent.click(await screen.findByTestId("node-Quote Source"))
    const label = (await findEditorTestId("api-input-table-1-label")) as HTMLInputElement
    fireEvent.change(label, { target: { value: "driver_risk" } })
    fireEvent.blur(label)

    await waitFor(() => {
      expect(useGraphStore.getState().edges).toEqual([
        expect.objectContaining({
          id: "e_api_policy_data",
          sourceHandle: "driver_risk",
        }),
      ])
    })
    const definition = useGraphStore.getState().submodels.definition_pricing as {
      graph: { nodes: Array<{ id: string; data: { config: Record<string, unknown> } }> }
    }
    const nestedConfig = (id: string) =>
      definition.graph.nodes.find((node) => node.id === id)?.data.config
    expect(nestedConfig("child_router")).toEqual({
      input_scenario_map: { drivers: "batch", stable_input: "live" },
      untouched: "child-router-config",
    })
    expect(nestedConfig("child_value_instance")).toEqual({
      instanceOf: "unrelated_original",
      inputMapping: { original_input: "drivers", stable_value: "stable_input" },
      untouched: "child-value-config",
    })
  })

})

describe("App integration — read-only submodel instance", () => {
  it("keeps the shared definition inspectable while disabling every exposed edit surface", async () => {
    const owner = makeNode("instance_inputs_owner", "Inputs", "submodel")
    owner.data.config = {
      definitionId: "definition_inputs",
      alias: "inputs",
    }
    const copy = makeNode("instance_inputs_copy", "Inputs instance", "submodel")
    copy.data.config = {
      definitionId: "definition_inputs",
      alias: "inputs_2",
      instanceOf: owner.id,
    }
    const child = makeNode("claims_input", "Claims input", "dataInput")
    child.data.config = {
      source_type: "file",
      format: "parquet",
      path: "data/claims.parquet",
    }
    vi.mocked(api.loadPipeline).mockResolvedValueOnce({
      nodes: [owner, copy],
      edges: [],
      preamble: "",
      preserved_blocks: [],
      source_file: "main.py",
      source_revision: "revision-owner-copy",
      submodels: {
        definition_inputs: {
          definitionId: "definition_inputs",
          file: "modules/inputs.py",
          graph: { nodes: [child], edges: [] },
          inputPorts: [],
          outputPorts: [],
        },
      },
    })
    render(<App />)
    await waitForAppReady()

    await waitFor(
      () => {
        expect(useGraphStore.getState().nodes.map((node) => node.id)).toEqual([owner.id, copy.id])
      },
      { timeout: 10000 },
    )

    const copyNode = await waitFor(
      () => {
        const element = document.querySelector<HTMLElement>(`[data-id="${copy.id}"]`)
        expect(element).not.toBeNull()
        return element as HTMLElement
      },
      { timeout: 10000 },
    )
    fireEvent.doubleClick(copyNode)
    expect(await screen.findByText("Read-only instance", {}, { timeout: 10000 })).toBeInTheDocument()
    expect(screen.getByTestId("toolbar-undo")).toBeDisabled()
    expect(screen.getByTestId("toolbar-redo")).toBeDisabled()
    expect(screen.getByTestId("toolbar-layout")).toBeDisabled()
    expect(screen.getByTestId("toolbar-utility")).toBeDisabled()
    expect(screen.getByTestId("toolbar-imports")).toBeDisabled()
    expect(screen.getByTestId("toolbar-assistant")).toBeDisabled()
    expect(document.querySelector('nav[aria-label="Node palette"]')).toHaveAttribute("inert")

    const childNode = await screen.findByTestId("node-Claims input", {}, { timeout: 10000 })
    fireEvent.click(childNode)
    expect(await screen.findByDisplayValue("Claims input")).toBeDisabled()
    const nodeIdsBeforeDelete = useGraphStore.getState().nodes.map((node) => node.id)

    fireEvent.keyDown(window, { key: "Delete" })
    expect(useGraphStore.getState().nodes.map((node) => node.id)).toEqual(nodeIdsBeforeDelete)
    fireEvent.contextMenu(childNode)
    expect(screen.queryByTestId("context-menu")).not.toBeInTheDocument()
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
    const closeBtn = await within(aside).findByRole("button", { name: /close/i })
    fireEvent.click(closeBtn)
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

  // The toolbar's Submodel/Instance buttons derive their enabled state from the
  // live selection, so these drive the real store rather than Toolbar props.
  async function selectNodes(ids: string[]): Promise<void> {
    useGraphStore.setState((s) => ({
      nodes: s.nodes.map((n) => ({ ...n, selected: ids.includes(n.id) })),
    }))
    await waitFor(() => {
      expect(useGraphStore.getState().nodes.filter((n) => n.selected)).toHaveLength(ids.length)
    })
  }

  it("Submodel and Instance are inert until the selection can support them", async () => {
    vi.mocked(api.loadPipeline).mockResolvedValueOnce({
      nodes: [makeNode("polars_1", "First"), makeNode("polars_2", "Second")],
      edges: [],
      preamble: "",
      preserved_blocks: [],
      source_file: "main.py",
      source_revision: "revision-selection",
      submodels: {},
    })
    render(<App />)
    await waitForAppReady()

    // Nothing selected — neither action applies.
    expect(screen.getByTestId("toolbar-submodel")).toBeDisabled()
    expect(screen.getByTestId("toolbar-instance")).toBeDisabled()

    // One node: instancing applies, grouping still needs a second node.
    await selectNodes(["polars_1"])
    await waitFor(() => expect(screen.getByTestId("toolbar-instance")).toBeEnabled())
    expect(screen.getByTestId("toolbar-submodel")).toBeDisabled()

    // Two nodes: grouping applies, instancing no longer has a single target.
    await selectNodes(["polars_1", "polars_2"])
    await waitFor(() => expect(screen.getByTestId("toolbar-submodel")).toBeEnabled())
    expect(screen.getByTestId("toolbar-instance")).toBeDisabled()
  })

  it("Submodel opens the naming dialog for the selected nodes", async () => {
    vi.mocked(api.loadPipeline).mockResolvedValueOnce({
      nodes: [makeNode("polars_1", "First"), makeNode("polars_2", "Second")],
      edges: [],
      preamble: "",
      preserved_blocks: [],
      source_file: "main.py",
      source_revision: "revision-group",
      submodels: {},
    })
    render(<App />)
    await waitForAppReady()

    await selectNodes(["polars_1", "polars_2"])
    await waitFor(() => expect(screen.getByTestId("toolbar-submodel")).toBeEnabled())
    fireEvent.click(screen.getByTestId("toolbar-submodel"))

    await waitFor(() => {
      expect(useUIStore.getState().submodelDialog).toEqual({ nodeIds: ["polars_1", "polars_2"] })
    })
  })

  it("Instance creates a linked copy of a plain node, not just submodels", async () => {
    vi.mocked(api.loadPipeline).mockResolvedValueOnce({
      nodes: [makeNode("polars_1", "First")],
      edges: [],
      preamble: "",
      preserved_blocks: [],
      source_file: "main.py",
      source_revision: "revision-instance",
      submodels: {},
    })
    render(<App />)
    await waitForAppReady()

    await selectNodes(["polars_1"])
    await waitFor(() => expect(screen.getByTestId("toolbar-instance")).toBeEnabled())
    fireEvent.click(screen.getByTestId("toolbar-instance"))

    await waitFor(() => {
      const instance = useGraphStore
        .getState()
        .nodes.find((n) => (n.data as { config?: { instanceOf?: string } }).config?.instanceOf === "polars_1")
      expect(instance).toBeDefined()
    })
  })

  it("the branch indicator opens the Version Control pane (mutually exclusive with Utility/Imports)", async () => {
    render(<App />)
    await waitForAppReady()

    // The toolbar Git button was removed; the branch indicator opens the pane.
    fireEvent.click(screen.getByTestId("branch-indicator-name"))
    await waitFor(() => {
      expect(useUIStore.getState().gitOpen).toBe(true)
      expect(useUIStore.getState().utilityOpen).toBe(false)
      expect(useUIStore.getState().importsOpen).toBe(false)
    })
  })
})
