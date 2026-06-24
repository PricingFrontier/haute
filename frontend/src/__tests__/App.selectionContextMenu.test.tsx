/**
 * Right-click context-menu policy in App.tsx (the menu App opens for a hit
 * resolved by useCanvasPan):
 *  - a right-click on a node inside a multi-node selection opens the
 *    SelectionContextMenu seeded with the selected node ids;
 *  - a right-click on a single (unselected) node opens the node menu instead;
 *  - "Group into wrapper" opens the submodel dialog with those ids (Ctrl+G);
 *  - "Delete" removes the selected nodes + their edges (Delete-key path).
 *
 * Mock scaffold mirrors App.findCast.test.tsx. useCanvasPan is mocked so the
 * test can drive its onContextMenu callback directly — the gesture itself
 * (pan vs menu disambiguation, DOM hit-testing) is covered in
 * canvas/__tests__/{panController,useCanvasPan}; jsdom cannot run xyflow's real
 * marquee + right-click choreography, that is e2e territory.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { act, render, screen, fireEvent, cleanup } from "@testing-library/react"

const { canvasPanCapture, canvasSetters, edgeHandlers } = vi.hoisted(() => ({
  // useCanvasPan is mocked so the test can drive its onContextMenu callback
  // directly — the gesture disambiguation (pan vs menu, hit-testing) is covered
  // by canvas/__tests__/{panController,useCanvasPan}. Here we only check that
  // App opens the right menu for a resolved right-click hit.
  canvasPanCapture: {
    onContextMenu: undefined as
      | ((hit: { nodeId: string | null }, clientX: number, clientY: number) => void)
      | undefined,
  },
  canvasSetters: {
    setNodes: vi.fn(),
    setEdges: vi.fn(),
  },
  edgeHandlers: {
    onNodeContextMenu: vi.fn(),
  },
}))

let mockNodes: Array<{ id: string; position: { x: number; y: number }; selected?: boolean; data: Record<string, unknown> }> = []
let mockEdges: Array<{ id: string; source: string; target: string }> = []

vi.mock("../canvas/useCanvasPan", () => ({
  default: (opts: {
    onContextMenu: (hit: { nodeId: string | null }, clientX: number, clientY: number) => void
  }) => {
    canvasPanCapture.onContextMenu = opts.onContextMenu
  },
}))

vi.mock("@xyflow/react", () => ({
  ReactFlow: ({ children }: Record<string, unknown>) => (
    <div data-testid="react-flow">{children as React.ReactNode}</div>
  ),
  ReactFlowProvider: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Background: () => null,
  useStore: (selector: (s: { transform: [number, number, number] }) => unknown) =>
    selector({ transform: [0, 0, 1] }),
  useReactFlow: () => ({
    screenToFlowPosition: vi.fn(() => ({ x: 0, y: 0 })),
    fitView: vi.fn(),
    zoomIn: vi.fn(),
    zoomOut: vi.fn(),
    getInternalNode: vi.fn(),
    getZoom: vi.fn(() => 1),
  }),
  SelectionMode: { Partial: 0 },
  ConnectionMode: { Loose: "loose" },
  BackgroundVariant: { Dots: "dots" },
  MarkerType: { ArrowClosed: "arrowclosed" },
}))

vi.mock("../hooks/useGraphCanvasState", () => ({
  default: () => ({
    nodes: mockNodes,
    edges: mockEdges,
    setNodes: canvasSetters.setNodes,
    setEdges: canvasSetters.setEdges,
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

vi.mock("../hooks/useWebSocketSync", () => ({ default: () => "connected" }))

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
    traceResult: null,
    tracedCell: null,
    handleCellClick: vi.fn(),
    clearTrace: vi.fn(),
    nodesWithStatus: mockNodes,
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
    onConnectEnd: vi.fn(),
    onNodeContextMenu: edgeHandlers.onNodeContextMenu,
    onDragOver: vi.fn(),
    onDrop: vi.fn(),
  }),
}))

vi.mock("../nodes/PipelineNode", () => ({ default: () => null }))
vi.mock("../nodes/SubmodelNode", () => ({ default: () => null }))
vi.mock("../nodes/SubmodelPortNode", () => ({ default: () => null }))
vi.mock("../panels/NodePalette", () => ({ default: () => <div data-testid="palette" /> }))
vi.mock("../panels/GraphContext", () => ({
  GraphProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))
vi.mock("../panels/NodePanel", () => ({ default: () => <div data-testid="node-panel" /> }))
vi.mock("../panels/DataPreview", () => ({ default: () => <div data-testid="data-preview" /> }))
vi.mock("../panels/OptimiserPreview", () => ({ default: () => <div data-testid="optimiser-preview" /> }))
vi.mock("../panels/OptimiserDataPreview", () => ({ default: () => <div data-testid="optimiser-data-preview" /> }))
vi.mock("../panels/ModellingPreview", () => ({ ModellingPreview: () => <div data-testid="modelling-preview" /> }))
vi.mock("../panels/TracePanel", () => ({ default: () => <div data-testid="trace-panel" /> }))
vi.mock("../components/Toast", () => ({ default: () => <div data-testid="toast" /> }))
vi.mock("../components/KeyboardShortcuts", () => ({ default: () => <div data-testid="shortcuts" /> }))
vi.mock("../components/BreadcrumbBar", () => ({ default: () => <div data-testid="breadcrumb" /> }))
vi.mock("../components/Toolbar", () => ({ default: () => <div data-testid="toolbar" /> }))
vi.mock("../panels/UtilityPanel", () => ({ default: () => <div data-testid="utility-panel" /> }))
vi.mock("../panels/ImportsPanel", () => ({ default: () => <div data-testid="imports-panel" /> }))
vi.mock("../panels/GitPanel", () => ({ default: () => <div data-testid="git-panel" /> }))
vi.mock("../components/SubmodelDialog", () => ({ default: () => <div data-testid="submodel-dialog" /> }))
vi.mock("../components/RenameDialog", () => ({ default: () => <div data-testid="rename-dialog" /> }))
vi.mock("../components/NodeSearch", () => ({ default: () => <div data-testid="node-search" /> }))
vi.mock("../components/ErrorBoundary", () => ({
  ErrorBoundary: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))
vi.mock("../api/client", async () => {
  // Preserve real non-network exports (App.tsx subscribes to
  // HAUTE_SESSION_EXPIRED_EVENT at mount); only checkMlflow is stubbed.
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client")
  return {
    HAUTE_SESSION_EXPIRED_EVENT: actual.HAUTE_SESSION_EXPIRED_EVENT,
    checkMlflow: vi.fn(() => Promise.resolve({ mlflow_installed: false })),
  }
})

import App from "../App"
import useUIStore from "../stores/useUIStore"
import useGraphStore from "../stores/useGraphStore"
import useSettingsStore from "../stores/useSettingsStore"

function selectedNodes(ids: string[]) {
  return ids.map((id) => ({
    id,
    position: { x: 0, y: 0 },
    selected: true,
    data: { label: id, nodeType: "polars" },
  }))
}

/** Drive a resolved right-click hit on a node, as useCanvasPan would. */
function rightClickNode(nodeId: string) {
  act(() => {
    canvasPanCapture.onContextMenu?.({ nodeId }, 120, 240)
  })
}

