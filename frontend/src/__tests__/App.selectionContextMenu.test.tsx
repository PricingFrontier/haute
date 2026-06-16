/**
 * Multi-select right-click menu wiring (App.tsx):
 *  - onSelectionContextMenu calls preventDefault (kills the browser menu)
 *    and opens the SelectionContextMenu seeded with the selected node ids.
 *  - "Group into submodel" opens the submodel dialog with those ids
 *    (mirrors Ctrl+G).
 *  - "Delete" removes the selected nodes + their edges (mirrors the
 *    Delete-key path).
 *
 * Mock scaffold mirrors App.findCast.test.tsx — ReactFlow is mocked so the
 * test can drive its onSelectionContextMenu prop directly (jsdom cannot run
 * xyflow's real marquee + right-click choreography; that is e2e territory).
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { act, render, screen, fireEvent, cleanup } from "@testing-library/react"

const { reactFlowCapture, canvasSetters } = vi.hoisted(() => ({
  reactFlowCapture: {
    onSelectionContextMenu: undefined as
      | ((event: unknown, nodes: { id: string }[]) => void)
      | undefined,
  },
  canvasSetters: {
    setNodes: vi.fn(),
    setEdges: vi.fn(),
  },
}))

let mockNodes: Array<{ id: string; position: { x: number; y: number }; selected?: boolean; data: Record<string, unknown> }> = []
let mockEdges: Array<{ id: string; source: string; target: string }> = []

vi.mock("@xyflow/react", () => ({
  ReactFlow: ({ children, ...props }: Record<string, unknown>) => {
    reactFlowCapture.onSelectionContextMenu =
      props.onSelectionContextMenu as typeof reactFlowCapture.onSelectionContextMenu
    return <div data-testid="react-flow">{children as React.ReactNode}</div>
  },
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
    onNodeContextMenu: vi.fn(),
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
vi.mock("../api/client", () => ({
  checkMlflow: vi.fn(() => Promise.resolve({ mlflow_installed: false })),
}))

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

function fireSelectionContextMenu(nodes: { id: string }[]) {
  const preventDefault = vi.fn()
  act(() => {
    reactFlowCapture.onSelectionContextMenu?.(
      { preventDefault, clientX: 120, clientY: 240 },
      nodes,
    )
  })
  return preventDefault
}

describe("App — multi-select right-click menu", () => {
  beforeEach(() => {
    mockNodes = []
    mockEdges = []
    reactFlowCapture.onSelectionContextMenu = undefined
    canvasSetters.setNodes.mockClear()
    canvasSetters.setEdges.mockClear()
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

  it("right-click over a multi-node selection suppresses the browser menu and opens the selection menu", () => {
    mockNodes = selectedNodes(["a", "b"])
    render(<App />)
    expect(screen.queryByTestId("selection-context-menu")).toBeNull()

    const preventDefault = fireSelectionContextMenu([{ id: "a" }, { id: "b" }])

    expect(preventDefault).toHaveBeenCalled()
    expect(screen.getByTestId("selection-context-menu")).toBeInTheDocument()
    expect(screen.getByTestId("context-menu-group-submodel")).toBeInTheDocument()
    expect(screen.getByTestId("context-menu-delete-selected")).toBeInTheDocument()
  })

  it("'Group into submodel' opens the submodel dialog with the selected ids", () => {
    mockNodes = selectedNodes(["a", "b"])
    render(<App />)
    fireSelectionContextMenu([{ id: "a" }, { id: "b" }])

    act(() => {
      fireEvent.click(screen.getByTestId("context-menu-group-submodel"))
    })

    expect(useUIStore.getState().submodelDialog).toEqual({ nodeIds: ["a", "b"] })
    // Menu closes after the action.
    expect(screen.queryByTestId("selection-context-menu")).toBeNull()
  })

  it("'Delete' removes the selected nodes and any edge touching them", () => {
    mockNodes = selectedNodes(["a", "b"])
    mockEdges = [
      { id: "e_ab", source: "a", target: "b" },
      { id: "e_bc", source: "b", target: "c" },
      { id: "e_cd", source: "c", target: "d" },
    ]
    render(<App />)
    fireSelectionContextMenu([{ id: "a" }, { id: "b" }])

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

  it("falls back to the React Flow node list when no node reports selected", () => {
    // Defensive: if graphRef shows nothing selected, use the nodes React Flow
    // handed the callback so the menu still has a target set.
    mockNodes = [
      { id: "a", position: { x: 0, y: 0 }, data: { label: "a", nodeType: "polars" } },
      { id: "b", position: { x: 0, y: 0 }, data: { label: "b", nodeType: "polars" } },
    ]
    render(<App />)
    fireSelectionContextMenu([{ id: "a" }, { id: "b" }])

    act(() => {
      fireEvent.click(screen.getByTestId("context-menu-group-submodel"))
    })
    expect(useUIStore.getState().submodelDialog).toEqual({ nodeIds: ["a", "b"] })
  })
})
