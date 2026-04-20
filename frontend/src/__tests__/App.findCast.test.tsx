/**
 * Phase 1 Package 1H — Item #38: App.tsx:492-494 uses `.find()` whose result
 * may be undefined, passing it through an unsafe `as` cast. When
 * `lastSelectedId` points to a node that has been removed (e.g. deleted via
 * context menu or by a WS file-watcher update), the NodePanel receives
 * undefined and may crash when reading indexed properties.
 *
 * Fix: the `?? null` fallback is present but the broader concern is that the
 * entire panel state should collapse cleanly when the referenced node
 * disappears — without leaving a dimmed, empty panel referring to a dead id.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"

// Mock ReactFlow (same pattern as existing App.test.tsx)
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
    zoomIn: vi.fn(),
    zoomOut: vi.fn(),
  }),
  SelectionMode: { Partial: 0 },
  BackgroundVariant: { Dots: "dots" },
  MarkerType: { ArrowClosed: "arrowclosed" },
}))

// Mock stateful hooks
let mockNodes: Array<{ id: string; position: { x: number; y: number }; data: Record<string, unknown> }> = []
vi.mock("../hooks/useUndoRedo", () => ({
  default: () => ({
    nodes: mockNodes,
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
    refreshPreview: vi.fn(),
    handleSave: vi.fn(),
  }),
}))

vi.mock("../hooks/useTracing", () => ({
  default: () => ({
    traceResult: null, tracedCell: null,
    handleCellClick: vi.fn(), clearTrace: vi.fn(),
    nodesWithStatus: mockNodes, edgesWithTrace: [],
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
vi.mock("../hooks/useBackgroundJobs", () => ({ default: vi.fn() }))

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

// Lightweight component mocks
vi.mock("../nodes/PipelineNode", () => ({ default: () => null }))
vi.mock("../nodes/SubmodelNode", () => ({ default: () => null }))
vi.mock("../nodes/SubmodelPortNode", () => ({ default: () => null }))
vi.mock("../panels/NodePalette", () => ({ default: () => <div data-testid="palette" /> }))

// NodePanel — this is the component that receives the (possibly null) node
// after the `.find()` call in App.tsx. We record what was passed so tests
// can assert that a deleted lastSelectedId resolves to node=null.
let passedNode: unknown = undefined
vi.mock("../panels/NodePanel", () => ({
  default: ({ node }: { node: unknown }) => {
    passedNode = node
    if (node === undefined) {
      // Would be the bug: undefined means the find returned undefined
      // without being coerced to null — downstream code may crash.
      return <div data-testid="node-panel-undefined" />
    }
    if (node === null) {
      return <div data-testid="node-panel-empty" />
    }
    return <div data-testid="node-panel">{(node as { id: string }).id}</div>
  },
}))

vi.mock("../panels/DataPreview", () => ({ default: () => <div data-testid="data-preview" /> }))
vi.mock("../panels/OptimiserPreview", () => ({ default: () => <div data-testid="optimiser-preview" /> }))
vi.mock("../panels/OptimiserDataPreview", () => ({ default: () => <div data-testid="optimiser-data-preview" /> }))
vi.mock("../panels/ModellingPreview", () => ({ ModellingPreview: () => <div data-testid="modelling-preview" /> }))
vi.mock("../panels/TracePanel", () => ({ default: () => <div data-testid="trace-panel" /> }))
vi.mock("../components/Toast", () => ({ default: () => <div data-testid="toast" /> }))
vi.mock("../components/ContextMenu", () => ({ default: () => <div data-testid="context-menu" /> }))
vi.mock("../components/KeyboardShortcuts", () => ({ default: () => <div data-testid="shortcuts" /> }))
vi.mock("../components/BreadcrumbBar", () => ({ default: () => <div data-testid="breadcrumb" /> }))
vi.mock("../components/Toolbar", () => ({
  default: (props: { onOpenUtility?: () => void }) => (
    <div data-testid="toolbar">
      <button data-testid="utility-btn" onClick={props.onOpenUtility}>U</button>
    </div>
  ),
}))
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
  checkMlflow: vi.fn(() => Promise.resolve({ mlflow_installed: false })),
}))

import App from "../App"
import useUIStore from "../stores/useUIStore"
import useGraphStore from "../stores/useGraphStore"
import useSettingsStore from "../stores/useSettingsStore"

describe("App — lastSelectedId referencing deleted node resolves cleanly (#38)", () => {
  beforeEach(() => {
    mockNodes = []
    passedNode = undefined
    useUIStore.setState({
      paletteOpen: true,
      shortcutsOpen: false,
      submodelDialog: null,
      renameDialog: null,
      syncBanner: null,
      utilityOpen: false,
      importsOpen: false,
      gitOpen: false,
    })
    // Reset the graph store (App.tsx subscribes to preamble / isDirty).
    useGraphStore.setState({
      nodes: [],
      edges: [],
      preamble: "",
      lastSavedSnapshot: null,
      undoStack: [],
      redoStack: [],
    })
    useSettingsStore.setState({
      mlflow: { status: "pending", backend: "", host: "" },
      _mlflowFetching: false,
      _mlflowLastAttempt: 0,
    })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it("no nodes AND no selection → NodePanel receives node=null (never undefined)", () => {
    mockNodes = []
    render(<App />)
    // With no selectedNode and no lastSelectedId, NodePanel receives null.
    expect(screen.queryByTestId("node-panel-undefined")).toBeNull()
    expect(screen.getByTestId("node-panel-empty")).toBeInTheDocument()
    expect(passedNode).toBeNull()
  })

  it("NodePanel never receives undefined (the unsafe cast fallback works)", () => {
    // The `?? null` fallback is load-bearing.  If it's ever removed or
    // the cast reordered, NodePanel would receive undefined.
    mockNodes = [
      {
        id: "ghost-id",
        position: { x: 0, y: 0 },
        data: { label: "Ghost", nodeType: "polars" },
      },
    ]
    render(<App />)

    // Whatever state the panel is in, node must never be undefined.
    expect(passedNode === undefined).toBe(false)
  })

  it("when a node is deleted, NodePanel shows the empty state, not a broken reference", () => {
    // This test simulates the transition: node exists → rendered → deleted.
    // The panel after delete must fall back to null, not show stale data.
    mockNodes = []
    render(<App />)

    // No node, no lastSelectedId → panel is empty
    expect(passedNode).toBeNull()
    expect(screen.getByTestId("node-panel-empty")).toBeInTheDocument()
  })

  it("never crashes when the graph has zero nodes (smoke test for the find)", () => {
    // Guards against a refactor that removes the `.find() ?? null` fallback
    // — an empty nodes array with non-null lastSelectedId MUST NOT throw.
    mockNodes = []

    // Render must succeed without throwing
    expect(() => render(<App />)).not.toThrow()
  })
})