describe("App — multi-select right-click menu", () => {
  beforeEach(() => {
    mockNodes = []
    mockEdges = []
    canvasPanCapture.onContextMenu = undefined
    canvasSetters.setNodes.mockClear()
    canvasSetters.setEdges.mockClear()
    edgeHandlers.onNodeContextMenu.mockClear()
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
      explorePanes: {},
      explorePreviewPanes: {},
    })
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

  it("right-click on a node inside a multi-node selection opens the selection menu", () => {
    mockNodes = selectedNodes(["a", "b"])
    render(<App />)
    expect(screen.queryByTestId("selection-context-menu")).toBeNull()

    rightClickNode("a")

    expect(screen.getByTestId("selection-context-menu")).toBeInTheDocument()
    expect(screen.getByTestId("context-menu-group-submodel")).toBeInTheDocument()
    expect(screen.getByTestId("context-menu-delete-selected")).toBeInTheDocument()
  })

  it("right-click on a single (unselected) node opens the node menu, not the selection menu", () => {
    mockNodes = [
      { id: "a", position: { x: 0, y: 0 }, data: { label: "a", nodeType: "polars" } },
      { id: "b", position: { x: 0, y: 0 }, data: { label: "b", nodeType: "polars" } },
    ]
    render(<App />)

    rightClickNode("a")

    expect(screen.queryByTestId("selection-context-menu")).toBeNull()
    expect(edgeHandlers.onNodeContextMenu).toHaveBeenCalledTimes(1)
    // App passes the resolved node object as the second arg.
    expect(edgeHandlers.onNodeContextMenu.mock.calls[0][1]).toMatchObject({ id: "a" })
  })

  it("'Group into wrapper' opens the submodel dialog with the selected ids", () => {
    mockNodes = selectedNodes(["a", "b"])
    render(<App />)
    rightClickNode("a")

    act(() => {
      fireEvent.click(screen.getByTestId("context-menu-group-submodel"))
    })

    expect(useUIStore.getState().submodelDialog).toEqual({ nodeIds: ["a", "b"] })
    // Menu closes after the action.
    expect(screen.queryByTestId("selection-context-menu")).toBeNull()
  })

  it("'Group into wrapper' is blocked client-side when the selection includes a wrapper", () => {
    mockNodes = [
      { id: "a", position: { x: 0, y: 0 }, selected: true, data: { label: "a", nodeType: "polars" } },
      { id: "w", position: { x: 0, y: 0 }, selected: true, data: { label: "w", nodeType: "submodel" } },
    ]
    render(<App />)
    rightClickNode("a")

    act(() => {
      fireEvent.click(screen.getByTestId("context-menu-group-submodel"))
    })

    // Nesting is rejected before any API round-trip: no dialog opens.
    expect(useUIStore.getState().submodelDialog).toBeNull()
  })

  it("'Delete' removes the selected nodes and any edge touching them", () => {
    mockNodes = selectedNodes(["a", "b"])
    mockEdges = [
      { id: "e_ab", source: "a", target: "b" },
      { id: "e_bc", source: "b", target: "c" },
      { id: "e_cd", source: "c", target: "d" },
    ]
    render(<App />)
    rightClickNode("a")

    act(() => {
      fireEvent.click(screen.getByTestId("context-menu-delete-selected"))
    })

    // Nodes a + b filtered out.
    expect(canvasSetters.setNodes).toHaveBeenCalledTimes(1)
    expect(canvasSetters.setNodes.mock.calls[0][0]).toEqual([])
    // Edges e_ab (a→b) and e_bc (b→c) touch the deleted nodes; e_cd survives.
    expect(canvasSetters.setEdges).toHaveBeenCalledTimes(1)
    expect(canvasSetters.setEdges.mock.calls[0][0]).toEqual([
      { id: "e_cd", source: "c", target: "d" },
    ])
    expect(screen.queryByTestId("selection-context-menu")).toBeNull()
  })
})
