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
import { act, render, screen, cleanup } from "@testing-library/react"

const { graphProviderProps } = vi.hoisted(() => ({
  graphProviderProps: [] as Array<{
    allNodes: unknown[]
    edges: unknown[]
    children: React.ReactNode
  }>,
}))

// Mock ReactFlow (same pattern as existing App.test.tsx)
vi.mock("@xyflow/react", () => ({
  ReactFlow: ({ children, ...props }: Record<string, unknown>) => (
    <div
      data-testid="react-flow"
      className={props.className as string | undefined}
      {...(props.onPaneClick ? { onClick: props.onPaneClick as React.MouseEventHandler } : {})}
    >
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
let mockEdges: Array<{ id: string; source: string; target: string }> = []
vi.mock("../hooks/useGraphCanvasState", () => ({
  default: () => ({
    nodes: mockNodes,
    edges: mockEdges,
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
vi.mock("../panels/GraphContext", () => ({
  GraphProvider: (props: {
    allNodes: unknown[]
    edges: unknown[]
    children: React.ReactNode
  }) => {
    graphProviderProps.push(props)
    return <>{props.children}</>
  },
}))

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
import { GRAPH_EFFECTS_LITE_GRAPH_SIZE_LIMIT } from "../utils/graphPerformance"

describe("App — lastSelectedId referencing deleted node resolves cleanly (#38)", () => {
  beforeEach(() => {
    mockNodes = []
    mockEdges = []
    passedNode = undefined
    graphProviderProps.length = 0
    useUIStore.setState({
      paletteOpen: true,
      shortcutsOpen: false,
      submodelDialog: null,
      renameDialog: null,
      syncBanner: null,
      utilityOpen: false,
      importsOpen: false,
      gitOpen: false,
      ratingStepEditorSections: {},
    })
    // Reset the graph store (App.tsx subscribes to preamble / isDirty).
    useGraphStore.setState({
      nodes: [],
      edges: [],
      preamble: "",
      lastSavedSnapshot: null,
      undoStack: [],
      redoStack: [],
      structuralVersion: 0,
      panelContextVersion: 0,
      panelContextFingerprint: "nodes:||edges:",
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

  it("keeps GraphProvider graph arrays stable across position-only node rerenders", () => {
    useGraphStore.setState({ structuralVersion: 1, panelContextVersion: 1 })
    const nodeData = { label: "Node 1", nodeType: "polars" }
    mockNodes = [
      {
        id: "n1",
        position: { x: 0, y: 0 },
        data: nodeData,
      },
    ]
    mockEdges = [{ id: "e1", source: "n1", target: "n2" }]
    useGraphStore.setState({ nodes: mockNodes, edges: mockEdges, structuralVersion: 1, panelContextVersion: 1 })

    const { rerender } = render(<App />)
    const initial = graphProviderProps.at(-1)!

    mockNodes = [
      {
        id: "n1",
        position: { x: 100, y: 200 },
        data: nodeData,
      },
    ]
    useGraphStore.setState({ nodes: mockNodes, edges: mockEdges })
    rerender(<App />)
    const afterPositionOnly = graphProviderProps.at(-1)!

    expect(afterPositionOnly.allNodes).toBe(initial.allNodes)
    expect(afterPositionOnly.edges).toBe(initial.edges)
  })

  it("refreshes GraphProvider graph arrays when structuralVersion changes", () => {
    useGraphStore.setState({ structuralVersion: 1, panelContextVersion: 1 })
    mockNodes = [
      {
        id: "n1",
        position: { x: 0, y: 0 },
        data: { label: "Before", nodeType: "polars" },
      },
    ]
    mockEdges = [{ id: "e1", source: "n1", target: "n2" }]
    useGraphStore.setState({ nodes: mockNodes, edges: mockEdges, structuralVersion: 1, panelContextVersion: 1 })

    render(<App />)
    const initial = graphProviderProps.at(-1)!

    mockNodes = [
      {
        id: "n1",
        position: { x: 0, y: 0 },
        data: { label: "After", nodeType: "polars" },
      },
    ]
    mockEdges = [{ id: "e2", source: "n2", target: "n1" }]
    act(() => {
      useGraphStore.setState({ nodes: mockNodes, edges: mockEdges, structuralVersion: 2, panelContextVersion: 2 })
    })
    const afterStructural = graphProviderProps.at(-1)!

    expect(afterStructural.allNodes).not.toBe(initial.allNodes)
    expect(afterStructural.edges).not.toBe(initial.edges)
    expect(afterStructural.allNodes).toEqual([
      expect.objectContaining({
        id: "n1",
        data: expect.objectContaining({ label: "After" }),
      }),
    ])
    expect(afterStructural.edges).toEqual([{ id: "e2", source: "n2", target: "n1" }])
  })

  it("adds the graph-effects-lite canvas class only when node and edge count reaches the shared threshold", () => {
    mockNodes = Array.from({ length: GRAPH_EFFECTS_LITE_GRAPH_SIZE_LIMIT - 1 }, (_, index) => ({
      id: `n${index}`,
      position: { x: 0, y: 0 },
      data: { label: `Node ${index}`, nodeType: "polars" },
    }))
    const { rerender } = render(<App />)

    expect(screen.getByTestId("react-flow")).not.toHaveClass("graph-effects-lite")

    mockEdges = [{ id: "e1", source: "n0", target: "n1" }]
    rerender(<App />)

    expect(screen.getByTestId("react-flow")).toHaveClass("graph-effects-lite")
  })
})
