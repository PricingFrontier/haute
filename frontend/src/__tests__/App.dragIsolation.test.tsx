import { memo, type ComponentType, type ReactNode } from "react"
import { act, cleanup, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Node, NodeChange } from "@xyflow/react"

const {
  renderCounts,
  capturedFlowProps,
  spyOnComponentRender,
} = vi.hoisted(() => {
  const counts = {
    toolbar: 0,
    nodePanel: 0,
    dataPreview: 0,
  }
  const flowProps: {
    onNodesChange?: (changes: NodeChange[]) => void
    onNodeClick?: (event: React.MouseEvent, node: Node) => void
  } = {}

  function wrapComponent<P extends object>(
    Component: ComponentType<P>,
    key: keyof typeof counts,
  ): ComponentType<P> {
    const maybeMemo = Component as unknown as {
      $$typeof?: symbol
      type?: ComponentType<P>
      compare?: ((prev: Readonly<P>, next: Readonly<P>) => boolean) | null
    }
    if (maybeMemo.$$typeof === Symbol.for("react.memo") && typeof maybeMemo.type === "function") {
      const Inner = maybeMemo.type
      return memo(function SpiedMemoComponent(props: P) {
        counts[key] += 1
        return <Inner {...props} />
      }, maybeMemo.compare ?? undefined) as unknown as ComponentType<P>
    }
    const Plain = Component
    return function SpiedPlainComponent(props: P) {
      counts[key] += 1
      return <Plain {...props} />
    }
  }

  return {
    renderCounts: counts,
    capturedFlowProps: flowProps,
    spyOnComponentRender: wrapComponent,
  }
})

vi.mock("@xyflow/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@xyflow/react")>()
  const reactFlowInstance = {
    screenToFlowPosition: vi.fn(() => ({ x: 0, y: 0 })),
    fitView: vi.fn(),
    zoomIn: vi.fn(),
    zoomOut: vi.fn(),
    getInternalNode: vi.fn(),
  }
  return {
    ...actual,
    ReactFlow: ({
      children,
      onNodesChange,
      onNodeClick,
    }: {
      children: ReactNode
      onNodesChange?: (changes: NodeChange[]) => void
      onNodeClick?: (event: React.MouseEvent, node: Node) => void
    }) => {
      capturedFlowProps.onNodesChange = onNodesChange
      capturedFlowProps.onNodeClick = onNodeClick
      return <div data-testid="react-flow">{children}</div>
    },
    ReactFlowProvider: ({ children }: { children: ReactNode }) => <div>{children}</div>,
    Background: () => null,
    useReactFlow: () => reactFlowInstance,
    useNodesInitialized: () => false,
    useStore: (selector: (state: { transform: [number, number, number] }) => unknown) =>
      selector({ transform: [0, 0, 1] }),
  }
})

vi.mock("../hooks/useActiveNodeReveal", () => {
  const reveal = { handleMoveStart: () => {}, centreNode: () => {} }
  return { useActiveNodeReveal: () => reveal }
})

vi.mock("../components/BoxSelectionReset", () => ({ default: () => null }))

vi.mock("../hooks/useWebSocketSync", () => ({
  default: () => "connected",
}))

const mockPreviewData = {
  nodeId: "n1",
  nodeLabel: "Transform 1",
  status: "ok" as const,
  row_count: 1,
  column_count: 1,
  columns: [{ name: "x", dtype: "Int64" }],
  preview: [{ x: 42 }],
  error: null,
}

const stableSetPreviewData = vi.fn()
const stableFetchPreview = vi.fn()
const stableCancelPreview = vi.fn()
const stableStopPreview = vi.fn()
const stableRefreshPreview = vi.fn()
const stablePreviewNodeFrame = vi.fn()
const stableHandleSave = vi.fn(() => Promise.resolve(true))
const stableAdoptPipelineDocument = vi.fn()
const stableNodeStatuses = {}

vi.mock("../hooks/usePipelineAPI", () => ({
  default: () => ({
    loading: false,
    loadError: null,
    previewData: mockPreviewData,
    setPreviewData: stableSetPreviewData,
    previewBusy: false,
    nodeStatuses: stableNodeStatuses,
    fetchPreview: stableFetchPreview,
    cancelPreview: stableCancelPreview,
    stopPreview: stableStopPreview,
    refreshPreview: stableRefreshPreview,
    previewNodeFrame: stablePreviewNodeFrame,
    handleSave: stableHandleSave,
    adoptPipelineDocument: stableAdoptPipelineDocument,
  }),
}))

vi.mock("../components/Toolbar", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../components/Toolbar")>()
  return {
    ...actual,
    default: spyOnComponentRender(actual.default, "toolbar"),
  }
})

vi.mock("../panels/NodePanel", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../panels/NodePanel")>()
  return {
    ...actual,
    default: spyOnComponentRender(actual.default, "nodePanel"),
  }
})

vi.mock("../panels/DataPreview", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../panels/DataPreview")>()
  return {
    ...actual,
    default: spyOnComponentRender(actual.default, "dataPreview"),
  }
})

vi.mock("../api/workbench", () => ({
  fetchWorkbenchStatus: vi.fn(() => Promise.resolve({ enabled: false, form: null })),
}))

