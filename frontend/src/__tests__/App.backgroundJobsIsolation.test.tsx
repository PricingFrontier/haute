import { act, cleanup, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const mockToolbarRender = vi.hoisted(() => vi.fn())

vi.mock("@xyflow/react", () => ({
  ReactFlow: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="react-flow">{children}</div>
  ),
  ReactFlowProvider: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Background: () => null,
  useReactFlow: () => ({
    screenToFlowPosition: vi.fn(() => ({ x: 0, y: 0 })),
    fitView: vi.fn(),
    zoomIn: vi.fn(),
    zoomOut: vi.fn(),
  }),
  SelectionMode: { Partial: 0 },
  ConnectionMode: { Loose: "loose" },
  BackgroundVariant: { Dots: "dots" },
}))

vi.mock("../hooks/useGraphCanvasState", () => ({
  default: () => ({
    nodes: [],
    edges: [],
    setNodes: vi.fn(),
    setEdges: vi.fn(),
    setNodesRaw: vi.fn(),
    setEdgesRaw: vi.fn(),
    onNodesChange: vi.fn(),
    onEdgesChange: vi.fn(),
    undo: vi.fn(),
    redo: vi.fn(),
    canUndo: false,
    canRedo: false,
  }),
}))

vi.mock("../hooks/useWebSocketSync", () => ({
  default: () => "connected",
}))

vi.mock("../hooks/usePipelineAPI", () => ({
  default: () => ({
    loading: false,
    previewData: null,
    setPreviewData: vi.fn(),
    nodeStatuses: {},
    fetchPreview: vi.fn(),
    cancelPreview: vi.fn(),
    refreshPreview: vi.fn(),
    handleSave: vi.fn(),
  }),
}))

vi.mock("../hooks/useTracing", () => ({
  default: () => ({
    traceResult: null,
    tracedCell: null,
    traceState: { status: "idle" },
    handleCellClick: vi.fn(),
    clearTrace: vi.fn(),
    cancelTrace: vi.fn(),
    retryTrace: vi.fn(),
    nodesWithStatus: [],
    edgesWithTrace: [],
  }),
}))

vi.mock("../hooks/useSubmodelNavigation", () => ({
  default: () => ({
    viewStack: [{ name: "main" }],
    handleDrillIntoSubmodel: vi.fn(),
    handleBreadcrumbNavigate: vi.fn(),
    handleCreateSubmodel: vi.fn(),
    handleDissolveSubmodel: vi.fn(),
  }),
}))

vi.mock("../hooks/useKeyboardShortcuts", () => ({ default: vi.fn() }))

vi.mock("../hooks/useNodeHandlers", () => ({
  default: () => ({
    handleDeleteNode: vi.fn(),
    handleDuplicateNode: vi.fn(),
    handleCreateInstance: vi.fn(),
    handleRenameNode: vi.fn(),
    handleAutoLayout: vi.fn(),
  }),
}))

vi.mock("../hooks/useEdgeHandlers", () => ({
  default: () => ({
    onConnect: vi.fn(),
    onSelectionChange: vi.fn(),
    onNodeClick: vi.fn(),
    handleDeleteEdge: vi.fn(),
    onNodeContextMenu: vi.fn(),
    onDragOver: vi.fn(),
    onDrop: vi.fn(),
  }),
}))

