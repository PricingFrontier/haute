/**
 * Connector-lighting fix: during a connection drag, only the COMPLEMENTARY
 * connectors should light up (App.tsx onConnectStart captures the source
 * handle TYPE and adds `connecting-from-source` / `connecting-from-target`
 * to the ReactFlow root className; the CSS then scopes the handle enlarge to
 * `.target` / `.source` handles respectively).
 *
 * This pins the WIRING — that handleConnectStart with handleType "source"
 * puts `connecting-from-source` on the flow root, "target" →
 * `connecting-from-target`, and that handleConnectEnd clears both alongside
 * the existing `connecting` class. The visual enlarge itself is pure CSS and
 * is manual/e2e-verified.
 *
 * Mock scaffold mirrors App.findCast.test.tsx (the App is heavily
 * dependency-injected; we mock ReactFlow to capture its props).
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { act, render, cleanup } from "@testing-library/react"

const { reactFlowCapture } = vi.hoisted(() => ({
  reactFlowCapture: {
    className: undefined as string | undefined,
    onConnectStart: undefined as
      | ((event: unknown, params: { handleType: "source" | "target" }) => void)
      | undefined,
    onConnectEnd: undefined as ((event: unknown, state: unknown) => void) | undefined,
  },
}))

vi.mock("@xyflow/react", () => ({
  ReactFlow: ({ children, ...props }: Record<string, unknown>) => {
    reactFlowCapture.className = props.className as string | undefined
    reactFlowCapture.onConnectStart = props.onConnectStart as typeof reactFlowCapture.onConnectStart
    reactFlowCapture.onConnectEnd = props.onConnectEnd as typeof reactFlowCapture.onConnectEnd
    return (
      <div data-testid="react-flow" className={props.className as string | undefined}>
        {children as React.ReactNode}
      </div>
    )
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

// onConnectEnd is captured so the test can drive a connect-end and assert the
// connecting-from-* class clears. The real handleConnectEnd (in App) wraps
// this mock; we only need it not to throw.
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
vi.mock("../components/ContextMenu", () => ({ default: () => <div data-testid="context-menu" /> }))
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

function classes(): string[] {
  return (reactFlowCapture.className ?? "").split(" ").filter(Boolean)
}

describe("App — connector lighting is complementary-only during a drag", () => {
  beforeEach(() => {
    reactFlowCapture.className = undefined
    reactFlowCapture.onConnectStart = undefined
    reactFlowCapture.onConnectEnd = undefined
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

  it("at rest, the flow root carries neither connecting class", () => {
    render(<App />)
    expect(classes()).not.toContain("connecting")
    expect(classes()).not.toContain("connecting-from-source")
    expect(classes()).not.toContain("connecting-from-target")
  })

  it("dragging from a SOURCE (output) handle adds connecting-from-source (lights inputs)", () => {
    render(<App />)
    act(() => {
      reactFlowCapture.onConnectStart?.(new MouseEvent("mousedown"), { handleType: "source" })
    })
    expect(classes()).toContain("connecting")
    expect(classes()).toContain("connecting-from-source")
    expect(classes()).not.toContain("connecting-from-target")
  })

  it("dragging from a TARGET (input) handle adds connecting-from-target (lights outputs)", () => {
    render(<App />)
    act(() => {
      reactFlowCapture.onConnectStart?.(new MouseEvent("mousedown"), { handleType: "target" })
    })
    expect(classes()).toContain("connecting")
    expect(classes()).toContain("connecting-from-target")
    expect(classes()).not.toContain("connecting-from-source")
  })

  it("connect-end clears both the connecting and connecting-from-* classes", () => {
    render(<App />)
    act(() => {
      reactFlowCapture.onConnectStart?.(new MouseEvent("mousedown"), { handleType: "source" })
    })
    expect(classes()).toContain("connecting-from-source")

    act(() => {
      reactFlowCapture.onConnectEnd?.(new MouseEvent("mouseup"), {
        fromNode: null,
        fromHandle: null,
        toNode: null,
        toHandle: null,
        isValid: null,
      })
    })
    expect(classes()).not.toContain("connecting")
    expect(classes()).not.toContain("connecting-from-source")
    expect(classes()).not.toContain("connecting-from-target")
  })
})