vi.mock("../api/client", () => ({
  HAUTE_SESSION_EXPIRED_EVENT: "haute:session-expired",
  HAUTE_SESSION_EXPIRED_REASON: "Missing or invalid Haute session token",
  getMlflowDestinations: vi.fn(() =>
    Promise.resolve({
      mlflow_installed: true,
      mlflow_importable: true,
      destinations: [],
      detail: "",
    }),
  ),
  getWorkingBranch: vi.fn(() =>
    Promise.resolve({
      state: "no-repository",
      working_branch: null,
      current_branch: "",
      head_sha: null,
      broken_pairs: [],
    }),
  ),
}))

vi.mock("../panels/ModellingPreview", () => ({
  ModellingPreview: ({ nodeId }: { nodeId: string }) => <div data-testid="modelling-preview">{nodeId}</div>,
}))

import App from "../App"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import { makeTrainResult } from "../test-utils/factories"
import useDocumentStatusStore from "../stores/useDocumentStatusStore"
import useGraphStore from "../stores/useGraphStore"
import useSettingsStore from "../stores/useSettingsStore"
import useUIStore from "../stores/useUIStore"

const initialNodes: Node[] = [
  {
    id: "n1",
    type: "polars",
    position: { x: 0, y: 0 },
    data: {
      label: "Transform 1",
      nodeType: "polars",
      config: { code: "df = df" },
      _functionName: "transform_1",
      _defaultInputName: "transform_1",
    },
  },
  {
    id: "n2",
    type: "polars",
    position: { x: 200, y: 0 },
    data: {
      label: "Transform 2",
      nodeType: "polars",
      config: { code: "df = transform_1" },
      _functionName: "transform_2",
      _defaultInputName: "transform_2",
    },
  },
]

describe("App — chrome drag-frame isolation (FGC-R03)", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "ResizeObserver",
      class ResizeObserver {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    )
    renderCounts.toolbar = 0
    renderCounts.nodePanel = 0
    renderCounts.dataPreview = 0
    capturedFlowProps.onNodesChange = undefined
    capturedFlowProps.onNodeClick = undefined

    useUIStore.setState({
      paletteOpen: true,
      shortcutsOpen: false,
      submodelDialog: null,
      renameDialog: null,
      syncBanner: null,
      utilityOpen: false,
      constantsOpen: false,
      gitOpen: false,
      filesPlace: null,
      assistantOpen: false,
      hoveredNodeId: null,
      traceFocusNodeId: null,
      changeFocus: null,
    })

    useDocumentStatusStore.setState({
      loadStatus: "ready",
      graphSynchronized: true,
      systemFailure: null,
      sourceSelectionTrusted: true,
      capabilities: {
        can_execute: true,
        can_preview: true,
        can_mutate: true,
        can_save: true,
        can_manage_submodels: true,
        can_repair: false,
        reserved_api_input_frame_labels: [],
      },
    })

    useSettingsStore.setState({
      mlflow: {
        status: "pending",
        installed: null,
        importable: null,
        destinations: [],
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

  it("renders Toolbar, NodePanel, and DataPreview at most once across a multi-frame node drag", () => {
    render(<App />)

    act(() => {
      useGraphStore.getState().loadGraphSnapshot({
        nodes: initialNodes,
        edges: [
          {
            id: "e1",
            source: "n1",
            target: "n2",
            data: { _inputName: "transform_1" },
          },
        ],
        preamble: "",
        submodels: {},
      })
    })

    const nodeToSelect = useGraphStore.getState().nodes.find((n) => n.id === "n1")!
    act(() => {
      capturedFlowProps.onNodeClick?.({} as React.MouseEvent, nodeToSelect)
    })

    renderCounts.toolbar = 0
    renderCounts.nodePanel = 0
    renderCounts.dataPreview = 0

    const dragFrames: NodeChange[][] = [
      [{ id: "n1", type: "position", dragging: true, position: { x: 10, y: 15 } }],
      [{ id: "n1", type: "position", dragging: true, position: { x: 25, y: 30 } }],
      [{ id: "n1", type: "position", dragging: true, position: { x: 40, y: 45 } }],
      [{ id: "n1", type: "position", dragging: true, position: { x: 55, y: 60 } }],
      [{ id: "n1", type: "position", dragging: false, position: { x: 70, y: 75 } }],
    ]

    for (const frame of dragFrames) {
      act(() => {
        capturedFlowProps.onNodesChange?.(frame)
      })
    }

    const movedNode = useGraphStore.getState().nodes.find((n) => n.id === "n1")
    expect(movedNode?.position).toEqual({ x: 70, y: 75 })
    expect(useGraphStore.getState().undoStack).toHaveLength(1)

    expect(renderCounts.toolbar).toBeLessThanOrEqual(1)
    expect(renderCounts.nodePanel).toBeLessThanOrEqual(1)
    expect(renderCounts.dataPreview).toBeLessThanOrEqual(1)
  })

  it("shows a training result that lands while its node is the active preview", async () => {
    render(<App />)
    act(() => {
      useGraphStore.getState().loadGraphSnapshot({ nodes: initialNodes, edges: [], preamble: "", submodels: {} })
    })
    const node = useGraphStore.getState().nodes.find((n) => n.id === "n1")!
    act(() => {
      capturedFlowProps.onNodeClick?.({} as React.MouseEvent, node)
    })
    expect(document.querySelector('[data-testid="modelling-preview"]')).toBeNull()

    act(() => {
      useNodeResultsStore.getState().startTrainJob("n1", "job-1", "n1", "config", "live", 0)
      useNodeResultsStore.getState().completeTrainJob("n1", "job-1", makeTrainResult())
    })

    expect(await screen.findByTestId("modelling-preview")).toHaveTextContent("n1")
  })
})
