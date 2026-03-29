/**
 * Tests for App.tsx — the main orchestrator component.
 *
 * Strategy: mock every hook and sub-component to lightweight stubs so we can
 * test orchestration logic, conditional rendering, and UI-store-driven
 * visibility without pulling in the full dependency tree.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, cleanup, fireEvent } from "@testing-library/react"

// ---------------------------------------------------------------------------
// Mock ReactFlow entirely
// ---------------------------------------------------------------------------

vi.mock("@xyflow/react", () => ({
  ReactFlow: ({ children, ...props }: Record<string, unknown>) => (
    <div data-testid="react-flow" {...(props.onPaneClick ? { onClick: props.onPaneClick as React.MouseEventHandler } : {})}>
      {children as React.ReactNode}
    </div>
  ),
  ReactFlowProvider: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Background: () => null,
  useReactFlow: () => ({
    screenToFlowPosition: vi.fn(() => ({ x: 0, y: 0 })),
    fitView: vi.fn(),
  }),
  SelectionMode: { Partial: 0 },
  BackgroundVariant: { Dots: "dots" },
  MarkerType: { ArrowClosed: "arrowclosed" },
}))

// ---------------------------------------------------------------------------
// Mock all hooks to return stable defaults
// ---------------------------------------------------------------------------

const mockSetNodes = vi.fn()
const mockSetEdges = vi.fn()
let mockLoading = false

vi.mock("../hooks/useUndoRedo", () => ({
  default: () => ({
    nodes: [],
    edges: [],
    setNodes: mockSetNodes,
    setEdges: mockSetEdges,
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

const mockSetPreviewData = vi.fn()
vi.mock("../hooks/usePipelineAPI", () => ({
  default: () => ({
    loading: mockLoading,
    previewData: null,
    setPreviewData: mockSetPreviewData,
    nodeStatuses: {},
    fetchPreview: vi.fn(),
    handleSave: vi.fn(),
  }),
}))

vi.mock("../hooks/useTracing", () => ({
  default: () => ({
    traceResult: null,
    tracedCell: null,
    handleCellClick: vi.fn(),
    clearTrace: vi.fn(),
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

vi.mock("../hooks/useKeyboardShortcuts", () => ({
  default: vi.fn(),
}))

vi.mock("../hooks/useBackgroundJobs", () => ({
  default: vi.fn(),
}))

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
    handleDeleteEdge: vi.fn(),
    onNodeContextMenu: vi.fn(),
    onDragOver: vi.fn(),
    onDrop: vi.fn(),
  }),
}))

// ---------------------------------------------------------------------------
// Mock all sub-components to lightweight stubs
// ---------------------------------------------------------------------------

vi.mock("../nodes/PipelineNode", () => ({ default: () => null }))
vi.mock("../nodes/SubmodelNode", () => ({ default: () => null }))
vi.mock("../nodes/SubmodelPortNode", () => ({ default: () => null }))

vi.mock("../panels/NodePalette", () => ({
  default: ({ onCollapse }: { onCollapse: () => void }) => (
    <div data-testid="node-palette">
      <button onClick={onCollapse}>Collapse</button>
    </div>
  ),
}))

vi.mock("../panels/NodePanel", () => ({
  default: ({ onClose }: { onClose?: () => void }) => (
    <div data-testid="node-panel">
      <button data-testid="node-panel-close" onClick={onClose}>Close</button>
    </div>
  ),
}))

vi.mock("../panels/DataPreview", () => ({
  default: () => <div data-testid="data-preview" />,
}))

vi.mock("../panels/OptimiserPreview", () => ({
  default: () => <div data-testid="optimiser-preview" />,
}))

vi.mock("../panels/TracePanel", () => ({
  default: () => <div data-testid="trace-panel" />,
}))

vi.mock("../components/Toast", () => ({
  default: () => <div data-testid="toast" />,
}))

vi.mock("../components/ContextMenu", () => ({
  default: () => <div data-testid="context-menu" />,
}))

vi.mock("../components/KeyboardShortcuts", () => ({
  default: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="shortcuts">
      <button onClick={onClose}>Close</button>
    </div>
  ),
}))

vi.mock("../components/BreadcrumbBar", () => ({
  default: () => <div data-testid="breadcrumb-bar" />,
}))

vi.mock("../components/Toolbar", () => ({
  default: (props: { onOpenUtility?: () => void; onOpenImports?: () => void; onOpenGit?: () => void; onZoomIn?: () => void; onZoomOut?: () => void }) => (
    <div data-testid="toolbar">
      <button data-testid="utility-btn" onClick={props.onOpenUtility}>
        Utility
      </button>
      <button data-testid="imports-btn" onClick={props.onOpenImports}>
        Imports
      </button>
      <button data-testid="git-btn" onClick={props.onOpenGit}>
        Git
      </button>
      <button data-testid="zoom-in-btn" onClick={props.onZoomIn}>
        Zoom In
      </button>
      <button data-testid="zoom-out-btn" onClick={props.onZoomOut}>
        Zoom Out
      </button>
    </div>
  ),
}))

vi.mock("../panels/UtilityPanel", () => ({
  default: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="utility-panel">
      <button onClick={onClose}>Close</button>
    </div>
  ),
}))

vi.mock("../panels/ImportsPanel", () => ({
  default: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="imports-panel">
      <button onClick={onClose}>Close</button>
    </div>
  ),
}))

vi.mock("../panels/GitPanel", () => ({
  default: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="git-panel">
      <button onClick={onClose}>Close</button>
    </div>
  ),
}))

vi.mock("../components/SubmodelDialog", () => ({
  default: () => <div data-testid="submodel-dialog" />,
}))

vi.mock("../components/ErrorBoundary", () => ({
  ErrorBoundary: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

// Mock the API client so the settings store fetchMlflow doesn't make real requests
vi.mock("../api/client", () => ({
  checkMlflow: vi.fn(() => Promise.resolve({ mlflow_installed: false })),
}))

// ---------------------------------------------------------------------------
// Imports (AFTER mocks are declared)
// ---------------------------------------------------------------------------

import App from "../App"
import useUIStore from "../stores/useUIStore"
import useSettingsStore from "../stores/useSettingsStore"

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

beforeEach(() => {
  mockLoading = false

  // Reset UI store to known defaults
  useUIStore.setState({
    paletteOpen: true,
    shortcutsOpen: false,
    submodelDialog: null,
    syncBanner: null,
    dirty: false,
  })

  // Reset settings store MLflow state so fetchMlflow runs cleanly
  useSettingsStore.setState({
    mlflow: { status: "pending", backend: "", host: "" },
    _mlflowFetching: false,
    _mlflowLastAttempt: 0,
  })
})

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("App", () => {
  // ── Basic rendering ─────────────────────────────────────────────

  it("renders without crashing", () => {
    const { container } = render(<App />)
    expect(container.innerHTML.length).toBeGreaterThan(0)
  })

  it("shows loading state when loading is true", () => {
    mockLoading = true
    render(<App />)
    expect(screen.getByText("Loading pipeline...")).toBeInTheDocument()
  })

  it("does not show Toolbar in loading state", () => {
    mockLoading = true
    render(<App />)
    expect(screen.queryByTestId("toolbar")).toBeNull()
  })

  it("shows Toolbar when not loading", () => {
    render(<App />)
    expect(screen.getByTestId("toolbar")).toBeInTheDocument()
  })

  it("shows ReactFlow canvas when not loading", () => {
    render(<App />)
    expect(screen.getByTestId("react-flow")).toBeInTheDocument()
  })

  it("shows BreadcrumbBar", () => {
    render(<App />)
    expect(screen.getByTestId("breadcrumb-bar")).toBeInTheDocument()
  })

  it("shows DataPreview by default (no trace or optimiser preview)", () => {
    render(<App />)
    expect(screen.getByTestId("data-preview")).toBeInTheDocument()
  })

  it("shows NodePanel when no trace result", () => {
    render(<App />)
    expect(screen.getByTestId("node-panel")).toBeInTheDocument()
  })

  it("shows Toast container", () => {
    render(<App />)
    expect(screen.getByTestId("toast")).toBeInTheDocument()
  })

  // ── Node palette ────────────────────────────────────────────────

  it("shows NodePalette when paletteOpen is true", () => {
    render(<App />)
    expect(screen.getByTestId("node-palette")).toBeInTheDocument()
  })

  it("shows palette toggle button when paletteOpen is false", () => {
    useUIStore.setState({ paletteOpen: false })
    render(<App />)
    expect(screen.queryByTestId("node-palette")).toBeNull()
    expect(screen.getByRole("button", { name: "Show node palette" })).toBeInTheDocument()
  })

  it("clicking palette toggle opens palette", () => {
    useUIStore.setState({ paletteOpen: false })
    render(<App />)
    fireEvent.click(screen.getByRole("button", { name: "Show node palette" }))
    expect(useUIStore.getState().paletteOpen).toBe(true)
  })

  // ── Modals ──────────────────────────────────────────────────────

  it("UtilityPanel not shown by default", () => {
    render(<App />)
    expect(screen.queryByTestId("utility-panel")).toBeNull()
  })

  it("clicking utility button opens UtilityPanel", () => {
    render(<App />)
    fireEvent.click(screen.getByTestId("utility-btn"))
    expect(screen.getByTestId("utility-panel")).toBeInTheDocument()
  })

  it("ImportsPanel not shown by default", () => {
    render(<App />)
    expect(screen.queryByTestId("imports-panel")).toBeNull()
  })

  it("clicking imports button opens ImportsPanel", () => {
    render(<App />)
    fireEvent.click(screen.getByTestId("imports-btn"))
    expect(screen.getByTestId("imports-panel")).toBeInTheDocument()
  })

  it("GitPanel not shown by default", () => {
    render(<App />)
    expect(screen.queryByTestId("git-panel")).toBeNull()
  })

  it("clicking git button opens GitPanel", () => {
    render(<App />)
    fireEvent.click(screen.getByTestId("git-btn"))
    expect(screen.getByTestId("git-panel")).toBeInTheDocument()
  })

  it("GitPanel takes priority over UtilityPanel", () => {
    useUIStore.setState({ gitOpen: true, utilityOpen: true })
    render(<App />)
    expect(screen.getByTestId("git-panel")).toBeInTheDocument()
    expect(screen.queryByTestId("utility-panel")).toBeNull()
  })

  it("KeyboardShortcuts not shown by default", () => {
    render(<App />)
    expect(screen.queryByTestId("shortcuts")).toBeNull()
  })

  it("KeyboardShortcuts shown when shortcutsOpen is true", () => {
    useUIStore.setState({ shortcutsOpen: true })
    render(<App />)
    expect(screen.getByTestId("shortcuts")).toBeInTheDocument()
  })

  // ── Sync banner ─────────────────────────────────────────────────

  it("no sync banner by default", () => {
    render(<App />)
    expect(screen.queryByText("File changed on disk")).toBeNull()
  })

  it("sync banner shown when syncBanner has text", () => {
    useUIStore.setState({ syncBanner: "File changed on disk" })
    render(<App />)
    expect(screen.getByText("File changed on disk")).toBeInTheDocument()
  })

  it("dismiss button clears sync banner", () => {
    useUIStore.setState({ syncBanner: "Outdated pipeline" })
    render(<App />)

    // The dismiss button is the one with the X character
    const dismissBtn = screen.getByText("\u2715")
    fireEvent.click(dismissBtn)
    expect(useUIStore.getState().syncBanner).toBeNull()
  })

  // ── SubmodelDialog ──────────────────────────────────────────────

  it("SubmodelDialog not shown when submodelDialog is null", () => {
    render(<App />)
    expect(screen.queryByTestId("submodel-dialog")).toBeNull()
  })

  it("SubmodelDialog shown when submodelDialog is set in store", () => {
    useUIStore.setState({ submodelDialog: { nodeIds: ["n1", "n2"] } })
    render(<App />)
    expect(screen.getByTestId("submodel-dialog")).toBeInTheDocument()
  })

  // ── Close panel clears preview data ─────────────────────────────
  // Regression: closing the optimiser panel left stale previewData,
  // causing DataPreview to re-render a large dataset and freeze the UI.

  // Regression: closing the optimiser panel left stale previewData,
  // causing DataPreview to re-render a large dataset and freeze the UI.
  it("clicking canvas (pane click) clears previewData to prevent stale render", () => {
    mockLoading = false
    render(<App />)
    mockSetPreviewData.mockClear()

    const canvas = screen.getByTestId("react-flow")
    fireEvent.click(canvas)

    expect(mockSetPreviewData).toHaveBeenCalledWith(null)
  })
})