vi.mock("../nodes/PipelineNode", () => ({ default: () => null }))
vi.mock("../nodes/SubmodelNode", () => ({ default: () => null }))
vi.mock("../nodes/SubmodelPortNode", () => ({ default: () => null }))
vi.mock("../panels/NodePalette", () => ({ default: () => <div data-testid="palette" /> }))
vi.mock("../panels/NodePanel", () => ({ default: () => <div data-testid="node-panel" /> }))
vi.mock("../panels/DataPreview", () => ({ default: () => <div data-testid="data-preview" /> }))
vi.mock("../panels/OptimiserPreview", () => ({ default: () => <div data-testid="optimiser-preview" /> }))
vi.mock("../panels/OptimiserDataPreview", () => ({ default: () => <div data-testid="optimiser-data-preview" /> }))
vi.mock("../panels/ModellingPreview", () => ({ ModellingPreview: () => <div data-testid="modelling-preview" /> }))
vi.mock("../panels/TracePanel", () => ({ default: () => <div data-testid="trace-panel" /> }))
vi.mock("../components/Toast", () => ({ default: () => <div data-testid="toast" /> }))
vi.mock("../components/ContextMenu", () => ({ default: () => <div data-testid="context-menu" /> }))
vi.mock("../components/KeyboardShortcuts", () => ({ default: () => <div data-testid="shortcuts" /> }))
vi.mock("../components/BreadcrumbBar", () => ({ default: () => <div data-testid="breadcrumb" /> }))
vi.mock("../components/Toolbar", async () => {
  const { default: useNodeResultsStore } =
    await vi.importActual<typeof import("../stores/useNodeResultsStore")>("../stores/useNodeResultsStore")

  return {
    default: function MockToolbar(props: { nodeCount: number; dirty: boolean }) {
      const jobCountSignature = useNodeResultsStore(
        (s) => `${Object.keys(s.solveJobs).length}:${Object.keys(s.trainJobs).length}`,
      )
      const [solveJobCount, trainJobCount] = jobCountSignature.split(":").map(Number)

      mockToolbarRender({
        nodeCount: props.nodeCount,
        dirty: props.dirty,
        solveJobCount,
        trainJobCount,
      })

      return <div role="toolbar" aria-label="pipeline toolbar" />
    },
  }
})
vi.mock("../panels/UtilityPanel", () => ({ default: () => <div data-testid="utility-panel" /> }))
vi.mock("../panels/ImportsPanel", () => ({ default: () => <div data-testid="imports-panel" /> }))
vi.mock("../panels/GitPanel", () => ({ default: () => <div data-testid="git-panel" /> }))
vi.mock("../components/SubmodelDialog", () => ({ default: () => <div data-testid="submodel-dialog" /> }))
vi.mock("../components/RenameDialog", () => ({ default: () => <div data-testid="rename-dialog" /> }))
vi.mock("../components/NodeSearch", () => ({ default: () => <div data-testid="node-search" /> }))
vi.mock("../components/ErrorBoundary", () => ({
  ErrorBoundary: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))
vi.mock("../api/client", () => ({
  HAUTE_SESSION_EXPIRED_EVENT: "haute:session-expired",
  HAUTE_SESSION_EXPIRED_REASON: "Missing or invalid Haute session token",
  checkMlflow: vi.fn(() => Promise.resolve({ mlflow_installed: false })),
  getWorkingBranch: vi.fn(() => Promise.resolve({
    state: "no-repository",
    working_branch: null,
    current_branch: "",
    head_sha: null,
  })),
  getOptimiserStatus: vi.fn(() => Promise.resolve({ status: "running", progress: 0 })),
  getTrainStatus: vi.fn(() => Promise.resolve({ status: "running", progress: 0 })),
  getExploreStatus: vi.fn(() => Promise.resolve({ status: "running", progress: 0, message: "running", result: null })),
  getExploreCacheSnapshot: vi.fn(() => Promise.resolve({ state: "missing", message: "No cache", result: null })),
}))

import App from "../App"
import useGraphStore from "../stores/useGraphStore"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import useSettingsStore from "../stores/useSettingsStore"
import useUIStore from "../stores/useUIStore"

function resetStores(): void {
  useGraphStore.setState({
    nodes: [],
    edges: [],
    preamble: "",
    lastSavedSnapshot: null,
    undoStack: [],
    redoStack: [],
    dirty: false,
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
    hoveredNodeId: null,
    nodeSearchOpen: false,
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
  })
}

function startBackgroundJobs(): void {
  useNodeResultsStore.getState().startSolveJob("optimizer_1", "solve-job-1", "Optimizer", {}, "solve-config", "live", 0)
  useNodeResultsStore.getState().startTrainJob("model_1", "train-job-1", "Model", "train-config", "live", 0)
}

function updateBackgroundJobProgress(): void {
  useNodeResultsStore.getState().updateSolveProgress("optimizer_1", {
    status: "running",
    progress: 0.4,
    message: "Solving",
    elapsed_seconds: 2,
  })
  useNodeResultsStore.getState().updateTrainProgress("model_1", {
    status: "running",
    progress: 0.7,
    message: "Training",
    iteration: 7,
    total_iterations: 10,
    train_loss: { loss: 0.12 },
    elapsed_seconds: 3,
  })
}

describe("App background job polling isolation", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    resetStores()
    mockToolbarRender.mockClear()
  })

  afterEach(() => {
    cleanup()
    vi.clearAllTimers()
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it("wires the toolbar probe to active background job counts", async () => {
    render(<App />)
    expect(mockToolbarRender).toHaveBeenLastCalledWith(
      expect.objectContaining({ solveJobCount: 0, trainJobCount: 0 }),
    )
    const renderCountBeforeJobs = mockToolbarRender.mock.calls.length

    await act(async () => {
      startBackgroundJobs()
    })

    expect(mockToolbarRender.mock.calls.length).toBeGreaterThan(renderCountBeforeJobs)
    expect(mockToolbarRender).toHaveBeenLastCalledWith(
      expect.objectContaining({ solveJobCount: 1, trainJobCount: 1 }),
    )
  })

  it("does not rerender the editor toolbar when solve or train progress changes", async () => {
    startBackgroundJobs()
    render(<App />)
    expect(mockToolbarRender).toHaveBeenCalledTimes(1)
    expect(mockToolbarRender).toHaveBeenLastCalledWith(
      expect.objectContaining({ solveJobCount: 1, trainJobCount: 1 }),
    )

    await act(async () => {
      updateBackgroundJobProgress()
    })

    expect(mockToolbarRender).toHaveBeenCalledTimes(1)
  })
})
